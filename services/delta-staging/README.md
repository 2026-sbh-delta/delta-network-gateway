# delta-staging

파이프라인 스테이징 도구. 관리 에이전트가 "스테이징이 필요하다"고 판단하면 이 도구를 호출한다. 빌드된 이미지를 클라우드별 스테이징에 띄우고, DAST 스캐너만 닿는 내부 주소를 돌려준다.

기반 인프라(클러스터, 서비스, 내부 ALB, 보안 그룹)는 Terraform이 한 번 만든다([staging-aws](../../terraform/staging-aws/README.md)). 이 도구는 실행마다 **이미지만 바꾼다**.

## 실행

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/pytest -q
```

설정 파일은 Terraform 출력을 그대로 쓴다. 형식은 [staging.example.json](staging.example.json)을 따른다.

```bash
terraform -chdir=../../terraform/staging-aws output -json tool_config > staging.json
```

서버로 띄우기(관리 에이전트가 HTTP로 호출):

```bash
DELTA_STAGING_TOKEN=<임의의 값> AWS_PROFILE=<스테이징 계정 프로필> .venv/bin/uvicorn delta_staging.api:app --port 8091
```

스크립트로 바로 쓰기(끝날 때까지 기다리고 결과 JSON을 출력한다. ready면 종료 코드 0):

```bash
.venv/bin/python -m delta_staging deploy --run-id run-42 --image <계정>.dkr.ecr.ap-northeast-2.amazonaws.com/demo-app@sha256:<digest> --commit <SHA>
.venv/bin/python -m delta_staging stop --run-id run-42
.venv/bin/python -m delta_staging tools
```

| 환경 변수 | 기본값 | 뜻 |
|---|---|---|
| `DELTA_STAGING_TOKEN` | 없음(서버 필수) | API Bearer 토큰 |
| `DELTA_STAGING_CONFIG` | `staging.json` | 대상 설정(Terraform `tool_config` 출력) |
| `DELTA_STAGING_STATE` | `var/deployments.json` | 배포 기록 |

AWS 자격 증명은 boto3 기본 경로(`AWS_PROFILE` 등)를 쓴다. 이 도구는 자격 증명 파일을 읽거나 출력하지 않는다.

## 관리 에이전트 연동

`GET /v1/tools`는 LLM 도구 형식(`name`, `description`, `input_schema`)의 `tools`와, 각 도구를 HTTP로 잇는 `routes`를 돌려준다. 에이전트는 `tools`를 그대로 등록하고, 도구 호출이 오면 `routes`의 경로로 보낸다.

| 도구 | HTTP | 용도 |
|---|---|---|
| `staging_deploy` | `POST /v1/staging/deployments` | 이미지를 스테이징에 띄운다. 바로 `202`, `state=deploying` |
| `staging_status` | `GET /v1/staging/deployments/{run_id}` | 상태와 단계 기록. `ready`면 `url`을 DAST에 넘긴다 |
| `staging_stop` | `POST /v1/staging/deployments/{run_id}/stop` | 점검 뒤 태스크를 0개로 내린다 |

모든 요청에 `Authorization: Bearer <DELTA_STAGING_TOKEN>`이 필요하다(`/healthz` 제외).

요청 예시:

```json
{"run_id": "run-42", "cloud": "aws", "image": "<계정>.dkr.ecr.ap-northeast-2.amazonaws.com/demo-app@sha256:<digest>",
 "commit": "abc1234", "env": {"APP_MODE": "staging"}}
