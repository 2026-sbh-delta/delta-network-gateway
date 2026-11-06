# 장애 원인 진단(연재님 delta-ai): 사설 주소 :8090. 보안 그룹이 오케스트레이터만 받는다.
# 멈춘 실행의 실패 단계·사유를 받아 원인을 설명한다(deployment_gate). 판정은 파이프라인이 한다.
pull_image
setup_llm ai
printf 'DELTA_API_TOKEN=%s\n' "$(secret ai/api_token)" > "$base/secrets.env"
# Claude 연결(OIDC 연동)이 없으면 규칙 문서 기반 설명만 돌려준다.
llm_mode=none; [ "$backend" != off ] && llm_mode=claude
install -d -o 10001 -g 10001 -m 0750 "$base/state/ai"

docker rm -f delta-ai >/dev/null 2>&1 || true
platform_logs ai ai
docker run -d --name delta-ai --restart unless-stopped --network host "${log_opts[@]}" \
  --env-file "$base/secrets.env" --env-file "$base/llm.env" -e DELTA_LLM="$llm_mode" \
  -v "$base/state/ai:/app/var" "${llm_mounts[@]}" \
  "$IMAGE" >/dev/null
wait_healthy http://127.0.0.1:8090/healthz delta-ai
