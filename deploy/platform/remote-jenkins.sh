# Jenkins 컨트롤러 :8080 + 스테이징 도구 :8091. 입력: IMAGE(컨트롤러), STAGING_IMAGE
: "${STAGING_IMAGE:?}"
pull_image
IMAGE="$STAGING_IMAGE" pull_image
install -d -o 1000 -g 1000 -m 0750 "$base/state/jenkins"
install -d -o 10001 -g 10001 -m 0750 "$base/state/staging"
{
  printf 'JENKINS_ADMIN_PASSWORD=%s\n' "$(secret jenkins/admin_password)"
  printf 'JENKINS_PLATFORM_PASSWORD=%s\n' "$(secret jenkins/platform_password)"
  # 배포 전용 실행기(내장 노드)의 신뢰된 배포 스크립트만 쓴다. 빌드 에이전트로는 넘어가지 않는다.
  printf 'DELTA_STAGING_WRITE_TOKEN=%s\n' "$(secret jenkins/staging_write_token)"
} > "$base/jenkins.env"
{
  printf 'DELTA_STAGING_READ_TOKEN=%s\n' "$(secret jenkins/staging_read_token)"
  printf 'DELTA_STAGING_WRITE_TOKEN=%s\n' "$(secret jenkins/staging_write_token)"
} > "$base/staging.env"
param jenkins/staging_config > "$base/state/staging/staging.json"
# GCP: 인스턴스 역할(IMDSv2)로 서명해 delta-staging-deploy@ 단기 토큰으로 바꾸는 WIF 설정(키 파일 아님)
gcp_env=()
gcp_creds="$(param jenkins/gcp_staging_credentials)"
if [ -n "$gcp_creds" ]; then
  printf '%s' "$gcp_creds" > "$base/state/staging/gcp-credentials.json"
  gcp_env=(-e GOOGLE_APPLICATION_CREDENTIALS=/state/gcp-credentials.json)
fi
chown 10001:10001 "$base/state/staging/"*.json

docker rm -f delta-jenkins delta-staging >/dev/null 2>&1 || true
platform_logs jenkins jenkins
docker run -d --name delta-jenkins --restart unless-stopped --network host "${log_opts[@]}" \
  --env-file "$base/jenkins.env" -v "$base/state/jenkins:/var/jenkins_home" "$IMAGE" >/dev/null
platform_logs jenkins staging
docker run -d --name delta-staging --restart unless-stopped --network host "${log_opts[@]}" \
  --env-file "$base/staging.env" -e DELTA_STAGING_CONFIG=/state/staging.json -e DELTA_STAGING_STATE=/state/deployments.json "${gcp_env[@]}" \
  -v "$base/state/staging:/state" "$STAGING_IMAGE" >/dev/null
llm_auth=n/a
wait_healthy http://127.0.0.1:8080/login delta-jenkins
wait_healthy http://127.0.0.1:8091/healthz delta-staging

# 권한 분리 자체 점검(토큰 값은 출력하지 않는다): 조회 토큰은 조회만, 변경은 403, 토큰 없음·예전 공유 토큰은 401.
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
S=http://127.0.0.1:8091
read_tok="$(secret jenkins/staging_read_token)"
write_tok="$(secret jenkins/staging_write_token)"
got="read_list=$(code -H "Authorization: Bearer $read_tok" $S/v1/staging/deployments)"
got="$got read_stop=$(code -X POST -H "Authorization: Bearer $read_tok" $S/v1/staging/deployments/authz-selfcheck/stop)"
got="$got write_list=$(code -H "Authorization: Bearer $write_tok" $S/v1/staging/deployments)"
got="$got none=$(code $S/v1/staging/deployments)"
old_tok="$(secret shared/staging_token 2>/dev/null || true)"
[ -n "$old_tok" ] && got="$got old_shared=$(code -H "Authorization: Bearer $old_tok" $S/v1/staging/deployments)"
unset read_tok write_tok old_tok
echo "staging_authz: $got"
want="read_list=200 read_stop=403 write_list=200 none=401"
case "$got" in
  "$want"|"$want old_shared=401") ;;
  *) echo "스테이징 API 권한 분리가 기대와 다르다(기대: $want [old_shared=401])" >&2; exit 1 ;;
esac
# 빌드 에이전트 접속 비밀값을 에이전트 경로에 넣는다(값은 출력하지 않음).
admin_pw="$(secret jenkins/admin_password)"
for _ in $(seq 1 30); do
  agent_secret=$(curl -fsS -u "admin:$admin_pw" http://127.0.0.1:8080/computer/builder/jenkins-agent.jnlp 2>/dev/null \
    | sed -n 's:.*<argument>\([0-9a-f]\{64\}\)</argument>.*:\1:p' | head -1) || true
  [ -n "${agent_secret:-}" ] && break
  sleep 5
done
unset admin_pw
[ -n "${agent_secret:-}" ] || { echo "에이전트 비밀값을 받지 못했다" >&2; exit 1; }
aws ssm put-parameter --region "$REGION" --name "${PREFIX}jenkins-agent/agent_secret" --type SecureString \
  --value "$agent_secret" --overwrite >/dev/null
unset agent_secret
echo "agent_secret: stored"
