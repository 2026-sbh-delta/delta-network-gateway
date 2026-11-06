"""서비스 도메인 되돌리기(시연 초기화): green 입구를 먼저 확인하고, 바꾼 뒤 확인하지 못하면 blue 로 되돌린다."""

from __future__ import annotations

from test_cutover import CFG, Clock, FakeCloudflare

from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.domain import revert


class Net:
    """공개 DNS 와 HTTP. green(AWS)은 X-Served-By 를 붙이지 않는다."""

    def __init__(self, cf, *, green=(200, ""), lag=1):
        self.cf, self.green, self.lag, self.calls = cf, green, lag, []

    def resolve(self, name):
        if self.lag > 0:
            self.lag -= 1
            return "gcp.adajosou.site"
        return self.cf.record["content"]

    def probe(self, url, host=None, timeout=10):
        self.calls.append((url, host))
        if isinstance(self.green, Exception):
            raise self.green
        return self.green


def _revert(cf, net, clock=None):
    clock = clock or Clock()
    reports = []
    result = revert(CFG, cf, probe=net.probe, resolve=net.resolve, now=clock.now, sleep=clock.sleep,
                    report=reports.append)
    return result, reports


def test_checks_green_then_switches_and_verifies_public_answer():
    cf = FakeCloudflare(content="gcp.adajosou.site")
    net = Net(cf, lag=2)
    result, reports = _revert(cf, net)
    assert result["state"] == "pass" and cf.writes == ["aws.adajosou.site"]
    # 바꾸기 전에 green 입구를 서비스 도메인 Host 로 먼저 확인했다
    assert net.calls[0] == ("https://aws.adajosou.site/healthz", "app.adajosou.site")
    assert ("https://app.adajosou.site/healthz", None) in net.calls
    assert reports[-1] == result


def test_unhealthy_green_never_touches_dns():
    for green in [(502, ""), (200, "gcp"), ServiceUnavailable("연결 거부")]:
        cf = FakeCloudflare(content="gcp.adajosou.site")
        result, _ = _revert(cf, Net(cf, green=green))
        assert result["state"] == "fail" and cf.writes == [] and "DNS 를 바꾸지 않았다" in result["summary"]


def test_unverified_switch_rolls_back_to_blue():
    cf = FakeCloudflare(content="gcp.adajosou.site")
    net = Net(cf)
    net.resolve = lambda name: "gcp.adajosou.site"  # 공개 DNS 가 끝내 green 을 돌려주지 않는다
    result, _ = _revert(cf, net)
    assert result["state"] == "fail" and cf.writes == ["aws.adajosou.site", "gcp.adajosou.site"]


def test_already_green_does_not_write_and_unexpected_record_stops():
    cf = FakeCloudflare(content="aws.adajosou.site")
    result, _ = _revert(cf, Net(cf, lag=0))
    assert result["state"] == "pass" and cf.writes == []
    for odd in [FakeCloudflare(content="other.example.net"), FakeCloudflare(content="gcp.adajosou.site", proxied=True)]:
        result, _ = _revert(odd, Net(odd))
        assert result["state"] == "fail" and odd.writes == []


def test_run_left_by_another_orchestrator_version_is_not_resumed(tmp_path):
    """같은 상태 폴더에 다른 버전이 남긴 미완료 실행(모르는 옵션)은 건너뛰고 시작은 계속된다."""
    import json

    from delta_pipeline.run import BOARD_DIR, latest_unfinished

    runs = tmp_path / BOARD_DIR
    runs.mkdir()
    base = {"repo": "https://github.com/o/r", "commit": "a" * 40}
    (runs / "old.json").write_text(json.dumps({"run_id": "old", "outcome": None, "options": base}))
    (runs / "other.json").write_text(json.dumps({"run_id": "other", "outcome": None,
                                                 "options": {**base, "execution_cloud": "onprem"}}))
    __import__("os").utime(runs / "other.json", (2e9, 2e9))  # 가장 최근
    assert latest_unfinished(tmp_path)[0] == "old"
