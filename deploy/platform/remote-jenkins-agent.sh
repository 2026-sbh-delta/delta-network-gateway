# 빌드 에이전트: 받는 연결 없음. 컨트롤러에 WebSocket으로 붙는다. 저장소 코드는 여기서만 실행된다.
: "${JENKINS_URL:?}"
pull_image
install -d -m 0700 "$base/agent"
secret jenkins-agent/agent_secret > "$base/agent/agent_secret"
# GCP 이미지 저장소 푸시용 WIF 설정(비밀값 아님): 이 인스턴스 역할 → delta-build-push@
param jenkins-agent/gcp_build_credentials > "$base/agent/gcp-build.json"
[ -s "$base/agent/gcp-build.json" ] || { echo "GCP 빌드 푸시 설정이 없다(onboarding-gcp 적용 필요)" >&2; exit 1; }
chmod 644 "$base/agent/gcp-build.json"
docker rm -f delta-jenkins-agent >/dev/null 2>&1 || true
platform_logs jenkins-agent jenkins-agent
docker run -d --name delta-jenkins-agent --restart unless-stopped --network host --privileged "${log_opts[@]}" \
  -e JENKINS_URL="$JENKINS_URL" -v "$base/agent/agent_secret:/run/secrets/agent_secret:ro" \
  -v "$base/agent/gcp-build.json:/run/delta/gcp-build.json:ro" \
  -v "$base/state/agent:/home/jenkins/agent" "$IMAGE" >/dev/null
for _ in $(seq 1 40); do
  docker logs delta-jenkins-agent 2>&1 | grep -q "Connected" && { echo "ok: delta-jenkins-agent connected"; docker ps --format '{{.Names}} {{.Status}}'; exit 0; }
  sleep 3
done
echo "에이전트가 컨트롤러에 붙지 못했다" >&2
docker logs --tail 30 delta-jenkins-agent >&2 || true
exit 1
