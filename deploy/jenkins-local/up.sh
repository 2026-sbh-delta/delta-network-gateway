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

docker compose up -d --build --wait "$@"
echo "Jenkins: http://127.0.0.1:18080 (admin, 비밀번호는 .env)"
