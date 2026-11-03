import json
import re
import time
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx


class SourceError(Exception):
    def __init__(self, code, message, retry=False, **details):
        super().__init__(message)
        self.payload = {"code": code, "message": message, **details}
        self.retry = retry


class GitHubReader:
    def __init__(self, client=None):
        self.client = client or httpx.Client(
            timeout=10,
            follow_redirects=False,
            trust_env=False,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "delta-platform-source-preflight",
            },
        )
        self.owns_client = client is None

    def close(self):
        if self.owns_client:
            self.client.close()

    def inspect(self, snapshot, pinned_sha, progress):
        count = 0
        deadline = time.monotonic() + 120
        root = f"/repos/{quote(snapshot['repo_owner'], safe='')}/{quote(snapshot['repo_name'], safe='')}"

        def get(path, missing_code, missing_message):
            nonlocal count
            count += 1
            if count > 24 or time.monotonic() >= deadline:
                raise SourceError("source_limit_exceeded", "검사 요청·시간 상한을 초과했습니다.")
            try:
                with self.client.stream("GET", "https://api.github.com" + path) as response:
                    if response.status_code == 429 or (
                        response.status_code == 403
                        and (
                            response.headers.get("x-ratelimit-remaining") == "0"
                            or response.headers.get("retry-after")
                        )
                    ):
                        retry_at = response.headers.get("x-ratelimit-reset", "")
                        detail = {}
                        if retry_at.isdigit() and len(retry_at) < 12:
                            try:
                                detail["retry_at"] = datetime.fromtimestamp(
                                    int(retry_at), UTC
                                ).isoformat()
                            except (OverflowError, ValueError, OSError):
                                pass
                        raise SourceError(
                            "github_rate_limited",
                            "GitHub 요청 한도에 도달했습니다. 잠시 후 새 검사를 실행하세요.",
                            **detail,
                        )
                    if response.status_code in (404, 422):
                        raise SourceError(missing_code, missing_message)
                    if response.status_code >= 500:
                        raise SourceError(
                            "github_unavailable", "GitHub 응답 오류입니다.", retry=True
                        )
                    if response.status_code != 200:
                        raise SourceError(
                            "repository_unavailable", "공개 저장소에 접근할 수 없습니다."
                        )
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if time.monotonic() >= deadline:
                            raise SourceError(
                                "source_limit_exceeded", "검사 시간 상한을 초과했습니다."
                            )
                        body.extend(chunk)
                        if len(body) > 4 * 1024 * 1024:
                            raise SourceError(
                                "source_limit_exceeded", "GitHub 응답 크기 상한을 초과했습니다."
                            )
                    value = json.loads(body)
                    if not isinstance(value, dict):
                        raise ValueError()
                    return value
            except httpx.TransportError:
                raise SourceError(
                    "github_unavailable", "GitHub 연결에 실패했습니다.", retry=True
                ) from None
            except (ValueError, KeyError, TypeError):
                raise SourceError(
                    "github_unavailable", "GitHub 응답 형식을 확인할 수 없습니다."
                ) from None

        progress("repository", "공개 저장소를 확인하고 있습니다.")
        repo = get(root, "repository_unavailable", "공개 저장소를 찾을 수 없습니다.")
        if repo.get("private") is not False:
            raise SourceError("repository_unavailable", "공개 저장소만 지원합니다.")
        progress("revision", "검사할 커밋을 확인하고 있습니다.")
        ref = pinned_sha or snapshot["source_ref"]
        commit = get(
            root + "/commits/" + quote(ref, safe=""),
            "ref_not_found",
            "브랜치 또는 커밋을 찾을 수 없습니다.",
        )
        try:
            sha, tree_sha = commit["sha"], commit["commit"]["tree"]["sha"]
            if not re.fullmatch(r"[0-9a-f]{40}", sha) or not re.fullmatch(
                r"[0-9a-f]{40}", tree_sha
            ):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise SourceError("github_unavailable", "커밋 정보를 확인할 수 없습니다.") from None
        if pinned_sha and pinned_sha != sha:
            raise SourceError("github_unavailable", "고정한 커밋과 응답이 다릅니다.")
        progress("revision", "검사할 커밋을 고정했습니다.", sha)
        trees = {}

        def tree(identifier):
            if identifier not in trees:
                response = get(
                    root + "/git/trees/" + identifier, "path_not_found", "경로를 찾을 수 없습니다."
                )
                if response.get("truncated") or not isinstance(response.get("tree"), list):
                    raise SourceError(
                        "source_limit_exceeded", "전체 경로 목록을 확인할 수 없습니다."
                    )
                entries = response["tree"]
                if any(
                    not isinstance(e, dict)
                    or not isinstance(e.get("path"), str)
                    or not re.fullmatch(r"[0-9a-f]{40}", str(e.get("sha", "")))
                    for e in entries
                ):
                    raise SourceError("github_unavailable", "경로 응답 형식이 올바르지 않습니다.")
                trees[identifier] = {entry["path"]: entry for entry in entries}
            return trees[identifier]

        def resolve(path):
            current = {"sha": tree_sha, "type": "tree", "mode": "040000"}
            for part in PurePosixPath(path).parts:
                if current.get("type") != "tree" or current.get("mode") != "040000":
                    raise SourceError(
                        "unsupported_path_type", "경로 중간에 일반 디렉터리가 아닌 항목이 있습니다."
                    )
                current = tree(current["sha"]).get(part)
                if current is None:
                    raise SourceError(
                        "path_not_found", "등록한 경로 또는 Dockerfile을 찾을 수 없습니다."
                    )
            return current

        progress("build_context", "빌드 경로를 확인하고 있습니다.")
        context = resolve(snapshot["build_context"])
        if context.get("type") != "tree" or context.get("mode") != "040000":
            raise SourceError("unsupported_path_type", "빌드 경로는 일반 디렉터리여야 합니다.")
        progress("dockerfile", "Dockerfile의 위치와 파일 유형을 확인하고 있습니다.")
        dockerfile = resolve(snapshot["dockerfile_path"])
        if dockerfile.get("type") != "blob" or dockerfile.get("mode") not in ("100644", "100755"):
            raise SourceError(
                "unsupported_path_type",
                "Dockerfile은 일반 파일이어야 합니다. 링크·서브모듈은 지원하지 않습니다.",
            )
        if not isinstance(dockerfile.get("size"), int) or not 0 < dockerfile["size"] <= 256 * 1024:
            raise SourceError(
                "source_limit_exceeded", "Dockerfile이 비어 있거나 256 KiB를 초과합니다."
            )
        return {
            "resolved_commit_sha": sha,
            "build_context": snapshot["build_context"],
            "dockerfile_path": snapshot["dockerfile_path"],
            "dockerfile_blob_sha": dockerfile["sha"],
            "checked_at": datetime.now(UTC).isoformat(),
        }
