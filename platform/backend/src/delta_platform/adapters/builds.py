"""CodeBuild execution; no customer commands run in the platform process."""

import re
import time
from datetime import UTC, datetime

import boto3
from botocore.config import Config

from delta_platform.core.errors import Problem


class BuildError(Exception):
    def __init__(self, code, message):
        self.payload = {"code": code, "message": message}
        super().__init__(code)


def build_target(settings):
    if settings.build_provider == "jenkins":
        from delta_platform.adapters.jenkins import jenkins_target

        return jenkins_target(settings)
    return codebuild_target(settings)


def build_runner(settings, run):
    provider = run["input_snapshot"]["build_target"].get("provider", "codebuild")
    if provider == "jenkins":
        from delta_platform.adapters.jenkins import JenkinsRunner

        return JenkinsRunner(settings)
    if provider == "codebuild":
        return CodeBuildRunner(settings)
    raise BuildError("build_provider_invalid", "저장된 빌드 실행기를 확인하세요.")


def codebuild_target(settings):
    if not settings.build_project or not settings.build_repository:
        raise Problem(
            "builder_not_configured", "이미지 빌드 실행기가 아직 연결되지 않았습니다.", 503
        )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,149}", settings.build_project):
        raise ValueError("Invalid build project")
    if not re.fullmatch(
        rf"[0-9]{{12}}\.dkr\.ecr\.{re.escape(settings.build_region)}\.amazonaws\.com/[a-z0-9][a-z0-9/_.-]+",
        settings.build_repository,
    ):
        raise ValueError("Invalid build repository")
    return {
        "project": settings.build_project,
        "repository": settings.build_repository,
        "region": settings.build_region,
    }


