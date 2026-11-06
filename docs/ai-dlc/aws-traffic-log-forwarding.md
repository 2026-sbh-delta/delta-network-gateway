# AWS Network Firewall 로그의 플랫폼 전달

기준일: 2026-11-06, Asia/Seoul.

## 현재 단계

- Ideation: 사용자 승인 완료.
- Inception: 2026-11-06 사용자 승인 완료.
- Construction: U1부터 U3까지 Design, Implementation Plan, 사용자 승인, Implementation, Test와 Review 완료.
- Operation: 실제 배포, API readback, 구독 전달 smoke, 최종 No changes 확인과 Runbook 갱신 완료.
- Terraform Plan과 Apply: 완료. 신규 4개, 기존 변경 1개, 삭제 0개.
- 커밋과 푸시 상태: Git 이력을 따른다.

## 확인된 전제

사용자가 다음 두 CloudWatch Logs 그룹이 중앙 보안 계정 `482840608926`의 서울 리전에 있다고 확인했다.

- `/aws/vendedlogs/network-firewall/delta-lab/flow`
- `/aws/vendedlogs/network-firewall/delta-lab/alert`

두 그룹은 AWS Network Firewall FLOW와 ALERT의 기존 원본 그룹이다. 사용자는 VPC Flow Log가 필요하지 않다고 정정하고, FLOW와 ALERT 모두 Network Firewall 로그로 전달하라고 지시했다.

새 기업 원본 그룹명은 다음으로 확정한다.

- `/delta/security/nfw-flow`
- `/delta/security/nfw-alert`

CloudWatch Logs 그룹은 이름을 직접 바꿀 수 없다. 새 그룹을 만들고 Network Firewall의 로그 대상을 전환해야 한다. 기존 그룹은 과거 로그 보존을 위해 삭제하거나 새 이름으로 오인해 재사용하지 않는다.

2026-11-06 `sbh-delta-security`와 `sbh-delta-workload`를 다시 인증하고 각각 `482840608926`, `616723161368`임을 STS로 확인했다. 적용 전 기존 두 그룹과 저장량, ANF 부재, `inspection_mode=disabled`, 플랫폼 Destination 정책, 수집 Lambda 상태와 중앙 그룹을 읽기 전용으로 조회했다. 플랫폼 Destination과 수집기는 두 계정을 허용했고 중앙 저장 대상은 `/delta-cp/central/aws`였다.

## Ideation

### 문제 정의

중앙 보안 계정의 Network Firewall FLOW와 ALERT가 기업 이름의 원본 그룹과 플랫폼 중앙 그룹으로 전달되지 않는다. 기존 AWS 관리형 이름을 기업 원본 이름으로 전환하되 과거 로그와 현재 ANF 비용 중단 상태를 보존해야 한다.

### 사용자

- Delta 중앙 네트워크와 보안 운영자
- `/delta-cp/central/aws`에서 계정별 트래픽 흐름과 방화벽 경보를 조회하는 플랫폼 운영자

### 성공 기준

1. Network Firewall FLOW의 새 저장 대상은 `/delta/security/nfw-flow`다.
2. Network Firewall ALERT의 새 저장 대상은 `/delta/security/nfw-alert`다.
3. 두 새 그룹에 이름 `delta-platform-central`, 빈 패턴, 지정된 논리 Destination의 구독 필터가 각각 존재한다.
4. 새 원본 이벤트가 있으면 원본 그룹과 `/delta-cp/central/aws`에서 출처, 발생 시각과 본문을 대조한다.
5. ANF는 `disabled` 상태를 유지하고 트래픽 경로, GuardDuty, GCP 수집기와 플랫폼 수신 리소스를 변경하지 않는다.
6. 기존 Network Firewall FLOW와 ALERT 그룹 및 과거 로그는 삭제하지 않는다.

### Scope

- 중앙 보안 계정 `482840608926`, `ap-northeast-2`의 Network Firewall FLOW와 ALERT
- Standard CloudWatch Logs 그룹 `/delta/security/nfw-flow`
- Standard CloudWatch Logs 그룹 `/delta/security/nfw-alert`
- 두 그룹의 교차 계정 구독 필터
- ANF가 다시 활성화될 때 FLOW와 ALERT가 새 그룹을 사용하도록 Terraform 연결 변경
- Terraform 입력, 출력, 모의 테스트, README, Runbook과 검증 기록

