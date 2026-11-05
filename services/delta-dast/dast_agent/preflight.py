"""점검 전 사전 확인. 에이전트·브라우저를 띄우기 전에 몇 초 안에 막힐 곳을 찾는다."""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request

TIMEOUT = 5.0


class PreflightError(Exception):
    """화면에 그대로 보여줄 원인 메시지를 담는다."""


def _status(url: str, timeout: float) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": "delta-dast-preflight"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


async def check_target(origin: str, entry_path: str, timeout: float = TIMEOUT) -> None:
    try:
        status = await asyncio.to_thread(_status, origin + entry_path, timeout)
    except (urllib.error.URLError, OSError):
        raise PreflightError(
            f"스테이징({origin})에 {timeout:g}초 안에 연결되지 않습니다. 스테이징이 내려가 있거나, "
            "주소가 바뀌었거나, 보안 그룹이 이 실행기의 IP를 막고 있는지 확인하세요."
        ) from None
    if status == 403:
        raise PreflightError(
            f"스테이징({origin})이 이 실행기의 접근을 막았습니다(403). "
            "Cloud Armor·보안 그룹에 실행기 공인 IP가 허용돼 있는지 확인하세요."
        )
    if status == 404:
        raise PreflightError(
            f"스테이징에 시작 경로({entry_path})가 없습니다(404). 배포된 앱과 점검 대상 설정이 맞는지 확인하세요."
        )
    if status >= 500:
        raise PreflightError(f"스테이징 앱이 오류({status})를 돌려줍니다. 배포 상태를 확인하세요.")
    if status >= 400:
        raise PreflightError(f"스테이징이 시작 경로({entry_path})에 {status}을 돌려줍니다.")
