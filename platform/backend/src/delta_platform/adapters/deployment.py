"""Reuse durable Jenkins submission/recovery with a separate trusted deployment job."""

import base64
import json

from delta_platform.adapters.builds import BuildError
from delta_platform.adapters.jenkins import JenkinsRunner
from delta_platform.core.deployment_targets import connection
from delta_platform.core.deployments import validate_report
from delta_platform.core.errors import Problem


def deployment_settings(settings):
    if (
        not settings.deployment_jenkins_user
        or not settings.deployment_jenkins_token.get_secret_value()
        or settings.deployment_jenkins_job == settings.jenkins_job
    ):
        raise Problem(
            "deployment_not_configured", "별도 Jenkins 배포 작업과 전용 계정을 연결하세요.", 503
        )
    return settings.model_copy(
        update={
            "jenkins_job": settings.deployment_jenkins_job,
            "jenkins_user": settings.deployment_jenkins_user,
            "jenkins_token": settings.deployment_jenkins_token,
        }
    )


class DeploymentRunner(JenkinsRunner):
    execution_timeout = 3900

    def __init__(self, settings, **kwargs):
        self.deployment_config = settings
        super().__init__(deployment_settings(settings), ecr=object(), **kwargs)

    def parameters(self, claim):
        source = claim["input_snapshot"]
        try:
            current = connection(self.deployment_config, source["target"]["cloud"])
        except Problem as exc:
            raise BuildError(exc.code, exc.message) from None
        if source["runner"] != self.target or source["connection"] != current:
            raise BuildError(
                "deployment_target_changed",
                "배포 실행기 연결이 변경됐습니다. 기존 작업을 확인하세요.",
            )
        payload = {k: v for k, v in source.items() if k != "runner"}
        payload["run_id"] = str(claim["id"])
        return {
            "DELTA_RUN_ID": str(claim["id"]),
            "DELTA_DEPLOYMENT": base64.b64encode(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).decode(),
        }

    def execute(self, claim, store, progress):
        def deployment_progress(step, message, sha=None):
            messages = {
                "build_queued": ("deploy_queued", "Jenkins 배포 작업을 기다립니다."),
                "build_build": ("deploy_running", "선택한 환경에 앱을 배포합니다."),
                "image_verify": (
                    "deploy_verify",
                    "배포한 자원·이미지·실행 검사 결과를 확인합니다.",
                ),
            }
            step, message = messages.get(step, (step, message))
            progress(step, message, sha)

        return super().execute(claim, store, deployment_progress)

    def result(self, external, claim):
        response = self.http(
            "GET", self.build_path(external) + "/artifact/result.json", limit=32768
        )
        try:
            return validate_report(claim, json.loads(response.content))
        except (ValueError, KeyError, TypeError, AttributeError):
            raise BuildError(
                "deployment_result_invalid",
                "배포 결과의 자원·이미지·실행 검사 정보가 요청과 다릅니다.",
            ) from None
