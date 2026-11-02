"""GitHub의 비밀값 없는 환경 설정으로 기존 Terraform state를 연결한다."""

import json
import os
import re
from pathlib import Path

config = json.loads(os.environ["PLATFORM_TF_INPUTS"])
account = config["account_id"]
region = config["region"]
bucket = os.environ["PLATFORM_STATE_BUCKET"]
if not re.fullmatch(r"[0-9]{12}", account) or account != os.environ["AWS_ACCOUNT_ID"]:
    raise SystemExit("잘못된 AWS 계정")
if region != os.environ["AWS_REGION"] or not re.fullmatch(r"[a-z0-9-]+", region):
    raise SystemExit("AWS 리전 불일치")
if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
    raise SystemExit("잘못된 state 버킷")
if config.get("bootstrap_origin_access", False):
    raise SystemExit("CI에서는 최초 Origin 임시 ingress를 허용하지 않습니다.")
root = Path(__file__).resolve().parents[1] / "terraform"
(root / "terraform.tfvars.json").write_text(json.dumps(config, indent=2) + "\n")
(root / "backend.hcl").write_text(
    f'bucket = "{bucket}"\nkey = "platform/terraform.tfstate"\n'
    f'region = "{region}"\nencrypt = true\nuse_lockfile = true\n'
    f'allowed_account_ids = ["{account}"]\n'
)
