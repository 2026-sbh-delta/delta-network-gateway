#!/bin/bash
# 인스턴스에서 SSM으로 실행된다(서비스 스크립트 앞에 붙는다). 입력: IMAGE(저장소@digest), REGION, PREFIX, LLM_ROLE_ARN
set -euo pipefail
: "${IMAGE:?}" "${REGION:?}" "${PREFIX:?}" "${LLM_ROLE_ARN:?}"
base=/opt/delta
umask 077

param() { aws ssm get-parameter --region "$REGION" --name "${PREFIX}$1" --query Parameter.Value --output text 2>/dev/null || true; }
secret() { aws ssm get-parameter --region "$REGION" --name "${PREFIX}$1" --with-decryption --query Parameter.Value --output text; }

# 플랫폼 서비스 로그는 플랫폼 중앙 CloudWatch(/delta-cp/platform/<서비스>)로 바로 보낸다. 로그 서버 인스턴스는 두지 않는다.
# 그룹은 Terraform(platform-aws)이 만들고, 역할은 자기 그룹에만 쓸 수 있다. 전송이 늦어도 서비스가 멈추지 않게 non-blocking.
# docker logs 는 Docker 이중 로깅(로컬 캐시)으로 그대로 볼 수 있다.
platform_logs() {
  log_opts=(--log-driver awslogs --log-opt awslogs-region="$REGION" --log-opt awslogs-group="${PREFIX}platform/$1"
            --log-opt awslogs-stream="$2" --log-opt mode=non-blocking --log-opt max-buffer-size=4m)
}

pull_image() {
  local registry="${IMAGE%%/*}"
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$registry" >/dev/null
  docker pull -q "$IMAGE" >/dev/null
  # ECR 임시 토큰을 디스크에 남기지 않는다
  docker logout "$registry" >/dev/null
}

# LLM 인증: ① OIDC(공통 경로에 페더레이션 ID가 있으면 인스턴스 역할의 AWS 신원 토큰을 단기 토큰으로 교환)
# ② 공통 경로의 SecureString API 키 ③ 둘 다 없으면 LLM 없이 동작. 결과: $base/llm.env, llm_mounts, backend
# 서비스 경로(<서비스>/anthropic_*)에 값이 있으면 그것을, 없으면 공통 경로(shared/anthropic_*)를 쓴다.
llm_param() { local v; v=$(param "$1/anthropic_$2"); [ -n "$v" ] || v=$(param "shared/anthropic_$2"); printf '%s' "$v"; }
setup_llm() {
  local svc="$1"
  : > "$base/llm.env"
  backend=off
  llm_mounts=()
  local rule org account workspace key
  rule=$(llm_param "$svc" federation_rule_id)
  org=$(llm_param "$svc" organization_id)
  account=$(llm_param "$svc" service_account_id)
  workspace=$(llm_param "$svc" workspace_id)
  if [ -n "$rule" ] && [ -n "$org" ] && [ -n "$account" ]; then
    local wif="$base/wif"
    mkdir -p "$wif/config/configs"
    chmod 755 "$wif" "$wif/config" "$wif/config/configs"
    # 인스턴스 역할로 공용 LLM 역할을 위임받고, 그 역할 이름으로 AWS 신원 토큰을 받는다(토큰의 sub = LLM 역할 ARN).
    # 토큰(15분)은 위임 세션보다 먼저 만료돼야 하므로 세션은 30분으로 받는다.
    cat > "$base/wif-refresh.sh" <<SH
#!/bin/bash
set -euo pipefail
eval "\$(aws sts assume-role --region "$REGION" --role-arn "$LLM_ROLE_ARN" --role-session-name "$svc" \\
  --duration-seconds 1800 --query Credentials --output json | python3 -c 'import json,sys; c=json.load(sys.stdin); print("export AWS_ACCESS_KEY_ID=%s AWS_SECRET_ACCESS_KEY=%s AWS_SESSION_TOKEN=%s" % (c["AccessKeyId"], c["SecretAccessKey"], c["SessionToken"]))')"
tmp=\$(mktemp "$wif/.token.XXXXXX")
aws sts get-web-identity-token --region "$REGION" --audience https://api.anthropic.com \\
  --signing-algorithm RS256 --duration-seconds 900 --query WebIdentityToken --output text > "\$tmp"
chown 10001:10001 "\$tmp" && chmod 400 "\$tmp" && mv -f "\$tmp" "$wif/token"
SH
    chmod 700 "$base/wif-refresh.sh"
    cat > /etc/systemd/system/delta-wif-refresh.service <<SH
[Unit]
Description=Refresh the AWS identity token exchanged for Anthropic credentials
[Service]
Type=oneshot
ExecStart=$base/wif-refresh.sh
SH
    cat > /etc/systemd/system/delta-wif-refresh.timer <<'SH'
[Unit]
Description=Refresh the AWS identity token every 5 minutes
[Timer]
OnBootSec=10s
OnUnitActiveSec=5min
AccuracySec=10s
[Install]
WantedBy=timers.target
SH
    systemctl daemon-reload
    systemctl enable --now delta-wif-refresh.timer >/dev/null
    systemctl start delta-wif-refresh.service
    {
      printf 'ANTHROPIC_FEDERATION_RULE_ID=%s\nANTHROPIC_ORGANIZATION_ID=%s\nANTHROPIC_SERVICE_ACCOUNT_ID=%s\n' "$rule" "$org" "$account"
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
    llm_auth=oidc
  elif key=$(secret shared/anthropic_api_key 2>/dev/null) && [ -n "$key" ]; then
    printf 'ANTHROPIC_API_KEY=%s\n' "$key" > "$base/llm.env"
    backend=api
    llm_auth=api_key
  else
    llm_auth=none
  fi
  unset key
}

wait_healthy() {
  local url="$1" name="$2"
  for _ in $(seq 1 60); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "ok: $name llm_auth=${llm_auth:-none}"
      docker image prune -f >/dev/null
      docker ps --format '{{.Names}} {{.Image}} {{.Status}}'
      return 0
    fi
    sleep 2
  done
  echo "$name 이(가) 120초 안에 준비되지 않았다" >&2
  docker logs --tail 40 "$name" >&2 || true
  return 1
}
