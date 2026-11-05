# 중앙 GuardDuty Finding의 플랫폼 로그 전달

기준일: 2026-11-06 (Asia/Seoul). 기존 두 계정의 GuardDuty 활성화와 보존 검증은 [활성화 기록](aws-guardduty.md)에 남긴다. 이 문서는 중앙 계정의 탐지 결과 전달을 추가하는 후속 작업이다.

## 승인과 현재 상태

| 단계 | 상태 |
| --- | --- |
| Ideation | 2026-11-06 사용자 `승인: 중앙 계정 전달 경로 추가`로 승인 |
| Inception | 2026-11-06 사용자 `승인: 구현과 실제 적용/검증`으로 승인 |
| Construction | U4/U5 구현/Test/Review와 실제 적용 및 수신 검증 완료. Terraform mock63개/Python11개 통과 |
| Terraform Plan | 최초 5개 추가만 검토했고 적용 후 최종 실제 Plan은 No changes/종료0. 기존 ID58개/출력18개 보존 |
| Apply 및 배포 | 검토한 저장 Plan 적용 완료. 5 added/0 changed/0 destroyed, 종료0 |
| 실제 중앙 수신 | 샘플 Finding 1종/1건의 원본 및 교차 계정 중앙 저장 확인. 본문/출처/시각/ID 대조 통과 |
| 커밋 및 푸시 | 구현 `c543892d`와 운영 기록 `01ff0708`을 main에 푸시. 이후 게시 확인 문서는 Git 이력과 원격 ref를 따름 |
| 원격 CI | `01ff0708`의 Platform checks 17개 통과. 중앙 네트워크 root는 이 workflow에 포함되지 않으며 로컬 mock63개/Python11개 결과를 별도로 기록 |

2026-11-06 사용자가 아래 Inception과 U4/U5 설계/구현 계획, 실제 Plan/Apply와 샘플 Finding 1종의 원본 및 중앙 수신 검증을 함께 승인했다. 단계별 실제 실행 결과는 각각 기록한다.

## Ideation

- 문제 정의: 중앙 계정의 GuardDuty는 활성 상태지만 Finding을 CloudWatch Logs에 저장하고 플랫폼으로 전달하는 경로가 없다.
- 사용자: Delta 보안 운영자와 플랫폼 중앙 로그를 조회하는 운영자.
- 성공 기준: 중앙 계정의 GuardDuty Finding이 `/delta/security/guardduty`에 원본 이벤트로 저장되고, 지정된 Destination을 통해 `/delta-cp/central/aws`에서 원본 계정과 본문을 확인할 수 있다.
- Scope: `terraform/network-security`의 EventBridge 규칙과 타깃, 원본 로그 그룹과 쓰기 정책, 그룹 단위 구독 필터 추가. 로컬 검증, 실제 중앙 수신 검증과 관련 README/Runbook 및 AI-DLC 문서 갱신.
- Non-goals: Workload 계정의 송신 경로, 다른 리전, GuardDuty 탐지/보호 설정이나 Organizations 변경, 실제 공격, 대시보드 코드 수정, 플랫폼 수집기 또는 플랫폼 Terraform root 변경/Apply, 과거 Findings 일괄 재전송. Git 게시는 초기 범위에서 제외했으나 사용자가 후속으로 커밋과 푸시를 요청했다.

## 적용 전 읽기 전용으로 확인한 대상

2026-11-06에 두 프로필의 STS 계정을 재확인한 후 조회했다. 조회를 실제 배포 또는 중앙 수신 검증으로 해석하지 않는다.

