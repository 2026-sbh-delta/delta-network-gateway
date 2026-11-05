# delta-staging

파이프라인 스테이징 도구. 관리 에이전트가 "스테이징이 필요하다"고 판단하면 이 도구를 호출한다. 빌드된 이미지를 클라우드별 스테이징에 띄우고, DAST 스캐너만 닿는 내부 주소를 돌려준다.

기반 인프라는 Terraform이 한 번 만든다. AWS는 ECS 서비스·ALB·보안 그룹([staging-aws](../../terraform/staging-aws/README.md)), GCP는 Cloud Run 서비스·외부 LB·Cloud Armor([staging-gcp](../../terraform/staging-gcp/README.md))다. 이 도구는 실행마다 **이미지만 바꾼다**. 호출 계약은 클라우드와 상관없이 같고 `cloud`만 다르다.

## 실행

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/pytest -q
```

설정 파일은 Terraform 출력을 그대로 쓴다. 형식은 [staging.example.json](staging.example.json)을 따른다.

```bash
terraform -chdir=../../terraform/staging-aws output -json tool_config > staging.json
```

두 클라우드를 함께 쓰면 두 `tool_config`를 JSON 배열 하나로 넣는다(`[{aws}, {gcp}]`).

서버로 띄우기(관리 에이전트가 HTTP로 호출):

```bash
DELTA_STAGING_READ_TOKEN=<조회 토큰> DELTA_STAGING_WRITE_TOKEN=<별도 변경 토큰> .venv/bin/uvicorn delta_staging.api:app --port 8091
```

스크립트로 바로 쓰기(끝날 때까지 기다리고 결과 JSON을 출력한다. ready면 종료 코드 0):

```bash
.venv/bin/python -m delta_staging deploy --run-id run-42 --image <계정>.dkr.ecr.ap-northeast-2.amazonaws.com/demo-app@sha256:<digest> --commit <SHA>
.venv/bin/python -m delta_staging stop --run-id run-42
.venv/bin/python -m delta_staging tools
```

| 환경 변수 | 기본값 | 뜻 |
|---|---|---|
| `DELTA_STAGING_READ_TOKEN` | 없음(서버 필수) | 조회·점유 해제 토큰 |
| `DELTA_STAGING_WRITE_TOKEN` | 없음(서버 필수) | 배포·중단 토큰. 조회 토큰과 달라야 한다 |
| `DELTA_STAGING_CONFIG` | `staging.json` | 대상 설정(Terraform `tool_config` 출력) |
| `DELTA_STAGING_STATE` | `var/deployments.json` | 배포 기록 |

AWS 자격 증명은 boto3 기본 경로(`AWS_PROFILE` 등)를 쓴다. 이 도구는 자격 증명 파일을 읽거나 출력하지 않는다.

## 관리 에이전트 연동

`GET /v1/tools`는 LLM 도구 형식(`name`, `description`, `input_schema`)의 `tools`와, 각 도구를 HTTP로 잇는 `routes`를 돌려준다. 에이전트는 `tools`를 그대로 등록하고, 도구 호출이 오면 `routes`의 경로로 보낸다.

| 도구 | HTTP | 용도 |
|---|---|---|
| `staging_deploy` | `POST /v1/staging/deployments` | 이미지를 스테이징에 띄운다. 바로 `202`, `state=deploying` |
| `staging_status` | `GET /v1/staging/deployments/{run_id}` | 상태와 단계 기록. `ready`면 `url`을 DAST에 넘긴다 |
| `staging_stop` | `POST /v1/staging/deployments/{run_id}/stop` | 태스크를 0개로 내린다. 상시 운영이라 시연·작업 세션을 마칠 때만 부른다 |

`GET /v1/staging/targets`는 클라우드별 현재 스테이징 주소 `{"targets": [{"cloud", "url"}]}`를 돌려준다(조회에 실패한 클라우드는 `url` 대신 `error`). 주소는 설정에 적어 두지 않고 부를 때마다 클라우드에서 조회한다. GCP는 전역 IP 이름(`address`)으로, AWS는 대상 그룹이 붙은 ALB로 찾는다. 스테이징을 다시 만들어 주소가 바뀌어도 설정을 고치지 않는다. DAST는 이 값으로 점검 주소를 받는다.

모든 요청에 `Authorization: Bearer <권한에 맞는 토큰>`이 필요하다(`/healthz` 제외). 조회 토큰으로 배포·중단하면403이다.

요청 예시:

```json
{"run_id": "run-42", "cloud": "aws", "image": "<계정>.dkr.ecr.ap-northeast-2.amazonaws.com/demo-app@sha256:<digest>",
 "commit": "abc1234", "env": {"APP_MODE": "staging"}}
