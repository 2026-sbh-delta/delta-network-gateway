"""승인·예약을 마친 운영 요청을 동규님 방식 배포기(release/deploy.py)로 실행하고 우리 결과 계약으로 바꾼다.

release_deploy.py가 Ed25519 승인 검증·nonce 예약을 끝낸 뒤에만 execute()를 부른다.
이 모듈은 import·validate_target에서 클라우드에 접속하지 않는다.

인벤토리 항목(/run/delta/release-targets.json, target_id를 키로 하는 dict의 값):
    {"target_id": "...", "environment": "production", "cloud": "gcp"|"aws",
     "region": "asia-northeast3"|"ap-northeast-2", "profile_id": "e2-small"|"t3.small",
     "database": {"profile_id": "<release-config.databases 키>", "mode": "new"|"existing"},
     "environment_id": "<선택: UUID. 없으면 target_id에서 고정 파생>"}
항목 전체의 canonical SHA256이 승인 claims의 config_sha256이다. 비밀·네트워크 값은 release-config에만 둔다.
"""

import base64
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from delivery_contract import bundle_from_images, canonical_hash

TARGET_KEYS = {"target_id", "environment", "cloud", "region", "profile_id", "database"}
OPTIONAL_KEYS = {"environment_id", "terraform_mapping"}
CLOUDS = ("aws", "gcp")  # 온프렘(K3s) 경로는 배포기에 이식돼 있으나 컨트롤러에 kubectl·kubeconfig가 없어 받지 않는다.
# Delta Notes(게시판) 템플릿: FE 3000, BE 8080, 둘 다 /healthz. 스테이징(ECS·Cloud Run)과 같다.
COMPONENTS = (("frontend", 3000), ("backend", 8080))
HEALTH_PATH = "/healthz"
NAMESPACE = uuid5(NAMESPACE_URL, "https://delta-cp/release")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
CODE_RE = re.compile(r"[a-z0-9_]{1,64}")


def release_dir():
    configured = os.environ.get("DELTA_RELEASE_DIR")
    if configured:
        return Path(configured)
    installed = Path("/opt/delta/release")
    if (installed / "deploy.py").exists():
        return installed
    return Path(__file__).resolve().parents[2] / "release"


