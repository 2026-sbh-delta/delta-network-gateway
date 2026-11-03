#!/bin/bash
# 서버(보안 도구 호스트)에서 SSM으로 실행된다. 이미지를 받아 SAST·DAST 컨테이너를 다시 띄운다.
# 입력(환경 변수): SAST_IMAGE, DAST_IMAGE, REGION, PARAMETER_PATH
set -euo pipefail
: "${SAST_IMAGE:?}" "${DAST_IMAGE:?}" "${REGION:?}"
PARAMETER_PATH="${PARAMETER_PATH:-/delta/tooling/}"
base=/opt/delta
umask 077

registry="${SAST_IMAGE%%/*}"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$registry" >/dev/null
docker pull -q "$SAST_IMAGE"
docker pull -q "$DAST_IMAGE"

# SAST·DAST가 같이 쓰는 내부 토큰. 처음 한 번만 만든다(서버 밖으로 나가지 않음).
if [ ! -s "$base/secrets.env" ]; then
  printf 'DELTA_SAST_TOKEN=%s\n' "$(openssl rand -hex 24)" > "$base/secrets.env"
fi

# LLM 인증: Parameter Store의 SecureString이 있으면 API 키로 쓴다. 없으면 LLM 없이 규칙·캐시까지만.
: > "$base/llm.env"
backend=off
if key=$(aws ssm get-parameter --region "$REGION" --name "${PARAMETER_PATH}anthropic_api_key" \
          --with-decryption --query Parameter.Value --output text 2>/dev/null) && [ -n "$key" ]; then
  printf 'ANTHROPIC_API_KEY=%s\n' "$key" > "$base/llm.env"
  backend=api
fi
unset key

# SAST가 점검할 예제 소스(DAST 실습 게시판과 같은 코드)를 DAST 이미지에서 꺼내 둔다
tmp=$(docker create "$DAST_IMAGE")
rm -rf "$base/sources/sample-target"
docker cp "$tmp:/app/sample-target" "$base/sources/sample-target"
docker rm "$tmp" >/dev/null
chmod -R a+rX "$base/sources"

docker rm -f delta-sast delta-dast >/dev/null 2>&1 || true
docker run -d --name delta-sast --restart unless-stopped --network host \
  --env-file "$base/secrets.env" --env-file "$base/llm.env" \
  -e SAST_LLM_BACKEND="$backend" -e SAST_LOCAL_ROOTS=/srv/sources \
  -v "$base/sast-state:/var/lib/delta-sast" -v "$base/sources:/srv/sources:ro" \
  "$SAST_IMAGE" >/dev/null
docker run -d --name delta-dast --restart unless-stopped --network host --shm-size=1g \
  --env-file "$base/secrets.env" --env-file "$base/llm.env" \
  -e DELTA_SAST_URL=http://127.0.0.1:8790 -e DELTA_SAST_REPO=/srv/sources/sample-target \
  -v "$base/dast-runs:/app/runs" -v "$base/dast-data:/app/data" \
  "$DAST_IMAGE" >/dev/null

for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8790/healthz >/dev/null 2>&1 && curl -fsS http://127.0.0.1:8765/api/sast/status >/dev/null 2>&1; then
    echo "ok: sast=$(curl -fsS http://127.0.0.1:8790/healthz) dast_sast_link=$(curl -fsS http://127.0.0.1:8765/api/sast/status) llm_backend=$backend"
    docker image prune -f >/dev/null
    docker ps --format '{{.Names}} {{.Image}} {{.Status}}'
    exit 0
  fi
  sleep 2
done
echo "서비스가 120초 안에 준비되지 않았다" >&2
docker logs --tail 40 delta-sast >&2 || true
docker logs --tail 40 delta-dast >&2 || true
exit 1