```

| 필드 | 필수 | 뜻 |
|---|---|---|
| `run_id` | 예 | 파이프라인 실행 ID. 같은 값으로 다시 부르면 새로 띄우지 않는다 |
| `image` | 예 | 빌드된 이미지. `@sha256:` digest를 권장한다. 태그면 `pinned=false`로 기록한다 |
| `cloud` | 아니오 | `aws`(기본) 또는 `gcp`. 설정 파일에 없는 클라우드면 `501` |
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
| `stopped` | 내렸다(세션 종료) |
| `replaced` | 다른 실행이 스테이징을 가져갔다 |

`steps`는 AWS가 `accepted → (scanner_allowed) → task_definition → service_updated → image_digest → healthy`, GCP가 `accepted → (scanner_allowed) → image_digest → service_updated → traffic_switched → healthy` 순서다. `scanner_ip`를 준 실행은 응답의 `scanner_ip`에 허용한 IP가 남는다.

`image_digest`는 요청 값이 아니라 새 개정판으로 **실행 중인 태스크**에서 ECS가 실제로 받은 이미지 digest(`sha256:<64자리>`)다. 빌드 기록과 대조하는 빌드 식별자로 쓴다. 요청 이미지를 digest로 줬는데 실행 중인 digest와 다르면 `failed`로 끝낸다. 확인한 태스크는 `task_arn`에 남는다.

GCP(Cloud Run v2)는 개정판에 실제로 받은 digest를 돌려주는 필드가 없다. 그래서 태그 이미지는 Artifact Registry에서 지금 가리키는 digest로 바꾼 뒤 **그 digest로 개정판을 만든다**. 개정판이 digest로 고정되므로 다른 이미지를 실행할 수 없고, 도구는 새 개정판의 이미지가 그 값과 같은지 다시 확인한다. `task_arn`에는 개정판 이름이 들어간다. `scanner_ip=auto`(도구가 곧 스캐너)면 LB·Cloud Armor를 거친 실제 응답까지 확인한 뒤 `ready`로 알린다.

빌드 담당은 linux/amd64 실행 매니페스트와 이미지 인덱스 digest를 구분해야 한다. 기존 provenance/SBOM 설정을 보존하며, ECS가 보고하는 실행 digest와 빌드 묶음의 digest가 다르면 배포를 통과시키지 않는다. 이 도구는 digest를 바꿔 통과시키거나 호스트에서 재빌드하지 않는다.

## v2 FE·BE 이미지 묶음

기존 HTTP 배포 API에 다음 세 필드를 함께 추가한다. `image`는 frontend 별칭이며 v2에서 `commit`은 full40 SHA다. CLI의 단일 `--image` 경로는 v1 호환용으로 유지한다.

```json
{
  "run_id": "pipeline-42-stage-1",
  "cloud": "aws",
  "commit": "0123456789abcdef0123456789abcdef01234567",
  "image": "<frontend 저장소>@sha256:<64hex>",
  "images": {
    "frontend": "<frontend 저장소>@sha256:<64hex>",
    "backend": "<backend 저장소>@sha256:<64hex>"
  },
  "image_digests": {"frontend": "sha256:<64hex>", "backend": "sha256:<64hex>"},
  "bundle_sha256": "<64hex>",
  "lease_holder": "pipeline-42"
}
```

위 꺾쇠 값은 설명용이다. `bundle_sha256`는 `{schema:2, commit, platform:"linux/amd64", image_digests}`의 키 정렬·공백 없는 UTF-8 JSON SHA-256이다. 저장소 주소는 해시에서 제외하지만 각 요청의 ref suffix·digest를 대조한다. 누락·null·빈 묶음, 추가 서비스, 태그 이미지, FE 별칭 불일치, 짧은 commit, 잘못된 해시는422이며 v1으로 후퇴하지 않는다. 두 이미지 모두 서버의 허용 저장소 정책을 통과해야 한다. DB 연결·reset 대상은 서버 설정으로만 받는다.

Registry는 전체 요청(환경 변수·스캐너·교체·점유 설정 포함)과 서버 target의 fingerprint를 배포 기록에 저장한다. 같은 run_id의 불일치는409이며 재시작 후에도 동일하다. 이전 형식 기록에 fingerprint가 없으면 입력 동일성을 입증할 수 없으므로409다. 다른 HOLDER의 v2 점유는 만료만으로 빼앗지 못한다. 기존 소유자의 명시적 해제 전에 이전 점검 종료를 확인해야 한다. 해제 API 자체는 그 종료를 관측하지 않는다.

대상 설정은 기존 설정에 다음 필드를 더한다. 이 문서는 설정 계약이며 Terraform 파일·실제 인프라를 변경하지 않는다.

| 필드 | AWS ECS | GCP Cloud Run |
|---|---|---|
| `container_name` | 한 task의 FE 이름 | ingress FE 이름 |
| `backend_container_name` | 같은 task의 BE 이름 | BE sidecar 이름 |
| 초기화 리소스 | `reset_task_family` | 기존 `reset_job` |
| `reset_container_name` | 기본 `reset` | 기본 `reset` |
| `expected_database_major` |18만 허용 |18만 허용 |
| `database_name` | 예: `board_staging` | 예: `board_staging` |
| `schema_prepared` | 기본 false | 기본 false |

`environment=staging`과 BE·reset·`database_name` 설정, 아래 S06 관측 훅 없이 v2를 요청하면 클라우드 변경 전에 거부한다. 최초 회차는 reset이 스키마를 준비하고 실제 구조화 출력을 확인한 뒤 앱을 배포한다. 같은 Provider 프로세스의 이후 회차는 reset과 새 배포 준비를 병렬로 실행한다. `schema_prepared=true`는 서버 운영자가 이미 준비한 스키마에만 사용한다. 기본 false이므로 재시작 후에는 다시 순차 준비한다.

- AWS: 기존 task definition의 FE·BE 이미지를 한 revision으로 교체한다. ALB의 대상은 FE3000만 허용하며 FE에는 loopback BE8080, BE에는 postgres storage를 설정한다. 실제 새 running task의 **두 `image`와 `imageDigest`**, 두 컨테이너 실행 상태, 새 task의 배타적인 ALB healthy를 확인한다.
- GCP: FE ingress3000·BE sidecar8080의 이미지를 함께 교체한다. FE가 BE startup probe를 기다리게 하며, 기존 SQL Proxy 등 보조 컨테이너의 이미지·비밀 참조는 유지한다. 서비스의 latestCreated/latestReady 일치·Ready·실제 trafficStatuses의 명시적 revision100%와 해당 revision의 두 pinned image를 확인한다. 설정의 `traffic`만으로 성공 처리하지 않는다.
- 둘 다 FE URL의 `/api/health`를 통해 BE의 `status=ok`, `environment=staging`, `storage=postgres`, `version=요청 full40`, `cloudProvider=요청 cloud`, 정수형 `databaseVersionNum`18xxxx를 관측한다. 이 헬스 진단은 클라우드 실행 이미지 관측을 대체하지 않는다. 도구 실행 위치에서 이 URL에 접근할 수 있어야 한다.

초기화는 동일한 BE 이미지의 `node scripts/reset-db.mjs`를 사용한다. 기존 reset 정의에 `APP_ENV=staging`, `STORAGE_BACKEND=postgres`, 서버 `database_name`과 동일한 `APP_RESET_DATABASE`·`PGDATABASE`, 전용 DB 자격의 **참조**가 있어야 한다. 요청 env를 reset에 복사하지 않는다. 앱 이미지에 이 명령·migration이 포함돼 있어야 하며 연결한 DB 식별·버전·트랜잭션은 Node reset 담당의 계약이다.

AWS는 서비스의 staging awsvpc 네트워크에서 전용 task를 실행한다. 정확한 task/revision, reset 컨테이너 exit0·실제 BE imageDigest를 확인하고 그 task ID의 awslogs stream을 조회한다. GCP는 Job의 reset 컨테이너만 BE 이미지·Node 명령으로 갱신하고, 실행 성공·정확한 execution의 BE 이미지와 해당 execution의 stdout을 확인한다. 실행 로그에서 단일 JSON `{ "remaining_rows": 0, "database_version_num": 180003 }`를 읽어야 한다. 정수0·DB18 관측만 허용하며 성공 카운트나 `남은 행 0` 문자열만으로는 통과하지 않는다. Proxy 등의 로그·이미지를 앱 것으로 덮어쓰지 않는다.

v2 성공 응답은 기존 필드와 `images`, `image_digests`, `bundle_sha256`, `data_reset_remaining`, `databaseVersionNum`, `database_version_num`를 포함한다. 두 버전 필드 값은 동일하며 파이프라인은 snake_case를 사용할 수 있다. `data_reset` 단계의 `…: 남은 행 0`·`image_digest` 이름은 유지한다. Registry는 누락·불일치·bool을 포함한 잘못된 구조화 증거를 거부하고 failed로 남긴다. v1 요청·구조화 증거 없는 기존 Outcome은 호환 경로를 유지한다.

**S06 라이브러리 계약·담당:** 두 Provider 생성자의 `safety_check`는 `async (req: DeployRequest, revision: str | None) -> ScanSafety` 관측 훅이다. `ScanSafety`·타입 별칭 `SafetyCheck`는 `delta_staging.aws_ecs`에서 가져온다. 훅이 없으면 실제 v2 Provider는 요청을 거부한다. reset/서비스 변경 전에는 `revision=None`으로 이전 DAST 종료·쓰기 drain을 확인한다. 새 task/revision 전환·두 이미지 검증·DB18 헬스가 끝난 뒤에는 정확한 revision으로 다시 호출하여 검사 직전 실제 행0을 확인한다. 훅 오류·증거 누락·다른 회차·행1/false 등은 ready를 차단한다. `ScanSafety`에 다음 값을 반환해야 한다.

| 관측 필드 | 수집 책임·조건 |
|---|---|
| `run_id`, `lease_holder`, `cloud`, `database_name` | 파이프라인 호출 어댑터. 현재 회차·HOLDER·target과 일치해야 한다 |
| `previous_dast_complete=True` | 파이프라인·DAST 담당. 이전 점검 terminal뿐 아니라 브라우저·진행 중 요청 종료까지 확인. 최초 회차도 이전 작업 부재를 확인 |
| `writes_drained=True` | 배포 호출 어댑터·기존 앱 연결 담당. 교체 전 쓰기 중단·진행 중 쓰기 종료를 관측. ALB의 healthy 해제나 lease 만료를 drain 완료로 간주하지 않음 |
| `revision`, `remaining_rows=0` | 최종 호출 시 필수. 파이프라인의 DAST 직전 게이트가 같은 staging DB를 **읽기 전용 재조회**하고, 현재 새 task/revision과 연결해 반환. reset 당시0이나 요청값을 복사하지 않음 |

`data_reset_remaining=0`은 reset 트랜잭션 당시 관측일 뿐이다. 최종 훅은 reset을 다시 실행하여 남은 요청의 쓰기를 지우는 방식으로 대체하면 안 된다. 성공한 실제 Provider는 `s06_verified=true`, `pre_dast_remaining=0`을 반환하며 Registry가 전파한다. S06 증거 없는 호환/주입 Provider 결과는 `s06_verified=false`, `handoff_required=[previous_dast_shutdown,write_drain,pre_dast_zero_rows]`로 남으므로 파이프라인은 ready만 보아 통과시키지 않는다.

이 변경은 라이브러리의 필수 관측 경계까지다. **관측 훅을 실제 파이프라인·플랫폼 API·DAST 세션/DB 읽기 경로에 연결하는 작업은 후속 담당 범위다.** 기존 HTTP 서버의 Provider 조립에는 훅을 임의로 채우지 않았으므로, 현재 그 조립 경로의 실제 v2 호출은 연결 전 차단된다. S06 실환경 완료나 지연 POST 반례 통과를 주장하지 않는다. reset timeout·Python 작업 취소는 원격 task/Job 종료 증거가 아니므로 원격 생존 여부를 확인한 뒤 다음 회차를 허용해야 한다. 최종 훅 이후 DAST 시작까지 쓰기 차단·점유를 유지하는 것도 파이프라인 호출자의 책임이다.

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
- v2 reset: 지정 reset family의 `ecs:RegisterTaskDefinition`·`ecs:RunTask`·`ecs:DescribeTaskDefinition`·`ecs:DescribeTasks`, 정확한 task/execution role의 `iam:PassRole`, reset 로그 stream의 `logs:GetLogEvents`. awslogs의 그룹·stream prefix와 같은 리전 설정이 필요하다. 앱 계정의 DB DDL/TRUNCATE 권한을 확대하지 않는다.
- `elasticloadbalancing:DescribeTargetHealth`, 주소 조회용 `elasticloadbalancing:DescribeTargetGroups`·`elasticloadbalancing:DescribeLoadBalancers`
- `iam:PassRole`(실행 역할·태스크 역할 두 개만)
- 공개 모드: ALB 보안 그룹의 `ec2:DescribeSecurityGroupRules`, `ec2:AuthorizeSecurityGroupIngress`, `ec2:RevokeSecurityGroupIngress`, `ec2:CreateTags`

실제 배포 환경에서는 장기 키 대신 OIDC(웹 아이덴티티)나 태스크 역할로 받는다. boto3 기본 체인이 둘 다 읽으므로 코드 변경은 없다.

## 필요한 GCP 권한(배포 주체)

GCP 자격 증명은 ADC(`gcloud auth application-default login`, 서비스 계정, 워크로드 아이덴티티 페더레이션)를 쓴다.

- 주소 조회: `compute.addresses.get`
- Cloud Run: `run.services.get`, `run.services.update`, `run.revisions.get`, `run.operations.get`, 런타임 서비스 계정의 `iam.serviceAccounts.actAs`
- Artifact Registry(태그 이미지): `artifactregistry.tags.get`
- v2 reset: 기존 Job 조회·갱신·실행, operation·execution 조회와 실행 stdout의 `logging.logEntries.list`. 앱 런타임과 분리된 reset DB 자격·PG18·필요한 Proxy 연결은 인프라 담당이 준비한다.
- 동적 스캐너 접근: Cloud Armor 정책의 `compute.securityPolicies.get`·`compute.securityPolicies.update`, `compute.globalOperations.get`

## 검증 범위와 독립 시험 인계

이번 구현은 로컬 계약·기존 회귀 시험을 대상으로 하며 실제 클라우드 호출·apply·배포는 실행하지 않았다. 기존66개와 새 `test_bundle_contract.py`47개가 통과해도 아래 Provider v2 경로의 독립 시험 또는 실환경 증거를 대신하지 않는다. 이 구현 역할은 tests 파일을 작성·수정하지 않는다.

독립 `test_bundle_providers.py` fixture는 정식 target 필드·관측 훅을 함께 사용해야 한다. 초기 입구 거부로 우연히 통과하는 부정 시험은 reset·runtime 경로의 검증으로 집계하지 않는다. `database_name="synthetic_staging"` 같은 합성 이름을 target·BE 환경·reset 환경·ScanSafety에 동일하게 넣고, revision 관측 fixture에는 정확한 `DELTA_RUN_ID`·BE `PGDATABASE`를 포함해야 한다. 정상 S06 훅 연결 후 성공 응답은 `s06_verified=true`, `pre_dast_remaining=0`, 빈 `handoff_required`여야 한다. 훅 없는 ready·handoff만 남긴 ready는 실제 v2 Provider의 성공 수용 조건이 아니다.

| 독립 작성자가 추가할 시험 | 구체적인 반례·확인할 결과 |
|---|---|
| AWS 실행 묶음 | BE 미실행/누락·잘못된 BE digest/ref·이전 task 혼입·FE 외 ALB binding이면 ready 금지. 한 task definition에 두 이미지·환경이 함께 갱신되는지 확인 |
| AWS reset | exit 실패/누락·실제 BE digest 불일치·잘못된 DB 설정·성공 exit이나 출력 없음·로그 페이지/지연·행1/음수/false/문자열0·DB16·중복 JSON이면 실패 |
| GCP 실행 묶음 | 실제 serving revision에 BE 누락/다른 pinned image, traffic50%, LATEST 설정만 있고 관측 revision 없음이면 실패. Proxy 이미지 보존·BE 외부 port 없음·FE 의존성 확인 |
| GCP reset | 성공 카운트만 존재, 다른 execution/BE 이미지, 구조화 로그 누락/중복·잔존 행·잘못된 DB 버전이면 실패. Job Proxy 이미지와 비밀 참조 보존 |
| 관측 헬스 | FE200·BE실패, memory fallback, 다른 cloud/commit/environment, DB16/문자열/누락이면 실패. 두 DB 버전 응답 필드 일치 |
| 순서·점유·로그 | 최초 schema 준비가 서비스 활성화보다 앞서고, 준비 뒤 reset·배포 병렬. 만료된 v2 다른 HOLDER·잘못된 해제자 차단. API/CLI/클라우드 예외에 합성 비밀 원문 없음 |
| S06 라이브러리·실환경 반례 | 훅 없음은 클라우드 호출 전 거부. 종료/drain=false·다른 run/holder/cloud/DB·최종 다른 revision·행1/false/누락이면 실패. 훅 호출 순서와 성공 시 s06_verified·pre_dast_remaining 전파 확인. 실제 지연 POST·쓰기 drain·DB 읽기 관측 연결은 후속 담당 |

- 배포 기록은 프로세스 1개 전제의 JSON 파일이다. 재시작하면 진행 중이던 배포는 `failed`로 표시한다.