| 항목 | 실제 확인 값 |
| --- | --- |
| 송신 root / 프로필 / 계정 | `terraform/network-security` / `sbh-delta-security` / `482840608926` |
| 수신 프로필 / 계정 | `sbh-delta-workload` / `616723161368`. 이번에는 기존 플랫폼 설정의 조회에만 사용 |
| 리전 | 양쪽 모두 `ap-northeast-2` |
| 송신 GuardDuty | detector `7c8f1cc155f9404fb4ed73ec3e37208e`, `ENABLED`, `SIX_HOURS` |
| 원본 로그 그룹 / EventBridge 규칙 | `/delta/security/guardduty` 그룹 없음. 기본 버스에서 이름 또는 패턴에 guardduty가 있는 규칙 없음 |
| 기존 CloudWatch Logs 리소스 정책 | `delta-lab-firewall-delivery` 1개. 새 정책으로 덮어쓰지 않음 |
| 플랫폼 Destination | `arn:aws:logs:ap-northeast-2:616723161368:destination:delta-platform-logs` |
| Destination 허용 계정 | `482840608926`, `616723161368` |
| Destination의 실제 Kinesis 타깃 | `arn:aws:kinesis:ap-northeast-2:616723161368:stream/delta-platform-logs-aws` |
| 기존 AWS 수집기 | `delta-platform-logs-aws`, `Active`, 마지막 갱신 `Successful`, 위 두 계정 허용 |
| 수집기의 실제 AWS 저장 그룹 | `DESTINATION_LOG_GROUP=/delta-cp/central/aws` |
| 실제 플랫폼 중앙 그룹 | `/delta-cp/central/aws`, `/delta-cp/central/gcp`, 각각 보관 3일 |

플랫폼의 tracked `platform/deploy/logging/aws.tf`는 아직 `/delta-platform/central/*`를 정의한다. 실제 그룹 이름과 다르므로 이번 문서 정정으로 플랫폼 코드가 수렴했다고 표시하지 않는다. 플랫폼 root의 실제 Plan/Apply는 이번 범위에 포함하지 않으며, 이 root를 그대로 실행해 기존 플랫폼 로그 이름을 되돌리지 않는다.

## Inception

### Functional Requirements

1. 기존 중앙 root의 Provider, 계정 검증과 backend/state를 사용한다. EventBridge 기본 버스에서 `source=aws.guardduty`, `detail-type=GuardDuty Finding`, 송신 계정과 리전이 일치하는 이벤트를 받는다. 심각도 필터로 일부 Findings를 누락시키지 않는다.
2. 원본 그룹 이름은 `/delta/security/guardduty`로 고정한다. 기존 `log_retention_days`와 `retain_logs_on_destroy`를 재사용한다. 기본값은 30일 보관과 삭제 시 그룹 보존이다. 로그 클래스는 구독을 지원하는 Standard를 사용한다.
3. EventBridge가 원본 그룹에만 `logs:CreateLogStream`, `logs:PutLogEvents`를 실행하도록 별도 CloudWatch Logs 리소스 정책을 만든다. 공식 예시의 `events.amazonaws.com`, `delivery.logs.amazonaws.com`을 사용하고 Resource를 해당 그룹의 스트림에 한정한다. 기존 방화벽 정책은 보존한다. CloudWatch Logs 타깃에는 `role_arn`을 지정하지 않는다. [AWS 권한 설명](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-use-resource-based.html)
4. EventBridge 타깃은 변환 없이 전체 이벤트 JSON을 저장한다. 이벤트 ID, account, region, time과 `detail`의 Finding ID/본문을 보존한다. Lambda나 별도 이벤트 버스를 송신 계정에 추가하지 않는다.
5. 원본 그룹 하나에 `delta-platform-central` 구독 필터를 만든다. 빈 필터로 그룹의 새 이벤트 전체를 지정 Destination에 보내고 `distribution=Random`을 사용한다. 논리 Destination에 연결하므로 송신 구독의 `role_arn`도 지정하지 않는다. 수신 측 기존 역할과 정책을 재사용한다. [AWS 구독 API](https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_PutSubscriptionFilter.html)
6. 전달 토글은 구독 필터만 제어한다. false로 바꿔도 원본 그룹, EventBridge 규칙/타깃/쓰기 정책과 GuardDuty를 유지해 로컬 로그 저장을 계속한다.
7. 입력 2개와 별도 `guardduty_findings` 출력을 추가한다. 기존 `guardduty`와 `log_groups` 출력의 구조와 의미는 바꾸지 않는다. Destination ARN은 동일 리전의 CloudWatch Logs 논리 Destination이며 수신 계정은 `616723161368`인지 검증한다. 실제 적용값은 요청의 ARN과 일치해야 한다.
8. GuardDuty detector ID/활성 상태/보호 기능/게시 주기와 기존 Findings, ANF disabled 상태, 네트워크/앱/DB 및 기존 로그를 보존한다. 같은 작업에서 중앙 root의 전체 입력/출력 표, Runbook, 관련 플랫폼 연결 안내와 AI-DLC 상태를 갱신한다.

