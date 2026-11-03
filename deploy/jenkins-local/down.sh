#!/usr/bin/env bash
# 로컬 스택을 내린다. 파이프라인이 만든 demo 컨테이너는 compose 소유가 아니라 먼저 지운다.
# Jenkins 이력까지 지우려면 ./down.sh --volumes
set -euo pipefail
cd "$(dirname "$0")"
docker rm -f demo-blue demo-green >/dev/null 2>&1 || true
docker compose down "$@"
