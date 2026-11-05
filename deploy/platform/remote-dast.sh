# DAST: 제어 화면 :8765(ALB에서만 받음, 공개 주소는 로그인 필요). 실습 게시판 :8766은 127.0.0.1에만 열린다.
: "${PUBLIC_ORIGIN:?}" "${SAST_URL:?}" "${STAGING_URL:?}"
pull_image
setup_llm dast
{
  printf 'DELTA_SAST_TOKEN=%s\n' "$(secret shared/sast_token)"
  printf 'DAST_ADMIN_PASSWORD=%s\n' "$(secret dast/admin_password)"
  printf 'DAST_SESSION_SECRET=%s\n' "$(secret dast/session_secret)"
  # 오케스트레이터 전용(내부 주소: /api/platform/* 와 수정안 요청만)
  printf 'DAST_PLATFORM_TOKEN=%s\n' "$(secret dast/platform_token)"
  # 스테이징 조회 토큰만 받는다(배포·중단 불가)
  printf 'DELTA_STAGING_TOKEN=%s\n' "$(secret jenkins/staging_read_token)"
} > "$base/secrets.env"
model=$(param dast/llm_model)
[ -n "$model" ] && printf 'DAST_CLAUDE_MODEL=%s\n' "$model" >> "$base/llm.env"
install -d -o 10001 -g 10001 -m 0750 "$base/state/runs" "$base/state/data"

# 파이프라인 점검 상한: 시연은 우선순위 1건(블루스크린 항목)만 본다(11/5 결정). 나머지 후보는 '미점검'으로 남아 사람이 확인한다.
docker rm -f delta-dast >/dev/null 2>&1 || true
docker run -d --name delta-dast --restart unless-stopped --network host --shm-size=1g \
  --env-file "$base/secrets.env" --env-file "$base/llm.env" \
  -e DELTA_RUNTIME=server -e DELTA_SAST_URL="$SAST_URL" -e DELTA_STAGING_URL="$STAGING_URL" \
  -e DAST_BIND_HOST=0.0.0.0 -e DAST_PUBLIC_ORIGIN="$PUBLIC_ORIGIN" -e DAST_INTERNAL_HOSTS=dast.cp.internal:8765 \
  -e DAST_PLATFORM_MAX_FINDINGS="${DAST_PLATFORM_MAX_FINDINGS:-1}" \
  -v "$base/state/runs:/app/runs" -v "$base/state/data:/app/data" "${llm_mounts[@]}" \
  "$IMAGE" >/dev/null
wait_healthy http://127.0.0.1:8765/healthz delta-dast
echo "sast_link: $(curl -fsS http://127.0.0.1:8765/api/sast/status || echo unavailable)"
