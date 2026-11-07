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
  # 장애 원인 진단(연재님 delta-ai 진단 호스트): 연결값 JSON(url·token·ca_base64) 하나. 있으면 진단을 필수로 켜고
  # 같은 실행의 중앙 로그를 근거로 붙인다. 사례 검토 화면 토큰은 진단 토큰과 다른 값이다.
  diag="$(secret orchestrator/diagnostic_ai_connection 2>/dev/null || true)"
  if [ -n "$diag" ]; then
    printf '%s' "$diag" | python3 -c 'import json,sys; c=json.load(sys.stdin); print("DELTA_DIAGNOSTIC_URL="+c["url"]); print("DELTA_DIAGNOSTIC_TOKEN="+c["token"]); print("DELTA_DIAGNOSTIC_CA_BASE64="+c.get("ca_base64",""))'
    printf 'DELTA_DIAGNOSTICS_REQUIRED=1\nDELTA_LOG_EVIDENCE_ENABLED=1\n'
  fi
  review="$(secret orchestrator/case_review_token 2>/dev/null || true)"
  [ -n "$review" ] && printf 'DELTA_CASE_REVIEW_TOKEN=%s\n' "$review"
  # 온프렘 실행(선택): 고정 SSM 문서 이름·해시. enabled 일 때만 켠다.
  onprem="$(param orchestrator/onprem_connection)"
  case "$onprem" in "{"*) printf '%s' "$onprem" | python3 -c 'import json,sys; c=json.load(sys.stdin)
if c.get("enabled"): print("DELTA_ONPREM_ENABLED=1"); print("DELTA_ONPREM_DOCUMENT="+c["document"]); print("DELTA_ONPREM_DOCUMENT_HASH="+c["document_hash"]); print("DELTA_ONPREM_VERIFICATION=0")' ;; esac
  # 서비스 도메인 전환(선택). 토큰은 해당 zone 의 DNS 편집 권한만. 없으면 전환을 켠 실행이 시작 단계에서 멈춘다.
  cf_token="$(secret orchestrator/cloudflare_token 2>/dev/null || true)"
  [ -n "$cf_token" ] && printf 'CLOUDFLARE_API_TOKEN=%s\n' "$cf_token"
  # 전환 설정은 JSON 일 때만 쓴다("disabled: …" 등은 꺼진 상태). 운영 진입점이 정해지기 전에는 끈다.
  cutover="$(param orchestrator/cutover_config)"
  case "$cutover" in "{"*) printf 'DELTA_CUTOVER=%s\n' "$cutover" ;; esac
  # 병렬 워크로드 차분(선택): 요청 파일은 읽기 전용으로 마운트하고, 연결값은 오케스트레이터가 SSM 에서 직접 읽는다.
  diff_request="$(param orchestrator/diff_request)"
  case "$diff_request" in "{"*)
    printf 'DELTA_DIFF_REQUIRED=1\nDELTA_DIFF_REQUEST_FILE=/run/delta-diff/request.json\n'
    printf 'DELTA_DIFF_CONNECTION_PARAMETER=%sshared/diff_connection\nDELTA_DIFF_ALLOW_PRIVATE_HTTP=1\n' "$PREFIX" ;;
  esac
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
diff_mount=()
case "$diff_request" in "{"*)
  install -d -m 0755 "$base/diff"
  printf '%s\n' "$diff_request" > "$base/diff/request.json"
  chmod 0644 "$base/diff/request.json"
  diff_mount=(-v "$base/diff/request.json:/run/delta-diff/request.json:ro") ;;
esac
docker rm -f delta-orchestrator >/dev/null 2>&1 || true
platform_logs orchestrator orchestrator
docker run -d --name delta-orchestrator --restart unless-stopped --network host "${log_opts[@]}" \
  --env-file "$base/secrets.env" -e ORCH_PUBLIC_ORIGIN="$PUBLIC_ORIGIN" -e DELTA_NAT_IP="$NAT_IP" \
  -e ORCH_STATE_DIR=/state -v "$base/state/orchestrator:/state" ${diff_mount[@]+"${diff_mount[@]}"} \
  "$IMAGE" >/dev/null
llm_auth=n/a
wait_healthy http://127.0.0.1:8080/healthz delta-orchestrator
