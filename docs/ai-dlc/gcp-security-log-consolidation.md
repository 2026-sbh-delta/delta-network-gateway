# GCP 방화벽 로그의 Security 계정 일원화

기준일: 2026-11-07, Asia/Seoul.

## 현재 단계

- Ideation: 사용자의 `어 그거 다시 Security계정으로 일원화해줘` 지시로 아래 두 로그의 Security 계정 집계 목표 승인.
- Inception: 2026-11-07 사용자 `기릿` 지시로 요구사항, 아키텍처와 수용 기준 승인.
- Construction: 같은 지시로 U1부터 U4의 구현과 실제 적용을 승인받았고 Implementation, Test와 Review 완료.
- Operation: 배포, API readback, 원본부터 중앙까지 수신 대조, 기존 직접 수집 제외와 Runbook 갱신 완료.
- Terraform Plan과 Apply: Security 신규 24개, 플랫폼 수신 준비 3개 갱신과 Sink 필터 1개 갱신 완료. 삭제 0개.
- 최종 실제 Plan: Security와 플랫폼 모두 No changes, 종료 코드 0.
- Git 커밋, 푸시와 원격 CI 상태: Git 이력을 따른다. 아래 로컬 검증과 구분한다.

## Ideation

### 문제 정의

GCP 방화벽 로그는 플랫폼 계정으로 직접 수집된다. AWS GuardDuty와 Network Firewall처럼 Security 계정에서 먼저 보안 로그를 보유하도록 GCP 방화벽 두 종류의 집계 위치를 일원화한다. 플랫폼의 GCP 조회 위치와 원본 식별 정보는 유지한다.

### 사용자

- AWS와 GCP 보안 로그를 Security 계정에서 관리하는 Delta 보안 운영자
- 플랫폼 중앙 GCP 로그를 조회하는 플랫폼 운영자

### 성공 기준

1. `compute.googleapis.com/firewall`과 `networksecurity.googleapis.com/firewall_threat`가 Security 계정 `482840608926`, 서울 리전에 먼저 저장된다.
2. 두 종류를 분리한 원본 그룹에서 기존 플랫폼 Destination으로 새 이벤트를 전달한다.
3. 플랫폼의 최종 저장 위치는 `/delta-cp/central/gcp`이고 GCP 프로젝트, 원본 로그 이름, 시각, insert ID, event ID와 본문이 보존된다.
4. 정상 전환 후 두 방화벽 로그의 플랫폼 직접 수집은 제외해 Security 경유 경로로 통일한다.
5. 나머지 GCP 수집 대상과 기존 AWS 보안 로그 전달은 정상 동작한다.

### Scope

- GCP 프로젝트 `term2-delta`의 방화벽 규칙 로그와 Cloud NGFW 위협 로그
- 전용 Sink, Pub/Sub, DLQ, WIF와 Security 계정의 수집 Lambda 및 주기 실행
- Security 계정의 새 원본 그룹 2개와 교차 계정 구독
- 기존 플랫폼 AWS 수신기의 신뢰한 GCP 중계 레코드 처리와 필요한 쓰기 권한
- 기존 플랫폼 Sink에서 두 로그를 제외하는 필터 변경
- Terraform 관리, 테스트, Runbook과 단계별 실제 검증

### Non-goals

- VPC Flow Logs, VPN, Router, VM과 Cloud Run 로그의 수집 위치 변경
- AWS ANF 또는 GCP Cloud NGFW Endpoint 활성화와 네트워크 경로 변경
- GCP Cloud Logging 원본이나 과거 AWS 로그 삭제 또는 백필
- 플랫폼 Destination, Kinesis 또는 중앙 그룹 재생성 및 이름 변경
- 장기 서비스 계정 키와 Access Key 생성

## 사전 확인 결과

