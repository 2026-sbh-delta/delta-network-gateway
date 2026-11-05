# AWS Workload와 Network/Security의 GuardDuty 활성화

기준일: 2026-11-05 (Asia/Seoul).

## 승인과 현재 상태

- 사용자 요청: `terraform/workload`와 `terraform/network-security`의 GuardDuty 활성화.
- Ideation: 기존 workload detector를 보존하며 Terraform에 등록하고, network-security에는 기본 탐지를 활성화하는 안을 2026-11-05 사용자 `ㄱㄱ`으로 승인받았다.
- Inception과 U1~U3의 Design/Implementation Plan 및 실제 적용: 2026-11-05 사용자 두 번째 `ㄱㄱ`으로 승인받았다.
- 완료: U1~U3 구현/Test/Review와 실제 적용 및 API/state 보존 검증을 완료했다. 서울 리전의 두 detector 모두 ENABLED이며 두 root의 최종 실제 Plan은 No changes/종료0이다. 사용자 요청으로 커밋과 푸시를 진행하며 게시 상태는 Git 이력을 따른다.
- 적용 결과: workload의 기존 ID와 모든 보호 설정을 보존해 등록했고, 중앙은 새 detector에서 기본 탐지만 활성화했다. 중앙의 선택 기능과 자동 에이전트 관리는 모두 DISABLED다.
- 보존 확인: 기존 managed 자원 workload77개/중앙50개의 ID, 기존 출력10개/17개 및 VPC Endpoint5개/0개를 보존했다. 각 root는 detector1개와 feature7개를 관리한다.
- 초기 중앙 준비 Apply는 S3/EKS API 400으로 일부 실패했으며, 중지 상태를 유지한 일괄 OFF 요청과 새 Plan/Apply로 복구했다. 최초 실패와 최종 성공을 아래 기록에서 구분한다.
- 사전 조회 기록: workload는 이미 ENABLED, 중앙 detector0개였으며 두 대상 root의 원격 state에는 GuardDuty 리소스가 없었다. 현재 상태와 구분한다.

## Ideation

- 문제 정의: 두 계정의 GuardDuty 활성 상태가 다르고, 현재 저장소의 Terraform 정의와 대상 root의 state에는 GuardDuty가 없다.
- 사용자: Delta 팀의 보안 및 인프라 운영자.
- 성공 기준: 서울 리전의 두 계정에서 GuardDuty가 `ENABLED`이고 Terraform 관리 상태가 일치한다. 기존 workload 보호 설정을 보존하고 두 root의 최종 실제 Plan에서 No changes를 확인한다.
- Scope: 기존 workload detector의 Terraform 등록, network-security의 기본 탐지 활성화, 보호 기능의 명시적 관리, 검증과 README/Runbook 갱신.
- Non-goals: 다른 리전, Organizations 또는 관리자 위임 변경, 계정 간 findings 통합, 알림/자동 대응 추가, 보안 에이전트 설치, 공격이나 sample finding 생성, 앱 재시작. Git 게시는 초기 범위에서 제외했으나 사용자가 후속으로 커밋과 푸시를 요청했다.

## 확인한 대상과 보존할 설정

GuardDuty는 VPC별 자원이 아니라 계정과 리전 단위의 서비스다. workload 계정의 다른 VPC도 같은 detector를 사용하므로 기존 detector를 새로 만들거나 중지하지 않는다. 두 계정은 독립적으로 운영하며 이번 요청의 network-security 이름이 관리자 위임이나 findings 통합을 뜻하지 않는다.

| Root | Profile | 계정 | 리전 | 실제 사전 조회 |
| --- | --- | --- | --- | --- |
| `terraform/workload` | `sbh-delta-workload` | `616723161368` | `ap-northeast-2` | detector `d4d0cf9edb23ea3875d2c9248d0844bc`, ENABLED, 알림 갱신 주기 SIX_HOURS, 태그 없음 |
| `terraform/network-security` | `sbh-delta-security` | `482840608926` | `ap-northeast-2` | detector 0개 |

