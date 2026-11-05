#!/usr/bin/env bash
# 로컬 스택을 띄운다. 값이 없으면 .env 를 만들고, 값은 출력하지 않는다.
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  umask 077
  {
    echo "JENKINS_ADMIN_PASSWORD=$(openssl rand -hex 16)"
    echo "DELTA_API_TOKEN=$(openssl rand -hex 24)"
    echo "DELTA_LLM=fake"
  } > .env
fi
# 이전 화면 토큰은 나중에 생겼다. 예전 .env 에도 넣는다
if ! grep -q '^TRAFFIC_CONTROL_TOKEN=' .env; then
  (umask 077; echo "TRAFFIC_CONTROL_TOKEN=$(openssl rand -hex 24)" >> .env)
fi

docker compose up -d --build --wait "$@"
echo "Jenkins: http://127.0.0.1:${JENKINS_PORT:-18080} (admin, 비밀번호는 .env)"
echo "이전 트래픽 조절: http://127.0.0.1:${TRAFFIC_CONTROL_PORT:-18082}"
