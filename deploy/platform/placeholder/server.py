"""아직 개발 중인 서비스 자리를 지키는 임시 서버. 연결 확인용으로 /healthz와 서비스 이름만 답한다."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVICE = os.environ.get("SERVICE_NAME", "unknown")
PORT = int(os.environ.get("PORT", "8080"))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"ok" if self.path == "/healthz" else json.dumps(
            {"service": SERVICE, "status": "placeholder"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain" if self.path == "/healthz" else "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
