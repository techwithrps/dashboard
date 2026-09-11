import os
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler
import oracledb

# Configuration
MASTER_PASSWORD = os.environ.get("PORTAL_PASSWORD", "SyncTally@2026")

ORACLE_HOST = os.environ.get("ORACLE_HOST", "103.234.185.186")
ORACLE_PORT = int(os.environ.get("ORACLE_PORT", 1521))
ORACLE_SERVICE = os.environ.get("ORACLE_SERVICE", "xe")

SCHOOL_DB_USER = os.environ.get("SCHOOL_DB_USER", "C##ELOGIPAYSCHOOL")
SCHOOL_DB_PASS = os.environ.get("SCHOOL_DB_PASS", "ELOGIPAYSCHOOL_1520#")

COLLEGE_DB_USER = os.environ.get("COLLEGE_DB_USER", "C##ELOGIPAYCOLLEGE")
COLLEGE_DB_PASS = os.environ.get("COLLEGE_DB_PASS", "ELOGIPAYCOLLEGE_1228#")

def get_connection(institute_type: str = "college"):
    dsn = f"{ORACLE_HOST}:{ORACLE_PORT}/{ORACLE_SERVICE}"
    if str(institute_type).lower() == "school":
        return oracledb.connect(user=SCHOOL_DB_USER, password=SCHOOL_DB_PASS, dsn=dsn)
    else:
        return oracledb.connect(user=COLLEGE_DB_USER, password=COLLEGE_DB_PASS, dsn=dsn)

def handle_login(data):
    code_input = str(data.get("company_code", "")).strip()
    password = str(data.get("password", "")).strip()
    explicit_type = str(data.get("institute_type", "")).strip().lower()
    
    if not code_input:
        return {"error": "Please enter your Company Code (e.g. SRHU, HJHS, SHOOLINI)."}, 400
        
    if password != MASTER_PASSWORD:
        return {"error": "Invalid Portal Password. Please check and try again."}, 401
    
    # Try searching across both schemas (College first, then School)
    schemas_to_search = []
    if explicit_type == "school":
        schemas_to_search = [("school", SCHOOL_DB_USER)]
    elif explicit_type == "college":
        schemas_to_search = [("college", COLLEGE_DB_USER)]
    else:
        schemas_to_search = [("college", COLLEGE_DB_USER), ("school", SCHOOL_DB_USER)]
        
    for inst_type, schema in schemas_to_search:
        conn = None
        try:
            conn = get_connection(inst_type)
            cur = conn.cursor()
            
            is_id = False
            comp_id_val = 0
            try:
                comp_id_val = int(code_input)
                is_id = True
            except ValueError:
                is_id = False
                
            if is_id:
                cur.execute(f"""
                    SELECT COMPANY_ID, COMPANY_NAME, COMPANY_CODE
                    FROM {schema}.COMPANY_MASTER
                    WHERE COMPANY_ID = :cid
                """, {"cid": comp_id_val})
            else:
                cur.execute(f"""
                    SELECT COMPANY_ID, COMPANY_NAME, COMPANY_CODE
                    FROM {schema}.COMPANY_MASTER
                    WHERE UPPER(REPLACE(COMPANY_CODE, ' ', '')) = UPPER(REPLACE(:code, ' ', ''))
                       OR UPPER(COMPANY_CODE) = UPPER(:code)
                """, {"code": code_input})
                
            row = cur.fetchone()
            
            if not row and not is_id:
                cur.execute(f"""
                    SELECT COMPANY_ID, COMPANY_NAME, COMPANY_CODE
                    FROM {schema}.COMPANY_MASTER
                    WHERE UPPER(COMPANY_NAME) LIKE :patt OR UPPER(COMPANY_CODE) LIKE :patt
                """, {"patt": f"%{code_input.upper()}%"})
                row = cur.fetchone()
                
            cur.close()
            
            if row:
                return {
                    "status": "success",
                    "company_id": row[0],
                    "company_name": row[1],
                    "company_code": row[2] or str(row[0]),
                    "institute_type": inst_type
                }, 200
        except Exception as e:
            pass
        finally:
            if conn:
                conn.close()
                
    return {"error": f"No active institution found matching '{code_input}' in College or School systems."}, 404

