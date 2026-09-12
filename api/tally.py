import os
import sys
import json
import re
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

def clean_float(val, default=0.0):
    if val is None:
        return default
    if isinstance(val, (int, float, Decimal)):
        return float(val)
    s = str(val).strip()
    if not s:
        return default
    
    is_negative = False
    if s.startswith("(") and s.endswith(")"):
        is_negative = True
        s = s[1:-1]
    if s.lower().endswith("dr"):
        s = s[:-2].strip()
    elif s.lower().endswith("cr"):
        s = s[:-2].strip()
    
    s = re.sub(r"[₹\$,\s]|rs\.?|inr", "", s, flags=re.IGNORECASE)
    
    match = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    if match:
        try:
            num = float(match.group(0))
            return -num if is_negative and num > 0 else num
        except Exception:
            pass
            
    try:
        num = float(s)
        return -num if is_negative and num > 0 else num
    except Exception:
        return default

def parse_tally_payload(raw_body):
    """
    Parses incoming body into a Python dict/list.
    Handles:
    - { Data : { Tally_msg: [ { ... } ] } }
    - { Data { Tally_msg: [ { ... } ] } }
    - Standard JSON: {"Data": {"Tally_msg": [...]}}
    - Quasi-JSON / Tally TDL format
    - Non-breaking spaces and unquoted keys
    """
    if isinstance(raw_body, (dict, list)):
        return raw_body
    if not raw_body or not isinstance(raw_body, str):
        return {}
    
    cleaned = raw_body.replace("\u00a0", " ").strip()

    # 1. Direct JSON parse
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 2. Lenient fix for unquoted keys before : or {
    try:
        fixed = cleaned
        fixed = re.sub(r"(?<!\")\b([a-zA-Z0-9_]+)\b\s*:", r"\"\1\":", fixed)
        fixed = re.sub(r"(?<!\")\b([a-zA-Z0-9_]+)\b\s*\{", r"\"\1\": {", fixed)
        fixed = re.sub(r",\s*([\}\]])", r"\1", fixed)
        if not fixed.startswith("{") and not fixed.startswith("["):
            fixed = "{" + fixed
        open_b = fixed.count("{")
        close_b = fixed.count("}")
        if open_b > close_b:
            fixed += "}" * (open_b - close_b)
        open_sq = fixed.count("[")
        close_sq = fixed.count("]")
        if open_sq > close_sq:
            fixed += "]" * (open_sq - close_sq)
        return json.loads(fixed)
    except Exception:
        pass

    # 3. Fallback: extract inner JSON object with company_id / amounts
    match = re.search(r"\{[^{}]*(?:company_id|due_amount|opening_balance|receipt_amount)[^{}]*\}", cleaned, re.IGNORECASE | re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass

    # 4. Fallback regex key-value extraction for valid fields
    valid_fields = {"entity", "company_id", "company_code", "opening_balance", "due_amount", "receipt_amount", "from_date", "to_date"}
    extracted = {}
    for m in re.finditer(r"\"?([a-zA-Z0-9_]+)\"?\s*:\s*\"?([^\",\}\n\[\]]+)\"?", cleaned):
        k = m.group(1).strip()
        v = m.group(2).strip().strip("\"").strip("'")
        if k in valid_fields or k.lower() in valid_fields:
            extracted[k] = v
    return extracted

def unwrap_payload(data):
    """
    Recursively unwraps wrappers:
    'Data', 'Tally_msg', nested lists, stringified JSONs
    """
    curr = data
    for _ in range(5):
        if isinstance(curr, list) and len(curr) > 0:
            curr = curr[0]
            continue
        if not isinstance(curr, dict):
            break
        # If curr already contains core payload metrics, do not unwrap further
        if any(k in curr for k in ["company_id", "company_code", "due_amount", "opening_balance"]):
            break
        unwrapped = False
        for k in ["Data", "data", "DATA", "Tally_msg", "tally_msg", "TALLY_MSG", "Tally", "tally"]:
            if k in curr:
                val = curr[k]
                if isinstance(val, str) and (val.startswith("{") or val.startswith("[")):
                    try:
                        val = parse_tally_payload(val)
                    except Exception:
                        pass
                if isinstance(val, dict) or (isinstance(val, list) and len(val) > 0):
                    curr = val
                    unwrapped = True
                    break
        if not unwrapped:
            break
    if isinstance(curr, list) and len(curr) > 0:
        curr = curr[0]
    return curr

def get_field_val(d, *keys):
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
        norm_k = k.lower().replace("_", "").replace(" ", "").rstrip("s")
        for actual_k in d:
            norm_actual = actual_k.lower().replace("_", "").replace(" ", "").rstrip("s")
            if norm_actual == norm_k and d[actual_k] is not None:
                return d[actual_k]
    return None

def handle_tally_post(data):
    if isinstance(data, str):
        data = parse_tally_payload(data)

    if not isinstance(data, (dict, list)):
        return {"status": "error", "message": "Invalid JSON body"}, 400

    # Unwrap if sent as {"Data": {"Tally_msg": {...}}} or {"Tally_msg": {...}} or arrays
    data = unwrap_payload(data)

    if not isinstance(data, dict):
        return {"status": "error", "message": "Payload could not be resolved into a valid data object"}, 400

    raw_cid = str(get_field_val(data, "company_id", "company_code", "companyid", "cid") or "").strip()
    if not raw_cid:
        return {"status": "error", "message": "company_id or company_code is required"}, 400

    entity = str(get_field_val(data, "entity", "institute_type", "type") or "school").lower().strip()
    table_name = "tally_college_summary" if "college" in entity else "tally_school_summary"
    company_id = resolve_company_id(raw_cid, entity) or raw_cid
    
    import datetime
    today = datetime.date.today()
    fy_start_year = today.year if today.month >= 4 else today.year - 1
    auto_from_date = f"{fy_start_year}-04-01"
    auto_to_date = today.strftime("%Y-%m-%d")

    raw_from_date = get_field_val(data, "from_date", "fromdate")
    raw_to_date = get_field_val(data, "to_date", "todate")
    
    from_date = normalize_date(raw_from_date) or auto_from_date
    to_date = normalize_date(raw_to_date) or auto_to_date

    try:
        opening_bal = clean_float(get_field_val(data, "opening_balance", "opening", "openingbalance", "opening balance"), 0.0)
        due_amt = clean_float(get_field_val(data, "due_amount", "due", "dues", "dueamount", "due amount"), 0.0)
        receipt_amt = clean_float(get_field_val(data, "receipt_amount", "receipts", "receipt", "receiptamount", "receipt amount"), 0.0)
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

        # Format clean short row for response
        res_data = {
            "entity": entity,
            "company_id": row["company_id"],
            "opening_balance": float(row["opening_balance"]),
            "due_amount": float(row["due_amount"]),
            "receipt_amount": float(row["receipt_amount"]),
            "balance": float(row["net_balance"]),
            "updated_at": str(row["updated_at"])
        }
        return {"status": "success", "message": "Synced successfully", "data": res_data}, 200
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

        # 3. Fallback: check alternative entity table (school <-> college)
        if not latest_row:
            alt_table = "tally_school_summary" if table_name == "tally_college_summary" else "tally_college_summary"
            cur.execute(f"""
            SELECT opening_balance, due_amount, receipt_amount, net_balance, from_date, to_date, updated_at
            FROM {alt_table}
            WHERE company_id = %s
            ORDER BY to_date DESC, updated_at DESC
            LIMIT 1;
            """, (cid,))
            latest_row = cur.fetchone()

        if latest_row:
            last_date = str(latest_row["to_date"])
            is_today = (last_date == clean_to)
            opn = float(latest_row["opening_balance"] or 0)
            due = float(latest_row["due_amount"] or 0)
            rcpt = float(latest_row["receipt_amount"] or 0)
            bal = float(latest_row["net_balance"]) if (latest_row.get("net_balance") is not None and latest_row.get("net_balance") != 0) else ((opn + due) - rcpt)
            msg = "Realtime synced with Tally" if is_today else f"Notice: Today's realtime data is pending sync. Displaying last recorded figures as of {last_date}."
            return {
                "has_data": True,
                "is_realtime": is_today,
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
        res, code = handle_tally_post(body)
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
