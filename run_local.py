import os
import sys
import json
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
from api.index import handle_login, handle_metrics
from api.tally import handle_tally_post, get_tally_summary

PORT = 9000
PUBLIC_DIR = os.path.join(BASE_DIR, "public")

class LocalPortalHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

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
            res, code = handle_tally_post(data)
            self._send_json(res, code)
        else:
            self._send_json({"error": "Endpoint not found"}, 404)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if "/metrics" in parsed.path:
            params = urllib.parse.parse_qs(parsed.query)
            res, code = handle_metrics(params)
            self._send_json(res, code)
        elif "/keepalive" in parsed.path:
            from api.keepalive import ping_neon_database
            res, code = ping_neon_database()
            self._send_json(res, code)
        elif "/tally" in parsed.path:
            params = urllib.parse.parse_qs(parsed.query)
            cid = params.get("company_id", [""])[0]
            entity = params.get("entity", ["school"])[0]
            f_date = params.get("from_date", [""])[0]
            t_date = params.get("to_date", [""])[0]
            summary = get_tally_summary(cid, entity, f_date, t_date)
            if summary:
                self._send_json({"status": "success", "data": summary}, 200)
            else:
                self._send_json({"status": "not_found", "message": "No Tally summary recorded"}, 404)
        else:
            req_path = parsed.path.lstrip("/")
            if not req_path:
                req_path = "index.html"
            file_path = os.path.join(PUBLIC_DIR, req_path)
            if os.path.exists(file_path) and os.path.isfile(file_path):
                self.send_response(200)
                if file_path.endswith(".html"):
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                elif file_path.endswith(".css"):
                    self.send_header("Content-Type", "text/css")
                elif file_path.endswith(".js"):
                    self.send_header("Content-Type", "application/javascript")
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not Found")

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), LocalPortalHandler)
    print(f"SyncTally Cloud Portal running at http://127.0.0.1:{PORT}")
    import webbrowser
    try:
        webbrowser.open(f"http://127.0.0.1:{PORT}")
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
