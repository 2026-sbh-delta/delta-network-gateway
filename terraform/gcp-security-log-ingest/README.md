# GCP 방화벽 로그의 Security 계정 수집

`term2-delta`의 방화벽 규칙 로그와 NGFW 위협 로그를 Security 계정 `482840608926`의 서울 CloudWatch Logs에 먼저 저장한다. [승인과 실제 검증](../../docs/ai-dlc/gcp-security-log-consolidation.md)을 따른다.

```text
Cloud Logging -> 전용 Sink/PubSub -> Security Lambda, WIF
  -> /delta/security/gcp-firewall
     /delta/security/gcp-firewall-threat
  -> delta-platform-central -> 기존 Destination/Kinesis
  -> 플랫폼 수신기 -> /delta-cp/central/gcp
```

전용 구독만 소비하며 원본 LogEntry와 안정적인 GCP event_id를 보존한다. 각 대상 그룹 저장에 성공한 메시지만 ACK한다. 알 수 없는 logName과 실패한 그룹의 메시지는 ACK하지 않는다. 전달은 at-least-once이며 `event_id`로 중복을 구분한다. 기존 과거 로그 그룹, GCP 원본 저장과 검사 모드는 이 root가 관리하지 않는다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `security_account_id` | string | `482840608926` | 고정 Security 계정 및 AWS provider 검사 |
| `aws_region` | string | `ap-northeast-2` | 원본 그룹과 기존 플랫폼 Destination의 같은 리전 |
| `gcp_project_id` | string | `term2-delta` | 유일한 원본 프로젝트. 번호도 별도 검사 |
| `name` | string | `delta-security-gcp-logs` | 독립 수집 리소스 접두어 |
| `collector_zip` | string | 필수 | Linux x86_64 Python 3.13용 검증한 ZIP |
| `retention_days` | number | `30` | 원본과 실행 로그 보관 일수 |
| `sink_enabled` | bool | `true` | 원본 Sink의 새 전달 활성화 |
| `collector_enabled` | bool | `true` | 1분 주기 pull 실행 활성화 |
| `forwarding_enabled` | bool | `true` | 플랫폼 구독 2개만 제어. 그룹은 유지 |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| `source_log_groups` | 인코딩된 GCP log ID별 Security 그룹 이름 |
| `platform_destination_arn` | 기존 플랫폼 Destination ARN |
| `subscription_filter_names` | log ID별 구독 이름. 전달 중지 시 null |
| `collector_function` | Security 수집 Lambda 이름 |
| `poll_rule` | 1분 주기 EventBridge 규칙 이름 |
| `gcp_sink` | 두 방화벽 로그 전용 Sink 이름 |
| `gcp_subscription` | 전용 일반 구독 경로 |
| `gcp_failed_subscription` | 실패 메시지 7일 보존 구독 |
| `wif_audience` | 전용 AWS WIF provider audience |

## 배포와 검증

기존 ADC와 Security AWS SSO를 사용한다. 프로젝트 공유 API와 Pub/Sub 서비스 에이전트는 미리 존재해야 한다. 새 WIF는 Security 계정과 전용 역할로 제한하며 장기 키를 만들지 않는다. 플랫폼 수신기의 GCP 중계 기능과 제한된 쓰기 권한을 먼저 준비한다.

```bash
python3 services/gcp-log-collector/build.py
terraform -chdir=terraform/gcp-security-log-ingest init -backend-config=backend.hcl
terraform -chdir=terraform/gcp-security-log-ingest validate
terraform -chdir=terraform/gcp-security-log-ingest test
AWS_PROFILE=sbh-delta-security terraform -chdir=terraform/gcp-security-log-ingest plan -out=reviewed.tfplan
AWS_PROFILE=sbh-delta-security terraform -chdir=terraform/gcp-security-log-ingest apply reviewed.tfplan
```

실제 ignored `backend.hcl`은 `sbh-delta-central-tf`의 전용 key `delta/lab/gcp-security-log-ingest/terraform.tfstate`, 서울 리전, 암호화와 S3 lockfile을 사용한다. 기존 네트워크 state에 합치지 않는다. ZIP 절대 경로와 실제 입력도 ignored tfvars에 둔다.

플랫폼 root는 `gcp_security_relay_enabled=true`로 수신을 먼저 준비하고 기존 직접 수집은 유지한다. Security 두 그룹의 전달과 실제 방화벽 원본 수신 확인 후 플랫폼의 `gcp_firewall_direct_collection_enabled=false`를 적용한다. 같은 플랫폼 구독을 두 Lambda가 경쟁 소비하지 않는다. 전환 구간 중복은 원래 GCP event_id로 구분한다.

수용 검증은 GCP 원본, Security 그룹과 `/delta-cp/central/gcp`에서 project, log_name, insert_id, event_id, source timestamp와 original을 대조한다. 수동 smoke와 실제 서비스 로그 검증을 구분한다. NGFW Endpoint가 꺼져 있으면 실제 새 위협 탐지는 미검증으로 남긴다.

## 관측과 복귀

각 원본 그룹의 구독은 `delta-platform-central`, 빈 패턴, Random과 기존 Destination을 사용한다. 원본 그룹은 Standard, 보관 30일이 기본이며 `skip_destroy=true`다. Lambda Errors와 `ingest_summary`의 stored/acked/failed, 구독 backlog와 DLQ를 함께 본다. 정상 구독에 수동 pull/ack를 실행하지 않는다.

```bash
aws logs describe-subscription-filters --profile sbh-delta-security --region ap-northeast-2 --log-group-name /delta/security/gcp-firewall
aws logs describe-subscription-filters --profile sbh-delta-security --region ap-northeast-2 --log-group-name /delta/security/gcp-firewall-threat
aws events describe-rule --profile sbh-delta-security --region ap-northeast-2 --name delta-security-gcp-logs-poll
```

복귀 시 기존 플랫폼 Sink의 `gcp_firewall_direct_collection_enabled=true`를 먼저 적용하고 실제 직접 수신을 확인한다. 그다음 새 Security Sink의 `sink_enabled=false`를 적용해 새 전달을 멈추고 대기열과 실행 종료를 확인한다. 필요하면 `collector_enabled=false`, `forwarding_enabled=false`를 별도 Plan으로 적용한다. 그룹과 대기 메시지를 삭제하지 않는다. 삭제 관리에서 보존한 그룹을 다시 관리할 때는 import가 필요하다.
