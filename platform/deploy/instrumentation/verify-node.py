"""Exercise unchanged FE/BE processes against a real OTLP receiver, including OFF."""

import argparse
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from profiles import environment


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    p = argparse.ArgumentParser(description="원본 Node.js FE·BE의 자동 계측 실제 검증")
    p.add_argument("--app-dir", type=Path, required=True)
    p.add_argument("--agent-dir", type=Path, required=True)
    p.add_argument("--node", default="node")
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    app = a.app_dir.resolve()
    if not subprocess.check_output([a.node, "--version"], text=True).startswith("v24."):
        raise ValueError("Verify this profile with Node.js 24")
    commit = subprocess.check_output(
        ["git", "-C", str(app), "rev-parse", "HEAD"], text=True
    ).strip()
    before = subprocess.check_output(["git", "-C", str(app), "diff", "HEAD"])
    if before:
        raise ValueError("Use a clean, unchanged sample checkout")
    received, lock = [], threading.Lock()

    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.headers.get("Transfer-Encoding") == "chunked":
                parts, total = [], 0
                while True:
                    size = int(self.rfile.readline().split(b";")[0].strip(), 16)
                    if size == 0:
                        self.rfile.readline()
                        break
                    total += size
                    if total > 1_000_000:
                        self.send_error(413)
                        return
                    parts.append(self.rfile.read(size))
                    self.rfile.read(2)
                body = b"".join(parts)
            else:
                size = int(self.headers.get("Content-Length", "0"))
                if size > 1_000_000:
                    self.send_error(413)
                    return
                body = self.rfile.read(size)
            with lock:
                received.append((self.path, body))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", "0")
            self.end_headers()

    # The production profile uses the standard private OTLP port. Tests substitute
    # an ephemeral local port only after validating the production configuration.
    receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=receiver.serve_forever, daemon=True).start()

    def request(url):
        with urllib.request.urlopen(url, timeout=2) as response:
            assert response.status == 200
            return json.load(response)

    def run_case(folder, enabled, collector_available=True):
        with ExitStack() as stack:
            fe, be = port(), port()
            processes = []
            try:
                for component, listen in (("backend", be), ("frontend", fe)):
                    env = {
                        k: v
                        for k, v in os.environ.items()
                        if not k.startswith("OTEL_") and k != "NODE_OPTIONS"
                    }
                    if enabled:
                        env.update(
                            environment(
                                "http://127.0.0.1:4318",
                                "verify-" + component,
                                commit,
                                root=str(a.agent_dir.resolve()),
                                metrics=component == "frontend",
                            )
                        )
                        target = receiver.server_port if collector_available else port()
                        env.update(
                            OTEL_EXPORTER_OTLP_ENDPOINT=f"http://127.0.0.1:{target}",
                            OTEL_METRIC_EXPORT_INTERVAL="500",
                            OTEL_BSP_SCHEDULE_DELAY="100",
                        )
                    env.update(PORT=str(listen), API_HOST="127.0.0.1", API_PORT=str(be))
                    log = stack.enter_context(open(folder / (component + ".log"), "w"))
                    processes.append(
                        subprocess.Popen(
                            [a.node, str(app / component / "server.mjs")],
                            env=env,
                            stdout=log,
                            stderr=log,
                        )
                    )
                deadline = time.monotonic() + 20
                while True:
                    if any(proc.poll() is not None for proc in processes):
                        raise RuntimeError(
                            "Injected application failed to start; inspect temporary process logs"
                        )
                    try:
                        value = request(f"http://127.0.0.1:{fe}/api/health")
                        assert (
                            value["service"] == "backend"
                            and value["storage"] == "memory"
                        )
                        break
                    except (OSError, AssertionError):
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(0.2)
                for _ in range(3):
                    request(f"http://127.0.0.1:{fe}/api/notes")
                time.sleep(2)
                assert all(proc.poll() is None for proc in processes)
            finally:
                for proc in processes:
                    proc.terminate()
                for proc in processes:
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()

    try:
        with tempfile.TemporaryDirectory(prefix="delta-node-verify-") as tmp:
            folder = Path(tmp)
            run_case(folder, True)
            with lock:
                signals = list(received)
            spans, metrics = [], []
            for path, body in signals:
                if path == "/v1/traces":
                    for resource in ExportTraceServiceRequest.FromString(
                        body
                    ).resource_spans:
                        name = next(
                            x.value.string_value
                            for x in resource.resource.attributes
                            if x.key == "service.name"
                        )
                        spans += [
                            (name, s)
                            for scope in resource.scope_spans
                            for s in scope.spans
                        ]
                if path == "/v1/metrics":
                    metrics += [
                        m
                        for resource in ExportMetricsServiceRequest.FromString(
                            body
                        ).resource_metrics
                        for scope in resource.scope_metrics
                        for m in scope.metrics
                    ]
            chain = None
            for name, child in spans:
                if name != "verify-backend" or child.kind != 2:
                    continue
                parent = next(
                    (
                        s
                        for n, s in spans
                        if n == "verify-frontend"
                        and s.span_id == child.parent_span_id
                        and s.trace_id == child.trace_id
                        and s.kind == 3
                    ),
                    None,
                )
                root = next(
                    (
                        s
                        for n, s in spans
                        if parent
                        and n == "verify-frontend"
                        and s.span_id == parent.parent_span_id
                        and s.kind == 2
                        and s.trace_id == child.trace_id
                    ),
                    None,
                )
                if root:
                    chain = child.trace_id.hex()
                    break
            if not chain:
                raise AssertionError("No FE server → FE client → BE server trace chain")
            hist = [m for m in metrics if m.name == "http.server.request.duration"]
            if not hist or not any(
                m.histogram.aggregation_temporality == 1
                and any(p.count for p in m.histogram.data_points)
                for m in hist
            ):
                raise AssertionError("No DELTA HTTP duration histogram")
            with lock:
                received.clear()
            run_case(folder, False)
            with lock:
                assert not received, "OFF mode emitted telemetry"
            run_case(folder, True, collector_available=False)
        assert (
            subprocess.check_output(["git", "-C", str(app), "diff", "HEAD"]) == before
        )
        a.output.write_text(
            json.dumps(
                {
                    "commit": commit,
                    "source_unchanged": True,
                    "trace_chain": "frontend_server -> frontend_client -> backend_server",
                    "trace_id": chain,
                    "http_delta_histogram": "passed",
                    "off": "no_telemetry",
                    "collector_unavailable": "app_responds",
                },
                indent=2,
            )
        )
        print(
            "Unchanged app: trace chain, DELTA HTTP metric, OFF and collector failure checks passed"
        )
    finally:
        receiver.shutdown()
        receiver.server_close()


if __name__ == "__main__":
    main()
