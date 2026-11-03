#!/usr/bin/env bash
# 콘솔과 DAST 를 끄고 Jenkins 스택을 내린다. 빌드 기록은 남는다.
set -euo pipefail
cd "$(dirname "$0")"
for name in console dast; do
  if [[ -f "var/$name.pid" ]]; then
    kill "$(cat "var/$name.pid")" 2>/dev/null || true
    rm -f "var/$name.pid"
  fi
done
# uv 가 띄운 하위 프로세스가 남지 않게 포트로도 확인한다
for port in 8765 8766 18100; do
  pids="$(lsof -tnP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then kill $pids 2>/dev/null || true; fi
done
docker rm -f demo-blue demo-green >/dev/null 2>&1 || true
../jenkins-local/down.sh