### Non-goals

- VPC Flow Log 생성 또는 변경
- ANF Firewall이나 Endpoint 재생성
- 기존 두 Network Firewall 그룹 또는 과거 로그 삭제
- 과거 이벤트를 새 그룹이나 플랫폼 중앙 그룹으로 다시 보내기
- GCP Sink, `/delta-cp/central/gcp`, 플랫폼 Destination, Kinesis, Lambda 또는 대시보드 변경
- Network Firewall 이벤트의 실제 발생을 검증하기 위한 검사 경로 원복

### 승인 기록

- 2026-11-06: 사용자가 기존 Network Firewall FLOW와 ALERT 그룹을 제시하고 기업 원본 그룹명 전환을 지시했다.
- 2026-11-06: 사용자가 VPC Flow Log는 필요 없으며 Network Firewall FLOW를 사용하라고 범위를 정정했다.
- 2026-11-06: FLOW 그룹은 `/delta/security/nfw-flow`로 정리했다. ALERT 그룹은 `/delta/security/nfw-alert`를 사용한다.
- 2026-11-06: 사용자 `가자잇` 지시로 아래 Inception과 U1부터 U3의 구현, 실제 Plan, Apply 및 smoke 검증을 승인했다.

## Inception

### Functional Requirements

1. Network Firewall FLOW의 CloudWatch Logs 대상은 `/delta/security/nfw-flow`로 전환한다.
2. Network Firewall ALERT의 CloudWatch Logs 대상은 `/delta/security/nfw-alert`로 전환한다.
3. 기존 FLOW와 ALERT 그룹은 Terraform 관리와 실제 로그를 유지하되 향후 Firewall logging configuration의 대상에서는 제외한다.
4. 두 새 그룹 각각에 `delta-platform-central` 구독 필터를 만든다. 필터 패턴은 `""`, 배포 방식은 `Random`, Destination은 `arn:aws:logs:ap-northeast-2:616723161368:destination:delta-platform-logs`다.
5. 송신 그룹과 Destination이 같은 리전인지 입력 검증으로 제한한다.
6. 전달 토글을 꺼도 새 원본 그룹과 Network Firewall 로그 대상 정의는 유지하고 두 구독만 제거할 수 있어야 한다.
7. 출력에는 두 새 원본 그룹, Destination, 전달 상태와 구독 이름을 포함하고 로그 본문이나 자격증명은 포함하지 않는다.

### Non-Functional Requirements

- 두 새 그룹은 기존 `log_retention_days`와 `retain_logs_on_destroy`를 사용한다. 기본값은 30일과 삭제 시 보존이다.
- 구독을 지원하는 `STANDARD` 로그 클래스를 명시한다.
- 기존 그룹은 Terraform 관리와 실제 로그를 유지한다. 이름 변경처럼 보이는 교체나 삭제 Plan을 만들지 않는다.
- 기존 Network Firewall 로그 전달 정책은 두 새 그룹을 추가 허용하고 기존 Resource와 조건을 유지한다.
- ANF `disabled` 입력을 유지한다. 새 로깅 기능 때문에 Firewall, Endpoint, ANF Route 또는 Firewall logging configuration을 생성하지 않는다.
- 플랫폼 수신 root는 조회만 하며 Plan이나 Apply를 실행하지 않는다.
- 실제 Plan은 신규 생성, 기존 정책의 제한된 갱신과 출력 변경을 구분해 검토한다.
- 구독은 생성 이후 새 이벤트만 전달한다. 과거 로그가 자동 전송된다고 기록하지 않는다.

### Architecture

```text
Network Firewall FLOW, ANF 활성 시
  -> /delta/security/nfw-flow
  -> delta-platform-central
  -> 기존 논리 Destination
  -> Kinesis와 Lambda
  -> /delta-cp/central/aws

Network Firewall ALERT, ANF 활성 시
  -> /delta/security/nfw-alert
  -> delta-platform-central
  -> 기존 논리 Destination
  -> Kinesis와 Lambda
  -> /delta-cp/central/aws

기존 Network Firewall FLOW와 ALERT 그룹
  -> 과거 로그 보존, 삭제와 백필 없음
```

### Unit of Work와 Construction 설계

