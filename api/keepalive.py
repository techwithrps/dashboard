import os
import sys
import json
from http.server import BaseHTTPRequestHandler
import datetime

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

DEFAULT_NEON_DB_URL = "postgresql://neondb_owner:npg_feZl30PimbRI@ep-solitary-scene-aea7vq5m-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require"

def ping_neon_database():
    try:
        import psycopg2
        db_url = os.environ.get("DATABASE_URL", DEFAULT_NEON_DB_URL)
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        res = cur.fetchone()
        
        # Check counts
        cur.execute("SELECT COUNT(*) FROM tally_school_summary;")
        school_count = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM tally_college_summary;")
        college_count = cur.fetchone()[0]
        
        conn.close()
        return {
            "status": "active",
            "neon_db": "connected",
            "school_records": school_count,
            "college_records": college_count,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }, 200
    except Exception as e:
        return {
            "status": "error",
            "neon_db": "connection_failed",
            "error": str(e),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }, 500

class handler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_OPTIONS(self):
        self._send_json({"status": "ok"}, 200)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

    def do_GET(self):
        res, code = ping_neon_database()
        self._send_json(res, code)