def handle_metrics(params):
    try:
        company_id = int(params.get("company_id", [0])[0])
    except Exception:
        company_id = 0
        
    institute_type = params.get("institute_type", ["college"])[0].lower()
    module_tab = params.get("module", ["master"])[0].lower()
    tab_filter = params.get("tab", ["all"])[0].lower()
    search_q = params.get("q", [""])[0].strip().upper()
    raw_from_date = params.get("from_date", [""])[0].strip()
    raw_to_date = params.get("to_date", [""])[0].strip()

    def parse_date_param(d_str):
        if not d_str:
            return None
        d_str = d_str.strip()
        if len(d_str) == 10 and d_str[4] == '-' and d_str[7] == '-':
            return d_str
        parts = d_str.replace('/', '-').split('-')
        if len(parts) == 3:
            try:
                if len(parts[0]) == 4:
                    return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
                elif len(parts[2]) == 4:
                    return f"{int(parts[2]):04d}-{int(parts[1]):02d}-{int(parts[0]):02d}"
            except Exception:
                return None
        return None

    clean_from_date = parse_date_param(raw_from_date)
    clean_to_date = parse_date_param(raw_to_date)
    
    schema = COLLEGE_DB_USER if institute_type == "college" else SCHOOL_DB_USER
    conn = None
    try:
        conn = get_connection(institute_type)
        cur = conn.cursor()
        
        # 1. Company Information
        cur.execute(f"SELECT COMPANY_NAME, COMPANY_CODE FROM {schema}.COMPANY_MASTER WHERE COMPANY_ID = :cid", {"cid": company_id})
        c_row = cur.fetchone()
        company_name = c_row[0] if c_row else f"Company ID {company_id}"
        company_code = c_row[1] if c_row else str(company_id)
        
        # 2. Aggregated Master Sync Metrics (S, U, NULL, L, P, Total)
        cur.execute(f"""
            SELECT 
                SUM(CASE WHEN TALLY_SYNC = 'S' THEN 1 ELSE 0 END) AS SYNCED_S,
                SUM(CASE WHEN TALLY_SYNC = 'U' OR TALLY_SYNC = ' ' THEN 1 ELSE 0 END) AS PENDING_U,
                SUM(CASE WHEN TALLY_SYNC IS NULL THEN 1 ELSE 0 END) AS NEW_NULL,
                SUM(CASE WHEN STUDENT_STATUS = 'L' THEN 1 ELSE 0 END) AS LEFT_L,
                SUM(CASE WHEN STUDENT_STATUS = 'P' THEN 1 ELSE 0 END) AS PASSOUT_P,
                COUNT(1) AS TOTAL_STUDENTS
            FROM {schema}.STUDENT_MASTER_DATA
            WHERE COMPANY_ID = :cid
        """, {"cid": company_id})
        row = cur.fetchone()
        
        # 3. Financial Totals for Transaction Module (Dynamic Date Range Supported)
        where_fee = ["COMPANY_ID = :cid"]
        fee_params = {"cid": company_id}
        if clean_from_date:
            where_fee.append("TRUNC(INVOICE_DATE) >= TO_DATE(:fdate, 'YYYY-MM-DD')")
            fee_params["fdate"] = clean_from_date
        if clean_to_date:
            where_fee.append("TRUNC(INVOICE_DATE) <= TO_DATE(:tdate, 'YYYY-MM-DD')")
            fee_params["tdate"] = clean_to_date
        where_fee_sql = " AND ".join(where_fee)

        cur.execute(f"""
            SELECT 
                NVL(SUM(CASE WHEN INVOICE_TYPE IN ('FE', 'FI') THEN AMOUNT ELSE 0 END), 0) AS TOTAL_DUE,
                NVL(SUM(CASE WHEN INVOICE_TYPE = 'OB' THEN AMOUNT ELSE 0 END), 0) AS RAW_OB,
                NVL(SUM(CASE WHEN INVOICE_TYPE = 'DI' THEN AMOUNT ELSE 0 END), 0) AS TOTAL_DISCOUNT,
                NVL(SUM(CASE WHEN INVOICE_TYPE = 'AD' THEN AMOUNT ELSE 0 END), 0) AS ADVANCE_ADJUSTED,
                NVL(SUM(CASE WHEN INVOICE_TYPE = 'REFUND' THEN AMOUNT ELSE 0 END), 0) AS TOTAL_REFUND,
                NVL(SUM(CASE WHEN INVOICE_TYPE = 'Bounce' THEN AMOUNT ELSE 0 END), 0) AS TOTAL_BOUNCE,
                COUNT(DISTINCT INVOICE_NO) AS TOTAL_INVOICES
            FROM {schema}.STUDENT_FEE_DETAILS
            WHERE {where_fee_sql}
        """, fee_params)
        fee_totals = cur.fetchone()
        
        where_pay = ["COMPANY_ID = :cid"]
        pay_params = {"cid": company_id}
        if clean_from_date:
            where_pay.append("TRUNC(PAYMENT_DATE) >= TO_DATE(:fdate, 'YYYY-MM-DD')")
            pay_params["fdate"] = clean_from_date
        if clean_to_date:
            where_pay.append("TRUNC(PAYMENT_DATE) <= TO_DATE(:tdate, 'YYYY-MM-DD')")
            pay_params["tdate"] = clean_to_date
        where_pay_sql = " AND ".join(where_pay)

        cur.execute(f"""
            SELECT 
                NVL(SUM(AMOUNT), 0) AS TOTAL_RECEIPTS,
                COUNT(1) AS TOTAL_PAYMENT_VOUCHERS
            FROM {schema}.STUDENT_FEE_PAYMENT
            WHERE {where_pay_sql}
        """, pay_params)
        pay_totals = cur.fetchone()

        total_due_amt = float(fee_totals[0] or 0)
        raw_ob_amt = float(fee_totals[1] or 0)
        total_discount_amt = float(fee_totals[2] or 0)
        total_adv_adj_amt = float(fee_totals[3] or 0)
        total_refund_amt = float(fee_totals[4] or 0)
        total_bounce_amt = float(fee_totals[5] or 0)
        total_invoices_count = int(fee_totals[6] or 0)
        
        total_receipts_amt = float(pay_totals[0] or 0)
        total_vouchers_count = int(pay_totals[1] or 0)

        # Calculate exact Opening Balance using cutoff (prior to from_date, default 2026-04-01)
        total_ob_amt = raw_ob_amt
        ob_cutoff = clean_from_date if clean_from_date else "2026-04-01"
        try:
            cur.execute(f"""
                WITH DistinctFD AS (
                  SELECT DISTINCT
                    ENRL_NO,
                    INVOICE_NO,
                    INVOICE_TYPE,
                    INVOICE_DATE,
                    AMOUNT
                  FROM {schema}.STUDENT_FEE_DETAILS
                  WHERE COMPANY_ID = :cid
                ),
                PaymentTotal AS (
                  SELECT
                    INVOICE_NO,
                    SUM(NVL(AMOUNT, 0)) AS RECEIPT
                  FROM {schema}.STUDENT_FEE_PAYMENT_DTLS
                  WHERE REMARKS NOT IN ('Advance')
                  GROUP BY INVOICE_NO
                ),
                OPENING_BAL AS (
                  SELECT
                    FD.ENRL_NO,
                    SUM(CASE WHEN FD.INVOICE_TYPE IN ('FE', 'OB', 'Bounce', 'REFUND') THEN NVL(FD.AMOUNT, 0) ELSE 0 END)
                    - SUM(CASE WHEN FD.INVOICE_TYPE IN ('DI', 'AD') THEN NVL(FD.AMOUNT, 0) ELSE 0 END)
                    - SUM(NVL(P.RECEIPT, 0)) AS OPENING_AMOUNT
                  FROM DistinctFD FD
                  LEFT JOIN PaymentTotal P ON FD.INVOICE_NO = P.INVOICE_NO
                  WHERE TRUNC(FD.INVOICE_DATE) < TO_DATE(:cutoff, 'YYYY-MM-DD')
                  GROUP BY FD.ENRL_NO
                )
                SELECT NVL(SUM(OB.OPENING_AMOUNT), 0)
                FROM OPENING_BAL OB
            """, {"cid": company_id, "cutoff": ob_cutoff})
            sp_ob_row = cur.fetchone()
            if sp_ob_row and sp_ob_row[0] is not None:
                calc_val = float(sp_ob_row[0])
                if calc_val != 0 or total_ob_amt == 0:
                    total_ob_amt = calc_val
        except Exception as ob_err:
            # Fallback to raw_ob_amt if prior period calculation is unavailable
            pass
        
        net_outstanding_balance = (total_due_amt + total_ob_amt + total_refund_amt + total_bounce_amt) - (total_adv_adj_amt + total_discount_amt + total_receipts_amt)
        
        # 4. If transaction module requested, fetch recent receipt vouchers
        transaction_records = []
        if module_tab == "transaction":
            where_vouchers = ["COMPANY_ID = :cid"]
            v_params = {"cid": company_id}
            if search_q:
                where_vouchers.append("(UPPER(PAYMENT_NO) LIKE :sq OR UPPER(TRN_ID) LIKE :sq OR UPPER(REMARKS) LIKE :sq)")
                v_params["sq"] = f"%{search_q}%"
            where_v_sql = " AND ".join(where_vouchers)
            
            cur.execute(f"""
                SELECT PAYMENT_ID, PAYMENT_NO, TRN_ID, TO_CHAR(PAYMENT_DATE, 'DD/MM/YYYY') AS P_DATE,
                       AMOUNT, PAYMENT_MODE, TALLY_STATUS, REMARKS,
                       COALESCE(CREATED_ON, PAYMENT_DATE) AS ACT_TIME
                FROM {schema}.STUDENT_FEE_PAYMENT
                WHERE {where_v_sql}
                ORDER BY PAYMENT_ID DESC
                FETCH FIRST 100 ROWS ONLY
            """, v_params)
            for vr in cur.fetchall():
                transaction_records.append({
                    "payment_id": vr[0],
                    "payment_no": vr[1] or "-",
                    "trn_id": vr[2] or "-",
                    "payment_date": vr[3] or "-",
                    "amount": float(vr[4] or 0),
                    "payment_mode": str(vr[5] or "Online/Bank"),
                    "tally_status": vr[6] or "Pending",
                    "remarks": vr[7] or "-",
                    "activity_time": str(vr[8]) if vr[8] else "-"
                })
        
        # 5. Dynamic Filtered Students Query for Master Tab
        student_records = []
        if module_tab != "transaction":
            where_clauses = ["COMPANY_ID = :cid"]
            sql_params = {"cid": company_id}
            
            if tab_filter == "s" or tab_filter == "synced":
                where_clauses.append("TALLY_SYNC = 'S'")
            elif tab_filter == "u" or tab_filter == "pending":
                where_clauses.append("(TALLY_SYNC = 'U' OR TALLY_SYNC = ' ')")
            elif tab_filter == "null" or tab_filter == "new":
                where_clauses.append("TALLY_SYNC IS NULL")
            elif tab_filter == "l" or tab_filter == "left":
                where_clauses.append("STUDENT_STATUS = 'L'")
            elif tab_filter == "p" or tab_filter == "passout":
                where_clauses.append("STUDENT_STATUS = 'P'")
                
            if search_q:
                where_clauses.append("(UPPER(ENRL_NO) LIKE :sq OR UPPER(STUDENT_NAME) LIKE :sq OR UPPER(BRANCH) LIKE :sq)")
                sql_params["sq"] = f"%{search_q}%"
                
            where_sql = " AND ".join(where_clauses)
            
            cur.execute(f"""
                SELECT ENRL_NO, STUDENT_NAME, BRANCH, STUDENT_STATUS, TALLY_SYNC, 
                       COALESCE(UPDATED_ON, DATA_UPDATED_DATE, DATA_POSTED_DATE, CREATED_ON) AS ACT_TIME,
                       STUDENT_ID
                FROM {schema}.STUDENT_MASTER_DATA
                WHERE {where_sql}
                ORDER BY COALESCE(UPDATED_ON, DATA_UPDATED_DATE, DATA_POSTED_DATE, CREATED_ON) DESC NULLS LAST, STUDENT_ID DESC
                FETCH FIRST 100 ROWS ONLY
            """, sql_params)
            
            for r in cur.fetchall():
                student_records.append({
                    "enrl_no": r[0],
                    "student_name": r[1],
                    "class_branch": r[2] or "-",
                    "student_status": r[3] or "Active",
                    "tally_sync": r[4] or "NULL",
                    "activity_time": str(r[5]) if r[5] else "N/A"
                })
            
        cur.close()
        
        synced = row[0] or 0
        pending = row[1] or 0
        new_null = row[2] or 0
        left_l = row[3] or 0
        passout_p = row[4] or 0
        total = row[5] or 0
        
        sync_percentage = round((synced / total * 100), 1) if total > 0 else 100.0
        
        return {
            "company_id": company_id,
            "company_name": company_name,
            "company_code": company_code,
            "institute_type": institute_type,
            # Master Sync Counts
            "synced_s": synced,
            "pending_u": pending,
            "new_null": new_null,
            "left_l": left_l,
            "passout_p": passout_p,
            "total_students": total,
            "sync_percentage": sync_percentage,
            "student_records": student_records,
            # Transaction & Financial Totals
            "total_due": total_due_amt,
            "total_opening_bal": total_ob_amt,
            "total_receipts": total_receipts_amt,
            "total_discount": total_discount_amt,
            "total_advance_adj": total_adv_adj_amt,
            "total_refund": total_refund_amt,
            "net_outstanding": net_outstanding_balance,
            "total_invoices_count": total_invoices_count,
            "total_vouchers_count": total_vouchers_count,
            "from_date": clean_from_date or "",
            "to_date": clean_to_date or "",
            "transaction_records": transaction_records
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500
    finally:
        if conn:
            conn.close()

class handler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_OPTIONS(self):
        self._send_json({"status": "ok"}, 200)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if "/login" in parsed.path:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body) if body else {}
            except Exception:
                data = {}
            res, code = handle_login(data)
            self._send_json(res, code)
        else:
            self._send_json({"error": "Endpoint not found"}, 404)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "/metrics" in parsed.path:
            res, code = handle_metrics(params)
            self._send_json(res, code)
        else:
            self._send_json({"status": "SyncTally API Online"}, 200)
