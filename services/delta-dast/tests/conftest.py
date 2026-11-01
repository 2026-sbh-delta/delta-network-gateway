"""Independent live fixtures. No legacy mock data or external destinations."""

import html
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest


@pytest.fixture(scope="session")
def browser_executable():
    path = Path(os.environ.get(
        "DAST_BROWSER_EXECUTABLE",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ))
    assert path.is_file(), f"Actual Chromium required: {path}"
    return str(path)


@pytest.fixture
def live_site():
    requests = []
    sink_requests = []

    class Sink(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            sink_requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"outside approved origin")

    sink = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    sink_origin = f"http://127.0.0.1:{sink.server_port}"

    class Target(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            value = parse_qs(parsed.query, keep_blank_values=True).get("content", [None])[0]
            requests.append({"path": parsed.path, "content": value,
                             "upgrade": self.headers.get("Upgrade")})
            if parsed.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", sink_origin + "/redirected")
                self.end_headers()
                return
            if parsed.path == "/slow":
                time.sleep(2)
            if parsed.path == "/worker.js":
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.end_headers()
                self.wfile.write(b"self.addEventListener('fetch', () => {});")
                return
            status = 401 if parsed.path == "/auth" else 200
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            if parsed.path == "/csp":
                self.send_header("Content-Security-Policy", "script-src 'none'; object-src 'none'")
            self.end_headers()
            raw = value or ""
            reflected = html.escape(raw) if parsed.path in {"/escaped", "/child"} else raw
            if parsed.path == "/noecho":
                reflected = "constant unrelated content"
            form = ('<form method="GET"><input name="content">'
                    '<button type="submit">Submit</button></form>')
            if parsed.path in {"/missing", "/auth"}:
                form = "<p>Form unavailable</p>"
            result = f'<div id="result">{reflected}</div>'
            if parsed.path == "/noresult":
                result = "<p>Result unavailable</p>"
            extra = ""
            if parsed.path == "/child" and value is not None:
                extra = f'<iframe srcdoc="{html.escape(raw, quote=True)}"></iframe>'
            if parsed.path == "/guard":
                # All destinations are temporary local servers. No DNS/external hosts.
                extra = f'''<script>
                  fetch('{sink_origin}/fetch').catch(() => {{}});
                  new WebSocket('ws://127.0.0.1:{sink.server_port}/ws-outside');
                  new WebSocket('ws://' + location.host + '/ws-inside');
                  navigator.serviceWorker.register('/worker.js').catch(() => {{}});
                  window.open('{sink_origin}/popup');
                </script>'''
            body = f'<!doctype html><title>Independent DAST fixture</title>{form}{result}{extra}'
            try:
                self.wfile.write(body.encode())
            except (BrokenPipeError, ConnectionResetError):
                pass  # Expected when a real browser navigation times out.

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (target, sink)]
    for thread in threads:
        thread.start()
    yield SimpleNamespace(origin=f"http://127.0.0.1:{target.server_port}",
                          requests=requests, sink_requests=sink_requests)
    for server in (target, sink):
        server.shutdown()
        server.server_close()
    for thread in threads:
        thread.join(timeout=3)


@pytest.fixture
def finding():
    return {"id": "xss-001", "title": "Reflected XSS", "cwe": "CWE-79",
            "severity": "HIGH", "description": "User content reaches HTML",
            "source_file": "app/view.py", "line": 12,
            "provenance": {"source": "manual", "finding_id": "xss-001"}}


@pytest.fixture
def plan(finding):
    return {"finding_id": finding["id"], "mapping_id": "approved-form",
            "payload_kind": "visual_xss", "rationale": "Check reflected HTML execution"}


@pytest.fixture
def mapping(live_site):
    def make(path="/vulnerable"):
        return {"approved-form": {"origin": live_site.origin, "path": path,
                "input_selector": '[name=content]',
                "submit_selector": 'button[type=submit]', "result_selector": "#result"}}
    return make


@pytest.fixture
def browser_audit(monkeypatch):
    """Observe real Playwright calls; do not fake browser outcomes or proof values."""
    from playwright.async_api import Browser, BrowserContext, Frame, Page

    audit = SimpleNamespace(contexts=[], proof_reads=[], pages_at_close=[], writes=[])
    original_frame_eval = Frame.evaluate
    original_page_eval = Page.evaluate
    original_context = Browser.new_context
    original_close = BrowserContext.close

    async def observe(frame):
        return await original_frame_eval(frame, "() => ({proof: window.__dastProof ?? null, html: document.documentElement.outerHTML})")

    async def frame_evaluate(self, expression, arg=None):
        before = await observe(self)
        result = await original_frame_eval(self, expression, arg)
        after = await observe(self)
        audit.proof_reads.append((self == self.page.main_frame, before["proof"], expression))
        if before != after:
            audit.writes.append(expression)
        return result

    async def page_evaluate(self, expression, arg=None):
        before = await observe(self.main_frame)
        result = await original_page_eval(self, expression, arg)
        after = await observe(self.main_frame)
        audit.proof_reads.append((True, before["proof"], expression))
        if before != after:
            audit.writes.append(expression)
        return result

    async def new_context(self, **kwargs):
        audit.contexts.append(kwargs)
        return await original_context(self, **kwargs)

    async def close(self, *args, **kwargs):
        audit.pages_at_close.append([page.url for page in self.pages if not page.is_closed()])
        return await original_close(self, *args, **kwargs)

    async def forbid_script_injection(*args, **kwargs):
        audit.writes.append("script/init injection")
        raise AssertionError("Runner must submit its payload through the actual form")

    monkeypatch.setattr(Frame, "evaluate", frame_evaluate)
    monkeypatch.setattr(Page, "evaluate", page_evaluate)
    monkeypatch.setattr(Browser, "new_context", new_context)
    monkeypatch.setattr(BrowserContext, "close", close)
    monkeypatch.setattr(Page, "add_script_tag", forbid_script_injection)
    monkeypatch.setattr(Frame, "add_script_tag", forbid_script_injection)
    monkeypatch.setattr(Page, "add_init_script", forbid_script_injection)
    # Context init scripts may listen for CSP events; page-level script injection is forbidden.
    yield audit
    assert not audit.writes, f"Runner injected proof/DOM effects via browser API: {audit.writes}"
