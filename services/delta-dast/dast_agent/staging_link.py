"""스테이징 도구(delta-staging HTTP API) 연동. 스테이징 대상의 주소를 점검할 때마다 받아 온다.

주소를 target JSON에 적어 두지 않는다. 스테이징을 다시 만들어 주소가 바뀌어도 설정을 고칠 필요가 없다.

환경 변수
- DELTA_STAGING_URL(기본 http://127.0.0.1:8091), DELTA_STAGING_TOKEN: 스테이징 도구 주소와 Bearer 토큰
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class StagingUnavailable(Exception):
    """화면에 그대로 보여줄 원인 메시지를 담는다."""


def settings() -> dict:
    return {"url": os.environ.get("DELTA_STAGING_URL", "http://127.0.0.1:8091").rstrip("/"),
            "token": os.environ.get("DELTA_STAGING_TOKEN", "")}


def origin(cloud: str, timeout: float = 15) -> str:
    cfg = settings()
    if not cfg["token"]:
        raise StagingUnavailable("스테이징 도구 연결이 설정되지 않았습니다(DELTA_STAGING_TOKEN).")
    request = urllib.request.Request(cfg["url"] + "/v1/staging/targets",
                                     headers={"Authorization": "Bearer " + cfg["token"]})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 설정한 스테이징 도구
            targets = json.loads(response.read())["targets"]
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        raise StagingUnavailable(f"스테이징 도구({cfg['url']})에서 점검 대상 주소를 받지 못했습니다.") from None
    entry = next((item for item in targets if item.get("cloud") == cloud), None)
    if entry is None:
        raise StagingUnavailable(f"스테이징 도구에 {cloud} 스테이징이 설정돼 있지 않습니다.")
    if not entry.get("url"):
        raise StagingUnavailable(f"{cloud} 스테이징 주소를 찾지 못했습니다: {entry.get('error', '알 수 없음')}. "
                                 "스테이징 인프라가 내려가 있는지 확인하세요.")
    return entry["url"]
