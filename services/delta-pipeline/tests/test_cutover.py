"""서비스 도메인 전환(명세 v1.1 6-3, 11/5 정정): DNS 전용 CNAME(TTL 60)을 green 일 때만 blue 로 바꾸고,
공개 DNS·실제 응답으로 확인한 뒤 green 을 TTL 만큼 유지한다. 확인하지 못하면 green 으로 되돌린다.
운영 묶음 verify를 먼저 완료하고, 운영 승인과 도메인 전환 승인을 따로 받는다."""

from __future__ import annotations

from copy import deepcopy

import pytest

from delivery_fakes import (TARGET, TEST_RELEASE_CONFIG, TEST_RELEASE_SIGNING_KEY,
                            RoundJenkins, bundle, release_result, stage_result)
from fakes import COMMIT, IMAGE_REPO, REPO, FakeDast, FakeSast, FakeStaging
from test_flow import _execute, _states

from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.model import Options
from delta_pipeline.run import Run
from delta_pipeline.steps import CutoverConfig

ZONE = "a" * 32
CFG = CutoverConfig(zone_id=ZONE, record="app.adajosou.site", green="aws.adajosou.site", blue="gcp.adajosou.site",
                    blue_origin="http://gcp-origin.example")


class Clock:
    def __init__(self):
        self.t, self.slept = 1000.0, 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds
        self.slept += seconds


class FakeCloudflare:
    def __init__(self, content="aws.adajosou.site", proxied=False, kind="CNAME", ttl=60):
        self.record = {"id": "r" * 32, "type": kind, "name": "app.adajosou.site", "content": content,
                       "proxied": proxied, "ttl": ttl}
        self.writes = []

    def find_record(self, zone_id, name):
        assert zone_id == ZONE and name == "app.adajosou.site"
        return dict(self.record)

    def set_content(self, zone_id, record_id, content):
        self.writes.append(content)
        self.record["content"] = content
        return dict(self.record)


class FakeInternet:
    """공개 DNS 응답과 HTTP 응답. DNS 전파는 lag 번 조회 뒤에 보인다."""

    def __init__(self, cloudflare, *, origin=(200, "gcp/abc"), blue_ok=True, lag=1):
        self.cf, self.origin, self.blue_ok, self.lag, self.calls = cloudflare, origin, blue_ok, lag, []

    def resolve(self, name):
        if self.lag > 0:
            self.lag -= 1
            return "aws.adajosou.site"
        return self.cf.record["content"]

    def probe(self, url, host=None, timeout=10):
        self.calls.append((url, host))
        if host:
            return self.origin
        if not self.blue_ok:
            raise ServiceUnavailable("연결 거부")
        return (200, "gcp/abc")


def _run(tmp_path, cloudflare, net, clock, cutover=CFG, *, release_target=TARGET, release=None):
    return Run(Options(REPO, COMMIT, cutover=True, release_target=release_target),
               sast=FakeSast(), dast=FakeDast(), build_jenkins=RoundJenkins(bundle),
               deploy_jenkins=RoundJenkins(lambda commit: stage_result(bundle(commit))),
               staging=FakeStaging(), image_repo=IMAGE_REPO,
               store_dir=tmp_path / "store", sleep=clock.sleep, cloudflare=cloudflare, cutover=cutover,
               probe=net.probe, resolve=net.resolve, now=clock.now,
               release_jenkins=release or RoundJenkins(lambda commit: release_result(bundle(commit))),
               release_signing_key=TEST_RELEASE_SIGNING_KEY if release_target else None,
               release_config=deepcopy(TEST_RELEASE_CONFIG) if release_target else None)


def _execute_cutover(run, *, approve_cutover=True):
    # 운영 승인과 DNS 승인은 서로 다른 interrupt다. DNS 반례가 운영 이전에
    # 멈춰서 우연히 통과하지 않도록 운영 verify 통과도 반드시 확인한다.
    snapshot = _execute(run, answers=["approve", "approve" if approve_cutover else "stop"])
    assert _states(snapshot)["release"] == "pass"
    return snapshot


def test_switch_waits_for_public_dns_and_blue_responses_then_keeps_green_for_ttl(tmp_path):
    cf, clock = FakeCloudflare(), Clock()
    net = FakeInternet(cf, lag=2)
    snapshot = _execute_cutover(_run(tmp_path, cf, net, clock))
    assert snapshot["outcome"] == "verified" and _states(snapshot)["cutover"] == "pass"
    assert cf.writes == ["gcp.adajosou.site"]
    # 전환 전에 blue 원본을 서비스 도메인 Host 로 먼저 확인했다
    assert net.calls[0] == ("http://gcp-origin.example/healthz", "app.adajosou.site")
    # 서비스 도메인 확인은 https 로 했고, green 은 TTL(60초) 이상 유지했다
    assert ("https://app.adajosou.site/healthz", None) in net.calls
    assert clock.slept >= 60


