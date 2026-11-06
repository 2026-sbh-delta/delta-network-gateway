"""Local integration adapter: reuse one successful platform build in the graph.

This does not publish a platform endpoint or grant deployment authority. The
caller must obtain the snapshot from the authenticated platform build API.
Only the immutable artifact fields are retained, never provider credentials.
"""

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.model import REPO_PREFIX


@dataclass(frozen=True)
class RegisteredBuild:
    service_id: str
    build_id: str
    repo: str
    commit: str
    image: str

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any], *, service: dict[str, Any]):
        try:
            service_id = str(UUID(str(service["id"])))
            if (snapshot["kind"], snapshot["status"]) != ("image_build", "succeeded"):
                raise ValueError()
            if str(UUID(str(snapshot["service_id"]))) != str(UUID(service_id)):
                raise ValueError()
            build_id = str(UUID(str(snapshot["id"])))
            # The public build API deliberately omits its internal input_snapshot.
            # Obtain repository identity from the matching authenticated service API.
            repo = service["repository_url"]
            if not isinstance(repo, str) or not re.fullmatch(
                re.escape(REPO_PREFIX) + r"[A-Za-z0-9._-]+", repo
            ):
                raise ValueError()
            commit = snapshot["resolved_commit_sha"]
            image = snapshot["result"]["image"]
            if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
                raise ValueError()
            if not isinstance(image, str) or not re.fullmatch(
                r"[a-z0-9][a-z0-9.:-]*(?:/[a-z0-9._-]+)+@sha256:[0-9a-f]{64}", image
            ):
                raise ValueError()
            return cls(service_id, build_id, repo, commit, image)
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ServiceUnavailable(
                "성공한 동일 서비스의 고정 커밋·이미지 빌드가 필요합니다"
            ) from None

    def assert_request(self, repo: str, commit: str):
        if (repo, commit) != (self.repo, self.commit):
            raise ServiceUnavailable(
                "등록된 빌드와 그래프 요청의 저장소·커밋이 다릅니다"
            )

    def find_build(self, run_key: str) -> int:
        # No external submission: expose the already completed artifact once.
        return 1

    def trigger(self, params, run_key):
        raise ServiceUnavailable(
            "기존 플랫폼 이미지를 재사용하는 시험에서는 새 빌드를 제출하지 않습니다"
        )

    def status(self, build: int) -> str:
        if build != 1:
            raise ServiceUnavailable("등록된 빌드가 아닙니다")
        return "SUCCESS"

    def file(self, build: int, name: str):
        self.status(build)
        return (
            {
                "commit": self.commit,
                "image_ref": self.image,
                "image_digest": self.image.rsplit("@", 1)[1],
            }
            if name == "result.json"
            else None
        )

    def console_url(self, build: int) -> str:
        self.status(build)
        return f"/api/v1/builds/{self.build_id}"
