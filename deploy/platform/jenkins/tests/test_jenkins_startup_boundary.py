"""기존 JCasC/컨트롤러 이미지의 시작 계약을 검증한다(실 Jenkins 미실행)."""

import re

from .delivery_support import CONTROLLER


def test_casc_registers_trusted_release_job():
    casc = (CONTROLLER / "casc.yaml").read_text()
    match = re.search(r"pipelineJob\(['\"]delta-release['\"]\)(.*?)(?=pipelineJob\(|\Z)", casc, re.S)
    assert match, "기존 JCasC startup에 delta-release entrypoint가 없다"
    job = match.group(1)
    assert "/opt/delta/Jenkinsfile.release" in job
    assert any(re.search(rf"stringParam\(['\"]{name}['\"]", job) for name in ("DEPLOYMENT_ID", "RUN_ID"))
    for name in ("COMMIT", "TARGET_ID", "IMAGES_JSON", "BUNDLE_SHA256", "APPROVAL"):
        assert re.search(rf"(?:stringParam|textParam|passwordParam)\(['\"]{name}['\"]", job), name
    assert "sandbox(true)" in job


def test_staging_job_accepts_bundle_parameters():
    casc = (CONTROLLER / "casc.yaml").read_text()
    job = casc.split("pipelineJob('delta-deploy')", 1)[1]
    for name in ("IMAGES_JSON", "BUNDLE_SHA256"):
        assert re.search(rf"(?:stringParam|textParam)\(['\"]{name}['\"]", job), name


def test_controller_image_ships_release_and_common_host_entrypoints():
    dockerfile = (CONTROLLER / "Dockerfile").read_text()
    copies = " ".join(re.findall(r"^COPY\s+(.+(?:\\\n.+)*)", dockerfile, re.M))
    for name in ("Jenkinsfile.release", "release_deploy.py", "compose_host.py"):
        assert name in copies, f"컨트롤러 startup image에 {name}이 포함되지 않는다"


def test_release_pipeline_runs_only_trusted_controller_script():
    casc = (CONTROLLER / "casc.yaml").read_text()
    # 먼저 기존 public startup 경계로 RED. 신규 파일 import/누락 자체를 RED로 쓰지 않는다.
    assert "Jenkinsfile.release" in casc, "운영 배포가 기존 startup 경계에 연결되지 않았다"
    pipeline = (CONTROLLER / "Jenkinsfile.release").read_text()
    assert re.search(r"label\s+['\"]delta-deploy['\"]", pipeline)
    assert "python3 /opt/delta/release_deploy.py" in pipeline
    assert "archiveArtifacts" in pipeline and "result.json" in pipeline
    assert not re.search(r"checkout\s+scm|git\s+clone|docker\s+build", pipeline)
