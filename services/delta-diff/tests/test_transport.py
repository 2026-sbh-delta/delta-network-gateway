"""실제 루프백 TLS로 인증서·토큰·직렬화와 FastAPI 계약을 함께 검사한다."""

import base64
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_api import FakeReader, STATE, TOKEN, _body, _client

from delta_diff.client import DiffClient, DiffUnavailable
from delta_diff.contract import DiffRequest


@pytest.fixture
def tls_api(tmp_path):
    cert, key, config = (tmp_path / name for name in ("cert.pem", "key.pem", "openssl.cnf"))
    config.write_text("[req]\ndistinguished_name=dn\n[dn]\nCN=localhost\n[v3]\n"
                      "subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\n")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=localhost", "-config", str(config), "-extensions", "v3",
                    "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    api = _client(tmp_path, FakeReader(STATE))

    class Handler(BaseHTTPRequestHandler):
        def handle_api(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            response = api.request(self.command, self.path, content=body, headers=dict(self.headers))
            self.send_response(response.status_code)
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)
        do_GET = do_POST = handle_api
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_port}", base64.b64encode(cert.read_bytes()).decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        api.close()


def test_real_https_calls_readiness_and_assessment_with_the_configured_ca(tls_api):
    url, ca = tls_api
    client = DiffClient(url, TOKEN, ca_base64=ca)

    client.check_health()
    result = client.run(DiffRequest.model_validate(_body()))

    assert result.source.serial == 12
    assert result.assessment.verdict == "needs_human"


def test_real_https_rejects_untrusted_certificate_and_wrong_token(tls_api):
    url, ca = tls_api
    with pytest.raises(DiffUnavailable, match="TLS"):
        DiffClient(url, TOKEN).check_health()
    with pytest.raises(DiffUnavailable, match="401"):
        DiffClient(url, "incorrect-token", ca_base64=ca).run(DiffRequest.model_validate(_body()))
