#!/usr/bin/env bash
# 플랫폼 서비스 사이 연결을 확인한다. 열어 둔 연결(Terraform links)은 모두 닿아야 하고, 그 밖은 모두 막혀야 한다.
# 기대 목록은 Terraform 출력(internal_names·service_ports·links)으로 계산한다. 판정은 ssmcheck.py가 한다:
# SSM 최종 상태 Success + 종료 코드 0, 항목별 대조(누락·중복·예상 밖·형식 오류 = 검사 오류), 전부 정상일 때만 종료 코드 0.
# 사용: AWS_PROFILE=sb deploy/platform/check-links.sh
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-sb}"
export AWS_PAGER=""
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
terraform -chdir="$here/../../terraform/platform-aws" output -json | python3 "$here/ssmcheck.py" links