| Unit | Design | Implementation Plan | Acceptance Criteria | 승인 |
| --- | --- | --- | --- | --- |
| U1. NFW 원본 전환 | FLOW와 ALERT용 새 Standard 그룹을 만들고 기존 그룹은 보존 | 새 그룹 2개, Firewall logging configuration의 FLOW와 ALERT 대상 변경, 전달 정책 확장, 출력과 모의 테스트 추가 | disabled에서 ANF 생성 없음, 기존 그룹 삭제 없음, enabled 모의 계획에서 두 로그 유형이 새 그룹 사용 | 완료. 로컬 Test와 실제 readback 통과 |
| U2. 플랫폼 전달 | 두 새 그룹의 독립 구독을 같은 논리 Destination에 연결 | 전달 토글과 ARN 검증, 두 subscription filter, 출력과 Runbook 추가 | 각 그룹에 이름과 패턴이 정확한 구독 1개, 플랫폼 수신 설정 불변 | 완료. 두 구독과 중앙 수신 확인 |
| U3. 실제 적용과 검증 | 중앙 root만 Plan과 Apply, 원본과 중앙 수신을 구분해 검증 | 재인증과 사전 조회, 저장 Plan 검토, 승인 범위 Apply, API readback, 전달 smoke, 최종 Plan | 의도한 변경만 적용, smoke 원본과 중앙 일치, 실제 NFW 이벤트는 ANF disabled로 미실행 명시, 최종 No changes | 완료. 실제 서비스 로그 검증만 승인된 Non-goal로 유지 |

### Acceptance Criteria

- 코드, 실제 계정과 Terraform state가 같은 중앙 계정과 기존 Network Firewall 로그 구성을 가리킨다.
- `/delta/security/nfw-flow`와 `/delta/security/nfw-alert`가 Standard, 설정된 보관 기간, 삭제 시 보존으로 생성된다.
- 두 새 그룹의 구독 필터 이름, 빈 패턴과 Destination ARN이 정확하다.
- 기존 `/aws/vendedlogs/network-firewall/delta-lab/flow`와 `/aws/vendedlogs/network-firewall/delta-lab/alert`는 존재하고 기존 과거 로그를 유지한다.
- ANF Firewall과 Endpoint는 재생성하지 않고 `inspection_mode=disabled`를 유지한다.
- 식별 가능한 smoke 이벤트를 각 새 그룹에 기록하면 `/delta-cp/central/aws`에서 계정, 그룹, 시각과 본문이 일치한다.
- smoke 이벤트는 Network Firewall 서비스가 만든 FLOW 또는 ALERT가 아니라 구독 전달 경로 검증임을 명시한다.
- 실제 Network Firewall FLOW와 ALERT 발생 검증은 ANF를 다시 활성화한 작업에서 수행하며 이번 결과를 PASS로 표시하지 않는다.
- GuardDuty 경로, GCP 로그 경로, 플랫폼 Destination, 수집 Lambda와 중앙 그룹 설정은 전후 동일하다.
- 중앙 root의 최종 실제 Plan이 No changes이고 종료 코드가 0이다.

## Operation

- Observability: 두 구독 필터와 원본 및 중앙의 최신 발생 시각을 확인한다. ANF가 꺼져 있으면 서비스 원본 이벤트가 없는 것이 예상 상태다.
- Rollback: 전달 토글을 끄면 두 새 원본 그룹과 향후 Network Firewall 대상 정의를 유지한 채 중앙 전달만 중단한다.
- ANF 복구: 이후 ANF를 복구하면 FLOW와 ALERT가 새 그룹으로 들어오는지 실제 허용 또는 차단 시험으로 별도 확인한다.
- 과거 그룹: 보존 기간과 별도 삭제 승인 전까지 유지한다. 새 그룹으로 과거 데이터를 자동 복사하지 않는다.
- Deployment: 저장 Plan으로 중앙 root만 적용했다. 플랫폼 root에는 Plan이나 Apply를 실행하지 않았다.
- Runbook: 그룹과 구독 조회, 저장 Plan 적용, smoke, 전달 중지와 ANF 복구 후 실제 서비스 로그 검증 절차를 갱신했다.

## 실제 결과

