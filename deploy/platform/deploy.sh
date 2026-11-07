#!/usr/bin/env bash
# 노트북에서 실행한다: 서비스 이미지를 빌드해 그 서비스 전용 ECR에 올리고, SSM으로 그 인스턴스의 컨테이너만 교체한다.
# 사용: AWS_PROFILE=sb deploy/platform/deploy.sh sast dast jenkins jenkins-agent orchestrator diff
#       TAG=<기존 태그> deploy/platform/deploy.sh dast   (빌드 없이 그 태그로 롤백)
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-sb}"
export AWS_PAGER=""
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
tf="$repo/terraform/platform-aws"
[ "$#" -gt 0 ] || { echo "사용: deploy.sh <서비스...> (sast dast jenkins jenkins-agent orchestrator diff)" >&2; exit 2; }

outputs="$(terraform -chdir="$tf" output -json)"
out() { python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(eval('d'+sys.argv[2]))" "$outputs" "$1"; }
region="$(out "['region']['value']")"
prefix="$(out "['parameter_prefix']['value']")"   # 예: /delta-cp/
name_of() { echo "$1.cp.internal"; }
app_url() { out "['app_urls']['value']['$1']"; }
# 인스턴스와 저장소는 Terraform 출력이 아니라 AWS에서 이름으로 찾는다(apply가 중간에 실패해도 배포할 수 있게).
name_prefix="$(echo "$prefix" | tr -d /)"
instance_of() {
  aws ec2 describe-instances --region "$region" --filters "Name=tag:Name,Values=$name_prefix-$1" \
    "Name=instance-state-name,Values=running" --query 'Reservations[0].Instances[0].InstanceId' --output text
}
repo_of() {
  aws ecr describe-repositories --region "$region" --repository-names "$name_prefix-$1" \
    --query 'repositories[0].repositoryUri' --output text
}

# 비밀값은 Terraform state에 남기지 않는다. 없을 때만 여기서 만들어 SecureString으로 넣는다(값은 출력하지 않음).
ensure_secret() {
  local name="$1"
  if ! aws ssm get-parameter --region "$region" --name "$name" --query Parameter.Name --output text >/dev/null 2>&1; then
    aws ssm put-parameter --region "$region" --name "$name" --type SecureString \
      --value "$(openssl rand -base64 48 | tr -d '\n/+=' | cut -c1-40)" >/dev/null
    echo "  비밀값 생성: $name"
  fi
}
# 스테이징 토큰은 조회·변경 둘로 나눈다. 변경 토큰은 Jenkins 경로에만 두고(shared/ 아님) 새로 만든다.
# 예전 shared/staging_token은 스테이징 API가 더 이상 받지 않는다.
for s in shared/sast_token dast/admin_password dast/session_secret dast/platform_token \
         jenkins/admin_password jenkins/platform_password jenkins/staging_read_token jenkins/staging_write_token \
         orchestrator/admin_password orchestrator/session_secret; do
  ensure_secret "${prefix}$s"
done
# 스테이징 도구 설정(비밀값 아님): 로컬 staging.json이 있으면 올린다.
if [ -f "$repo/services/delta-staging/staging.json" ]; then
  aws ssm put-parameter --region "$region" --name "${prefix}jenkins/staging_config" --type String \
    --value "$(cat "$repo/services/delta-staging/staging.json")" --overwrite >/dev/null
fi

# 운영 배포(delta-release) 설정(비밀값 아님, 저장소 파일): 대상 인벤토리·배포 설정(공개 CA 포함)·ECR 사본 저장소.
release_dir="$repo/deploy/platform/release"
if [ -f "$release_dir/targets.json" ] && [ -f "$release_dir/config.json" ]; then
  aws ssm put-parameter --region "$region" --name "${prefix}jenkins/release_targets" --type String \
    --value "$(cat "$release_dir/targets.json")" --overwrite >/dev/null
  aws ssm put-parameter --region "$region" --name "${prefix}jenkins/release_config" --type String --tier Advanced \
    --value "$(cat "$release_dir/config.json")" --overwrite >/dev/null
  aws ssm put-parameter --region "$region" --name "${prefix}orchestrator/ecr_image_repo" --type String \
    --value "$(aws sts get-caller-identity --query Account --output text).dkr.ecr.${region}.amazonaws.com/delta-lab-staging" --overwrite >/dev/null
fi
# 운영 승인 서명 키(Ed25519): 없을 때만 만든다. 개인키는 오케스트레이터 경로(SecureString), 공개키는 Jenkins 경로.
# 개인키는 출력·파일로 남기지 않고 Terraform state 에도 넣지 않는다(다른 비밀값과 같은 규칙).
if ! aws ssm get-parameter --region "$region" --name "${prefix}orchestrator/release_signing_key" --query Parameter.Name --output text >/dev/null 2>&1; then
  REGION="$region" PREFIX="$prefix" uv run --quiet --with cryptography python - <<'PYKEY'
import os, subprocess
from cryptography.hazmat.primitives import serialization as s
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
k = Ed25519PrivateKey.generate()
put = ["aws", "ssm", "put-parameter", "--region", os.environ["REGION"], "--overwrite"]
subprocess.run(put + ["--name", os.environ["PREFIX"] + "orchestrator/release_signing_key", "--type", "SecureString",
               "--value", k.private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()).decode()],
               check=True, stdout=subprocess.DEVNULL)
