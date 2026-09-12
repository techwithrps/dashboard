import os
import sys
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler
from decimal import Decimal

# Ensure project root is in sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

DEFAULT_NEON_DB_URL = "postgresql://neondb_owner:npg_feZl30PimbRI@ep-solitary-scene-aea7vq5m-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require"

def get_neon_connection():
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        db_url = os.environ.get("DATABASE_URL", DEFAULT_NEON_DB_URL)
        conn = psycopg2.connect(db_url)
        return conn
    except Exception as e:
        print(f"[NeonDB] Connection error: {e}")
        return None

def normalize_date(d_str):
    if not d_str:
        return None
    d_str = str(d_str).strip()
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

def resolve_company_id(cid_or_code, entity="school"):
    if not cid_or_code:
        return None
    val = str(cid_or_code).strip()
    if val.isdigit():
        return val
    try:
        from api.index import get_connection, COLLEGE_DB_USER, SCHOOL_DB_USER
        schema = COLLEGE_DB_USER if "college" in entity.lower() else SCHOOL_DB_USER
        conn = get_connection(entity)
        cur = conn.cursor()
        cur.execute(f"SELECT COMPANY_ID FROM {schema}.COMPANY_MASTER WHERE UPPER(COMPANY_CODE) = :code", {"code": val.upper()})
        row = cur.fetchone()
        conn.close()
        if row:
            return str(row[0])
    except Exception as e:
        print(f"Resolve company error: {e}")
    return val

def handle_tally_post(data):
    if not isinstance(data, dict):
        return {"status": "error", "message": "Invalid JSON body"}, 400

    raw_cid = str(data.get("company_id") or data.get("company_code") or "").strip()
    if not raw_cid:
        return {"status": "error", "message": "company_id or company_code is required"}, 400

    entity = str(data.get("entity") or data.get("institute_type") or "school").lower().strip()
    table_name = "tally_college_summary" if "college" in entity else "tally_school_summary"
    company_id = resolve_company_id(raw_cid, entity) or raw_cid
    
    import datetime
    today = datetime.date.today()
    fy_start_year = today.year if today.month >= 4 else today.year - 1
    auto_from_date = f"{fy_start_year}-04-01"
    auto_to_date = today.strftime("%Y-%m-%d")

    raw_from_date = data.get("from_date")
    raw_to_date = data.get("to_date")
    
    from_date = normalize_date(raw_from_date) or auto_from_date
    to_date = normalize_date(raw_to_date) or auto_to_date

    try:
        opening_bal = float(data.get("opening_balance") or data.get("opening") or 0.0)
        due_amt = float(data.get("due_amount") or data.get("due") or 0.0)
        receipt_amt = float(data.get("receipt_amount") or data.get("receipts") or 0.0)
        # Calculate balance automatically from the 3 values: (Opening + Due - Receipts)
        net_bal = (opening_bal + due_amt) - receipt_amt
    except Exception as e:
        return {"status": "error", "message": f"Invalid numeric amounts: {str(e)}"}, 400

    conn = get_neon_connection()
    if not conn:
        return {"status": "error", "message": "Failed to connect to Neon PostgreSQL"}, 500

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        upsert_query = f"""
        INSERT INTO {table_name} (
            company_id, from_date, to_date,
            opening_balance, due_amount, receipt_amount, net_balance,
            updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (company_id, from_date, to_date)
        DO UPDATE SET
            opening_balance = EXCLUDED.opening_balance,
            due_amount = EXCLUDED.due_amount,
            receipt_amount = EXCLUDED.receipt_amount,
            net_balance = EXCLUDED.net_balance,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id, company_id, from_date, to_date, opening_balance, due_amount, receipt_amount, net_balance, updated_at;
        """
        cur.execute(upsert_query, (
            company_id, from_date, to_date,
            opening_bal, due_amt, receipt_amt, net_bal
        ))
        row = cur.fetchone()
        conn.commit()

        # Format row for response
        res_data = {
            "id": row["id"],
            "table": table_name,
            "company_id": row["company_id"],
            "from_date": str(row["from_date"]),
            "to_date": str(row["to_date"]),
            "opening_balance": float(row["opening_balance"]),
            "due_amount": float(row["due_amount"]),
            "receipt_amount": float(row["receipt_amount"]),
            "calculated_balance": float(row["net_balance"]),
            "updated_at": str(row["updated_at"])
        }
        return {"status": "success", "message": "Tally 3 metrics synced and balance calculated successfully into Neon DB", "data": res_data}, 200
    except Exception as e:
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}, 500
    finally:
        if conn:
            conn.close()