- `/delta/security/nfw-flow`와 `/delta/security/nfw-alert`를 Standard, 보관 30일로 생성했다.
- 각 그룹에 이름 `delta-platform-central`, 빈 패턴, 배포 방식 `Random`, 지정된 Destination을 사용하는 구독을 만들었다. 송신 역할은 없다.
- 기존 로그 전달 Resource Policy는 기존 두 그룹과 새 두 그룹의 스트림만 허용하도록 확장했고 기존 SourceAccount와 SourceArn 제한을 유지했다.
- 기존 두 그룹의 보관 기간과 저장량은 적용 전후 같았고 Network Firewall 목록은 계속 비어 있었다. ANF는 `disabled` 상태를 유지했다.
- 플랫폼 Destination 정책, 수집 Lambda의 CodeSha256과 RevisionId, 중앙 저장 그룹은 적용 전후 같았다.
- 수동 smoke는 구독과 중앙 수집 경로를 검증했다. ANF가 꺼져 있어 Network Firewall 서비스가 실제 생성한 FLOW와 ALERT는 검증하지 않았고 PASS로 기록하지 않는다.

## 검증 기록

| 날짜 | 명령 또는 확인 | 결과 |
| --- | --- | --- |
| 2026-11-06 | 샌드박스에서 `terraform/network-security/scripts/validate.sh` | Python helper 11개 통과. AWS Provider schema 프로세스가 샌드박스에서 시작되지 않아 Terraform validate와 mock 결과는 성공으로 기록하지 않음 |
| 2026-11-06 | 정상 실행 환경에서 `terraform/network-security/scripts/validate.sh` | Python helper 11개 통과. Terraform fmt, init, validate와 mock Plan 71개 통과, 실패 0개 |
| 2026-11-06 | `terraform fmt -check -recursive terraform/network-security`, `git diff --check`, 입력과 출력 README 표 대조, 새 변경의 금지 문자와 VPC Flow 리소스 검사 | 통과. 전체 입력과 출력이 README 주 표에 있고 VPC Flow 리소스 및 `/delta/security/vpc-flow` 없음 |
| 2026-11-06 | `aws sso login`과 `aws sts get-caller-identity`를 `sbh-delta-security`, `sbh-delta-workload`에 실행 | 재인증 완료. 보안 계정 `482840608926`, 플랫폼 계정 `616723161368` 확인 |
| 2026-11-06 | 기존 그룹, Network Firewall 목록, Terraform state와 outputs, 플랫폼 Destination, 수집 Lambda와 중앙 그룹 API 조회 | 기존 그룹과 과거 로그 존재, Network Firewall 없음, `inspection_mode=disabled`, Destination과 수집기 정상, 중앙 대상 `/delta-cp/central/aws` 확인 |
| 2026-11-06 | `terraform plan -input=false -parallelism=1 -lock-timeout=2m -detailed-exitcode -out=.validation/network-firewall-logging/connect.tfplan`과 JSON Plan 검토 | 종료 코드 2. 새 그룹 2개와 구독 2개 생성, 기존 전달 정책 1개 갱신, 삭제 0개. Network Firewall, Route, 기존 그룹, GuardDuty와 플랫폼 변경 없음 |
| 2026-11-06 | 저장 Plan에 `terraform apply -input=false -parallelism=1` | 성공. 4 added, 1 changed, 0 destroyed |
| 2026-11-06 | `describe-log-groups`, `describe-subscription-filters`, `describe-resource-policies`, state와 outputs 재조회 | 두 그룹은 Standard와 보관 30일. 두 구독의 이름, 빈 패턴, Destination, Random과 role 없음 확인. 기존 그룹, ANF disabled, GuardDuty와 플랫폼 수신 설정 보존 |
| 2026-11-06 23:55:57 KST | 두 새 그룹에 식별 가능한 수동 이벤트를 각각 1개 기록하고 원본과 `/delta-cp/central/aws`를 대조 | 원본 각 1개와 중앙 2개가 계정, 리전, 그룹, 스트림, 원본 시각과 본문까지 일치. FLOW 3922ms, ALERT 4151ms 뒤 수집. 서로 다른 안정적 event_id 확인 |
| 2026-11-06 | `terraform plan -input=false -parallelism=1 -lock-timeout=2m -detailed-exitcode` | `No changes`, 종료 코드 0 |

## 공식 제약 근거

- [Network Firewall의 CloudWatch Logs 전송](https://docs.aws.amazon.com/network-firewall/latest/developerguide/logging-cw-logs.html)
- [CloudWatch Logs 교차 계정 구독](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/Cross-Account-Log_Subscription-New.html)