subprocess.run(put + ["--name", os.environ["PREFIX"] + "jenkins/release_public_key", "--type", "String",
               "--value", k.public_key().public_bytes(s.Encoding.PEM, s.PublicFormat.SubjectPublicKeyInfo).decode()],
               check=True, stdout=subprocess.DEVNULL)
PYKEY
  echo "  운영 승인 서명 키 생성"
fi

port_of() {
  case "$1" in
    orchestrator|jenkins) echo 8080 ;;
    diff) echo 8095 ;;
    sast) echo 8790 ;;
    dast) echo 8765 ;;
  esac
}

# GCP 자격 증명 설정(비밀값 아님): onboarding-gcp가 적용돼 있으면 스테이징 배포용 설정을 올린다.
gcp_tf="$repo/terraform/onboarding-gcp"
if gcp_cfg="$(terraform -chdir="$gcp_tf" output -json credential_configs 2>/dev/null)" && [ -n "$gcp_cfg" ] && [ "$gcp_cfg" != "{}" ]; then
  aws ssm put-parameter --region "$region" --name "${prefix}jenkins/gcp_staging_credentials" --type String \
    --value "$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['staging-deploy'])" "$gcp_cfg")" --overwrite >/dev/null
  if python3 -c "import json,sys; sys.exit(0 if 'release-deployer' in json.loads(sys.argv[1]) else 1)" "$gcp_cfg"; then
    aws ssm put-parameter --region "$region" --name "${prefix}jenkins/gcp_release_credentials" --type String \
      --value "$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['release-deployer'])" "$gcp_cfg")" --overwrite >/dev/null
  fi
  if python3 -c "import json,sys; sys.exit(0 if 'build-push' in json.loads(sys.argv[1]) else 1)" "$gcp_cfg"; then
    aws ssm put-parameter --region "$region" --name "${prefix}jenkins-agent/gcp_build_credentials" --type String \
      --value "$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['build-push'])" "$gcp_cfg")" --overwrite >/dev/null
  fi
fi

# 서비스 도메인 전환 설정(비밀값 아님): service-domain 이 적용돼 있으면 올린다. Cloudflare 토큰은 사람이 SSM 에 넣는다.
if cutover_cfg="$(terraform -chdir="$repo/terraform/service-domain" output -raw cutover_config 2>/dev/null)" && [ "${cutover_cfg#\{}" != "$cutover_cfg" ]; then
  aws ssm put-parameter --region "$region" --name "${prefix}orchestrator/cutover_config" --type String \
    --value "$cutover_cfg" --overwrite >/dev/null