class CodeBuildRunner:
    def __init__(self, settings, *, codebuild=None, ecr=None, logs=None, sleep=time.sleep):
        self.target = codebuild_target(settings)
        config = Config(
            connect_timeout=5, read_timeout=15, retries={"mode": "standard", "max_attempts": 2}
        )
        # Default credential chain: the deployed worker uses its VM role, never static keys.
        self.codebuild = codebuild or boto3.client(
            "codebuild", region_name=settings.build_region, config=config
        )
        self.ecr = ecr or boto3.client("ecr", region_name=settings.build_region, config=config)
        self.logs = logs or boto3.client("logs", region_name=settings.build_region, config=config)
        self.sleep = sleep

    def request(self, claim):
        source = claim["input_snapshot"]
        if source["build_target"] != self.target:
            raise BuildError(
                "build_target_changed",
                "빌드 실행기 설정이 변경됐습니다. 기존 실행을 확인한 뒤 다시 요청하세요.",
            )
        return {
            "projectName": self.target["project"],
            "sourceLocationOverride": source["repository_url"] + ".git",
            "sourceVersion": claim["resolved_commit_sha"],
            "idempotencyToken": str(claim["id"]),
            "environmentVariablesOverride": [
                {"name": k, "value": v, "type": "PLAINTEXT"}
                for k, v in {
                    "DELTA_RUN_ID": str(claim["id"]),
                    "DELTA_COMMIT": claim["resolved_commit_sha"],
                    "DELTA_CONTEXT": source["build_context"],
                    "DELTA_DOCKERFILE": source["dockerfile_path"],
                }.items()
            ],
        }

    def execute(self, claim, store, progress):
        run_id, token = claim["id"], claim["lease_token"]
        request = self.request(claim)
        external_id = claim["external_id"]
        if not external_id:
            submission = store.prepare_submission(run_id, token, request)
            if submission["request"] != request:
                raise BuildError(
                    "build_request_changed",
                    "저장된 빌드 요청과 설정이 다릅니다. 운영자 확인이 필요합니다.",
                )
            age = (
                datetime.now(UTC) - datetime.fromisoformat(submission["prepared_at"])
            ).total_seconds()
            if age >= 240:
                # AWS tokens expire at 5 minutes. Never blindly submit after that window.
                raise BuildError(
                    "build_submission_uncertain",
                    "빌드 접수 결과를 확정할 수 없습니다. CodeBuild에서 실행 여부를 확인하세요. 자동 재실행하지 않았습니다.",
                )
            build = self.codebuild.start_build(**submission["request"])["build"]
            external_id = build["id"]
            store.attach_build(run_id, token, external_id)
        deadline = time.monotonic() + 1700
        previous = None
        while time.monotonic() < deadline:
            # Lease check before every external read; stale owners cannot publish results.
            store.heartbeat(run_id, token)
            build = self.get(external_id)
            self.validate(build, claim)
            phase = build.get("currentPhase", "QUEUED")
            if phase != previous:
                progress("build_" + phase.lower(), f"빌드 단계: {phase}")
                previous = phase
            status = build["buildStatus"]
            if status == "SUCCEEDED":
                if build.get("resolvedSourceVersion") != claim["resolved_commit_sha"]:
                    raise BuildError(
                        "build_commit_mismatch", "빌드한 커밋이 요청한 커밋과 다릅니다."
                    )
                progress("image_verify", "저장된 이미지 digest를 확인합니다.")
                repository = self.target["repository"].split("/", 1)[1]
                images = self.ecr.describe_images(
                    repositoryName=repository, imageIds=[{"imageTag": f"run-{run_id}"}]
                )["imageDetails"]
                if len(images) != 1 or not re.fullmatch(
                    r"sha256:[a-f0-9]{64}", images[0].get("imageDigest", "")
                ):
                    raise BuildError("image_missing", "저장된 이미지 digest를 확인할 수 없습니다.")
                image = images[0]
                if image.get("imageManifestMediaType") not in (
                    "application/vnd.docker.distribution.manifest.v2+json",
                    "application/vnd.oci.image.manifest.v1+json",
                ):
                    raise BuildError(
                        "image_manifest_invalid",
                        "ECS 검사에 필요한 단일 이미지 manifest가 아닙니다.",
                    )
                digest = image["imageDigest"]
                return {
                    "image": f"{self.target['repository']}@{digest}",
                    "image_digest": digest,
                    "commit_sha": claim["resolved_commit_sha"],
                    "platform": "linux/amd64",
                    "source_check_id": claim["input_snapshot"]["source_check_id"],
                    "build_id": external_id,
                    "completed_at": datetime.now(UTC).isoformat(),
                }
            if status in {"FAILED", "FAULT", "STOPPED", "TIMED_OUT"}:
                failed = next(
                    (
                        p
                        for p in build.get("phases", [])
                        if p.get("phaseStatus") in {"FAILED", "FAULT", "TIMED_OUT", "STOPPED"}
                    ),
                    {},
                )
                label = failed.get("phaseType", phase)
                code = "build_" + status.lower()
                raise BuildError(
                    code,
                    f"{label} 단계에서 빌드가 종료됐습니다 ({status}). 빌드 로그에서 원인을 확인하세요.",
                )
            self.sleep(5)
        # Server-side timeout is the final guard if the worker itself disappears.
        self.codebuild.stop_build(id=external_id)
        raise BuildError("build_wait_timeout", "빌드 확인 시간을 초과해 중단을 요청했습니다.")

    def get(self, external_id):
        if not external_id.startswith(self.target["project"] + ":"):
            raise BuildError("build_id_invalid", "허용된 빌드 프로젝트가 아닙니다.")
        items = self.codebuild.batch_get_builds(ids=[external_id]).get("builds", [])
        if len(items) != 1 or items[0].get("id") != external_id:
            raise BuildError("build_missing", "CodeBuild 실행 기록을 찾을 수 없습니다.")
        return items[0]

    def validate(self, build, run):
        values = {
            v["name"]: v["value"]
            for v in build.get("environment", {}).get("environmentVariables", [])
        }
        if (
            build.get("projectName") != self.target["project"]
            or values.get("DELTA_RUN_ID") != str(run["id"])
            or build.get("sourceVersion") != run["resolved_commit_sha"]
        ):
            raise BuildError(
                "build_identity_mismatch", "빌드 실행과 플랫폼 요청이 일치하지 않습니다."
            )

    def log_tail(self, run):
        if not run["external_id"]:
            return {"text": "", "available": False, "truncated": False}
        build = self.get(run["external_id"])
        self.validate(build, run)
        log = build.get("logs", {})
        if not log.get("streamName"):
            return {"text": "", "available": False, "truncated": False}
        expected_group = "/aws/codebuild/" + self.target["project"]
        if log.get("groupName") != expected_group:
            raise BuildError("build_logs_invalid", "허용된 빌드 로그 그룹이 아닙니다.")
        events = self.logs.get_log_events(
            logGroupName=expected_group,
            logStreamName=log["streamName"],
            limit=100,
            startFromHead=False,
        ).get("events", [])
        raw = "\n".join(e["message"] for e in events)
        # Logs are untrusted text, never HTML. Strip terminal control sequences.
        raw = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw)
        text = "".join(c for c in raw if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127))
        return {
            "text": text[-20000:],
            "available": True,
            "truncated": len(events) == 100 or len(text) > 20000,
        }
