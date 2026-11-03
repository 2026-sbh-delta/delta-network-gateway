"""같은 저장소·워크플로·main 커밋의 성공한 Plan만 적용 대상으로 허용한다."""

import json
import os
import re
import subprocess

run_id = os.environ["PLAN_RUN_ID"]
if not re.fullmatch(r"[1-9][0-9]*", run_id):
    raise SystemExit("검토한 Plan run ID가 필요합니다.")
run = json.loads(
    subprocess.check_output(
        ["gh", "api", f"repos/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{run_id}"],
        text=True,
    )
)
if not (
    run["conclusion"] == "success"
    and run["event"] == "workflow_dispatch"
    and run["path"] == ".github/workflows/platform-infra.yml"
    and run["head_branch"] == "main"
    and run["head_sha"] == os.environ["GITHUB_SHA"]
):
    raise SystemExit("현재 main 커밋과 일치하는 성공한 Plan 실행만 적용할 수 있습니다.")
