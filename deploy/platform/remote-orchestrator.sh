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
  # 멈춘 실행의 원인 진단(delta-ai). 없으면 진단하지 않는다.
  ai_token="$(secret ai/api_token 2>/dev/null || true)"
  [ -n "$ai_token" ] && printf 'DELTA_AI_TOKEN=%s\n' "$ai_token"
  # 서비스 도메인 전환(선택). 토큰은 해당 zone 의 DNS 편집 권한만. 없으면 전환을 켠 실행이 시작 단계에서 멈춘다.
  cf_token="$(secret orchestrator/cloudflare_token 2>/dev/null || true)"
  [ -n "$cf_token" ] && printf 'CLOUDFLARE_API_TOKEN=%s\n' "$cf_token"
  # 전환 설정은 JSON 일 때만 쓴다("disabled: …" 등은 꺼진 상태). 운영 진입점이 정해지기 전에는 끈다.
  cutover="$(param orchestrator/cutover_config)"
  case "$cutover" in "{"*) printf 'DELTA_CUTOVER=%s\n' "$cutover" ;; esac
  printf 'DELTA_PUBLIC_URLS=%s\n' "$(echo "$PUBLIC_URLS_B64" | base64 -d)"
  # 운영 배포(선택): 사람 승인 서명 개인키(오케스트레이터만 가진다)·대상 인벤토리·빌드 묶음 ECR 사본 저장소.
  # 줄바꿈 있는 PEM 은 env 파일 한 줄로 넣을 수 없어 base64 로 감싸 두고 앱이 푼다.
  rel_key="$(secret orchestrator/release_signing_key 2>/dev/null || true)"
  [ -n "$rel_key" ] && printf 'DELTA_RELEASE_SIGNING_KEY_B64=%s\n' "$(printf '%s' "$rel_key" | base64 | tr -d '\n')"
  rel_targets="$(param jenkins/release_targets)"
  case "$rel_targets" in "{"*) printf 'DELTA_RELEASE_TARGETS=%s\n' "$(printf '%s' "$rel_targets" | tr -d '\n')" ;; esac
  ecr_repo="$(param orchestrator/ecr_image_repo)"
  [ -n "$ecr_repo" ] && printf 'DELTA_ECR_IMAGE_REPO=%s\n' "$ecr_repo"
} > "$base/secrets.env"
# 체크포인트·승인·점유 기록은 호스트 디렉터리에 둔다(컨테이너를 재배포해도 남는다, 명세 v1.1 11장).
install -d -o 10001 -g 10001 -m 0750 "$base/state/orchestrator"
docker rm -f delta-orchestrator >/dev/null 2>&1 || true
platform_logs orchestrator orchestrator
docker run -d --name delta-orchestrator --restart unless-stopped --network host "${log_opts[@]}" \
  --env-file "$base/secrets.env" -e ORCH_PUBLIC_ORIGIN="$PUBLIC_ORIGIN" -e DELTA_NAT_IP="$NAT_IP" \
  -e ORCH_STATE_DIR=/state -v "$base/state/orchestrator:/state" \
  "$IMAGE" >/dev/null
llm_auth=n/a
wait_healthy http://127.0.0.1:8080/healthz delta-orchestrator