fi

# 빌드 대상: <서비스> → 플랫폼 아키텍처, 빌드 경로, Dockerfile
build_spec() {
  case "$1" in
    sast|dast) echo "linux/arm64 services/delta-$1 Dockerfile" ;;
    orchestrator) echo "linux/arm64 services delta-orchestrator/Dockerfile" ;;   # delta-pipeline 함께
    diff) echo "linux/arm64 deploy/platform/placeholder Dockerfile" ;;   # 개발 전 임시 서버
    jenkins) echo "linux/amd64 deploy/platform/jenkins controller/Dockerfile" ;;
    jenkins-agent) echo "linux/amd64 deploy/platform/jenkins agent/Dockerfile" ;;
    *) return 1 ;;
  esac
}

build_push() {  # build_push <platform> <context> <dockerfile> <image:tag> → digest 참조를 출력
  local platform="$1" context="$2" dockerfile="$3" ref="$4"
  if [ -z "${TAG:-}" ]; then
    # Jenkins 컨트롤러는 운영 배포기(deploy/platform/release)도 함께 담는다
    local extra=()
    [ "$context" = "deploy/platform/jenkins" ] && extra=(--build-context "release=$repo/deploy/platform/release")
    docker buildx build --platform "$platform" --provenance=false --sbom=false ${extra[@]+"${extra[@]}"} \
      -f "$repo/$context/$dockerfile" -t "$ref" --push "$repo/$context" >&2 || { echo "빌드·푸시 실패: $ref" >&2; return 1; }
  fi
  local digest
  digest="$(aws ecr describe-images --region "$region" --repository-name "$(echo "${ref%:*}" | cut -d/ -f2-)" \
    --image-ids imageTag="${ref##*:}" --query 'imageDetails[0].imageDigest' --output text)" || return 1
  echo "${ref%:*}@$digest"
}

# 이미지 태그: 커밋 SHA. 그 이미지의 소스 경로에 커밋하지 않은 변경이 있으면 시각을 붙인다(저장소가 태그 덮어쓰기를 막는다).
tag_for() {
  local t
  t="$(git -C "$repo" rev-parse --short=12 HEAD)"
  # 추적하지 않는 새 파일도 변경으로 본다(git diff는 못 잡는다).
  [ -z "$(git -C "$repo" status --porcelain -- "$1")" ] || t="$t-dirty-$(date +%Y%m%d%H%M%S)"
  echo "$t"
}

for svc in "$@"; do
  read -r platform context dockerfile <<<"$(build_spec "$svc")" || { echo "알 수 없는 서비스: $svc" >&2; exit 2; }
  instance="$(instance_of "$svc")"
  [ "$instance" != "None" ] || { echo "$svc 인스턴스가 실행 중이 아니다" >&2; exit 1; }
  image_repo="$(repo_of "$svc")"
  registry="${image_repo%%/*}"

  if [ -n "${TAG:-}" ]; then
    tag="$TAG"
    echo "[$svc 1/3] 빌드 생략, 기존 태그 $tag 재배포"
  else
    tag="$(tag_for "$context")"
    echo "[$svc 1/3] 빌드·푸시 (${platform#linux/}, tag $tag)"
    aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "$registry" >/dev/null 2>&1
  fi
  image="$(build_push "$platform" "$context" "$dockerfile" "$image_repo:$tag")" || exit 1

  env_args="IMAGE=$image REGION=$region PREFIX=$prefix LLM_ROLE_ARN=$(out "['llm_role_arn']['value']")"
  script="remote-$svc.sh"
  case "$svc" in
    dast)
      env_args="$env_args PUBLIC_ORIGIN=$(app_url dast) SAST_URL=http://$(name_of sast):8790"
      env_args="$env_args STAGING_URL=http://$(name_of jenkins):8091"
      # 시연 점검 범위(remote-dast.sh 기본값: 1건, 게시글 본문 경로). 바꾸려면 배포할 때 값을 준다.
      env_args="$env_args DAST_PLATFORM_MAX_FINDINGS=${DAST_PLATFORM_MAX_FINDINGS:-1}"
      env_args="$env_args DAST_PLATFORM_PREFER=${DAST_PLATFORM_PREFER:-2d3b1d57b297b50cf62704e6cb34a5be}"
      env_args="$env_args DAST_PLATFORM_DIRECT=${DAST_PLATFORM_DIRECT:-1}" ;;
    jenkins)
      # 스테이징 도구도 Jenkins 컨트롤러에서 돈다(배포 권한을 가진 곳). 이미지는 같은 저장소에 staging- 태그로 둔다.
      # 롤백(TAG=…) 때 처음 배포한 스테이징 도구 태그가 달랐으면 STAGING_TAG 로 그 태그를 준다
      staging_tag="${STAGING_TAG:-${TAG:+staging-$TAG}}"; staging_tag="${staging_tag:-staging-$(tag_for services/delta-staging)}"
      staging_image="$(build_push linux/amd64 services/delta-staging Dockerfile "$image_repo:$staging_tag")" || exit 1
      env_args="$env_args STAGING_IMAGE=$staging_image" ;;
    jenkins-agent)
      env_args="$env_args JENKINS_URL=http://$(name_of jenkins):8080" ;;
    orchestrator)
      # 화면에 보여 줄 공개 주소 묶음(JSON)은 공백·따옴표 때문에 base64로 넘긴다.
      urls_b64="$(python3 -c "import json,sys; print(__import__('base64').b64encode(json.dumps(json.loads(sys.argv[1])['app_urls']['value']).encode()).decode())" "$outputs")"
      env_args="$env_args PUBLIC_ORIGIN=$(app_url orchestrator) PUBLIC_URLS_B64=$urls_b64 NAT_IP=$(out "['nat_egress_ip']['value']")" ;;
    diff)
      script="remote-placeholder.sh"
      env_args="$env_args SERVICE=$svc PORT=$(port_of "$svc")" ;;
  esac

  echo "[$svc 2/3] SSM으로 컨테이너 교체 ($instance, ${image##*@})"
  params="$(python3 - "$here/remote-common.sh" "$here/$script" "$env_args" <<'PY'
