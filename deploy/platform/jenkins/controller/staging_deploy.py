"""스테이징 배포(신뢰된 스크립트). 스테이징 도구에 변경 토큰으로 배포를 요청하고 ready까지 기다린다.

입력(Jenkins 파라미터 환경 변수): RUN_ID(배포 ID), IMAGE, COMMIT, CLOUD, HOLDER(선택: 점유 실행 ID).
변경 토큰: DELTA_STAGING_WRITE_TOKEN(컨트롤러 환경).
같은 RUN_ID로 다시 부르면 스테이징 도구가 기존 배포를 돌려준다(멱등).
다른 실행이 스테이징을 점유하고 있으면(점검 중) 스테이징 도구가 409로 거부하고, 데이터 초기화도 하지 않는다.
결과: result.json(실패해도 남긴다)
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

from delivery_contract import IMAGE_RE, bundle_from_images

BASE = "http://127.0.0.1:8091"


def finish(result, error=None):
    if error:
        result = {**result, "state": result.get("state") or "rejected", "error": error}
    json.dump(result, open("result.json", "w"))
    print(json.dumps(result, ensure_ascii=False))
    if error:
        sys.exit(error)


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["DELTA_STAGING_WRITE_TOKEN"],
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        finish({}, f"스테이징 도구 {exc.code}: {exc.read()[:300].decode(errors='replace')}")


def main():
    dep, commit, cloud = (os.environ[k] for k in ("RUN_ID", "COMMIT", "CLOUD"))
    image = os.environ.get("IMAGE", "")
    bundle = None
    if os.environ.get("IMAGES_JSON") or os.environ.get("BUNDLE_SHA256"):
        try:
            bundle = bundle_from_images(commit, json.loads(os.environ.get("IMAGES_JSON", "null")),
                                        os.environ.get("BUNDLE_SHA256"))
            if image and image != bundle["image_ref"]:
                raise ValueError("IMAGE 별칭이 frontend와 다르다")
            image = bundle["image_ref"]
        except (ValueError, TypeError):
            finish({"run_id": dep}, "이미지 묶음 입력이 올바르지 않다")
    holder = os.environ.get("HOLDER") or dep
    ok = re.compile(r"[A-Za-z0-9._-]{1,64}")
    if (not ok.fullmatch(dep) or not ok.fullmatch(holder) or not IMAGE_RE.fullmatch(image) or cloud not in ("gcp", "aws")
            or not re.fullmatch(r"[0-9a-f]{40}", commit)):
        finish({"run_id": dep}, "입력이 올바르지 않다")
    body = {"run_id": dep, "cloud": cloud, "image": image, "commit": commit,
            "scanner_ip": "auto", "replace": True, "lease_holder": holder}
    if bundle:
        body.update(images=bundle["images"], image_digests=bundle["image_digests"], bundle_sha256=bundle["bundle_sha256"])
    call("POST", "/v1/staging/deployments", body)
    deadline = time.time() + 600
    while True:
        d = call("GET", f"/v1/staging/deployments/{dep}")
        if d["state"] != "deploying":
            break
        if time.time() > deadline:
            finish({}, "스테이징 배포가 600초 안에 끝나지 않았다")
        time.sleep(1)  # 스테이징 도구 조회는 같은 호스트(127.0.0.1)다. 끝난 뒤 최대 1초 안에 알아챈다
    # 단계 이름과 내용(예: data_reset "…: 남은 행 0")을 그대로 남겨 오케스트레이터가 확인한다
    result = {k: d.get(k) for k in ("run_id", "state", "url", "image_digest", "revision", "scanner_ip", "error")}
    result["steps"] = [{"name": s["name"], "detail": s.get("detail", "")} for s in d.get("steps", [])]
    for key in ("commit", "schema", "platform", "image_ref", "images", "image_digests", "bundle_sha256", "data_reset_remaining"):
        if key in d:
            result[key] = d[key]
    version = d.get("database_version_num", d.get("databaseVersionNum"))
    if version is not None:
        result["database_version_num"] = version
    if bundle and d["state"] == "ready":
        # ref/digest/reset은 관측 결과만 사용한다. 입력을 실행 증거로 복사하지 않는다.
        if (any(d.get(k) != bundle[k] for k in ("images", "image_digests", "bundle_sha256", "image_digest"))
                or type(d.get("data_reset_remaining")) is not int or d["data_reset_remaining"] != 0):
            finish({**result, "state": "rejected"}, "스테이징의 두 이미지 또는 구조화 reset 관측이 다르다")
        if "commit" in d and d["commit"] != commit:
            finish({**result, "state": "rejected"}, "관측 commit이 다르다")
        if version is not None and (type(version) is not int or version // 10000 != 18):
            finish({**result, "state": "rejected"}, "관측 DB 버전이 PostgreSQL18이 아니다")
    finish(result, None if d["state"] == "ready" else f"스테이징 배포 실패: {d.get('error')}")


if __name__ == "__main__":
    main()
