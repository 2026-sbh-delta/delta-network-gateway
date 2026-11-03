#!/usr/bin/env bash
# 노트북에서 실행한다: SAST·DAST 이미지를 arm64 단일 매니페스트로 빌드해 ECR에 올리고, SSM으로 서버에 배포한다.
# 사용: AWS_PROFILE=sb deploy/tooling/deploy.sh
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-sb}"
export AWS_PAGER=""
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
tf="$repo/terraform/tooling-aws"

outputs="$(terraform -chdir="$tf" output -json)"
read_out() { python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(eval(sys.argv[2]))" "$outputs" "$1"; }
instance="$(read_out "d['instance_id']['value']")"
sast_repo="$(read_out "d['ecr_repository_urls']['value']['sast']")"
dast_repo="$(read_out "d['ecr_repository_urls']['value']['dast']")"
region="$(echo "$sast_repo" | cut -d. -f4)"
registry="${sast_repo%%/*}"

# 이미지 태그: 커밋 SHA. 커밋하지 않은 변경이 있으면 시각을 붙인다(저장소가 태그 덮어쓰기를 막는다).
# 롤백: TAG=<이전 태그> 를 주면 빌드 없이 그 태그를 다시 배포한다.
if [ -n "${TAG:-}" ]; then
  tag="$TAG"
  echo "[1/3] 빌드 생략, 기존 태그 $tag 재배포"
else
  tag="$(git -C "$repo" rev-parse --short=12 HEAD)"
  git -C "$repo" diff --quiet HEAD -- services/delta-sast services/delta-dast || tag="$tag-dirty-$(date +%Y%m%d%H%M%S)"
  echo "[1/3] ECR 로그인·빌드·푸시 (tag $tag)"
  aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "$registry" >/dev/null
  for svc in sast dast; do
    target="$sast_repo"; [ "$svc" = dast ] && target="$dast_repo"
    docker buildx build --platform linux/arm64 --provenance=false --sbom=false \
      -t "$target:$tag" --push "$repo/services/delta-$svc"
  done
fi

echo "[2/3] SSM으로 서버에 배포 ($instance)"
params="$(python3 - "$here/remote.sh" "$sast_repo:$tag" "$dast_repo:$tag" "$region" <<'PY'
import base64, json, sys
script, sast, dast, region = sys.argv[1:]
body = base64.b64encode(open(script, "rb").read()).decode()
print(json.dumps({"commands": [
    f"echo {body} | base64 -d > /opt/delta/remote.sh",
    f"SAST_IMAGE={sast} DAST_IMAGE={dast} REGION={region} bash /opt/delta/remote.sh",
]}))
PY
)"
cmd="$(aws ssm send-command --region "$region" --instance-ids "$instance" --document-name AWS-RunShellScript \
  --comment "delta tooling deploy $tag" --parameters "$params" --query Command.CommandId --output text)"
aws ssm wait command-executed --region "$region" --command-id "$cmd" --instance-id "$instance" || true

echo "[3/3] 결과"
aws ssm get-command-invocation --region "$region" --command-id "$cmd" --instance-id "$instance" \
  --query '{status:Status,out:StandardOutputContent,err:StandardErrorContent}' --output json
echo
echo "작업실 열기: $(read_out "d['port_forward_command']['value']")"
echo "그다음 브라우저에서 http://127.0.0.1:8765"
