"""Jenkinsfile의 실제 sh 본문을 격리 실행하고 result.json을 검사한다."""

import json
import re
import subprocess
import sys
from pathlib import Path

from .delivery_support import CONTROLLER, canonical_hash


def run_build(tmp_path, *, fail_service=""):
    pipeline = (CONTROLLER / "Jenkinsfile.build").read_text()
    shells = re.findall(r"\bsh\s+'''(.*?)'''", pipeline, re.S)
    assert len(shells) >= 2, "관리 Jenkinsfile의 입력/빌드 shell 경계를 찾을 수 없다"
    # Groovy의 triple-single-quoted literal에서 \\\\는 shell의 \\ 하나다.
    # 이를 복원하지 않으면 허용된 REPO까지 입력 검사에서 탈락해 가짜 RED가 된다.
    shells = [shell.replace("\\\\", "\\").replace("\\'", "'") for shell in shells]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    source = (Path(__file__).parent / "fixtures/docker_build_fake.py").read_text()
    docker.write_text("#!" + sys.executable + "\n" + source)
    docker.chmod(0o700)
    python = bin_dir / "python3"
    python.write_text("#!" + sys.executable + "\n"
        "import os, sys\n"
        "if sys.argv[1:] == ['/opt/delta/ar-token.py']:\n"
        "    print('fixture-only-not-a-credential')\n"
        "else:\n"
        "    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n")
    python.chmod(0o700)
    for service in ("frontend", "backend"):
        (tmp_path / "src" / service).mkdir(parents=True)
        (tmp_path / "src" / service / "Dockerfile").write_text("FROM scratch\n")
    # 자격증명 환경을 상속하지 않는다. 실제 체크아웃/클라우드 CLI도 실행하지 않는다.
    env = {"PATH": str(bin_dir) + ":/usr/bin:/bin", "RUN_ID": "fixture-build", "COMMIT": "c" * 40,
           "REPO": "https://github.com/2026-sbh-delta/fixture-app",
           "IMAGE_REPO": "asia-northeast3-docker.pkg.dev/term2-delta/delta-lab-staging/fixture-app",
           "FRONTEND_IMAGE_REPO": "asia-northeast3-docker.pkg.dev/term2-delta/delta-lab-staging/fixture-app/frontend",
           "BACKEND_IMAGE_REPO": "asia-northeast3-docker.pkg.dev/term2-delta/delta-lab-staging/fixture-app/backend",
           "FIXTURE_DOCKER_CALLS": str(tmp_path / "docker-calls.jsonl"),
           "FIXTURE_FAIL_SERVICE": fail_service}
    # stale 성공 산출물을 지우는 실제 입력 stage도 함께 실행한다.
    (tmp_path / "result.json").write_text('{"schema":2,"stale":true}')
    completed = subprocess.run(["/bin/bash", "-c", shells[0] + "\n" + shells[-1]],
                               cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    calls_path = tmp_path / "docker-calls.jsonl"
    calls = [json.loads(line) for line in calls_path.read_text().splitlines()] if calls_path.exists() else []
    result_path = tmp_path / "result.json"
    result = json.loads(result_path.read_text()) if result_path.exists() else None
    return completed, calls, result


def test_build_shell_uses_frontend_and_backend_contexts_with_same_platform(tmp_path):
    completed, calls, _ = run_build(tmp_path)
    assert completed.returncode == 0, completed.stderr
    builds = [call for call in calls if call[:2] == ["buildx", "build"]]
    assert sorted(Path(call[-1]).name for call in builds) == ["backend", "frontend"], \
        "실제 Jenkins build shell이 FE/BE 대신 저장소 루트 하나만 빌드한다"
    for call in builds:
        assert call[call.index("--platform") + 1] == "linux/amd64"
        assert "--push" in call
        assert "--provenance=false" in call and "--sbom=false" in call


def test_build_result_contains_two_manifest_digests_and_canonical_bundle(tmp_path):
    completed, _, result = run_build(tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert result.get("schema") == 2, "result.json이 단일 이미지 v1에 머물렀다"
    assert result["platform"] == "linux/amd64" and result["commit"] == "c" * 40
    assert result["image_digests"] == {"frontend": "sha256:" + "a" * 64, "backend": "sha256:" + "b" * 64}
    assert set(result["images"]) == {"frontend", "backend"}
    for service, digest in result["image_digests"].items():
        assert result["images"][service].endswith("@" + digest)
    core = {key: result[key] for key in ("schema", "commit", "platform", "image_digests")}
    assert result["bundle_sha256"] == canonical_hash(core)
    assert result["image_ref"] == result["images"]["frontend"]
    assert result["image_digest"] == result["image_digests"]["frontend"]


def test_backend_build_failure_never_publishes_complete_bundle_or_stale_success(tmp_path):
    completed, calls, result = run_build(tmp_path, fail_service="backend")
    assert any(call[:2] == ["buildx", "build"] and Path(call[-1]).name == "backend" for call in calls), \
        "BE 빌드 경계에 도달하지 않아 실패 주입을 검사할 수 없다"
    assert completed.returncode != 0, "BE 빌드가 실패하도록 했는데 Jenkins shell이 성공했다"
    assert result is None or result.get("state") in {"failed", "rejected"}, result
