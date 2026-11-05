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
# 받은 뒤 바로 로그아웃해서 ECR 임시 토큰을 서버 디스크에 남기지 않는다
docker logout "$registry" >/dev/null

# SAST·DAST가 같이 쓰는 내부 토큰. 처음 한 번만 만든다(서버 밖으로 나가지 않음).
if [ ! -s "$base/secrets.env" ]; then
  printf 'DELTA_SAST_TOKEN=%s\n' "$(openssl rand -hex 24)" > "$base/secrets.env"
fi

# LLM 인증. ① OIDC: Parameter Store에 페더레이션 ID가 있으면 서버 역할의 AWS 신원 토큰을 Anthropic 단기 토큰으로
# 바꿔 쓴다(키 없음). ② 없으면 SecureString API 키. ③ 둘 다 없으면 LLM 없이 규칙·캐시까지만.
param() { aws ssm get-parameter --region "$REGION" --name "${PARAMETER_PATH}$1" --query Parameter.Value --output text 2>/dev/null || true; }
: > "$base/llm.env"
backend=off
llm_mounts=()
rule=$(param anthropic_federation_rule_id)
org=$(param anthropic_organization_id)
account=$(param anthropic_service_account_id)
workspace=$(param anthropic_workspace_id)
if [ -n "$rule" ] && [ -n "$org" ] && [ -n "$account" ]; then
  # AWS 신원 토큰(15분)을 5분마다 새로 받아 둔다. SDK와 Claude Code는 교환할 때마다 이 파일을 다시 읽는다.
  wif="$base/wif"
  mkdir -p "$wif/config/configs"
  chmod 755 "$wif" "$wif/config" "$wif/config/configs"
  cat > "$base/wif-refresh.sh" <<EOF
#!/bin/bash
set -euo pipefail
tmp=\$(mktemp "$wif/.token.XXXXXX")
aws sts get-web-identity-token --region "$REGION" --audience https://api.anthropic.com \\
  --signing-algorithm RS256 --duration-seconds 900 --query WebIdentityToken --output text > "\$tmp"
chown 10001:10001 "\$tmp" && chmod 400 "\$tmp" && mv -f "\$tmp" "$wif/token"
EOF
  chmod 700 "$base/wif-refresh.sh"
  cat > /etc/systemd/system/delta-wif-refresh.service <<EOF
[Unit]
Description=Refresh the AWS identity token exchanged for Anthropic credentials
[Service]
Type=oneshot
ExecStart=$base/wif-refresh.sh
EOF
  cat > /etc/systemd/system/delta-wif-refresh.timer <<'EOF'
[Unit]
Description=Refresh the AWS identity token every 5 minutes
[Timer]
OnBootSec=10s
OnUnitActiveSec=5min
AccuracySec=10s
[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now delta-wif-refresh.timer >/dev/null
  systemctl start delta-wif-refresh.service
  # Python SDK는 환경 변수, Claude Code(DAST 에이전트)는 같은 값의 페더레이션 프로필을 읽는다
  {
    printf 'ANTHROPIC_FEDERATION_RULE_ID=%s\nANTHROPIC_ORGANIZATION_ID=%s\nANTHROPIC_SERVICE_ACCOUNT_ID=%s\n' \
      "$rule" "$org" "$account"
    [ -n "$workspace" ] && printf 'ANTHROPIC_WORKSPACE_ID=%s\n' "$workspace"
    printf 'ANTHROPIC_IDENTITY_TOKEN_FILE=/run/anthropic/token\nANTHROPIC_CONFIG_DIR=/run/anthropic/config\n'
  } > "$base/llm.env"
  python3 - "$rule" "$org" "$account" "$workspace" > "$wif/config/configs/default.json" <<'PY'
import json, sys
rule, org, account, workspace = sys.argv[1:]
profile = {"version": "1.0", "organization_id": org, "base_url": "https://api.anthropic.com",
           "authentication": {"type": "oidc_federation", "federation_rule_id": rule, "service_account_id": account,
                              "identity_token": {"source": "file", "path": "/run/anthropic/token"}}}
if workspace:
    profile["workspace_id"] = workspace
print(json.dumps(profile, indent=2))
PY
  echo default > "$wif/config/active_config"
  chmod 644 "$wif/config/configs/default.json" "$wif/config/active_config"
  llm_mounts=(-v "$wif:/run/anthropic:ro")
  backend=api
elif key=$(aws ssm get-parameter --region "$REGION" --name "${PARAMETER_PATH}anthropic_api_key" \
          --with-decryption --query Parameter.Value --output text 2>/dev/null) && [ -n "$key" ]; then
  printf 'ANTHROPIC_API_KEY=%s\n' "$key" > "$base/llm.env"
  backend=api
fi
unset key
# 모델: Parameter Store에 있으면 그 값, 없으면 각 서비스 기본값
sast_model=$(param sast_llm_model)
dast_model=$(param dast_llm_model)
[ -n "$sast_model" ] && printf 'SAST_LLM_MODEL=%s\n' "$sast_model" >> "$base/llm.env"
[ -n "$dast_model" ] && printf 'DAST_CLAUDE_MODEL=%s\n' "$dast_model" >> "$base/llm.env"

# SAST가 점검할 소스는 시연 게시판 저장소(delta-sample-app)를 커밋 단위로 받는다. 로컬 소스 폴더는 비워 둔다
mkdir -p "$base/sources"
rm -rf "$base/sources/sample-target"

# Preserve the repository policy configured by the platform connection.
sast_runtime=()
if [ -f "$base/sast-runtime.env" ]; then
  sast_runtime=(--env-file "$base/sast-runtime.env")
fi

docker rm -f delta-sast delta-dast >/dev/null 2>&1 || true
docker run -d --name delta-sast --restart unless-stopped --network host \
  --env-file "$base/secrets.env" --env-file "$base/llm.env" \
  "${sast_runtime[@]}" -e SAST_LLM_BACKEND="$backend" -e SAST_LOCAL_ROOTS=/srv/sources \
  -v "$base/sast-state:/var/lib/delta-sast" -v "$base/sources:/srv/sources:ro" "${llm_mounts[@]}" \
  "$SAST_IMAGE" >/dev/null
docker run -d --name delta-dast --restart unless-stopped --network host --shm-size=1g \
  --env-file "$base/secrets.env" --env-file "$base/llm.env" \
  -e DELTA_RUNTIME=server -e DELTA_SAST_URL=http://127.0.0.1:8790 \
  -v "$base/dast-runs:/app/runs" -v "$base/dast-data:/app/data" "${llm_mounts[@]}" \
  "$DAST_IMAGE" >/dev/null

for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8790/healthz >/dev/null 2>&1 && curl -fsS http://127.0.0.1:8765/api/sast/status >/dev/null 2>&1; then
    echo "ok: sast=$(curl -fsS http://127.0.0.1:8790/healthz) dast_sast_link=$(curl -fsS http://127.0.0.1:8765/api/sast/status) llm_backend=$backend sast_model=${sast_model:-default} dast_model=${dast_model:-default} llm_auth=$([ -n "${rule:-}" ] && echo oidc || echo ${backend})"
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
