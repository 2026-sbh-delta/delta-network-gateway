#!/usr/bin/env python3
"""Private HTTP control listeners. No dependencies or external calls."""
import argparse
import json
import signal
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"cloud": "aws", "service": "delta-private-server",
                           "hostname": socket.gethostname(),
                           "port": self.server.server_port,
                           "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Query strings are not needed for network validation logs.
        print(json.dumps({"client": self.client_address[0],
                          "port": self.server.server_port,
                          "method": self.command}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--ports", nargs="+", type=int, default=[8080, 8081])
    args = parser.parse_args()
    servers = [ThreadingHTTPServer((args.bind, port), Handler) for port in args.ports]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    stopped.wait()
    for server in servers:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
