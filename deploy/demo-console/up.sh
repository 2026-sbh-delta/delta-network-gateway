#!/usr/bin/env bash
# 시연 스택 전체를 띄운다. 키는 파일에 쓰지 않고 이 프로세스에서 compose 로만 넘긴다.
# DEMO_LLM=fake 로 실행하면 Claude 를 부르지 않는다.
set -euo pipefail
cd "$(dirname "$0")"
HERE="$PWD"
VAR="$HERE/var"
KEY_FILE="${ANTHROPIC_KEY_FILE:-$HOME/.config/anthropic/delta-ai.key}"
mkdir -p "$VAR/onprem"

if [[ "${DEMO_LLM:-claude}" == "claude" ]]; then
  [[ -r "$KEY_FILE" ]] || { echo "키 파일을 읽지 못했다: $KEY_FILE (DEMO_LLM=fake 로 실행 가능)" >&2; exit 1; }
  ANTHROPIC_API_KEY="$(cat "$KEY_FILE")" DELTA_LLM=claude ../jenkins-local/up.sh
else
  ../jenkins-local/up.sh
fi

(cd ../../services/delta-ai && uv sync --quiet --all-extras)
(cd ../../services/delta-dast && uv sync --quiet)
(cd ../../services/delta-pipeline && uv sync --quiet --all-extras)

start() {  # 이름, 포트, 실행 폴더, 명령...
  local name="$1" port="$2" dir="$3"
  shift 3
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "$name: 이미 실행 중 (포트 $port)"
    return
  fi
  # cd 를 && 로 묶으면 서브셸째 백그라운드가 되어 pid 가 틀리고 호출자의 출력을 붙잡는다
  (cd "$dir"; nohup "$@" >"$VAR/$name.log" 2>&1 </dev/null & echo $! >"$VAR/$name.pid")
}

start dast 8765 ../../services/delta-dast uv run python -m dast_agent serve --port 8765 --target-port 8766
start console 18100 "$HERE" ../../services/delta-pipeline/.venv/bin/python server.py --port 18100
sleep 2
echo "시연 콘솔: http://127.0.0.1:18100"