| 보호 기능 | Workload에서 보존할 상태 | Network/Security의 최종 상태 |
| --- | --- | --- |
| 기본 CloudTrail 관리 이벤트, VPC Flow, DNS 탐지 | ENABLED | ENABLED |
| S3_DATA_EVENTS | ENABLED | DISABLED |
| EKS_AUDIT_LOGS | ENABLED | DISABLED |
| EBS_MALWARE_PROTECTION | ENABLED | DISABLED |
| RDS_LOGIN_EVENTS | ENABLED | DISABLED |
| RDS_DATA_RISK 추가 설정 | ENABLED | DISABLED |
| LAMBDA_NETWORK_LOGS | ENABLED | DISABLED |
| RUNTIME_MONITORING | DISABLED | DISABLED |
| Runtime의 EKS/ECS Fargate/EC2 에이전트 자동 관리 | 모두 DISABLED | 모두 DISABLED |
| AI_PROTECTION | DISABLED | DISABLED |

Workload의 과거 `EKS_RUNTIME_MONITORING` 응답도 DISABLED다. 새 구성에서는 이를 별도 feature 리소스로 추가하지 않고 `RUNTIME_MONITORING`을 사용한다. 두 runtime feature를 함께 구성하지 않는다. `AI_ANALYST`는 사전 조회의 서울 detector 응답에 없었다. 신규 중앙 detector를 중지 상태로 만든 후 응답 전체를 확인하며, 위 목록 밖의 지원되는 선택 기능이 나타나면 DISABLED로 명시하고 검증한 뒤 기본 탐지를 켠다. 다른 리전의 지원 여부를 추정해 API에 전달하지 않는다.

## Inception

### Functional Requirements

1. 각 root는 자신의 Provider와 기존 backend/state에서 detector 1개를 관리한다. 기존 `allowed_account_ids` 검증을 유지한다.
2. Workload detector는 ID와 활성 상태를 보존하며 Import한다. 기존 보호 기능과 RDS 추가 설정, Runtime 에이전트 관리 상태 및 SIX_HOURS 주기를 유지한다. 기존 공통 Provider 태그를 추가하는 변경은 허용 범위다.
3. Network/Security detector는 먼저 `enable=false`로 생성한다. 선택 기능과 자동 에이전트 관리를 명시적으로 끄고 API에서 실제 상태를 확인한 다음, 두 번째 Plan/Apply에서 `enable=true`로 바꾼다.
4. Detector는 `aws_guardduty_detector`, 선택 기능은 `aws_guardduty_detector_feature`로 관리한다. deprecated `datasources`와 feature 리소스를 혼용하지 않는다.
5. 두 root에 `guardduty_enabled` bool, `guardduty_finding_publishing_frequency` string, `guardduty_features` map(object) 입력과 비밀값 없는 `guardduty` 출력을 추가한다. 기본 활성 상태는 true, 주기는 SIX_HOURS로 명시한다. 기능별 기본값은 위 보존/최종 상태 표를 따른다.
6. Feature object는 `status`와 `additional_configuration`을 관리한다. 상태값과 기능별 추가 설정 조합을 검증하고, 필요한 기능 키를 누락해 Terraform 관리만 해제하는 구성을 막는다.
7. Detector에는 `prevent_destroy`를 적용하고 `guardduty_enabled=false`는 삭제가 아닌 일시 중지를 뜻하도록 한다. 입력 토글로 detector의 존재 자체를 제거하지 않는다.
8. 기존 VPC, TGW, VPN, RAM, 앱/DB, 로그, backend key와 현재 ANF disabled 상태를 유지한다. 실제 Plan의 모든 변경 주소가 승인된 GuardDuty 자원과 출력인지 검토한다.

### Non-Functional Requirements

- Terraform 1.16.4와 AWS Provider 6.71.0, 두 root의 독립 실행 및 state 경계를 유지한다.
- 자격 증명, raw state, Plan 및 실제 입력은 Git에 넣지 않는다. 검증 산출물은 ignored 경로에 보관하고 비밀값을 출력하지 않는다.
- 최초 중앙 적용은 탐지를 중지한 상태에서 진행해 선택 기능이 기본 활성화된 채 분석을 시작하지 않게 한다. 기능 설정 도중 실패하면 중지 상태를 유지하며 정상화 후에만 최종 활성화를 진행한다.
- GuardDuty가 생성하는 필수 서비스 연결 역할은 허용하되, Runtime 에이전트 자동 설치와 관련 VPC Endpoint 추가는 활성화하지 않는다.
- 변경한 root README의 전체 입력/출력 및 복합 속성 표를 함께 갱신한다.
- mock 테스트, 실제 Plan, Import, Apply, API readback, 최종 Plan과 Git 게시를 구분한다. 실제 공격 탐지 성능을 검증한 것으로 표시하지 않는다.