def get_tally_summary(company_id, institute_type="school", from_date=None, to_date=None):
    import datetime
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    
    table_name = "tally_college_summary" if "college" in str(institute_type).lower() else "tally_school_summary"
    clean_from = normalize_date(from_date)
    clean_to = normalize_date(to_date) or today_str
    cid = str(company_id or "").strip()

    conn = get_neon_connection()
    if not conn:
        return {
            "has_data": False,
            "is_realtime": False,
            "opening_balance": 0.0,
            "due_amount": 0.0,
            "receipt_amount": 0.0,
            "net_balance": 0.0,
            "message": "Database connection error"
        }

    try:
        from psycopg2.extras import RealDictCursor
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 1. Check for exact match for requested clean_to
        if clean_from:
            query = f"""
            SELECT opening_balance, due_amount, receipt_amount, net_balance, from_date, to_date, updated_at
            FROM {table_name}
            WHERE company_id = %s AND from_date = %s AND to_date = %s
            ORDER BY updated_at DESC
            LIMIT 1;
            """
            cur.execute(query, (cid, clean_from, clean_to))
        else:
            query = f"""
            SELECT opening_balance, due_amount, receipt_amount, net_balance, from_date, to_date, updated_at
            FROM {table_name}
            WHERE company_id = %s AND to_date = %s
            ORDER BY updated_at DESC
            LIMIT 1;
            """
            cur.execute(query, (cid, clean_to))

        row = cur.fetchone()

        if row:
            opn = float(row["opening_balance"] or 0)
            due = float(row["due_amount"] or 0)
            rcpt = float(row["receipt_amount"] or 0)
            bal = float(row["net_balance"]) if (row.get("net_balance") is not None and row.get("net_balance") != 0) else ((opn + due) - rcpt)
            return {
                "has_data": True,
                "is_realtime": True,
                "opening_balance": opn,
                "due_amount": due,
                "receipt_amount": rcpt,
                "net_balance": bal,
                "from_date": str(row["from_date"]),
                "to_date": str(row["to_date"]),
                "updated_at": str(row["updated_at"]),
                "message": "Realtime synced with Tally"
            }

        # 2. If no record for today's date, fetch the latest recorded summary for this company
        cur.execute(f"""
        SELECT opening_balance, due_amount, receipt_amount, net_balance, from_date, to_date, updated_at
        FROM {table_name}
        WHERE company_id = %s
        ORDER BY to_date DESC, updated_at DESC
        LIMIT 1;
        """, (cid,))
        latest_row = cur.fetchone()

        if latest_row:
            last_date = str(latest_row["to_date"])
            opn = float(latest_row["opening_balance"] or 0)
            due = float(latest_row["due_amount"] or 0)
            rcpt = float(latest_row["receipt_amount"] or 0)
            bal = float(latest_row["net_balance"]) if (latest_row.get("net_balance") is not None and latest_row.get("net_balance") != 0) else ((opn + due) - rcpt)
            msg = f"Notice: Today's realtime data is pending sync. Displaying last recorded figures as of {last_date}. Please sync from Tally to view live updates."
            return {
                "has_data": True,
                "is_realtime": False,
                "opening_balance": opn,
                "due_amount": due,
                "receipt_amount": rcpt,
                "net_balance": bal,
                "from_date": str(latest_row["from_date"]),
                "to_date": last_date,
                "last_synced_date": last_date,
                "updated_at": str(latest_row["updated_at"]),
                "message": msg
            }

        return {
            "has_data": False,
            "is_realtime": False,
            "opening_balance": 0.0,
            "due_amount": 0.0,
            "receipt_amount": 0.0,
            "net_balance": 0.0,
            "last_synced_date": None,
            "to_date": clean_to,
            "message": "No sync records found for this company. Please perform initial sync from Tally."
        }
    except Exception as e:
        print(f"[NeonDB] Query error in get_tally_summary: {e}")
        return {
            "has_data": False,
            "is_realtime": False,
            "opening_balance": 0.0,
            "due_amount": 0.0,
            "receipt_amount": 0.0,
            "net_balance": 0.0,
            "message": f"Error querying Neon DB: {str(e)}"
        }
    finally:
        if conn:
            conn.close()

class handler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_OPTIONS(self):
        self._send_json({"status": "ok"}, 200)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}
        res, code = handle_tally_post(data)
        self._send_json(res, code)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        cid = params.get("company_id", [""])[0]
        entity = params.get("entity", ["school"])[0]
        f_date = params.get("from_date", [""])[0]
        t_date = params.get("to_date", [""])[0]

        summary = get_tally_summary(cid, entity, f_date, t_date)
        if summary:
            self._send_json({"status": "success", "data": summary}, 200)
        else:
            self._send_json({"status": "not_found", "message": "No Tally summary recorded for this query in Neon DB"}, 404)
