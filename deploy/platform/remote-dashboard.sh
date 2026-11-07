# 대시보드 시험 화면: 플랫폼 웹·API(:8088) + 같은 호스트의 PostgreSQL. 발표 모드(로그인 화면 없이 공통 계정 세션)로 쓰고,
# 접근은 CloudFront 함수가 IP 대역으로 막는다. 파이프라인 화면은 오케스트레이터 실행 기록을 서버가 대신 읽는다
# (관리자 비밀번호는 이 호스트의 env 파일에만 둔다).
: "${PUBLIC_ORIGIN:?}" "${ORCH_PUBLIC_HOST:?}"
PG_IMAGE="${PG_IMAGE:-public.ecr.aws/docker/library/postgres:18-alpine}"
pull_image
docker pull -q "$PG_IMAGE" >/dev/null
docker network inspect delta-dash >/dev/null 2>&1 || docker network create delta-dash >/dev/null

db_password="$(secret dashboard/db_password)"
if [ "$(docker inspect -f '{{.State.Status}}' delta-dash-db 2>/dev/null)" != running ]; then
  docker rm -f delta-dash-db >/dev/null 2>&1 || true
  docker run -d --name delta-dash-db --restart unless-stopped --network delta-dash \
    -e POSTGRES_USER=platform -e POSTGRES_DB=platform -e POSTGRES_PASSWORD="$db_password" \
    -v delta-dash-pg:/var/lib/postgresql "$PG_IMAGE" >/dev/null   # Docker 볼륨(호스트 경로는 SELinux 라벨 때문에 쓰기 거부)
fi
for _ in $(seq 1 30); do docker exec delta-dash-db pg_isready -U platform -d platform >/dev/null 2>&1 && break; sleep 2; done

# 발표 모드는 공통 계정이 있어야 한다. 비밀번호는 아무도 쓰지 않으므로 처음 한 번 임의로 만들고 해시만 남긴다.
# 해시를 유지해야 재배포해도 세션이 끊기지 않는다(세션은 계정·해시가 같을 때만 유효).
hash_file="$base/state/dashboard-login.hash"
if [ ! -s "$hash_file" ]; then
  docker run --rm --entrypoint python "$IMAGE" -c \
    'import secrets; from delta_platform.config import PASSWORD_HASHER as h; print(h.hash(secrets.token_urlsafe(32)))' > "$hash_file"
  chmod 600 "$hash_file"
fi
password_hash="$(cat "$hash_file")"
{
  printf 'PLATFORM_DATABASE_URL=postgresql+psycopg://platform:%s@delta-dash-db:5432/platform\n' "$db_password"
  printf 'PLATFORM_LOGIN_ID=delta\nPLATFORM_PASSWORD_HASH=%s\nPLATFORM_PRESENTATION_MODE=true\n' "$password_hash"
  printf 'PLATFORM_PUBLIC_ORIGIN=%s\n' "$PUBLIC_ORIGIN"
  printf 'PLATFORM_ORCHESTRATOR_URL=http://orchestrator.cp.internal:8080\nPLATFORM_ORCHESTRATOR_PUBLIC_HOST=%s\n' "$ORCH_PUBLIC_HOST"
  printf 'PLATFORM_ORCHESTRATOR_PASSWORD=%s\n' "$(secret orchestrator/admin_password)"
} > "$base/dashboard.env"

docker run --rm --network delta-dash --env-file "$base/dashboard.env" "$IMAGE" alembic upgrade head
docker rm -f delta-dashboard >/dev/null 2>&1 || true
platform_logs dashboard api
docker run -d --name delta-dashboard --restart unless-stopped --network delta-dash -p 8088:8088 "${log_opts[@]}" \
  --env-file "$base/dashboard.env" --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true \
  "$IMAGE" >/dev/null
llm_auth=n/a
wait_healthy http://127.0.0.1:8088/health/ready delta-dashboard
# 시연 샘플앱을 서비스로 등록해 둔다(같은 요청 키라 다시 배포해도 한 번만 생긴다). 발표 모드 세션의 쿠키·CSRF 로 보낸다.
PUBLIC_ORIGIN="$PUBLIC_ORIGIN" python3 - <<'PY' && echo "샘플앱 서비스 등록 확인" || echo "샘플앱 서비스 등록 실패(화면에서 등록 가능)" >&2
import json, os, urllib.request
base, origin = "http://127.0.0.1:8088/api/v1", os.environ["PUBLIC_ORIGIN"]
with urllib.request.urlopen(base + "/auth/session") as r:
    session = json.load(r)
    cookie = r.headers["Set-Cookie"].split(";", 1)[0]
body = {"name": "delta-sample-app", "repository_url": "https://github.com/2026-sbh-delta/delta-sample-app",
        "source_ref": "main", "build_context": "backend", "dockerfile_path": "backend/Dockerfile",
        "container_port": 8080, "healthcheck_path": "/healthz"}
req = urllib.request.Request(base + "/services", data=json.dumps(body).encode(), method="POST", headers={
    "Origin": origin, "X-Platform-Request": "1", "Content-Type": "application/json", "Cookie": cookie,
    "X-CSRF-Token": session["csrf_token"], "Idempotency-Key": "seed-delta-sample-app"})
urllib.request.urlopen(req).close()
PY
