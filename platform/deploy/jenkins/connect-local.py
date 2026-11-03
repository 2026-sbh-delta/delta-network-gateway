"""Obtain local Jenkins API/agent tokens; persist only in ignored 0600 files."""

import os
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx


def private_write(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(content)


def main():
    root = Path(__file__).resolve().parent
    outputs = [root / ".runtime/platform.env", root / ".runtime/agent-secret"]
    if any(p.exists() for p in outputs):
        raise ValueError("기존 API/Agent 토큰이 있습니다. 기존 설정을 재사용하세요.")
    config = dict(
        line.split("=", 1) for line in (root / ".env").read_text().splitlines() if line
    )
    origin = "http://127.0.0.1:18081"
    with httpx.Client(
        base_url=origin,
        auth=("platform", config["JENKINS_PLATFORM_PASSWORD"]),
        timeout=30,
        trust_env=False,
    ) as client:
        crumb = client.get("/crumbIssuer/api/json")
        crumb.raise_for_status()
        result = client.post(
            "/user/platform/descriptorByName/jenkins.security.ApiTokenProperty/generateNewToken",
            data={"newTokenName": "delta-platform"},
            headers={crumb.json()["crumbRequestField"]: crumb.json()["crumb"]},
        )
        result.raise_for_status()
        token = result.json()["data"]["tokenValue"]
    with httpx.Client(
        base_url=origin,
        auth=("admin", config["JENKINS_ADMIN_PASSWORD"]),
        timeout=30,
        trust_env=False,
    ) as client:
        result = client.get("/computer/image-builder/jenkins-agent.jnlp")
        result.raise_for_status()
        secret = ET.fromstring(result.text).find(".//argument").text
        if not secret or len(secret) < 32:
            raise ValueError("Agent secret 응답을 확인하세요.")
    private_write(
        outputs[0],
        f"PLATFORM_BUILD_PROVIDER=jenkins\nPLATFORM_JENKINS_URL={origin}\nPLATFORM_JENKINS_JOB=delta-platform-image\nPLATFORM_JENKINS_USER=platform\nPLATFORM_JENKINS_TOKEN={token}\nPLATFORM_BUILD_REPOSITORY={config['DELTA_ECR_REPOSITORY']}\nPLATFORM_BUILD_REGION=ap-northeast-2\n",
    )
    private_write(outputs[1], secret)
    print("플랫폼 API/에이전트 연결 파일 생성 완료. 토큰은 출력하지 않습니다.")


if __name__ == "__main__":
    main()
