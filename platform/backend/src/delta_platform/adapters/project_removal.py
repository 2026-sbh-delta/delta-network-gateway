"""Cleanup adapters use registered identities, never a caller-supplied resource name."""

import base64
import json
import re

import boto3
import sqlalchemy as sa
from botocore.config import Config
from botocore.exceptions import ClientError

from delta_platform.adapters.builds import BuildError
from delta_platform.adapters.deployment import DeploymentRunner
from delta_platform.adapters.jenkins import JenkinsRunner
from delta_platform.adapters.onprem_removal import OnpremRemover
from delta_platform.core.deployment_targets import connection
from delta_platform.core.errors import Problem
from delta_platform.core.schema import runs
from delta_platform.core.store import digest


class DeploymentRemovalRunner(DeploymentRunner):
    def execute(self, claim, store, progress):
        return JenkinsRunner.execute(
            self,
            claim,
            store,
            lambda step, message, sha=None: progress(
                "project_resources",
                "배포 실행기에서 프로젝트 자원을 정리하고 결과를 확인합니다.",
                sha,
            ),
        )

    def parameters(self, claim):
        source = claim["input_snapshot"]
        if source["runner"] != self.target or any(
            d["connection"] != connection(self.deployment_config, d["target"]["cloud"])
            for d in source["deployments"]
        ):
            raise BuildError("cleanup_target_changed", "배포 당시의 실행기·계정 연결을 복원하세요.")
        payload = {
            "operation": "remove",
            "schema_version": 1,
            "run_id": str(claim["id"]),
            "service_id": str(claim["service_id"]),
            "snapshot_hash": digest(source),
            "deployments": source["deployments"],
        }
        return {
            "DELTA_RUN_ID": str(claim["id"]),
            "DELTA_DEPLOYMENT": base64.b64encode(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).decode(),
        }

    def result(self, external, claim):
        response = self.http(
            "GET", self.build_path(external) + "/artifact/result.json", limit=32768
        )
        expected = {
            "run_id": str(claim["id"]),
            "snapshot_hash": digest(claim["input_snapshot"]),
            "cleanup": "complete",
        }
        try:
            if json.loads(response.content) != expected:
                raise ValueError()
        except ValueError:
            raise BuildError(
                "cleanup_result_invalid", "자원 삭제 결과가 요청과 일치하지 않습니다."
            ) from None
        return expected


class ProjectRemover:
    def __init__(self, settings, *, remover=None, deployment=None, ecr=None):
        self.settings, self.remover, self.deployment, self.ecr = settings, remover, deployment, ecr

    def execute(self, claim, store, progress):
        snapshot = claim["input_snapshot"]
        try:
            if snapshot["deployments"]:
                progress(
                    "project_resources", "배포한 VM·앱과 실패한 배포의 남은 자원을 정리합니다."
                )
                (self.deployment or DeploymentRemovalRunner(self.settings)).execute(
                    claim, store, progress
                )
            for runtime in snapshot["runtimes"]:
                (self.remover or OnpremRemover(self.settings)).execute(
                    {**claim, "input_snapshot": runtime}, store, progress
                )
            progress(
                "project_images",
                "프로젝트의 빌드 이미지 태그를 정리합니다. 공유 이미지는 보존합니다.",
            )
            for image in snapshot["images"]:
                store.heartbeat(claim["id"], claim["lease_token"])
                self.remove_image(image, claim, store)
        except Problem as exc:
            raise BuildError(exc.code, exc.message) from None
        progress("project_removed", "실행 자원 정리를 확인했습니다. 등록 정보와 이력을 삭제합니다.")
        return {"cleanup": "complete", "snapshot_hash": digest(snapshot)}

    def remove_image(self, image, claim, store):
        if (
            image["repository"] != self.settings.build_repository
            or image["region"] != self.settings.build_region
            or not re.fullmatch(r"run-[0-9a-f-]{36}", image["tag"])
        ):
            raise BuildError(
                "cleanup_registry_changed", "빌드 당시 이미지 저장소 연결을 복원하세요."
            )
        registry, repository = image["repository"].split("/", 1)
        client = self.ecr or boto3.client(
            "ecr",
            region_name=image["region"],
            config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}),
        )
        args = {
            "registryId": registry.split(".")[0],
            "repositoryName": repository,
            "imageIds": [{"imageTag": image["tag"]}],
        }
        try:
            details = client.describe_images(**args)["imageDetails"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ImageNotFoundException":
                return
            raise
        for item in details:
            image_digest = item["imageDigest"]
            # Other services may pin the same digest without using this tag.
            with store.engine.connect() as conn:
                shared = conn.scalar(
                    sa.select(runs.c.id)
                    .where(
                        runs.c.service_id != claim["service_id"],
                        sa.or_(
                            runs.c.input_snapshot.cast(sa.Text).contains(image_digest),
                            runs.c.result.cast(sa.Text).contains(image_digest),
                        ),
                    )
                    .limit(1)
                )
            if shared:
                return
        result = client.batch_delete_image(**args)
        if any(f.get("failureCode") != "ImageNotFound" for f in result.get("failures", [])):
            raise BuildError(
                "image_cleanup_failed", "빌드 이미지 정리에 실패했습니다. 다시 시도하세요."
            )
        try:
            if client.describe_images(**args)["imageDetails"]:
                raise BuildError(
                    "image_cleanup_pending", "이미지 삭제 확인이 지연됩니다. 다시 시도하세요."
                )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ImageNotFoundException":
                raise
