# 오케스트레이터: 플랫폼 메인 페이지(임시) :8080. 공개 주소는 로그인이 필요하다. 다른 서비스는 내부망으로만 조회한다.
: "${PUBLIC_ORIGIN:?}" "${PUBLIC_URLS_B64:?}" "${NAT_IP:?}"
pull_image
{
  printf 'ORCH_ADMIN_PASSWORD=%s\n' "$(secret orchestrator/admin_password)"
  printf 'ORCH_SESSION_SECRET=%s\n' "$(secret orchestrator/session_secret)"
  printf 'JENKINS_PLATFORM_PASSWORD=%s\n' "$(secret jenkins/platform_password)"
  printf 'DELTA_STAGING_TOKEN=%s\n' "$(secret jenkins/staging_read_token)"
  printf 'DELTA_SAST_TOKEN=%s\n' "$(secret shared/sast_token)"
  printf 'DAST_PLATFORM_TOKEN=%s\n' "$(secret dast/platform_token)"
  # 서비스 도메인 전환(선택). 토큰은 해당 zone 의 DNS 편집 권한만. 없으면 전환을 켠 실행이 시작 단계에서 멈춘다.
  cf_token="$(secret orchestrator/cloudflare_token 2>/dev/null || true)"
  [ -n "$cf_token" ] && printf 'CLOUDFLARE_API_TOKEN=%s\n' "$cf_token"
  # 전환 설정은 JSON 일 때만 쓴다("disabled: …" 등은 꺼진 상태). 운영 진입점이 정해지기 전에는 끈다.
  cutover="$(param orchestrator/cutover_config)"
  case "$cutover" in "{"*) printf 'DELTA_CUTOVER=%s\n' "$cutover" ;; esac
  printf 'DELTA_PUBLIC_URLS=%s\n' "$(echo "$PUBLIC_URLS_B64" | base64 -d)"
} > "$base/secrets.env"
# 체크포인트·승인·점유 기록은 호스트 디렉터리에 둔다(컨테이너를 재배포해도 남는다, 명세 v1.1 11장).
install -d -o 10001 -g 10001 -m 0750 "$base/state/orchestrator"
docker rm -f delta-orchestrator >/dev/null 2>&1 || true
docker run -d --name delta-orchestrator --restart unless-stopped --network host \
  --env-file "$base/secrets.env" -e ORCH_PUBLIC_ORIGIN="$PUBLIC_ORIGIN" -e DELTA_NAT_IP="$NAT_IP" \
  -e ORCH_STATE_DIR=/state -v "$base/state/orchestrator:/state" \
  "$IMAGE" >/dev/null
llm_auth=n/a
wait_healthy http://127.0.0.1:8080/healthz delta-orchestrator
