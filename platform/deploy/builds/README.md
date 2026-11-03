# 고객 이미지 빌드 기반

U2 전용 CodeBuild·ECR·로그·IAM을 만든다. 고객 Dockerfile은 플랫폼 VM, GitHub의 Terraform CI, Jenkins 컨트롤러에서 실행하지 않는다. 기존 Jenkins 로컬 시연과 ECS 검사 도구는 변경하지 않는다.

## 구성과 제한

- 공개 GitHub 저장소의 고정 커밋 + 플랫폼이 관리하는 inline buildspec. 고객의 buildspec/Jenkinsfile은 읽지 않는다.
- on-demand `BUILD_GENERAL1_SMALL`, `aws/codebuild/standard:7.0`, 동시 실행 1개, 빌드 15분·AWS 대기 10분. 빌드하지 않을 때 상시 빌드 VM 비용은 없다. 이미지 저장·로그 등의 비용은 별도다.
- `linux/amd64` 단일 manifest, provenance/SBOM index 생성 비활성. U3는 `repository@sha256:…`을 받아 ECS의 실제 digest와 대조한다. 스캔 결과 판정은 이 단위에 포함하지 않는다.
- 전용 ECR immutable 태그 `run-<UUID>`. 자동 수명 삭제는 두지 않는다. 후속 배포·복구가 참조할 이미지이므로 임의 기간으로 지우지 않는다.
- 빌드 역할은 이 ECR의 이미지 작업과 이 로그 그룹의 쓰기만 가능하다. 플랫폼 역할은 이 프로젝트 시작·실행 조회·중단, digest·로그 조회만 추가한다. 빌드 역할은 플랫폼 SSM, IAM, Terraform state, 운영 DB에 접근할 수 없다.
- 고객 빌드는 VPC에 연결하지 않는다. Docker의 forwarded traffic에서 link-local/private IPv4를 차단한다. Docker build에는 host network, secret, SSH, 플랫폼 credentials를 전달하지 않는다. privileged Docker 빌드 자체가 임의의 적대적 고객에 대한 완전한 격리를 보장하지는 않는다. 공개 다중 고객 서비스로 확장하기 전 고객별 권한·registry·network 분리가 필요하다.
- 같은 플랫폼 작업 공간의 빌드는 같은 ECR 역할을 사용한다. 고객별 보안 경계를 완성했다고 표현하지 않는다.

## 초기 구성

이 root는 IAM 역할·정책을 포함하므로 **기존 GitHub Apply 역할로 실행하지 않는다.** 플랫폼/배포 CI 역할에 IAM 변경 권한을 확대하지 않고, SSO 관리자가 최초 기반을 구성한다. 플랫폼 앱의 이후 배포는 기존 GitHub OIDC를 사용한다. 코드 게시 전에 실행한 초기 검증은 AI-DLC에 따로 기록한다.

저장소 루트에서 실행한다. ignored `backend.hcl`에는 기존 state 버킷과 별도 key `platform/builds.tfstate`, 리전, 암호화·S3 lock을 지정한다. ignored `terraform.tfvars`에는 실제 `account_id`를 넣는다.

```bash
AWS_PROFILE=delta-platform terraform -chdir=platform/deploy/builds init -backend-config=backend.hcl
AWS_PROFILE=delta-platform terraform -chdir=platform/deploy/builds plan -out=builds.tfplan
AWS_PROFILE=delta-platform terraform -chdir=platform/deploy/builds apply builds.tfplan
AWS_PROFILE=delta-platform terraform -chdir=platform/deploy/builds output -json > platform/.runtime/build-outputs.json
```

`runtime_env`의 세 값을 기존 SSM `/delta-platform/runtime`의 `app_env`에 병합한다. 기존 로그인·DB 설정을 덮어쓰지 않는다. 값은 공개 설정이므로 GitHub secret이나 새 Access Key가 필요 없다. 동봉한 `configure-runtime.py`는 기존 값을 읽어 세 값만 병합하고, 값 대신 버전 번호만 출력한다.

