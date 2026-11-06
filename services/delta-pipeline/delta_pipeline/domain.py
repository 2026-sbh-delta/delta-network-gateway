"""서비스 도메인 되돌리기(시연 초기화): app CNAME 을 blue(GCP)에서 green(AWS)으로 돌린다.

이전 실행 밖의 별도 동작이다. 사람이 화면에서 승인해야 시작하고, 전환 단계와 같은 검사를 방향만 바꿔 쓴다.
green 입구가 서비스 도메인으로 정상 응답하지 않으면 DNS 를 건드리지 않는다. 바꾼 뒤 공개 DNS·실제 응답으로
green 을 확인하지 못하면 blue 로 되돌린다. blue 는 내리지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.steps import (CUTOVER_GRACE_SECONDS, CUTOVER_STREAK, POLL_SECONDS, CloudflareClient,
                                  CutoverConfig, hostname)

Report = Callable[[dict[str, Any]], None]


def _green_ok(status: int, served: str, cfg: CutoverConfig) -> bool:
    # green(AWS)은 X-Served-By 를 붙이지 않을 수 있다. 200 이면서 blue 가 응답한 것이 아니어야 한다.
    return status == 200 and not served.startswith(cfg.expect_served_by)


def current_target(cfg: CutoverConfig, cf: CloudflareClient) -> str:
    return hostname(cf.find_record(cfg.zone_id, cfg.record).get("content"))


def revert(cfg: CutoverConfig, cf: CloudflareClient, *, probe: Callable[..., tuple[int, str]],
           resolve: Callable[[str], str | None], now: Callable[[], float], sleep: Callable[[float], None],
           report: Report = lambda _: None) -> dict[str, Any]:
    """결과: {"state": "pass"|"fail", "summary": …}. 예외 대신 결과로 돌려준다(화면에 그대로 보인다)."""
    def done(state: str, summary: str, **extra: Any) -> dict[str, Any]:
        result = {"state": state, "summary": summary, **extra}
        report(result)
        return result

    path = cfg.green_verify_path or cfg.verify_path
    try:
        record = cf.find_record(cfg.zone_id, cfg.record)
    except ServiceUnavailable as exc:
        return done("fail", f"레코드를 읽지 못했다({exc}). DNS 를 바꾸지 않았다")
    if record.get("type") != "CNAME" or record.get("proxied") is not False:
        return done("fail", f"{cfg.record} 는 DNS 전용 CNAME 이어야 한다. DNS 를 바꾸지 않았다")
    current = hostname(record.get("content"))
    if current not in (cfg.blue, cfg.green):
        return done("fail", f"현재 대상({current})이 blue·green 이 아니다. 사람이 확인해야 한다")

    report({"state": "running", "summary": f"{cfg.green} 입구 확인 중"})
    origin = cfg.green_origin or f"https://{cfg.green}"
    try:
        status, served = probe(origin + path, host=cfg.record)
    except ServiceUnavailable as exc:
        status, served = 0, str(exc)
    if not _green_ok(status, served, cfg):
        return done("fail", f"{cfg.green} 입구가 정상이 아니다(HTTP {status}). DNS 를 바꾸지 않았다")

    if current == cfg.blue:
        cf.set_content(cfg.zone_id, record["id"], cfg.green)
    switched_at, ttl = now(), record.get("ttl")
    wait = 300 if ttl == 1 else int(ttl) if isinstance(ttl, int) else 300
    report({"state": "running", "summary": f"{cfg.record} → {cfg.green}(설정 반영), 실제 DNS·응답 확인 중"})

    url = f"{cfg.scheme}://{cfg.record}{path}"
    streak, answer, last = 0, None, (0, "")
    while now() < switched_at + wait + CUTOVER_GRACE_SECONDS:
        try:
            answer = resolve(cfg.record)
            last = probe(url) if answer == cfg.green else (0, "")
        except ServiceUnavailable:
            last = (0, "")
        streak = streak + 1 if answer == cfg.green and _green_ok(*last, cfg) else 0
        if streak >= CUTOVER_STREAK:
            return done("pass", f"{cfg.record} → {cfg.green} 확인(HTTP {last[0]}). {cfg.blue} 는 내리지 않음",
                        previous=current)
        sleep(POLL_SECONDS)
    if current == cfg.green:
        rolled = "레코드는 처음부터 green 이었다(바꾸지 않음)"
    else:
        try:
            cf.set_content(cfg.zone_id, record["id"], cfg.blue)
            rolled = f"{cfg.blue} 로 되돌렸다"
        except ServiceUnavailable as exc:
            rolled = f"되돌리지 못했다({exc}). 사람이 바로 확인해야 한다"
    return done("fail", f"되돌린 뒤 확인 실패(DNS {answer or '없음'}, HTTP {last[0]}). {rolled}")