```

| 필드 | 필수 | 뜻 |
|---|---|---|
| `run_id` | 예 | 파이프라인 실행 ID. 같은 값으로 다시 부르면 새로 띄우지 않는다 |
| `image` | 예 | 빌드된 이미지. `@sha256:` digest를 권장한다. 태그면 `pinned=false`로 기록한다 |
| `cloud` | 아니오 | `aws`(기본). `gcp`는 계약에만 있고 아직 `501`이다 |
| `commit` | 아니오 | 이미지를 만든 커밋. 컨테이너 env `DELTA_COMMIT`으로도 넣는다 |
| `env` | 아니오 | 비밀이 아닌 env. `PASSWORD`·`TOKEN`·`SECRET`·`API_KEY` 등이 든 이름은 거부한다 |
| `replace` | 아니오 | 다른 실행이 스테이징을 쓰는 중이면 교체한다. 배포 중인 실행은 교체하지 않는다 |
| `scanner_ip` | 아니오 | 공개 스테이징에 허용할 DAST 실행기 공인 IPv4. `auto`면 도구를 실행하는 곳의 현재 공인 IP를 매번 확인해 쓴다. 내부 스테이징에는 보내면 400 |

## 상태와 응답

| `state` | 뜻 |
|---|---|
| `deploying` | 배포 중. `steps`에 진행 단계가 쌓인다 |
| `ready` | 헬스 체크 통과. `url`이 채워진다 |
| `failed` | `error`에 사유. 서비스를 0개로 내린다 |
| `stopped` | 점검 뒤 내렸다 |
| `replaced` | 다른 실행이 스테이징을 가져갔다 |

`steps`는 `accepted → (scanner_allowed) → task_definition → service_updated → rollout_completed → image_digest → healthy` 순서다. `scanner_ip`를 준 실행은 응답의 `scanner_ip`에 허용한 IP가 남는다.

`image_digest`는 요청 값이 아니라 새 개정판으로 **실행 중인 태스크**에서 ECS가 실제로 받은 이미지 digest(`sha256:<64자리>`)다. 빌드 기록과 대조하는 빌드 식별자로 쓴다. 요청 이미지를 digest로 줬는데 실행 중인 digest와 다르면 `failed`로 끝낸다. 확인한 태스크는 `task_arn`에 남는다.

빌드는 아키텍처별 단일 매니페스트로 만든다. `docker buildx`는 플랫폼 하나만 지정해도 기본으로 provenance 증명을 붙여 이미지 인덱스를 만들기 때문에, `--platform linux/amd64 --provenance=false --sbom=false`로 빌드해야 빌드 기록의 digest와 ECS가 보고하는 digest가 같다.

| HTTP | 뜻 |
|---|---|
| 202 | 배포 시작(또는 같은 `run_id`의 기존 상태) |
| 400 | 허용하지 않은 이미지 저장소, 내부 스테이징에 `scanner_ip` |
| 409 | 다른 실행이 사용 중, 또는 같은 `run_id`로 다른 이미지 |
| 422 | 스키마 위반, 비밀처럼 보이는 env |
| 501 | 설정되지 않은 클라우드 |

## 안전장치

- 내부 모드면 Terraform이 스캐너 CIDR이나 보안 그룹만 허용한다. 공개 모드면 보안 그룹이 닫힌 채로 시작하고, 도구가 실행기의 현재 IP /32 하나만 열고 이전 IP는 지운다. 중지하거나 배포가 실패하면 그 IP도 닫는다. 어느 모드든 전체 대역은 받지 않는다.
- 이미지는 설정의 `allowed_image_prefixes`(기본: 같은 계정의 ECR)만 받는다. 에이전트가 임의 이미지를 띄우지 못한다.
- 배포 전에 STS로 자격 증명 계정이 설정의 `account_id`와 같은지 확인한다.
- 비밀 값은 env로 받지 않는다. 시크릿이 필요하면 태스크 정의의 secrets(Secrets Manager 참조)로 Terraform에서 넣는다.

## 필요한 AWS 권한(배포 역할)

- `sts:GetCallerIdentity`
- `ecs:DescribeTaskDefinition`, `ecs:RegisterTaskDefinition`, `ecs:TagResource`, 해당 서비스의 `ecs:UpdateService`·`ecs:DescribeServices`
- 실행 중 digest 확인: `ecs:ListTasks`, `ecs:DescribeTasks`
- `elasticloadbalancing:DescribeTargetHealth`
- `iam:PassRole`(실행 역할·태스크 역할 두 개만)
- 공개 모드: ALB 보안 그룹의 `ec2:DescribeSecurityGroupRules`, `ec2:AuthorizeSecurityGroupIngress`, `ec2:RevokeSecurityGroupIngress`, `ec2:CreateTags`

실제 배포 환경에서는 장기 키 대신 OIDC(웹 아이덴티티)나 태스크 역할로 받는다. boto3 기본 체인이 둘 다 읽으므로 코드 변경은 없다.

## 현재 한계

- AWS ECS만 구현했다. GCP(Cloud Run)는 같은 계약으로 추가할 예정이다.
- 실제 AWS 계정에서는 아직 돌리지 않았다. 테스트는 ECS/ELB/STS 응답을 흉내 낸 단위 테스트다.
- 실행마다 앱 데이터를 초기화하는 단계가 없다. 저장형 XSS 점검 뒤 남은 데이터가 다음 점검에 섞일 수 있다.
- 배포 기록은 프로세스 1개 전제의 JSON 파일이다. 재시작하면 진행 중이던 배포는 `failed`로 표시한다.