### Non-Functional Requirements

- Terraform `1.16.4`, AWS Provider `6.71.0`과 기존 독립 실행 경계를 유지한다.
- 송신 계정의 신규 AWS 관리 리소스는 아래 5개로 제한한다. 신규 IAM 역할, 플랫폼 Destination/Kinesis/Lambda 또는 별도 수신 그룹은 만들지 않는다.
- 배포 주체는 원본 로그 그룹과 Destination에 대한 구독 설정 권한을 가져야 한다. 수신 정책의 계정 허용을 송신 IAM 권한 검증 완료로 표현하지 않는다. 적용 전 기존 그룹/구독/정책 이름 충돌을 다시 확인한다.
- 전송 중복은 가능하다. 원본 EventBridge 이벤트 ID와 Finding ID를 보존하고 플랫폼 수집기의 원본 메타데이터 및 안정적인 `event_id`를 확인한다. Exactly-once를 보장한다고 표현하지 않는다.
- 구독 추가 전에 저장된 로그나 과거 Findings의 자동 소급 전달을 가정하지 않는다. 새로운 Finding과 기존 Finding의 갱신 알림 주기는 구분하며 기존 `SIX_HOURS`는 유지한다. [AWS 알림 주기](https://docs.aws.amazon.com/guardduty/latest/ug/guardduty_findings_eventbridge.html)
- 자격 증명, raw state/Plan과 전체 검증 이벤트는 Git에 넣지 않는다. 실제 검증 산출물은 ignored `.validation/` 아래에 보관하고 문서에는 비밀값 없는 결과를 기록한다.
- 로컬 mock, 실제 Plan, Apply, 원본 저장, 교차 계정 중앙 수신 및 Git 게시를 각각 기록한다. 수행하지 않은 검증은 PASS로 표시하지 않는다.

### Architecture

```text
중앙 계정 482840608926 / ap-northeast-2
  기존 GuardDuty
    -> 기본 EventBridge 버스의 Finding 규칙
    -> CloudWatch Logs /delta/security/guardduty
    -> 그룹 단위 구독 delta-platform-central
       -> arn:aws:logs:ap-northeast-2:616723161368:destination:delta-platform-logs

플랫폼 계정 616723161368 / ap-northeast-2 (기존 수신 경로)
  Destination -> Kinesis -> Lambda -> /delta-cp/central/aws
```

| 구현한 리소스 주소 | 역할 |
| --- | --- |
| `aws_cloudwatch_log_group.guardduty_findings` | 기업 원본 로그를 저장하고 기존 보관/보존 입력 적용 |
| `aws_cloudwatch_log_resource_policy.guardduty_findings` | EventBridge의 해당 그룹 스트림 쓰기 허용 |
| `aws_cloudwatch_event_rule.guardduty_findings` | 기본 버스에서 중앙 계정/리전의 GuardDuty Finding 선택 |
| `aws_cloudwatch_event_target.guardduty_findings` | 원본 그룹을 규칙의 타깃으로 연결. 쓰기 정책 준비 뒤 생성 |
| `aws_cloudwatch_log_subscription_filter.guardduty_platform[0]` | 원본 그룹의 새 이벤트를 기존 플랫폼 Destination으로 전달 |

### 입력과 출력

승인된 입력과 출력을 `variables.tf`, `outputs.tf` 및 중앙 root README의 전체 표에 구현했다. 테스트와 실제 적용 상태는 상단과 확인 기록을 따른다.

| 입력 속성 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `guardduty_findings_forwarding_enabled` | `bool` | `true` | 플랫폼 구독 필터 생성 여부. false여도 로컬 Finding 저장 유지 |
| `guardduty_findings_destination_arn` | `string` | `arn:aws:logs:ap-northeast-2:616723161368:destination:delta-platform-logs` | 같은 리전/지정 플랫폼 계정의 논리 Destination |

| `guardduty_findings` 출력 내부 속성 | 역할 |
| --- | --- |
| `log_group_name`, `log_group_arn` | 원본 그룹 이름과 ARN |
| `event_rule_name`, `event_rule_arn` | EventBridge 규칙 식별자 |
| `destination_arn` | 전달할 플랫폼 Destination |
| `forwarding_enabled`, `subscription_filter_name` | 구독 활성 구성과 필터 이름. 토글 false이면 필터 이름은 null |

### Unit of Work, Design와 Implementation Plan

기존 활성화 작업의 U1~U3 뒤에 이어지는 단위다. 2026-11-06 아래 두 단위의 설계와 구현 계획 및 실제 적용/검증을 승인받았다.

| Unit | Design | Implementation Plan | Test/Review 기준 | 승인 |
| --- | --- | --- | --- | --- |
| U4. 송신 구성과 로컬 검증 | 원본 저장 4개와 선택형 플랫폼 구독 1개를 분리. 탐지 설정/ANF와 독립 | `guardduty-findings.tf`, 입력 2개, 별도 출력과 example/README/Runbook 작성. 전용 mock 시나리오 추가 | fmt/init/validate와 mock63개/Python11개 통과. 신규 8개 시나리오, 전체 입력26개/출력20개 문서 및 정적 Review 완료 | 2026-11-06 승인, 구현/Test/Review 완료 |
| U5. 실제 연결과 운영 검증 | 기존 수신기를 이용해 교차 계정 경로 검증. 중앙 root만 적용 | 계정/기존 ID/출력/설정 기준과 저장 Plan 검토 후 5개만 Apply. 샘플 Finding 1종의 원본/중앙 대조와 최종 Plan 및 운영 기록 완료 | 원본 JSON/중앙 message와 메타데이터 일치. 기존 ID58개/출력18개 및 detector/ANF/플랫폼 설정 보존. 최종 실제 Plan No changes/종료0 | 2026-11-06 승인, 적용/Test/Review 완료 |

### Acceptance Criteria

1. 중앙 root의 fmt/init/validate와 기존 및 신규 mock 검사가 실제로 통과한다. 모의 테스트가 실제 AWS 수신 검증을 대신하지 않는다.
2. 실제 저장 Plan은 위 5개 관리 리소스 생성과 새 출력 추가만 포함한다. 기존 자원 변경/교체/삭제 또는 출력의 예상 밖 변화가 있으면 원인을 확인한 뒤 범위를 다시 검토한다.
3. EventBridge 규칙/타깃/리소스 정책과 원본 그룹, 지정 ARN의 구독 필터를 실제 API로 확인한다. 기존 detector와 모든 보호 설정 및 ANF disabled 상태를 보존한다.
4. 검증용 GuardDuty 샘플 Finding 1종이 원본 그룹에 도착하고, 플랫폼 `/delta-cp/central/aws`의 저장 JSON에서 `account_id=482840608926`, `log_group=/delta/security/guardduty`, 원본 message와 ID/시각을 대조한다. 중앙 레코드의 `event_id`와 수집 시각은 원본 이벤트 ID/시각과 구분한다.
5. 생성할 샘플 유형의 기존 Findings를 먼저 확인한다. `CreateSampleFindings`로 한 유형만 생성하며 전체 유형 일괄 생성이나 실제 공격을 하지 않는다. 생성한 샘플 ID와 검증 결과를 기록하고 기존 Findings를 보관 처리하거나 삭제하지 않는다. 실제 샘플 생성도 U5 승인 범위에 명시한다. [AWS 샘플 생성](https://docs.aws.amazon.com/guardduty/latest/ug/sample_findings.html)
6. 로그 도착 실패나 대기 시간 초과를 PASS로 처리하지 않는다. 샘플 생성 응답만으로 전달 성공을 주장하지 않는다. 최종 중앙 root Plan에서 No changes/종료0과 기존 ID/출력 보존을 확인한다.

## Operation 계획

- Deployment: U4 로컬 검증 후 U5의 새 저장 Plan을 검토한다. 송신 계정만 Apply하고 수신 계정은 조회만 한다. 승인된 실제 Plan/Apply 및 샘플 생성의 실행 결과는 상단과 확인 기록을 따른다.
- Observability: 규칙 상태/타깃, EventBridge `FailedInvocations`, 원본 로그의 Finding ID, 구독 ARN과 `DeliveryErrors`/`DeliveryThrottling`, 기존 수집기 오류와 중앙 레코드의 원본 정보를 구분해 확인한다. 중앙 보관 3일과 원본 기본 보관 30일을 구분한다.
- Rollback: 플랫폼 전달 문제는 `guardduty_findings_forwarding_enabled=false`의 저장 Plan을 검토해 구독만 제거하고 원본 저장과 탐지를 유지한다. 수신 플랫폼을 변경하거나 detector를 중지하는 방식으로 원복하지 않는다. 전송 중단 구간은 자동 소급 전달되지 않는다. 이 토글의 실제 중단/재개 시험 여부는 배포 후 별도로 기록한다.
- Runbook: 계정/규칙/그룹/구독 조회, 지정 유형의 샘플 생성과 원본/중앙 대조, 전달 토글과 로그 보존/재관리 시 Import, 중복 및 실패 진단 절차를 중앙 root Runbook에 추가한다.

## 확인 기록

| 날짜 | 실행한 명령 또는 확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-06 | `git status --short --branch`, AGENTS.md, docs/README 및 관련 AI-DLC, 중앙 root와 플랫폼 로그 코드 읽기 | main 작업 트리 clean에서 시작. Ideation 승인과 구현 전 승인 규칙, 기존 보관/Provider/출력 경계 확인 |
| 2026-11-06 | `aws sts get-caller-identity --profile sbh-delta-security --region ap-northeast-2` 및 `--profile sbh-delta-workload` (계정 필드 조회) | 각각 `482840608926`, `616723161368`. 읽기 전용 |
| 2026-11-06 | `aws guardduty get-detector --profile sbh-delta-security --region ap-northeast-2 --detector-id 7c8f1cc155f9404fb4ed73ec3e37208e` (상태/주기 필드 조회) | `ENABLED`, `SIX_HOURS` |
| 2026-11-06 | `aws logs describe-log-groups --profile sbh-delta-security --region ap-northeast-2 --log-group-name-prefix /delta/security/guardduty`, `aws events list-rules`의 guardduty 이름/패턴 필터 | 양쪽 결과 `[]`. 원본 그룹과 해당 기본 버스 규칙 없음 |
| 2026-11-06 | `aws logs describe-resource-policies --profile sbh-delta-security --region ap-northeast-2` (이름만 조회) | 기존 방화벽 정책 1개 |
| 2026-11-06 | `aws logs describe-destinations --profile sbh-delta-workload --region ap-northeast-2 --destination-name-prefix delta-platform-logs` | 요청 ARN/Kinesis 타깃과 두 허용 계정 확인 |
| 2026-11-06 | `aws lambda get-function-configuration --profile sbh-delta-workload --region ap-northeast-2 --function-name delta-platform-logs-aws` (저장 그룹/허용 계정/상태 필드만 조회) | `/delta-cp/central/aws`, 두 계정 허용, `Active` / `Successful`. 다른 환경 변수나 비밀값은 출력하지 않음 |
| 2026-11-06 | `aws logs describe-log-groups --profile sbh-delta-workload --region ap-northeast-2 --log-group-name-prefix /delta-cp/central/` | AWS/GCP 두 그룹과 보관 3일 확인. 실제 이벤트 수신 검사는 아님 |
| 2026-11-06 | AWS 공식 EventBridge 권한/GuardDuty 알림 주기/샘플 생성/구독 API 문서 확인 | 역할을 지정하지 않는 Logs 타깃/논리 Destination과 단일 샘플 유형 검증 설계 근거 확인 |
| 2026-11-06 | `git diff --check`와 Python 일회성 문서 검사 (`Path`, `re`, `subprocess`) | 문서 5개, 상대 파일 링크 118개, 추가 내용의 금지 문자/새 문서 공백과 문서만 변경한 범위 검사 통과. Terraform 파일 변경 없음 |

## Construction과 Operation 확인 기록

| 날짜 | 실행한 명령 또는 확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-06 | sandbox에서 `DELTA_KEEP_VALIDATION=1 bash terraform/network-security/scripts/validate.sh` | Python11개 통과 뒤 Provider schema handshake 실패로 중단. Terraform 검증 통과가 아님 |
| 2026-11-06 | 같은 검증을 sandbox 밖에서 실행 | init/validate 및 기존 mock55개 통과. 새 시나리오의 optional/computed RoleArn 모의 응답이 미확정이어서 1개 실패/7개 건너뜀. 테스트 모의 값 보완 |
| 2026-11-06 | 보완 후 `DELTA_KEEP_VALIDATION=1 bash terraform/network-security/scripts/validate.sh` 재실행 | fmt, 격리 init/validate, mock63개(신규8개 포함)와 Python11개 모두 통과. 실제 AWS 배포/수신 검증은 아님 |
| 2026-11-06 | example 입력을 `terraform fmt -`로 확인, Python으로 README 전체 입력/출력 및 상대 링크/추가 내용 검사, `git diff --check` | 입력26개/출력20개 문서화, 상대 파일 링크140개와 금지 문자/공백 검사 통과 |
| 2026-11-06 | ignored 검증 도구 `verify.py before` | STS 두 계정, 기존 managed58개 ID/출력18개와 detector 전체 보호 상태, 기존 수신 Destination/수집기/정책 및 Findings0개 기준 보관. 원본 그룹/관련 규칙 없음, ANF disabled 확인 |
| 2026-11-06 | `verify.py plan`: 중앙 `terraform plan -input=false -parallelism=1 -lock-timeout=2m -detailed-exitcode -out=.../connect.tfplan`과 `terraform show -json` 검토 | 종료2. 위 5개 추가만 존재, 기존 변경/삭제0개. 기존 ID58개/출력18개 및 그룹/보관/이벤트 필터/타깃 입력과 지정 Destination 대조 통과 |
| 2026-11-06 | `AWS_PROFILE=sbh-delta-security terraform -chdir=terraform/network-security apply -input=false -parallelism=1 .validation/guardduty-logging-20261106/connect.tfplan` | 종료0. 5 added/0 changed/0 destroyed. 기존 네트워크/탐지/출력 변경 없음 |
| 2026-11-06 | `verify.py ready`: 실제 규칙/타깃/구독/리소스 정책 API 대조 | 규칙 ENABLED, 올바른 계정/리전/Finding 필터, Logs 타깃1개/구독1개, 역할과 변환 부재 및 원본 그룹 스트림에 한정한 쓰기 권한 확인 |
| 2026-11-06 | `verify.py sample`: 기존 유형별 Findings 조회 후 `create-sample-findings --finding-types Backdoor:EC2/DenialOfService.Tcp` | 기존 해당 유형0개 확인 후 단일 유형 생성 종료0. 이후 실제 Finding1개 확인 |
| 2026-11-06 | `verify.py receive`: 원본/중앙 `filter-log-events`, 샘플 `get-findings` 및 본문/메타데이터 대조 | 원본1건/중앙1건에서 같은 Finding 확인. JSON 문자열 완전 일치, 원본 계정/리전/그룹/스트림/시각과 원본 로그 ID 기반 SHA-256 `event_id` 일치. API의 실제 `sample=true`로 샘플 판별 |
| 2026-11-06 | 수신 검증 도구의 샘플 판별 확인 | 초기에는 콘솔의 `[SAMPLE]` 제목 표시를 원본 API에도 가정해 이미 도착한 이벤트를 판별하지 못했다. 이벤트의 `detail.service.additionalInfo.sample`와 API `Service.AdditionalInfo.Value` 내부 sample=true를 기준으로 수정 후 실제 대조 통과. 전송 구성 변경이나 샘플 재생성 없음 |
| 2026-11-06 | `verify.py after`: STS/API/state 적용 전후 대조 | 기존 managed58개 ID/출력18개, detector 기본/선택 보호 상태와 SIX_HOURS, ANF disabled, 기존 방화벽 정책 및 플랫폼 Destination/수집기 설정 보존. 신규 관리 자원5개, Findings0개에서 검증 샘플1개로 증가. 원본 Standard/보관30일과 실제 타깃/구독 확인 |
| 2026-11-06 | `verify.py final-plan`: 중앙 `terraform plan -input=false -parallelism=1 -lock-timeout=2m -detailed-exitcode -out=.../final.tfplan`과 JSON 검토 | No changes/종료0. 관리 자원과 출력 변경0개, 기존 ID58개/출력18개 재확인 |
| 2026-11-06 | 최종 `git diff --check`와 Python의 변경 범위/상대 링크/전체 입력출력 표/검증 산출물 대조 | 변경12개 파일, 상대 링크144개, 입력26개/출력20개 문서 및 금지 문자/공백 검사 통과. 수신/보존/최종 Plan 산출물의 성공 상태 재확인, ignored 검증 파일의 추적0개 |

최종 mock 산출물은 임시 `delta-network-validation.pmMbeS`, 실제 검증 원문은 중앙 root의 ignored `.validation/guardduty-logging-20261106/`에 보관한다. `connect-review.json`, `ready-result.json`, `receive-result.json`, `after-review.json`, `final-review.json`이 단계별 결과이며 raw state/Plan/원문 이벤트는 Git 대상에서 제외한다.

## Operation 실제 결과

| 항목 | 실제 결과 |
| --- | --- |
| 원본 저장 | 계정 `482840608926`, `/delta/security/guardduty`, Standard, 보관30일 |
| 규칙 | 기본 버스의 `delta-lab-guardduty-findings`, ENABLED, 원본 그룹 타깃1개 |
| 전달 | `delta-platform-central`, 빈 필터, Random, 지정된 `delta-platform-logs` 논리 Destination |
| 중앙 저장 | 계정 `616723161368`, 기존 `/delta-cp/central/aws`. 실제 원본 account_id/log_group와 JSON 본문 확인 |
| 검증 샘플 | `Backdoor:EC2/DenialOfService.Tcp`, Finding ID `e54fc478694e4438b1c9b358eb640d73` |
| 수신 시각 | 원본 이벤트 시각 2026-11-06 00:37:14 KST, 중앙 수집 시각 00:37:21.017 KST |
| 보존과 최종 수렴 | 기존 ID58개/출력18개와 탐지/보호/ANF/플랫폼 설정 보존. 중앙 root 최종 실제 Plan No changes/종료0 |

- Deployment: 검토한 저장 Plan의 송신 자원5개만 실제 적용했다. 플랫폼 root의 코드/state/설정/Apply는 변경 또는 실행하지 않았다.
- Observability: 실제 샘플의 전체 메시지와 원본 메타데이터, 안정적인 플랫폼 event_id를 대조했다. 이 한 건의 전달 검증을 실제 위협 탐지 성능이나 부하/장기 장애 검증으로 해석하지 않는다. 대시보드 UI는 이번 검증 범위가 아니다.
- Rollback/Runbook: [중앙 Runbook](../../terraform/network-security/RUNBOOK.md#guardduty-finding-로그-전달)에 조회, 샘플 생성, 실패 진단과 구독만 중지/재개하는 절차를 기록했다. 전달 토글의 실제 중지/재개는 실행하지 않았고 mock에서 원본 저장/탐지 보존을 확인했다.
- Git 게시: 구현 `c543892d`와 운영 기록 `01ff0708`을 main에 푸시했다. 두 커밋의 GitHub 서명 검증은 valid다. 게시 확인 문서의 최신 상태는 Git 이력과 원격 ref를 따른다.

## 게시 확인

2026-11-06 사용자 요청으로 구현/테스트와 운영 문서를 두 커밋으로 나눴다. 각 커밋 전에 `git diff --cached --check`를 실행했고 `git push origin main`은 종료0이었다. 게시 확인 기록을 작성하기 전 `git ls-remote --heads origin main`은 `01ff07085f90ce415cf2f43472a81ace02134c89`와 일치했고 `git status --short --branch`에서 작업 트리가 깨끗했다. GitHub API에서 두 커밋의 서명 검증 결과는 모두 valid였다.

`gh run view 42058840223 --json status,conclusion,headSha,url,jobs`에서 `01ff07085f90ce415cf2f43472a81ace02134c89`의 [Platform checks](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/42058840223)가 17개 작업 모두 통과했음을 확인했다. 이 workflow는 중앙 `terraform/network-security` root를 실행하지 않는다. 중앙 root의 실제 검증은 위 로컬 mock63개/Python11개와 실제 Plan/Apply/수신 기록을 따른다. 이후 이 게시 확인 기록은 `docs/`만 변경하며 해당 workflow의 push 경로 필터에 포함되지 않는다.
