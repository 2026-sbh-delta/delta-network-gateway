#!/usr/bin/env bash
# run_logged.sh <단계> <명령...> — 출력을 logs/<단계>.log 에 남기고 명령의 종료 코드를 돌려준다.
set -uo pipefail
stage="$1"; shift
mkdir -p logs
echo "$stage" > logs/.last-stage
"$@" 2>&1 | tee "logs/${stage}.log"
code="${PIPESTATUS[0]}"
echo "$code" > "logs/${stage}.exit"
exit "$code"