```bash
uv run --project platform/deploy python platform/deploy/builds/configure-runtime.py \
  --profile delta-platform --account-id 616723161368 \
  --outputs platform/.runtime/build-outputs.json
```

플랫폼 Terraform의 app VM은 IMDSv2 hop limit 2로 변경된다. API/Worker의 Docker bridge에서 임시 VM 역할을 사용하기 위해 필요하다. NAT VM은 1을 유지한다. 이 VM에는 신뢰된 플랫폼 프로세스만 두며 고객 코드는 실행하지 않는다. 플랫폼 Infra Plan/Apply 후 Release로 앱을 갱신한다. 빌드 설정을 저장한 것만으로 실행 중인 앱 설정이 바뀌지는 않는다.

## 검증·실패 확인·복구

```bash
terraform -chdir=platform/deploy/builds init -backend=false -lockfile=readonly
terraform -chdir=platform/deploy/builds validate
terraform -chdir=platform/deploy/builds test
```

mock은 AWS에 자원을 만들지 않는다. 실제 검증은 별도다. API 빌드 접수 → Worker 시작 → CodeBuild 성공 → ECR digest → DB 결과를 대조한다. `AWS_PROFILE=delta-platform`을 사용하며 다른 계정의 default profile은 사용하지 않는다.

- `build_submission_uncertain`: 시작 요청 뒤 실행 ID 기록 전에 중단됐고 4분을 넘겼다. AWS 토큰이 5분 뒤 만료되므로 자동 재접수하지 않는다. 프로젝트 최근 빌드의 `DELTA_RUN_ID`로 기존 실행을 확인한다. 기존 실행 중이라면 종료/중단을 확인한 뒤 새 빌드를 요청한다.
- `attempts_exhausted`: 외부 API 또는 Worker가 반복 실패했다. 기록된 외부 실행 ID를 확인한다. Worker 중단만으로 CodeBuild가 취소되지는 않으며, AWS의 실행 시간 제한이 최종 비용 보호다.
- 이미지가 있어도 작업이 실패했다면 자동 배포하지 않는다. 성공한 결과의 digest만 U3에 전달한다.
- 빌드 로그는 로그인한 팀 작업 공간에 최근 100개 이벤트/20,000자까지만 텍스트로 표시한다. 고객이 출력한 내용 자체는 신뢰하지 않는다. 비밀값을 소스나 로그에 넣지 않는다.
- Release는 API/Worker를 멈추고 기존 DB를 S3에 백업한 뒤 migration을 실행한다. 단일 VM이므로 이 구간에는 짧은 중단이 있다. 백업 실패 시 schema 변경 전에 멈추며 운영자가 원인을 해결하고 재배포한다.
- 앱 롤백 시 DB `0002`를 유지하는 구버전 호환성을 확인한다. U1 앱은 스키마 버전 `0001`을 요구하므로 단순 이미지 롤백은 readiness 실패한다. DB 백업 복구 또는 `0002`를 이해하는 수정 릴리스를 사용한다. 빌드 기록이 있으면 migration downgrade는 거절한다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| account_id | string | 필수 | 생성 계정, provider 계정 제한 |
| region | string | ap-northeast-2 | 빌드·ECR·로그 리전 |
| name | string | delta-platform-builds | 프로젝트·ECR·빌드 역할 이름 |
| platform_role_name | string | delta-platform-app | 호출 권한을 붙일 기존 VM 역할 |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| project_name | CodeBuild 프로젝트 이름 |
| repository_url | 고객 이미지 전용 ECR 주소 |
| build_role_arn | 빌드 전용 제한 역할 ARN |
| runtime_env | SSM app_env에 병합할 공개 설정 맵 |

`runtime_env` 내부 속성:

| 속성명 | 역할 |
| --- | --- |
| PLATFORM_BUILD_PROJECT | Worker가 시작할 프로젝트 |
| PLATFORM_BUILD_REPOSITORY | digest 조회·결과 주소의 기준 ECR |
| PLATFORM_BUILD_REGION | AWS SDK 접속 리전 |
