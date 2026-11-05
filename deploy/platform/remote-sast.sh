# SAST: 사설 주소 :8790. 보안 그룹이 DAST(이후 Jenkins·오케스트레이터)만 받는다.
pull_image
setup_llm sast
printf 'DELTA_SAST_TOKEN=%s\n' "$(secret shared/sast_token)" > "$base/secrets.env"
model=$(param sast/llm_model)
[ -n "$model" ] && printf 'SAST_LLM_MODEL=%s\n' "$model" >> "$base/llm.env"
install -d -o 10001 -g 10001 -m 0750 "$base/state/sast" "$base/state/sources"

docker rm -f delta-sast >/dev/null 2>&1 || true
platform_logs sast sast
docker run -d --name delta-sast --restart unless-stopped --network host "${log_opts[@]}" \
  --env-file "$base/secrets.env" --env-file "$base/llm.env" \
  -e SAST_LLM_BACKEND="$backend" -e SAST_LOCAL_ROOTS=/srv/sources \
  -v "$base/state/sast:/var/lib/delta-sast" -v "$base/state/sources:/srv/sources:ro" "${llm_mounts[@]}" \
  "$IMAGE" uvicorn delta_sast.api:app --host 0.0.0.0 --port 8790 --log-level warning >/dev/null
wait_healthy http://127.0.0.1:8790/healthz delta-sast