def catalog():
    """배포기와 같은 targets.py를 읽는다(패키지 설치·클라우드 접속 없음)."""
    path = release_dir() / "targets.py"
    spec = importlib.util.spec_from_file_location("delta_release_targets", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_target(target):
    if not isinstance(target, dict) or TARGET_KEYS - set(target) or set(target) - TARGET_KEYS - OPTIONAL_KEYS:
        raise ValueError("운영 대상 인벤토리 형식이 올바르지 않다")
    if target["environment"] != "production" or target["cloud"] not in CLOUDS:
        raise ValueError("AWS 또는 GCP production 대상 하나가 필요하다")
    if not isinstance(target["target_id"], str) or not ID_RE.fullmatch(target["target_id"]):
        raise ValueError("대상 식별자가 올바르지 않다")
    catalog().DeploymentTarget.model_validate(
        {"cloud": target["cloud"], "region": target["region"], "profile_id": target["profile_id"]}
    )
    database = target["database"]
    if (not isinstance(database, dict) or set(database) != {"profile_id", "mode"}
            or not isinstance(database["profile_id"], str) or not ID_RE.fullmatch(database["profile_id"])
            or database["mode"] not in ("new", "existing")):
        raise ValueError("운영 DB 선택이 올바르지 않다")
    if "terraform_mapping" in target:
        selectors = target["terraform_mapping"]
        if (target["cloud"] != "gcp" or database["mode"] != "existing"
                or not isinstance(selectors, dict) or set(selectors) != {"source_compute", "source_subnet"}
                or any(not isinstance(value, str) or not 1 <= len(value) <= 256 for value in selectors.values())):
            raise ValueError("차분 배포는 기존 DB와 GCP VM 매핑 선택이 필요하다")
    if "environment_id" in target:
        value = target["environment_id"]
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError("environment_id는 소문자 UUID여야 한다")


def identities(request):
    """같은 대상은 같은 환경(VM·DB)을 재사용한다. 새 VM이 필요하면 인벤토리 environment_id를 바꿔 새로 승인받는다."""
    target, deployment_id = request["target"], request["deployment_id"]
    return {
        "run_id": str(uuid5(NAMESPACE, "run:" + deployment_id)),
        "plan_id": str(uuid5(NAMESPACE, "plan:" + deployment_id)),
        "environment_id": target.get("environment_id") or str(uuid5(NAMESPACE, "environment:" + target["target_id"])),
        "service_id": str(uuid5(NAMESPACE, "service:" + target["target_id"])),
        "project_id": str(uuid5(NAMESPACE, "project:" + target["target_id"])),
    }


def deployment_request(request, config):
    """우리 요청 → 동규님 DELTA_DEPLOYMENT 형식(schema_version 1)."""
    target, bundle, commit = request["target"], request["bundle"], request["commit_sha"]
    cloud = target["cloud"]
    images = (bundle.get("registry_images") or {}).get(cloud) or bundle["images"]
    if images != bundle["images"]:
        raise ValueError("대상 클라우드 이미지가 승인 묶음과 다르다")
    profile = config["databases"][target["database"]["profile_id"]]
    mapping = request.get("terraform_mapping")
    profile_id = mapping["profile_id"] if mapping else target["profile_id"]
    return {
        "schema_version": 1,
        **identities(request),
        "deployment_id": request["deployment_id"],
        **({"terraform_mapping": mapping} if mapping else {}),
        "plan_hash": canonical_hash({"deployment_id": request["deployment_id"],
                                     "bundle_sha256": bundle["bundle_sha256"],
                                     "config_sha256": request["config_sha256"],
                                     **({"mapping_sha256": canonical_hash(mapping)} if mapping else {})}),
        "commit_sha": commit,
        "target": {"cloud": cloud, "region": target["region"], "profile_id": profile_id},
        "connection": config["connections"][cloud]["identity"],
        "components": [
            {"name": name, "image": images[name], "image_digest": bundle["image_digests"][name],
             "commit_sha": commit, "container_port": port, "healthcheck_path": HEALTH_PATH}
            for name, port in COMPONENTS
        ],
        "database": {"profile_id": target["database"]["profile_id"], "revision": profile["revision"],
                     "engine": "postgres", "mode": target["database"]["mode"]},
    }


def child_env(config, config_path, data, encoded, directory):
    # 승인 토큰·Jenkins 파라미터는 배포기에 넘기지 않는다.
    env = {k: v for k, v in os.environ.items()
           if k not in ("APPROVAL", "IMAGES_JSON", "BUNDLE_JSON", "BUNDLE_SHA256", "TERRAFORM_MAPPING",
                        "DELTA_MAPPED_PHASE", "DELTA_MAPPED_DIR", "DELTA_APPROVED_PLAN_SHA256")}
    env.update(DELTA_RUN_ID=data["run_id"], DELTA_DEPLOYMENT=encoded, DELTA_DEPLOY_CONFIG=str(config_path),
               PYTHONDONTWRITEBYTECODE="1", AWS_PAGER="")
    if data["target"]["cloud"] == "gcp":
        # remote-jenkins.sh가 GCP prod-deploy WIF 설정(키 파일 아님)을 이 경로로 마운트한다.
        credentials = (config["connections"]["gcp"].get("credentials_file")
                       or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
                       or "/run/delta/gcp-release-credentials.json")
        env.update(GOOGLE_APPLICATION_CREDENTIALS=credentials,
                   CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE=credentials,
                   CLOUDSDK_CONFIG=str(Path(directory) / "gcloud"),
                   CLOUDSDK_CORE_DISABLE_PROMPTS="1")
    return env


def python():
    configured = os.environ.get("DELTA_RELEASE_PYTHON")
    if configured:
        return configured
    venv = Path("/opt/delta/venv/bin/python")
    return str(venv) if venv.exists() else sys.executable


def failure(base, state, stage, code):
    stage = stage if isinstance(stage, str) and CODE_RE.fullmatch(stage) else "deploy"
    code = code if isinstance(code, str) and CODE_RE.fullmatch(code) else "deploy_failed"
    return {**base, "state": state, "stage": stage, "code": code}


def to_contract(request, data, deployed):
    """배포기 관측값만으로 결과 계약을 만든다. 요청값으로 증거를 채우지 않는다."""
    bundle = request["bundle"]
    base = {"deployment_id": request["deployment_id"], "target_id": request["target"]["target_id"],
            "environment": "production", "config_sha256": request["config_sha256"]}
    if not isinstance(deployed, dict) or deployed.get("state") != "verified":
        deployed = deployed if isinstance(deployed, dict) else {}
        return failure(base, "failed", deployed.get("stage"), deployed.get("code"))
    try:
        observed = deployed["observed"]["images"]
        version = deployed["database"]["server_version_num"]
        expected = {c["name"]: c["image"] for c in data["components"]}
        if (deployed.get("run_id") != data["run_id"] or deployed.get("environment_id") != data["environment_id"]
                or deployed.get("commit_sha") != request["commit_sha"]
                or observed != expected or observed != bundle["images"]
                or type(version) is not int or version // 10000 != 18):
            raise ValueError("관측 결과가 요청과 다르다")
        # 관측 이미지의 digest로 묶음 해시를 다시 계산해 승인 묶음과 대조한다.
        contract = bundle_from_images(request["commit_sha"], observed, bundle["bundle_sha256"])
        instance = deployed["environment"]["instances"][0]
    except (KeyError, IndexError, TypeError, ValueError):
        return failure(base, "failed", "verify", "observation_mismatch")
    if "terraform_mapping" in request:
        proof = deployed.get("terraform")
        if (not isinstance(proof, dict) or set(proof) != {"mapping_sha256", "plan_sha256"}
                or proof["mapping_sha256"] != canonical_hash(request["terraform_mapping"])
                or proof["plan_sha256"] != os.environ.get("DELTA_APPROVED_PLAN_SHA256")):
            return failure(base, "failed", "verify", "terraform_plan_mismatch")
        contract["terraform"] = proof
    return {**contract, **base, "state": "verified", "database_version_num": version,
            "environment_id": data["environment_id"],
            "instance": {k: str(instance.get(k, "")) for k in ("id", "name", "zone")},
            "service_url": str(deployed.get("service_url", ""))}


def execute(request, *, phase="deploy", runner=subprocess.run):
    """execute(request) -> result. 배포기는 별도 프로세스로 돌리고 stdout/stderr는 버린다."""
    target = request["target"]
    validate_target(target)
    base = {"deployment_id": request["deployment_id"], "target_id": target["target_id"],
            "environment": "production", "config_sha256": request["config_sha256"]}
    config_path = Path(os.environ.get("DELTA_RELEASE_CONFIG") or os.environ.get("DELTA_DEPLOY_CONFIG")
                       or "/run/delta/release-config.json")
    try:
        config = json.loads(config_path.read_text())
        data = deployment_request(request, config)
    except (OSError, KeyError, TypeError, ValueError):
        return failure(base, "failed", "request", "config_or_request_invalid")
    encoded = base64.b64encode(json.dumps(data).encode()).decode()
    with tempfile.TemporaryDirectory(prefix="delta-release-") as directory:
        os.chmod(directory, 0o700)
        env = child_env(config, config_path, data, encoded, directory)
        if "terraform_mapping" in data:
            env.update(DELTA_MAPPED_PHASE=phase, DELTA_MAPPED_DIR=str(Path.cwd() / ".mapped-terraform"))
            if phase == "apply":
                env["DELTA_APPROVED_PLAN_SHA256"] = os.environ["DELTA_APPROVED_PLAN_SHA256"]
        try:
            runner([python(), str(release_dir() / "deploy.py")], cwd=directory, check=False,
                   capture_output=True, text=True, timeout=3300,
                   env=env)
        except subprocess.TimeoutExpired:
            return failure(base, "unknown", "deploy", "deployer_timeout")
        try:
            deployed = json.loads((Path(directory) / "result.json").read_text())
        except (OSError, ValueError):
            return failure(base, "unknown", "deploy", "deployer_no_result")
    if phase == "plan" and deployed.get("state") == "planned":
        return deployed
    return to_contract(request, data, deployed)