| 날짜 | 명령 또는 확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-07 | 현재 코드와 [GCP 수집기 정리 기록](gcp-central-network.md#2026-11-05-gcp-로그-전달-경로-정리-검토) 대조 | 예전 Security 전용 AWS root와 GCP 전용 전달 정의는 제거됨. 공통 수집 코드와 플랫폼 수집 정의는 존재 |
| 2026-11-07 | `aws sts get-caller-identity --profile sbh-delta-security`와 필요한 Lambda 및 로그 그룹 목록 조회 | 계정 `482840608926` 일치. 이름에 gcp 또는 log를 포함한 Lambda 없음. 과거 `/delta/lab/gcp/term2-delta/network-security`와 기존 AWS 보안 그룹 존재, 보관 30일 |
| 2026-11-07 | `aws lambda get-function-configuration --profile sbh-delta-workload --function-name delta-platform-logs-gcp`의 필요한 필드 조회 | Active, Successful. 구독 `projects/term2-delta/subscriptions/delta-platform-logs-aws`, 저장 `/delta-cp/central/gcp` |
| 2026-11-07 | 활성 gcloud CLI 계정으로 `logging sinks describe` | `logging.sinks.get` 권한 거부. Sink 부재로 판단하지 않음 |
| 2026-11-07 | 기존 ADC의 단기 토큰을 메모리에서 사용해 Logging API로 플랫폼 Sink GET | `delta-platform-logs`가 기존 Pub/Sub Topic을 사용. 두 방화벽 로그와 VM 및 Cloud Run 등을 포함하는 필터 확인. 명시적 disabled 값 없음 |
| 2026-11-07 | `services/gcp-log-collector/collector.py`, `platform/deploy/logging/aws_collector.py` 읽기 검토 | 현재 GCP 수집기는 단일 그룹에 저장. AWS 수신기는 모든 수신 이벤트에 cloud=aws를 부여하고 단일 AWS 중앙 그룹에 저장. Security 경유 GCP의 기존 분류를 유지하려면 두 코드의 제한된 확장이 필요 |

위 조회는 새 이벤트 수신과 Terraform state 수렴을 검증한 결과가 아니다. 실제 Plan 전에 최신 state 소유권과 배포 코드의 차이도 확인한다.

## Inception

### Functional Requirements

1. Security 원본 그룹은 `/delta/security/gcp-firewall`, `/delta/security/gcp-firewall-threat`를 사용한다. Standard, 보관 30일, Terraform 제거 시 데이터 보존을 기본으로 한다.
2. 새 Sink는 원본 logName 두 개만 선택한다. 기존 플랫폼 구독을 다른 수집기와 경쟁 소비하지 않고 전용 Topic과 Subscription을 사용한다.
3. Security 수집기는 WIF로 자기 전용 구독만 읽고, 정확한 원본 logName에 따라 두 그룹으로 분기한다. 그룹에 저장이 성공한 메시지만 ACK한다. 실패는 재시도하고 DLQ로 보존한다.
4. 각 그룹에 이름 `delta-platform-central`, 빈 패턴, `Random`, Destination `arn:aws:logs:ap-northeast-2:616723161368:destination:delta-platform-logs`의 구독을 만든다.
5. 플랫폼 수신기는 송신 계정이 `482840608926`이고 그룹이 위 두 개 중 하나이며 내부 GCP 레코드의 프로젝트와 logName이 해당 그룹과 일치하는 경우만 GCP 중계로 처리한다. 임의의 AWS 메시지 내용만으로 GCP 분류를 허용하지 않는다.
6. GCP 중계 레코드는 기존 `/delta-cp/central/gcp`에 저장하고 기존 GCP event_id와 original을 보존한다. 중계 계정과 원본 CloudWatch 그룹도 추적할 수 있게 한다. 기존 AWS 경로는 `/delta-cp/central/aws`로 계속 저장한다.
7. Security 경로 검증 후 기존 플랫폼 Sink의 필터 전체에 두 logName의 제외 조건을 적용한다. VM 리소스 종류와 같은 기존 OR 조건을 통해 두 로그가 다시 포함되지 않도록 한다.
8. 플랫폼 직접 수집기는 나머지 GCP 로그와 전환 전에 적재된 큐 처리를 계속 수행한다. 기존 Subscription과 DLQ는 삭제하거나 수동 ACK하지 않는다.

### Non-Functional Requirements

- 전송은 at-least-once다. 전환 중 중복 가능성과 안정적인 GCP event_id 기반 중복 구분을 명시한다.
- WIF는 Security 계정과 전용 역할로 제한한다. 과거 삭제된 WIF pool을 무조건 재사용하지 않는다.
- 수집 코드의 기존 단일 그룹 동작과 기존 플랫폼 직접 GCP 소비 계약을 유지한다.
- 플랫폼 AWS 수신기에 추가하는 CloudWatch 쓰기 권한은 기존 GCP 중앙 그룹에 한정한다.
- 기존 과거 로그 그룹은 새 원본 그룹으로 이름을 바꾸거나 재생성하지 않는다.
- 플랫폼 tracked Terraform과 실제 중앙 그룹 이름이 달랐던 과거 기록이 있으므로 원본 state와 실리소스를 대조한다. 2026-11-07 실제 Plan에서는 이름이 일치했다. 무관한 이름, 보관 정책 또는 수신 리소스 교체가 있는 Plan은 적용하지 않는다.
- 실제 입력, Plan, state와 원본 로그 증거는 ignored 경로에서 제한된 파일 권한으로 보관한다. 인증값은 출력하거나 커밋하지 않는다.

### Architecture

```text
GCP Cloud Logging: firewall / firewall_threat
  -> 전용 Sink와 Pub/Sub
  -> Security 계정 수집 Lambda, WIF
  -> /delta/security/gcp-firewall
     /delta/security/gcp-firewall-threat
  -> 각 delta-platform-central 구독
  -> 기존 플랫폼 Destination, Kinesis
  -> 플랫폼 AWS 수신기, 신뢰한 GCP 중계만 분기
  -> /delta-cp/central/gcp

기타 GCP 로그
  -> 기존 플랫폼 Sink와 Pub/Sub
  -> 기존 플랫폼 GCP 수집기
  -> /delta-cp/central/gcp
```

### Unit of Work

| Unit | 결과물 | Acceptance Criteria |
| --- | --- | --- |
| U1. Security 수집 기반 | 독립 Terraform root와 전용 Sink, Pub/Sub, WIF, Lambda, 그룹 및 구독 | 소유권 분리, 두 종류만 수집, 기존 네트워크와 과거 로그 보존 |
| U2. 공통 수집기 분기 | 선택형 logName별 그룹 매핑 | 저장 성공 후 ACK, 일부 그룹 실패 처리, 기존 단일 그룹 회귀 통과 |
| U3. 플랫폼 중계와 전환 | 신뢰한 GCP 중계 처리, 제한된 IAM, 기존 Sink 제외 필터 | 원래 GCP 스키마와 저장 위치 유지, AWS 회귀 통과, 중복 직접 경로 제외 |
| U4. 실제 적용과 운영 검증 | 저장 Plan 검토, 단계별 Apply, 수신 대조, Runbook과 Rollback | 원본부터 중앙까지 식별자 일치, 범위 밖 변경 없음, 최종 실제 Plan 확인 |

### Acceptance Criteria

- 실제 계정, 프로젝트, state와 대상 리소스가 승인 범위와 일치한다.
- 방화벽 규칙 로그의 실제 새 원본 이벤트를 GCP, Security와 플랫폼에서 대조한다.
- 두 그룹의 구독과 플랫폼 GCP 분기는 식별 가능한 별도 smoke로 각각 검증한다. smoke를 실제 서비스 위협 탐지 성공으로 기록하지 않는다.
- GCP NGFW가 꺼져 있으면 실제 새 firewall_threat 이벤트 발생은 미검증으로 남긴다. 검증 목적으로 Endpoint를 복구하지 않는다.
- 원본 project_id, log_name, insert_id, event_id, source timestamp와 original이 Security와 플랫폼에서 일치한다.
- 기존 플랫폼 필터에서 두 로그가 명시적으로 제외되고 나머지 로그 대상은 유지된다.
- 관련 Python 테스트, Terraform fmt, validate와 필요한 mock을 실제 실행한다. 실제 Plan과 Apply 및 배포 결과는 별도로 기록한다.
- 각 변경 root의 최종 Plan과 범위 밖 리소스 및 과거 로그 보존 여부를 확인한다.

## Construction Design과 Implementation Plan

U1은 새 독립 root `terraform/gcp-security-log-ingest`에 AWS와 GCP의 전용 로그 전달 리소스를 함께 관리하는 설계다. backend는 Security 계정의 기존 S3 안에서 별도 key로 분리하고 실제 소유권 확인 후 확정한다. 모듈의 전체 입력과 출력 및 복합 속성은 README 표로 작성한다. 기존 네트워크 root의 리소스는 참조만 한다.

U2는 공통 GCP 수집기에 선택형 대상 매핑을 추가한다. 환경 설정이 없으면 기존 단일 그룹 동작을 유지한다. 매핑 대상 밖의 이벤트, 부분 저장 실패, ACK 실패 후 재전달, 크기 제한과 안정적인 event_id를 검증한다.

U3는 기존 AWS 수신기의 기본 AWS 처리와 GCP 중계 처리를 분리한다. 명시한 Security 계정, 두 그룹, 프로젝트와 logName의 결합을 검사하고 GCP original과 event_id를 보존한다. 플랫폼 GCP 쓰기 권한과 목적지 설정을 추가한 뒤 실제 중앙 이름에 수렴한 제한된 Plan인지 검토한다. Sink 제외는 전체 OR 표현식 바깥에 적용한다.

U4의 실행 순서는 플랫폼의 중계 수신 준비, Security 기반 생성, 두 구독 전달 검증, 새 Sink를 통한 실제 원본 수신 확인, 플랫폼 직접 Sink의 두 로그 제외, 기존 대기열과 최종 수신 확인이다. 전환의 일시 중복은 허용하되 event_id로 추적한다. 각 단계는 최신 저장 Plan과 실제 API readback으로 확인한다.

2026-11-07 사용자 `기릿` 지시로 U1부터 U4의 구현과 실제 적용을 승인받았다. 저장 Plan의 범위 검토와 단계별 readback 후 진행한다.

## Operation

- Deployment: 네트워크와 수집기 운영 상태를 사전 기록하고 각 변경 root의 저장 Plan을 검토해 순서대로 적용한다.
- Observability: 원본 로그 최신 시각, Security 두 그룹, 중앙 GCP 수신, Lambda 오류와 Pub/Sub backlog 및 DLQ를 확인한다.
- Rollback: 먼저 기존 플랫폼 Sink에서 두 로그의 직접 수집을 재허용한다. 실제 직접 수신 복구와 대기열을 확인한 뒤 새 Security Sink와 실행을 중단한다. 과거 로그와 미처리 메시지를 삭제하지 않는다.
- Runbook: 인증, source-to-central 식별자 대조, 전환과 복귀, 두 구독 중지 및 장애 지표를 문서화한다.

## 구현과 실제 검증 결과

Security의 새 전용 root와 플랫폼 수신기 및 직접 Sink를 순서대로 적용했다. 기존 GCP 직접 수집기의 ZIP 경로는 다른 작업자의 로컬 경로가 state에 기록돼 있어 이번 실행 경로로 갱신됐다. 같은 배포 ZIP을 먼저 내려받아 재업로드했으며 기존 GCP 수집기의 CodeSha256, 환경, 역할, Runtime, MemorySize와 Timeout은 전후 동일했다. 플랫폼 AWS 수신기는 신뢰한 Security GCP 그룹을 분기하는 새 코드와 GCP 중앙 그룹의 제한된 쓰기 권한을 적용했다.

- 실제 그룹: `/delta/security/gcp-firewall`, `/delta/security/gcp-firewall-threat`, Standard, 보관 30일.
- 구독 2개: 각 `delta-platform-central`, 빈 패턴, Random, 지정 Destination, 송신 역할 없음.
- Security의 전용 Lambda, WIF, Sink, Topic, Subscription과 DLQ가 정상 동작한다. 저장 후 ACK와 그룹별 실패 처리를 검증했다.
- 두 종류의 합성 메시지는 전용 Pub/Sub Topic에 게시했다. 실제 서비스의 위협 탐지 로그를 생성한 것으로 보고하지 않는다.
- 실제 새 방화벽 규칙 로그는 전환 전 19건, 전환 후 17건을 GCP 원본, Security와 플랫폼에서 대조했다. project, log_name, insert_id, event_id와 payload가 일치한다.
- Security와 플랫폼의 original JSON 및 source_timestamp는 문자열까지 완전히 같았다. GCP Logging 조회와 Pub/Sub 전달의 RFC3339 timestamp 및 receiveTimestamp는 소수점 끝 0 표기 차이가 있어 나노초를 잃지 않는 정규화 후 같은 시각인지 비교했다. 다른 원본 필드는 변경하거나 비교에서 제외하지 않았다.
- 2026-11-07 00:34:12 KST의 전환 이후 기존 플랫폼 Sink는 두 방화벽 logName을 전체 OR 조건 밖에서 제외한다. 그 밖의 GCP 직접 수집 smoke 1건과 기존 AWS 구독 smoke 1건도 각각 GCP 및 AWS 중앙 그룹에서 확인했다.
- 플랫폼의 기존 managed 리소스 43개 ID와 필터를 제외한 출력은 같다. 기존 Destination과 GuardDuty 및 NFW 구독, 기존 Security 그룹의 식별자와 보관 기간, 과거 GCP 그룹의 저장량을 보존했다.
- AWS와 GCP 네트워크 state의 inspection_mode는 모두 disabled다. 실제 새 NGFW 위협 로그 발생은 미검증이며 PASS로 기록하지 않는다.
- 두 root의 실제 마지막 Plan은 No changes, 종료0이다. 플랫폼 필터 출력에만 남았던 공백 정규화 차이는 실리소스 변경 0개의 별도 Apply로 state에 반영한 뒤 재확인했다.

### 검증 기록

실제 입력, 원본 state와 배포 ZIP, Plan 및 원본 로그는 ignored `.validation/gcp-security-logs-20261107/`에 제한된 권한으로 보관했다. 아래 Python 명령은 이 경로의 고정 의존성 venv를 사용했다.

| 날짜 | 실행한 명령 또는 확인 | 결과 |
| --- | --- | --- |
| 2026-11-07 | `PYTHONPATH=services/gcp-log-collector:platform/deploy/logging python -m unittest discover -s services/gcp-log-collector/tests -v` | 19개 통과. 부분 그룹 실패, 알 수 없는 logName, 저장 후 ACK와 기존 단일 그룹 계약 포함 |
| 2026-11-07 | 같은 PYTHONPATH로 `python -m unittest discover -s platform/deploy/logging/tests -v` | 15개 통과. 두 GCP 분기, 계정 및 원본 식별자 위조 거부, 잘못된 분류 방지와 기존 AWS 동작 포함 |
| 2026-11-07 | 두 root에 독립 `terraform init -backend=false`, `validate`, `test` | Security mock 3개와 플랫폼 mock 4개 통과. 실제 Apply가 아닌 모의 검증 |
| 2026-11-07 | `ruff check`와 `ruff format --check --config platform/backend/pyproject.toml platform/deploy/logging` | 통과. 첫 검사에서 import 순서 1건과 형식을 수정하고 플랫폼 15개 테스트를 다시 통과 |
| 2026-11-07 | 두 수집기 `build.py`, 새 root의 `terraform providers lock -platform=darwin_arm64 -platform=linux_amd64` | 현재 소스, ZIP 무결성과 Linux x86_64 라이브러리 검증. 공식 provider 서명과 양쪽 플랫폼 체크섬 기록 |
| 2026-11-07 | `operate.py baseline`, 두 root의 `init` | 계정과 state 소유권, 잠금 부재 및 기존 배포 코드 확보. 새 Security key에 기존 state 없음 |
| 2026-11-07 | `operate.py plan/show/apply --root platform --stage platform-relay` | Plan 종료2. 제한된 IAM 정책과 AWS Lambda, 같은 코드의 기존 GCP ZIP 경로만 갱신. Apply 생성0, 변경3, 삭제0 |
| 2026-11-07 | `operate.py plan/show/apply --root security --stage security-create` | Plan 종료2, 전용24개 생성만 확인. Apply 24 added, 0 changed, 0 destroyed |
| 2026-11-07 00:30:13 KST | `verify.py configure`, `publish`, `receive --require-real` | API 설정 일치. 두 합성 메시지 전달과 실제 규칙 로그19건의 원본 대조 완료. 초기 다음 주기 수신 대기는 성공으로 기록하지 않음 |
| 2026-11-07 00:34:12 KST | `operate.py plan/show/apply --root platform --stage platform-cutover` | 기존 Sink의 필터1개만 갱신. 생성과 삭제0. 전환 이후 새 수신 검증 시작 시각 기록 |
| 2026-11-07 | `verify.py receive --require-real --after-cutover`, `regression-publish`, `regression-receive` | 전환 뒤 실제 규칙 로그17건 일치. 나머지 GCP 직접 경로1건과 기존 AWS 경로1건도 올바른 중앙 그룹에 수신 |
| 2026-11-07 | `verify.py configure`, `health` | 구독과 보존 대상 재확인, 기존 managed43개 ID 유지, 양쪽 검사 disabled, 수집 stored=acked와 failed0 확인. 관측한 두 DLQ 표본0. backlog 지표에는 시각 지연과 일반 구독 미관측 구간이 있어 전체 큐가 비었다고 단정하지 않음 |
| 2026-11-07 | `operate.py plan --root security --stage security-final` | 실제 No changes, 종료0 |
| 2026-11-07 | `operate.py show/apply --root platform --stage platform-final`, `plan --root platform --stage platform-converged` | 첫 Plan은 리소스 변경0과 필터 출력 공백 차이뿐임을 기계 검증하고 state만 갱신. 마지막 실제 No changes, 종료0 |

Review: 승인한 두 보안 로그의 Security 우선 저장과 플랫폼 GCP 분류를 구현하고 실제 새 이벤트로 전후 검증했다. 장기 부하, 실제 DLQ 재처리, 대시보드 UI 및 새 NGFW 위협 발생 검증은 이번 결과에 포함하지 않는다. 원격 CI의 결과와 로컬 검증을 혼동하지 않는다.

## 공식 근거

- [Cloud Logging Sink와 전달 대상 설정](https://docs.cloud.google.com/logging/docs/export/configure_export_v2)
- [AWS와 Google Cloud의 Workload Identity Federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-other-clouds)
- [CloudWatch Logs 교차 계정 구독과 같은 리전 제약](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CrossAccountSubscriptions.html)