import base64, json, sys
common, service, env = sys.argv[1:]
body = base64.b64encode(open(common, "rb").read() + b"\n" + open(service, "rb").read()).decode()
print(json.dumps({"commands": [
    f"echo {body} | base64 -d > /opt/delta/remote.sh && chmod 700 /opt/delta/remote.sh",
    f"{env} bash /opt/delta/remote.sh",
]}))
PY
)"
  cmd="$(aws ssm send-command --region "$region" --instance-ids "$instance" --document-name AWS-RunShellScript \
    --comment "delta-cp $svc $tag" --parameters "$params" --query Command.CommandId --output text)"
  # 첫 배포는 이미지가 커서 오래 걸릴 수 있다. 최대 15분. Success + 종료 코드 0이 아니면 여기서 멈춘다(다음 서비스로 넘어가지 않음).
  echo "[$svc 3/3] 결과"
  if ! python3 "$here/ssmcheck.py" wait --region "$region" --command-id "$cmd" --instance-id "$instance" --timeout 900; then
    echo "[$svc] 배포 실패. 이후 서비스 배포를 멈춘다." >&2
    exit 1
  fi
done

echo
echo "공개 주소: $(out "['app_urls']['value']")"
echo "DAST 비밀번호: aws ssm get-parameter --region $region --name ${prefix}dast/admin_password --with-decryption --query Parameter.Value --output text"