### Architecture

```text
terraform/workload          -> 기존 workload state
  계정 616723161368 / 서울   -> 기존 detector Import + 기존 보호 기능 유지

terraform/network-security  -> 기존 중앙 state
  계정 482840608926 / 서울   -> 중지 상태 생성 -> 선택 기능 OFF 확인 -> 기본 탐지 ON
```

두 detector 사이에 관리자/멤버 연결이나 findings 전달 경로를 추가하지 않는다. 기본 탐지를 위해 새 CloudTrail trail, VPC Flow Log 또는 DNS query logging 자원을 만들 필요는 없다. GuardDuty는 독립된 데이터 스트림을 분석한다. [AWS 기본 데이터 소스](https://docs.aws.amazon.com/guardduty/latest/ug/guardduty_data-sources.html)

### Unit of Work, Design와 Implementation Plan

| Unit | Design | Implementation Plan | Test/Review 기준 | 승인 |
| --- | --- | --- | --- | --- |
| U1. 두 root의 Terraform 구성 | Detector 존재를 유지하고 기본 탐지와 선택 기능을 분리 | `guardduty.tf`, 입력/검증, 출력, mock 시나리오, README/Runbook 작성. 기존 workload 기능과 중앙의 모든 선택 기능 OFF를 각각 명시 | 기본 활성/일시 중지, 기능 보존, 잘못된 상태/기능/추가 설정 및 필수 키 누락 거부. 기존 회귀와 fmt/init/validate 완료. Terraform mock103개/Python14개 통과, 정적 Review 완료 | 2026-11-05 승인/구현 완료 |
| U2. 기존 detector 등록과 중앙 활성화 | Workload 무중단 등록, 중앙은 중지/활성 두 단계 | STS/API/state 재조회 후 기존 detector만 Import. Feature 리소스는 동일 설정을 Update하는 방식으로 등록. Workload 저장 Plan을 검토/Apply하고 보존 확인. 중앙은 중지 상태에서 개별 S3/EKS 요청 거부를 일괄 OFF 요청으로 복구한 뒤 새 Plan/Apply로 등록 완료. OFF readback 후 별도 Plan/Apply로 기본 탐지 활성화 | Workload ID와 모든 보호 상태 보존. 중앙 detector 1개/기본 탐지 ENABLED/선택 기능 OFF. 기존 네트워크와 앱 변경/교체/삭제 없음 | 2026-11-05 승인/실제 적용 및 Review 완료 |
| U3. 최종 검증과 운영 기록 | 실제 AWS 상태와 Terraform의 최종 수렴 확인 | 양쪽 list/get-detector, 출력과 최종 실제 Plan 확인. 중지/재개와 관리 해제 및 초기 S3/EKS 복구 절차를 Runbook에 기록하고 본 문서 및 docs/README 갱신 | 양쪽 ENABLED, 기대한 기능/추가 설정 일치, 두 최종 Plan No changes/종료0. 실제 실행 명령과 결과 기록 | 2026-11-05 승인/검증 및 Review 완료 |

Provider 6.71.0의 detector 생성 API 호출은 `Features`를 직접 지정하지 않는다. 또한 feature 리소스는 Create 시 UpdateDetector를 호출하고 Importer는 제공하지 않으므로 기존 detector와 feature를 같은 방식으로 Import하지 않는다. Feature 리소스를 삭제해도 AWS의 기능은 꺼지지 않아, 중단은 반드시 status 변경으로 관리한다. [Provider detector 구현](https://github.com/hashicorp/terraform-provider-aws/blob/v6.71.0/internal/service/guardduty/detector.go), [Provider feature 구현](https://github.com/hashicorp/terraform-provider-aws/blob/v6.71.0/internal/service/guardduty/detector_feature.go), [feature 문서](https://github.com/hashicorp/terraform-provider-aws/blob/v6.71.0/website/docs/r/guardduty_detector_feature.html.markdown)

### Acceptance Criteria

1. 두 계정의 서울 리전에 detector가 각각 1개이며 Status는 ENABLED다. CloudTrail/DNS/Flow 기본 탐지도 모두 ENABLED다.
2. Workload detector ID와 기존 보호 기능/추가 설정, SIX_HOURS 주기를 보존한다. 기존 findings를 삭제하는 API를 호출하지 않는다.
3. Network/Security의 지원되는 모든 선택 기능 및 자동 에이전트 관리가 DISABLED다. 활성화 직전과 직후 전체 API 응답을 대조한다.
4. GuardDuty 이외 기존 관리 자원의 변경/교체/삭제가 없다. 두 root의 최종 실제 Plan이 No changes/종료0이다.
5. 두 검증 스크립트의 기존 회귀 및 새 mock 시나리오가 실제 통과한다. 기능 입력/출력 문서와 실제 코드가 일치한다.

## Operation 계획

- Deployment: U1 검증 후 최신 state와 API를 재확인한다. Workload 등록을 먼저 완료하고, 중앙은 중지 상태로 준비한 후 활성화한다. 실제 Apply마다 새 저장 Plan과 JSON을 검토한다. GuardDuty 설정 갱신은 순차 실행해 동시 UpdateDetector 호출을 피한다.
- Observability: 계정/리전/detector ID, Status, DataSources, Features와 AdditionalConfiguration, 게시 주기 및 Terraform 출력을 비교한다. Findings 0건을 이상 없음이나 탐지 검증 성공으로 해석하지 않는다.
- Rollback: 신규 중앙 detector에 문제가 생기면 `guardduty_enabled=false`로 일시 중지하고 detector와 findings는 유지한다. Workload는 시작 전 보호 상태를 기준으로 복구하며 원래 켜져 있던 탐지를 중지하지 않는다. Terraform 관리 등록의 원복이 필요하면 리소스 삭제와 state 관리 해제를 구분한다.
- Runbook: 두 root의 조회, 기존 detector 등록, 중앙의 두 단계 적용, 중지/재개, feature 제거와 실제 비활성화의 차이를 기록한다. Detector 삭제는 findings와 설정을 잃으므로 이번 작업의 원복 절차로 사용하지 않는다. [AWS 중지/비활성화 동작](https://docs.aws.amazon.com/guardduty/latest/ug/guardduty_suspend-disable.html)

## 확인 기록

| 날짜 | 실행한 명령 또는 확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-05 | `git status --short --branch`, AGENTS.md, docs/README, 관련 AWS AI-DLC 및 두 root의 Provider/입력/README/검증 스크립트 읽기 | 시작 시 main 작업 트리 clean. 두 계정/서울 리전/고정 버전과 단계별 승인 규칙 확인 |
| 2026-11-05 | `aws sts get-caller-identity --profile sbh-delta-workload` 및 `--profile sbh-delta-security` | 네트워크 허용 실행에서 각각 616723161368 / 482840608926 확인. 최초 sandbox 실행은 endpoint 연결 실패였으며 계정 상태 실패로 해석하지 않음 |
| 2026-11-05 | `aws guardduty list-detectors --profile <각 profile> --region ap-northeast-2` | Workload detector 1개, 중앙 0개. 읽기 전용 |
| 2026-11-05 | `aws guardduty get-detector --profile sbh-delta-workload --region ap-northeast-2 --detector-id d4d0cf9edb23ea3875d2c9248d0844bc` | ENABLED, SIX_HOURS, 기본 3종/선택 5종/RDS_DATA_RISK ENABLED, Runtime/에이전트 관리/AI_PROTECTION DISABLED, 태그 없음 |
| 2026-11-05 | `aws guardduty get-administrator-account --profile sbh-delta-workload --region ap-northeast-2 --detector-id d4d0cf9edb23ea3875d2c9248d0844bc` | 종료0, 관리자 연결 응답 없음 |
| 2026-11-05 | `AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload state list` / `AWS_PROFILE=sbh-delta-security terraform -chdir=terraform/network-security state list` | 각각 83개/56개 주소. 두 state 모두 guardduty 주소0개. state 변경 없음 |
| 2026-11-05 | `terraform -chdir=terraform/workload providers schema -json` | sandbox 기본 인증의 네트워크 갱신 실패로 schema 확인 미완료. 검증 성공 아님. 이후 고정 버전 Provider의 공식 소스와 문서를 직접 확인 |
| 2026-11-05 | Provider 6.71.0의 공식 detector/feature 소스와 AWS create-detector/중지 동작 문서 확인 | 신규 생성 시 선택 기능 기본 활성화 가능, detector 직접 Features 설정 부재, feature Update 방식 및 Importer 부재, 삭제와 일시 중지의 차이 확인 |
| 2026-11-05 | `git diff --check` 및 Python으로 새 문서와 docs/README의 로컬 링크/추가 내용 금지 문자/새 문서 공백 확인 | 문서 검사 통과. GuardDuty 코드나 인프라 테스트 결과는 아님 |

Inception과 U1~U3 구현 및 실제 적용 승인을 받아 Construction과 Operation을 완료했다. 단계별 실제 실행 결과는 아래 기록을 따른다.

## Construction 확인 기록

| 날짜 | 실행한 명령 또는 확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-05 | `DELTA_KEEP_VALIDATION=1 bash terraform/workload/scripts/validate.sh` | fmt, 격리 init/validate와 root mock39개/child mock9개 및 Python3개 통과. 새 GuardDuty 시나리오11개 포함. 실제 AWS API/Plan/Apply는 아님 |
| 2026-11-05 | `DELTA_KEEP_VALIDATION=1 bash terraform/network-security/scripts/validate.sh` | fmt, 격리 init/validate와 mock55개/Python11개 통과. 새 GuardDuty 시나리오11개 포함. 실제 AWS API/Plan/Apply는 아님 |
| 2026-11-05 | `terraform fmt -`로 두 terraform.tfvars.example의 입력 구문/형식 확인 | stdin 방식 통과. .example 경로를 fmt에 직접 전달한 최초 시도는 지원하지 않는 확장자로 거부됐으며 성공으로 기록하지 않음 |
| 2026-11-05 | 적용 직전 STS/list/get-detector, `terraform show -json`, `ec2 describe-vpc-endpoints` | 두 예상 계정 일치. 기존 workload detector의 활성/기능 설정 유지, 중앙 detector0개. 기존 managed ID는 workload77개/중앙50개, VPC Endpoint는 각각5개/0개로 보존 기준 기록 |
| 2026-11-05 | `AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload import -input=false -no-color -lock-timeout=2m aws_guardduty_detector.account d4d0cf9edb23ea3875d2c9248d0844bc` | 종료0. 기존 detector를 state에 등록. AWS detector 삭제/재생성/중지 없음 |
| 2026-11-05 | Workload `terraform plan -parallelism=1 -detailed-exitcode -out=.../register.tfplan`, `terraform show -json` 검토 | 종료2, feature 등록7개/detector 태그 갱신1개/삭제0개. 기존 활성/보호 설정 및 다른 자원 변경 없음 |
| 2026-11-05 | Workload `terraform apply -input=false -parallelism=1 .../register.tfplan`, 실제 API/state 비교 | 종료0, 7 added/1 changed/0 destroyed. 기존 detector ID와 모든 기능/추가 설정, 기존 managed77개 ID/출력10개 및 Endpoint5개 보존 |
| 2026-11-05 | Workload 최종 `terraform plan -parallelism=1 -detailed-exitcode` | No changes/종료0 |
| 2026-11-05 | 중앙 `terraform plan -var=guardduty_enabled=false -parallelism=1 -detailed-exitcode -out=.../prepare.tfplan`, JSON 검토 후 Apply | Plan은 GuardDuty8개 추가만 존재. Apply 종료1: 중지 detector와 feature5개 등록 완료, S3/EKS 개별 DISABLED 요청은 BadRequestException 400. 최초 Apply는 성공이 아님 |
| 2026-11-05 | 중앙 `aws guardduty get-detector --detector-id 7c8f1cc155f9404fb4ed73ec3e37208e` | Status DISABLED 확인. S3/EKS만 ENABLED 설정으로 남았으며 기본 탐지는 계속 중지. 나머지 선택 기능/추가 설정은 모두 DISABLED |
| 2026-11-05 | 중앙 `aws guardduty update-detector`에 같은 ID, Enable=false, S3_DATA_EVENTS/EKS_AUDIT_LOGS의 DISABLED를 한 요청으로 전달 | 종료0. 후속 API에서 detector DISABLED와 모든 선택 기능/추가 설정 DISABLED 확인. 서버 내부의 정확한 검증 조건은 미확인 |
| 2026-11-05 | 중앙 `terraform plan -var=guardduty_enabled=false -out=.../prepare-recovery.tfplan`, JSON 검토 및 Apply | Plan은 미등록 S3/EKS feature2개 추가뿐. Apply 종료0, 2 added/0 changed/0 destroyed. Detector와 기존 자원은 보존 |
| 2026-11-05 | 중앙 준비 완료 후 STS/get-detector, `terraform show -json` 및 VPC Endpoint 재조회 | Detector DISABLED, 선택 기능/추가 설정 전부 DISABLED와 Terraform 출력 일치. 기존 managed50개 ID/출력17개 및 Endpoint0개 보존 |
| 2026-11-05 | 중앙 기본 true의 `terraform plan -out=.../enable.tfplan` JSON 검토 및 `terraform apply -parallelism=1 .../enable.tfplan` | detector enable false→true 한 항목만 변경. Apply 종료0, 0 added/1 changed/0 destroyed |
| 2026-11-05 22:58 KST | 중앙 최종 STS/list/get-detector, `terraform show -json`, VPC Endpoint 및 기존 값 비교 | ENABLED, 기본 CloudTrail/DNS/Flow ENABLED, 모든 선택 기능/추가 설정 DISABLED. 새 ID `7c8f1cc155f9404fb4ed73ec3e37208e`와 Terraform 출력 일치. 기존 managed50개 ID/출력17개/Endpoint0개 보존 |
| 2026-11-05 | 중앙 최종 `terraform plan -parallelism=1 -detailed-exitcode` | No changes/종료0 |
| 2026-11-05 | 두 root `terraform fmt -check -recursive`, README의 전체 입력/출력 표 자동 대조 및 새 내용 금지 문자/로컬 링크/`git diff --check` 확인 | 형식/문서 검사 통과. Workload 입력20개/출력11개, 중앙 입력24개/출력19개를 모두 문서화 |

mock 이벤트는 `delta-workload-validation.0GVxIr`, `delta-network-validation.UMvqbF` 임시 디렉터리에 보존했다. 실제 검증 산출물은 각 root의 ignored `.validation/guardduty-20261105/`에 보관하며 raw state나 비밀값은 Git에 포함하지 않는다.

## Operation 실제 결과

| 대상 | 계정 | 서울 detector ID | 현재 기본 탐지 | 현재 선택 보호 |
| --- | --- | --- | --- | --- |
| Workload | 616723161368 | d4d0cf9edb23ea3875d2c9248d0844bc | ENABLED | 기존 S3/EKS Audit/EBS Malware/RDS/Lambda와 RDS_DATA_RISK 유지, Runtime/에이전트/AI Protection OFF |
| Network/Security | 482840608926 | 7c8f1cc155f9404fb4ed73ec3e37208e | ENABLED | 모두 OFF, 과거 EKS Runtime 응답과 모든 추가 설정도 OFF |

- Deployment: 위 저장 Plan과 API 복구 요청을 실제 적용했다. 중앙은 선택 기능 OFF를 API로 검증할 때까지 중지 상태를 유지했다. GuardDuty 외 자원의 변경/교체/삭제는 없었다.
- Observability: 계정/리전, detector 상태, 기본 데이터 소스, 모든 feature와 추가 설정, 게시 주기, Terraform 출력과 기존 ID/출력/Endpoint 보존을 대조했다. 각 root의 `after-verification.json`, `final-review.json`, `final-plan-exit.json`이 최종 증거다.
- Rollback/Runbook: [Workload](../../terraform/workload/RUNBOOK.md#guardduty-등록과-운영), [중앙](../../terraform/network-security/RUNBOOK.md#guardduty-등록과-운영)에 중지/재개와 중앙 S3/EKS 복구를 기록했다. 활성화 후 별도 일시 중지/재개 시험은 실행하지 않았고, mock과 최초 중지 준비 단계의 결과로 구분한다.
- 검증 범위: 실제 공격이나 sample finding 생성은 실행하지 않았다. 활성화와 설정 보존 검증이며 실제 위협 탐지 성능을 검증한 것은 아니다. 사용자 후속 요청으로 커밋과 푸시를 진행하며 게시 및 원격 CI 상태는 Git 이력을 따른다.
