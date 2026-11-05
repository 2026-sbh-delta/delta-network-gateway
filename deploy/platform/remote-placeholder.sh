# 아직 개발 중인 서비스: 임시 서버로 자리만 잡는다(/healthz). 실제 이미지가 나오면 같은 경로로 교체한다.
: "${SERVICE:?}" "${PORT:?}"
pull_image
docker rm -f "delta-$SERVICE" >/dev/null 2>&1 || true
platform_logs "$SERVICE" "$SERVICE"
docker run -d --name "delta-$SERVICE" --restart unless-stopped --network host "${log_opts[@]}" \
  -e SERVICE_NAME="$SERVICE" -e PORT="$PORT" "$IMAGE" >/dev/null
llm_auth=n/a
wait_healthy "http://127.0.0.1:$PORT/healthz" "delta-$SERVICE"
