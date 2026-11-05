"""스테이징 배포(신뢰된 스크립트). 스테이징 도구에 변경 토큰으로 배포를 요청하고 ready까지 기다린다.

입력(Jenkins 파라미터 환경 변수): RUN_ID(배포 ID), IMAGE, COMMIT, CLOUD. 변경 토큰: DELTA_STAGING_WRITE_TOKEN(컨트롤러 환경).
같은 RUN_ID로 다시 부르면 스테이징 도구가 기존 배포를 돌려준다(멱등). 결과: result.json
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8091"


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["DELTA_STAGING_WRITE_TOKEN"],
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        sys.exit(f"스테이징 도구 {exc.code}: {exc.read()[:300].decode(errors='replace')}")


def main():
    dep, image, commit, cloud = (os.environ[k] for k in ("RUN_ID", "IMAGE", "COMMIT", "CLOUD"))
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", dep) or "@sha256:" not in image or cloud not in ("gcp", "aws"):
        sys.exit("입력이 올바르지 않다")
    call("POST", "/v1/staging/deployments", {"run_id": dep, "cloud": cloud, "image": image, "commit": commit[:40],
                                             "scanner_ip": "auto", "replace": True})
    deadline = time.time() + 600
    while True:
        d = call("GET", f"/v1/staging/deployments/{dep}")
        if d["state"] != "deploying":
            break
        if time.time() > deadline:
            sys.exit("스테이징 배포가 600초 안에 끝나지 않았다")
        time.sleep(5)
    # 단계 이름과 내용(예: data_reset "…: 남은 행 0")을 그대로 남겨 오케스트레이터가 확인한다
    steps = [{"name": s["name"], "detail": s.get("detail", "")} for s in d.get("steps", [])]
    result = {k: d.get(k) for k in ("run_id", "state", "url", "image_digest", "revision", "scanner_ip", "error")}
    result["steps"] = steps
    json.dump(result, open("result.json", "w"))
    print(json.dumps(result, ensure_ascii=False))
    if d["state"] != "ready":
        sys.exit(f"스테이징 배포 실패: {d.get('error')}")


if __name__ == "__main__":
    main()
