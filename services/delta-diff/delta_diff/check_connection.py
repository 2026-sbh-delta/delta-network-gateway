"""호출자 호스트에서 실행: 실제 S3 조회·판정까지 확인하고 비밀값 없는 요약만 출력한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from delta_diff.client import DiffUnavailable, from_ssm
from delta_diff.contract import DiffRequest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--connection-parameter", default="/delta/diff/connection")
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--allow-private-http", action="store_true")
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args()
    import boto3
    try:
        request = DiffRequest.model_validate_json(args.request.read_text())
        client = from_ssm(boto3.client("ssm", region_name=args.region), args.connection_parameter,
                          allow_private_http=args.allow_private_http)
        client.check_health()
        result = client.run(request)
    except (DiffUnavailable, ValueError, OSError) as exc:
        # 입력 파일의 ValidationError는 원본 값을 포함할 수 있어 출력하지 않는다.
        message = str(exc) if isinstance(exc, DiffUnavailable) else "차분 요청 파일을 확인한다"
        print(json.dumps({"connected": False, "error": message}, ensure_ascii=False))
        return 1
    verdict = result.assessment.verdict
    print(json.dumps({"connected": True, "diff_id": result.diff_id, "state_serial": getattr(result.source, "serial", None),
                      "source_kind": getattr(result.source, "kind", "terraform_state"),
                      "resource_count": result.source.resource_count, "verdict": verdict,
                      "can_proceed": verdict == "pass", "llm_used": result.assessment.meta.llm_used},
                     ensure_ascii=False))
    return 2 if args.require_pass and verdict != "pass" else 0


if __name__ == "__main__":
    raise SystemExit(main())
