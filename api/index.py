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
                    "company_code": row[2] or f"ID-{row[0]}",
                    "institute_type": inst_type
                }, 200
                
        except Exception as e:
            pass
        finally:
            if conn:
                conn.close()
                
    return {"error": f"Company '{code_input}' not found in database. Please check code."}, 404

def handle_metrics(params):
    try:
        company_id = int(params.get("company_id", [0])[0])
    except Exception:
        company_id = 0
        
    institute_type = params.get("institute_type", ["college"])[0].lower()
    tab_filter = params.get("tab", ["all"])[0].lower()
    search_q = params.get("q", [""])[0].strip().upper()
    
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
        
        # 2. Aggregated Sync Metrics (S, U, NULL, L, P, Total)
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
        
        # 3. Dynamic Filtered Students Query according to Active Tab
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
        
        student_records = []
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
            "synced_s": synced,
            "pending_u": pending,
            "new_null": new_null,
            "left_l": left_l,
            "passout_p": passout_p,
            "total_students": total,
            "sync_percentage": sync_percentage,
            "student_records": student_records
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
