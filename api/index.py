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

_pools = {}

try:
    from api.tally import get_tally_summary
except ImportError:
    try:
        from tally import get_tally_summary
    except ImportError:
        get_tally_summary = None


def get_pool(institute_type: str = "college"):
    inst = str(institute_type).lower()
    if inst not in _pools:
        dsn = f"{ORACLE_HOST}:{ORACLE_PORT}/{ORACLE_SERVICE}"
        if inst == "school":
            _pools[inst] = oracledb.create_pool(user=SCHOOL_DB_USER, password=SCHOOL_DB_PASS, dsn=dsn, min=1, max=10, increment=1)
        else:
            _pools[inst] = oracledb.create_pool(user=COLLEGE_DB_USER, password=COLLEGE_DB_PASS, dsn=dsn, min=1, max=10, increment=1)
    return _pools[inst]

def get_connection(institute_type: str = "college"):
    try:
        return get_pool(institute_type).acquire()
    except Exception:
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
        
        # Initialize defaults
        synced = pending = new_null = left_l = passout_p = total = 0
        sync_percentage = 100.0
        student_records = []
        total_ob_amt = total_due_amt = total_receipts_amt = total_discount_amt = total_adv_adj_amt = total_refund_amt = net_outstanding_balance = 0.0
        total_invoices_count = total_vouchers_count = 0
        transaction_records = []

        if module_tab == "transaction":
            import datetime
            today_obj = datetime.date.today()
            today_str = today_obj.strftime("%Y-%m-%d")
            cur_year = today_obj.year
            cur_month = today_obj.month
            fy_start_year = cur_year if cur_month >= 4 else cur_year - 1
            default_fy_start = f"{fy_start_year}-04-01"

            # Financial Totals for Transaction Module (Exact eLOGiPay ERP Procedure Calculation)
            f_date = clean_from_date if clean_from_date else default_fy_start
            t_date = clean_to_date if clean_to_date else today_str

            cur.execute(f"""
                WITH STUDENT_INFO AS (
                  SELECT SM.STUDENT_ID, SM.ENRL_NO
                  FROM {schema}.STUDENT_MASTER_DATA SM
                  LEFT JOIN {schema}.STUDENT_CLASS_MASTER CM ON CM.STUDENT_CLASS_ID = SM.STUDENT_CLASS_ID
                  WHERE SM.STUDENT_STATUS IS NULL AND SM.ACTIVE_STATUS_ID = 1 AND SM.COMPANY_ID = :cid
                ),
                DistinctFD AS (
                  SELECT DISTINCT ENRL_NO, INVOICE_NO, INVOICE_TYPE, INVOICE_DATE, AMOUNT
                  FROM {schema}.STUDENT_FEE_DETAILS
                  WHERE COMPANY_ID = :cid
                ),
                PaymentTotal AS (
                  SELECT INVOICE_NO, SUM(NVL(AMOUNT, 0)) AS RECEIPT
                  FROM {schema}.STUDENT_FEE_PAYMENT_DTLS
                  WHERE REMARKS NOT IN ('Advance')
                  GROUP BY INVOICE_NO
                ),
                OPENING_BAL AS (
                  SELECT FD.ENRL_NO,
                    SUM(CASE WHEN FD.INVOICE_TYPE IN ('FE', 'OB', 'Bounce', 'REFUND') THEN NVL(FD.AMOUNT, 0) ELSE 0 END)
                    - SUM(CASE WHEN FD.INVOICE_TYPE IN ('DI', 'AD') THEN NVL(FD.AMOUNT, 0) ELSE 0 END)
                    - SUM(NVL(P.RECEIPT, 0)) AS OPENING_AMOUNT
                  FROM DistinctFD FD
                  LEFT JOIN PaymentTotal P ON FD.INVOICE_NO = P.INVOICE_NO
                  WHERE FD.INVOICE_DATE < TO_DATE(:fdate, 'YYYY-MM-DD')
                  GROUP BY FD.ENRL_NO
                ),
                CURRENT_PERIOD AS (
                  SELECT FD.ENRL_NO,
                    SUM(CASE WHEN FD.INVOICE_TYPE IN ('FE', 'OB', 'Bounce') THEN NVL(FD.AMOUNT, 0) ELSE 0 END) AS DUE_AMOUNT,
                    SUM(CASE WHEN FD.INVOICE_TYPE = 'DI' THEN NVL(FD.AMOUNT, 0) ELSE 0 END) AS DISCOUNT,
                    SUM(CASE WHEN FD.INVOICE_TYPE = 'REFUND' THEN NVL(FD.AMOUNT, 0) ELSE 0 END) AS REFUND_AMOUNT,
                    SUM(CASE WHEN FD.INVOICE_TYPE = 'AD' THEN NVL(FD.AMOUNT, 0) ELSE 0 END) AS ADVANCE_AMOUNT,
                    SUM(NVL(P.RECEIPT, 0)) AS RECEIPT
                  FROM DistinctFD FD
                  LEFT JOIN PaymentTotal P ON FD.INVOICE_NO = P.INVOICE_NO
                  WHERE FD.INVOICE_DATE BETWEEN TO_DATE(:fdate, 'YYYY-MM-DD') AND TO_DATE(:tdate, 'YYYY-MM-DD')
                  GROUP BY FD.ENRL_NO
                ),
                STUDENT_TOTALS AS (
                  SELECT SI.ENRL_NO,
                    TRUNC(MAX(NVL(OB.OPENING_AMOUNT, 0)), 2) AS OPENING_BALANCE,
                    TRUNC(MAX(NVL(CP.DUE_AMOUNT, 0)), 2) AS DUE_AMOUNT,
                    TRUNC(MAX(NVL(CP.REFUND_AMOUNT, 0)), 2) AS REFUND_AMOUNT,
                    TRUNC(MAX(NVL(CP.ADVANCE_AMOUNT, 0)), 2) AS ADVANCE_ADJUSTED,
                    TRUNC(MAX(NVL(CP.DISCOUNT, 0)), 2) AS DISCOUNT,
                    TRUNC(MAX(NVL(CP.RECEIPT, 0)), 2) AS PAYMENT
                  FROM STUDENT_INFO SI
                  LEFT JOIN OPENING_BAL OB ON OB.ENRL_NO = SI.ENRL_NO
                  LEFT JOIN CURRENT_PERIOD CP ON CP.ENRL_NO = SI.ENRL_NO
                  WHERE EXISTS (
                    SELECT 1 FROM DistinctFD FD
                    WHERE FD.ENRL_NO = SI.ENRL_NO
                      AND (FD.INVOICE_DATE < TO_DATE(:fdate, 'YYYY-MM-DD')
                           OR FD.INVOICE_DATE BETWEEN TO_DATE(:fdate, 'YYYY-MM-DD') AND TO_DATE(:tdate, 'YYYY-MM-DD'))
                  )
                  GROUP BY SI.ENRL_NO
                )
                SELECT
                  NVL(SUM(OPENING_BALANCE), 0),
                  NVL(SUM(DUE_AMOUNT), 0),
                  NVL(SUM(PAYMENT), 0),
                  NVL(SUM(DISCOUNT), 0),
                  NVL(SUM(ADVANCE_ADJUSTED), 0),
                  NVL(SUM(REFUND_AMOUNT), 0)
                FROM STUDENT_TOTALS
            """, {"cid": company_id, "fdate": f_date, "tdate": t_date})
            fin_row = cur.fetchone() or (0, 0, 0, 0, 0, 0)
            total_ob_amt = float(fin_row[0] or 0)
            total_due_amt = float(fin_row[1] or 0)
            total_receipts_amt = float(fin_row[2] or 0)
            total_discount_amt = float(fin_row[3] or 0)
            total_adv_adj_amt = float(fin_row[4] or 0)
            total_refund_amt = float(fin_row[5] or 0)

            net_outstanding_balance = (total_due_amt + total_ob_amt + total_refund_amt) - (total_discount_amt + total_adv_adj_amt + total_receipts_amt)

            # Recent receipt vouchers for transaction list
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
        else:
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
            row = cur.fetchone() or (0, 0, 0, 0, 0, 0)
            synced = row[0] or 0
            pending = row[1] or 0
            new_null = row[2] or 0
            left_l = row[3] or 0
            passout_p = row[4] or 0
            total = row[5] or 0
            sync_percentage = round((synced / total * 100), 1) if total > 0 else 100.0

            # Dynamic Filtered Students Query for Master Tab
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
        
        # Tally Metrics from Neon DB
        tally_data = {
            "has_data": False,
            "opening_balance": 0.0,
            "due_amount": 0.0,
            "receipt_amount": 0.0,
            "net_balance": 0.0,
            "message": "Not received data from Tally of today"
        }
        if module_tab == "transaction" and get_tally_summary:
            try:
                ts = get_tally_summary(company_id, institute_type, f_date, t_date)
                if ts:
                    tally_data = ts
            except Exception as te:
                print(f"[NeonDB] Tally fetch error: {te}")
        
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
            "from_date": clean_from_date or (f_date if module_tab == "transaction" else ""),
            "to_date": clean_to_date or (t_date if module_tab == "transaction" else ""),
            "transaction_records": transaction_records,
            # Tally Synced Metrics from Neon DB
            "tally_has_data": bool(tally_data.get("has_data", False)),
            "tally_is_realtime": bool(tally_data.get("is_realtime", False)),
            "tally_message": str(tally_data.get("message") or ""),
            "tally_last_synced_date": str(tally_data.get("last_synced_date") or ""),
            "tally_opening": float(tally_data.get("opening_balance") or 0.0),
            "tally_due": float(tally_data.get("due_amount") or 0.0),
            "tally_receipts": float(tally_data.get("receipt_amount") or 0.0),
            "tally_balance": float(tally_data.get("net_balance") or 0.0),
            "tally_updated_at": str(tally_data.get("updated_at") or "")
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
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else ""
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}

        if "/login" in parsed.path:
            res, code = handle_login(data)
            self._send_json(res, code)
        elif "/tally" in parsed.path:
            try:
                from api.tally import handle_tally_post
                res, code = handle_tally_post(data)
                self._send_json(res, code)
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)}, 500)
        else:
            self._send_json({"error": "Endpoint not found"}, 404)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "/metrics" in parsed.path:
            res, code = handle_metrics(params)
            self._send_json(res, code)
        elif "/keepalive" in parsed.path:
            try:
                from api.keepalive import ping_neon_database
                res, code = ping_neon_database()
                self._send_json(res, code)
            except Exception as e:
                self._send_json({"status": "error", "error": str(e)}, 500)
        elif "/tally" in parsed.path:
            try:
                from api.tally import get_tally_summary
                cid = params.get("company_id", [""])[0]
                entity = params.get("entity", ["school"])[0]
                f_date = params.get("from_date", [""])[0]
                t_date = params.get("to_date", [""])[0]
                summary = get_tally_summary(cid, entity, f_date, t_date)
                if summary:
                    self._send_json({"status": "success", "data": summary}, 200)
                else:
                    self._send_json({"status": "not_found", "message": "No Tally summary recorded"}, 404)
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)}, 500)
        else:
            self._send_json({"status": "SyncTally API Online"}, 200)
