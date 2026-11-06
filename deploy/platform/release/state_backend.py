"""환경별 Terraform 작업 디렉터리와 우리 S3 원격 state 연결.

로컬 디스크 state를 쓰지 않는다. 작업 디렉터리는 모듈 사본·plan·provider 캐시만 담는 버려도 되는 공간이다.
state 키: delta-cp/release/<environment_id>/<module>.tfstate (deploy·remove 공통).
"""

import re
import shutil
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parent
MODULES = {
    ("vm", "aws"): ROOT / "terraform" / "vm-aws",
    ("vm", "gcp"): ROOT / "terraform" / "vm-gcp",
    ("database", "aws"): ROOT / "terraform" / "db-aws",
    ("database", "gcp"): ROOT / "terraform" / "db-gcp",
}


def state_key(environment_id, module):
    if module not in ("vm", "database"):
        raise ValueError("Unknown Terraform module")
    return f"delta-cp/release/{UUID(environment_id)}/{module}.tfstate"


def init_command(config, environment_id, module):
    bucket, region = config.get("state_bucket"), config.get("state_region")
    if not isinstance(bucket, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise ValueError("state_bucket is not configured")
    if not isinstance(region, str) or not re.fullmatch(r"[a-z]{2}(-[a-z]+)+-[0-9]", region):
        raise ValueError("state_region is not configured")
    return [
        "terraform", "init", "-input=false", "-lockfile=readonly", "-reconfigure",
        "-backend-config=bucket=" + bucket,
        "-backend-config=key=" + state_key(environment_id, module),
        "-backend-config=region=" + region,
        "-backend-config=encrypt=true",
        "-backend-config=use_lockfile=true",
    ]


def workdir(config, environment_id, module, cloud):
    """모듈 파일을 매번 새로 복사한다. 이전 실행의 .tf가 섞이지 않게 한다."""
    source = MODULES[(module, cloud)]
    root = Path(config.get("work_root", "/var/jenkins_home/delta-release/work")).resolve()
    directory = root / str(UUID(environment_id)) / module
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.resolve().parent.parent != root or directory.is_symlink():
        raise ValueError("Invalid work directory")
    directory.chmod(0o700)
    for stale in directory.glob("*.tf"):
        stale.unlink()
    for file in [*source.glob("*.tf"), source / ".terraform.lock.hcl"]:
        shutil.copyfile(file, directory / file.name)
    return directory


def env(config, base=None):
    """provider 캐시를 작업 루트에 공유한다. 자격 증명 값은 여기서 다루지 않는다."""
    import os

    value = dict(os.environ if base is None else base)
    root = Path(config.get("work_root", "/var/jenkins_home/delta-release/work"))
    cache = root / "plugin-cache"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    value.update(TF_IN_AUTOMATION="1", TF_INPUT="0", TF_PLUGIN_CACHE_DIR=str(cache))
    return value