def test_auto_ttl_keeps_green_for_300_seconds(tmp_path):
    cf, clock = FakeCloudflare(ttl=1), Clock()
    snapshot = _execute_cutover(_run(tmp_path, cf, FakeInternet(cf), clock))
    assert snapshot["outcome"] == "verified" and clock.slept >= 300


def test_failed_precheck_never_touches_dns(tmp_path):
    cf = FakeCloudflare()
    snapshot = _execute_cutover(_run(tmp_path, cf, FakeInternet(cf, origin=(403, "")), Clock()))
    assert snapshot["outcome"] == "stopped" and cf.writes == []
    assert "전환 전 검증 실패" in next(n for n in snapshot["nodes"] if n["id"] == "cutover")["summary"]


def test_blue_unreachable_after_switch_rolls_back_to_green(tmp_path):
    cf = FakeCloudflare()
    snapshot = _execute_cutover(_run(tmp_path, cf, FakeInternet(cf, blue_ok=False), Clock()))
    assert snapshot["outcome"] == "stopped"
    assert cf.writes == ["gcp.adajosou.site", "aws.adajosou.site"] and cf.record["content"] == "aws.adajosou.site"
    assert "되돌렸다" in next(n for n in snapshot["nodes"] if n["id"] == "cutover")["summary"]


@pytest.mark.parametrize("cf", [FakeCloudflare(content="other.example.net"), FakeCloudflare(proxied=True),
                                FakeCloudflare(kind="A"), FakeCloudflare(ttl=10)])
def test_unexpected_record_state_stops_without_change(tmp_path, cf):
    snapshot = _execute_cutover(_run(tmp_path, cf, FakeInternet(cf), Clock()))
    assert snapshot["outcome"] == "stopped" and cf.writes == []


def test_already_blue_on_resume_does_not_write_again(tmp_path):
    cf = FakeCloudflare(content="gcp.adajosou.site")
    snapshot = _execute_cutover(_run(tmp_path, cf, FakeInternet(cf, lag=0), Clock()))
    assert snapshot["outcome"] == "verified" and cf.writes == []


def test_record_changed_during_green_hold_stops(tmp_path):
    cf, clock = FakeCloudflare(), Clock()

    class Meddler(FakeInternet):
        def probe(self, url, host=None, timeout=10):
            result = super().probe(url, host, timeout)
            if not host:
                self.cf.record["content"] = "someone-else.example.net"  # 대기 중 다른 도구가 바꿈
            return result

    net = Meddler(cf, lag=0)
    net.resolve = lambda name: "gcp.adajosou.site"
    snapshot = _execute_cutover(_run(tmp_path, cf, net, clock))
    assert snapshot["outcome"] == "stopped"


def test_refused_approval_or_missing_config_never_switches(tmp_path):
    cf = FakeCloudflare()
    assert _execute_cutover(_run(tmp_path / "no", cf, FakeInternet(cf), Clock()),
                            approve_cutover=False)["outcome"] == "stopped"
    assert _execute_cutover(_run(tmp_path / "cfg", cf, FakeInternet(cf), Clock(), cutover=None))["outcome"] == "stopped"
    assert cf.writes == []


def test_cutover_without_release_target_stops_without_touching_dns(tmp_path):
    cf, clock = FakeCloudflare(), Clock()
    net = FakeInternet(cf)
    release = RoundJenkins(lambda commit: release_result(bundle(commit)))
    snapshot = _execute(_run(tmp_path, cf, net, clock, release_target=None, release=release))
    assert snapshot["outcome"] == "stopped"
    assert cf.writes == [] and net.calls == [] and release.params == []


def test_cutover_config_validation():
    base = {"record": "app.a.site", "green": "g.a.site", "blue": "b.a.site", "blue_origin": "http://h"}
    with pytest.raises(ValueError):
        CutoverConfig(zone_id="x", **base)
    with pytest.raises(ValueError):
        CutoverConfig(zone_id=ZONE, **{**base, "blue": "G.A.SITE."})
    with pytest.raises(ValueError):
        CutoverConfig(zone_id=ZONE, **{**base, "blue": "10.0.0.1"})
    with pytest.raises(ValueError):
        CutoverConfig(zone_id=ZONE, **base, keep_green_seconds=30)
    assert CutoverConfig(zone_id=ZONE, **{**base, "record": "App.A.Site."}).record == "app.a.site"
