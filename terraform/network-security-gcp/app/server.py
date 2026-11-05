"""A real two-port HTTP control server; no external packages or downloads."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
        body = json.dumps({
            "cloud": "gcp", "service": "delta-network-demo",
            "port": self.server.server_port, "path": parsed.path,
            "request_id": parse_qs(parsed.query).get("request_id", [""])[0],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(ports=(8080, 8081), host="0.0.0.0"):
    servers = [ThreadingHTTPServer((host, port), Handler) for port in ports]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return servers


if __name__ == "__main__":
    serve()
    threading.Event().wait()
