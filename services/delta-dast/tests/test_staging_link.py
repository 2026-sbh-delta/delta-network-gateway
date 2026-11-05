"""스테이징 대상 주소를 target JSON이 아니라 스테이징 도구에서 받는지."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dast_agent import staging_link
from dast_agent.registry import RegistryCatalog, TargetDefinition, load_catalog

TOKEN = "staging-test-token"
TARGETS = {"targets": [{"cloud": "gcp", "url": "http://34.8.9.10"},
                       {"cloud": "aws", "error": "대상 그룹에 붙은 ALB 가 없다"}]}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path == "/v1/staging/targets" and self.headers.get("Authorization") == "Bearer " + TOKEN
        self.send_response(200 if ok else 401)
        self.end_headers()
        if ok:
            self.wfile.write(json.dumps(TARGETS).encode())

    def log_message(self, *_args):
        pass


@pytest.fixture(scope="module")
def staging_tool():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture
def linked(monkeypatch, staging_tool):
    monkeypatch.setenv("DELTA_STAGING_URL", staging_tool)
    monkeypatch.setenv("DELTA_STAGING_TOKEN", TOKEN)


def test_origin_comes_from_the_staging_tool(linked):
    assert staging_link.origin("gcp") == "http://34.8.9.10"


@pytest.mark.parametrize(("cloud", "words"), [
    ("aws", ["aws 스테이징 주소를 찾지 못했습니다", "ALB"]),
    ("azure", ["azure 스테이징이 설정돼 있지 않습니다"]),
])
def test_missing_or_down_staging_says_why(linked, cloud, words):
    with pytest.raises(staging_link.StagingUnavailable) as caught:
        staging_link.origin(cloud)
    assert all(word in str(caught.value) for word in words)


def test_unconfigured_link_fails_fast(monkeypatch):
    monkeypatch.delenv("DELTA_STAGING_TOKEN", raising=False)
    with pytest.raises(staging_link.StagingUnavailable, match="DELTA_STAGING_TOKEN"):
        staging_link.origin("gcp")


def test_registered_staging_targets_carry_no_fixed_address():
    catalog = load_catalog()
    for target_id in ("staging-aws", "staging-gcp"):
        target = catalog.targets[target_id]
        assert target.origin is None and target.staging == target_id.split("-")[1]
        with pytest.raises(ValueError, match="스테이징 주소를 받지 못했습니다"):
            catalog.resolve_target(target_id, "vulnerable", 8766)
        assert catalog.resolve_target(target_id, "vulnerable", 8766, "http://34.8.9.10/").origin == "http://34.8.9.10"


def test_target_needs_exactly_one_origin_source():
    base = {"id": "t", "label": "t", "entry_path": "/"}
    for extra in ({}, {"origin": "http://a.example", "staging": "gcp"}):
        with pytest.raises(ValueError):
            TargetDefinition.model_validate(base | extra)
    assert isinstance(RegistryCatalog({}, {}), RegistryCatalog)
