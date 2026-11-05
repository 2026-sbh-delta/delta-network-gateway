# AWS·GCP 중앙 로그 수신

AWS와 GCP에 이미 모이는 로그를 **플랫폼 계정의 CloudWatch Logs에 복사**한다. 별도 로그 서버 IP는 없다. AWS 원본 로그와 GCP Cloud Logging 저장은 유지한다.

```text
AWS CloudWatch Logs → 구독 필터 → Destination → Kinesis → Lambda ─┐
                                                              ├→ 플랫폼 CloudWatch Logs
GCP Cloud Logging  → Sink → Pub/Sub → Lambda(WIF 인증) ──────────┘
```

- 중앙 계정: `616723161368`, 리전: `ap-northeast-2`(서울).
- AWS 저장: `/delta-cp/central/aws` (2026-11-06 조회).
- GCP 저장: `/delta-cp/central/gcp` (2026-11-06 조회).
- 플랫폼 서비스(오케스트레이터·Jenkins·SAST·DAST·차분 에이전트) 로그: `/delta-cp/platform/<서비스>`(각 호스트가 직접 기록, `terraform/platform-aws`).
- **이름 분리(11/5):** 같은 계정에 A 기업 로그(`/delta/…`, `/aws/…`)가 함께 있다. 플랫폼 로그는 모두 `/delta-cp/` 아래에 둔다. 권한·KMS 분리는 후순위다.
- GCP 프로젝트: `term2-delta`. VM·Cloud Run·방화벽/VPN/Router 로그를 기본 수집한다. 감사 로그는 기본 제외한다.
- 원본 계정/프로젝트·로그 이름·원본 시각·이벤트 ID를 함께 저장한다. CloudWatch 화면의 이벤트 시각은 **수집 시각**이다.
- 같은 메시지가 재전달될 수 있다. 집계 시 `event_id`로 중복을 구분한다.
- 중앙 로그 기본 3일, 전송 대기 24시간, 실패 보관 7일. 원본 로그 보관 설정은 변경하지 않는다.
- OTel 메트릭·트레이스 수신 주소가 아니다. 이 작업으로 플랫폼 웹의 로그 화면이 자동 연결되지는 않는다.

2026-11-06 현재 운영 그룹은 위 `/delta-cp/central/*`이며 AWS 수집기의 `DESTINATION_LOG_GROUP`도 확인했다. 다만 tracked `aws.tf`는 아직 이전 `/delta-platform/central/*`를 정의한다. 아래 배포 명령은 최초 구축 절차이며, 현재 환경에 실행하려면 이 차이를 먼저 검토해야 한다. 이번 GuardDuty 송신 경로 설계에서는 플랫폼 root의 코드/state/운영 설정이나 Plan/Apply를 변경 또는 실행하지 않았다. [조회 근거와 검증 한계](../../../docs/ai-dlc/platform-central-logging.md#2026-11-06-guardduty-송신-연결-설계와-현재-로그-이름)

## 팀원에게 전달할 AWS 설정

수신 주소는 다음 ARN이다. Terraform `aws_destination_arn` 출력과 일치하는지 확인한다.

```text
arn:aws:logs:ap-northeast-2:616723161368:destination:delta-platform-logs
```

현재 허용 계정은 `482840608926`, `616723161368`다. 다른 계정은 Terraform의 `source_aws_accounts`에 먼저 추가한다. 송신 로그 그룹도 **서울 리전**이어야 한다. 다른 리전에는 해당 리전용 Destination을 별도 준비한다.

2026-11-06 중앙 계정 `482840608926`의 `/delta/security/guardduty`는 `delta-platform-central` 구독으로 위 Destination에 연결했다. 원본과 `/delta-cp/central/aws`에서 같은 GuardDuty 샘플1건의 본문/출처/시각/ID를 확인했다. 구독은 이 저장소의 중앙 Terraform root가 관리하며 [중앙 Runbook](../../../terraform/network-security/RUNBOOK.md#guardduty-finding-로그-전달)으로 조회/중지/재개한다. [실제 검증 기록](../../../docs/ai-dlc/aws-guardduty-logging.md)을 따른다.

송신 계정에서 필요한 로그 그룹 하나를 선택하고 실행한다. `SOURCE_LOG_GROUP`만 실제 값으로 바꾼다. 빈 필터는 **선택한 그룹의 모든 새 이벤트**를 전달하며, 과거 로그를 복사하지 않는다. 기존 구독은 먼저 조회하고 덮어쓰지 않는다.

```bash
SOURCE_LOG_GROUP='/실제/원본/로그그룹'
aws logs describe-subscription-filters \
  --region ap-northeast-2 --log-group-name "$SOURCE_LOG_GROUP"
aws logs put-subscription-filter \
  --region ap-northeast-2 \
  --log-group-name "$SOURCE_LOG_GROUP" \
  --filter-name delta-platform-central \
  --filter-pattern '' \
  --distribution Random \
  --destination-arn arn:aws:logs:ap-northeast-2:616723161368:destination:delta-platform-logs
```

실행 주체에는 원본 로그 그룹과 Destination에 대한 `logs:PutSubscriptionFilter`, 원본 그룹의 `logs:DescribeSubscriptionFilters` 권한이 필요하다. 수신 측 Destination 정책만으로 송신 IAM 권한을 대신할 수 없다. 논리 Destination에 연결하므로 위 명령에 `--role-arn`이나 Access Key를 넣지 않는다.

**재전송 금지:** `/delta-cp/*`(중앙·플랫폼 서비스), 이전 `/delta-platform/central/*`와 `/aws/lambda/delta-platform-logs-*`는 구독 대상에서 제외한다. 수집기도 이 그룹에서 온 이벤트는 버린다. 계정 전체 구독을 만드는 작업이 아니다. 그룹당 기존 구독 한도도 확인한다.

## 팀원에게 전달할 GCP 설정

- Sink: `delta-platform-logs`
- Topic: `projects/term2-delta/topics/delta-platform-logs`
- AWS 수집기 전용 Subscription: `projects/term2-delta/subscriptions/delta-platform-logs-aws`
- 별도 실패 Subscription: `projects/term2-delta/subscriptions/delta-platform-logs-dlq`

이 프로젝트에서 기본 필터에 해당하는 로그는 **기존 Cloud Logging에 올리면 된다**. 팀원이 AWS 주소로 직접 전송하거나 새 SA 키를 만들 필요가 없다. AWS Lambda가 자신의 임시 IAM 자격 증명을 WIF로 교환해 전용 Subscription을 읽는다. 다른 수집기는 같은 Subscription을 소비하지 않는다.

VM의 앱 파일/stdout 로그가 Cloud Logging에 없다면 Ops Agent 등 원본 수집 설정이 먼저 필요하다. 이 Sink는 이미 있는 로그만 전달한다. 추가 로그 종류는 `gcp_log_filter`를 검토해 확장한다. 다른 프로젝트는 별도 Sink·권한·수집기 계약이 필요하다.

이 root는 플랫폼 전용 Sink와 Topic 및 Subscription만 소유한다. 기존 네트워크 계정 전용 `delta-lab-network-security` 전달 경로의 정리와 보존 로그는 [네트워크 정리 기록](../../../docs/ai-dlc/gcp-central-network.md#2026-11-05-gcp-로그-전달-경로-정리-검토)을 따른다. 공통 `services/gcp-log-collector`는 이 플랫폼 수집에서도 계속 사용한다.

## 배포

Terraform `1.16.4`, AWS `6.71.0`, Google `8.10.0`을 사용한다. AWS SSO와 GCP ADC로 실행한다. 장기 키를 입력하거나 커밋하지 않는다. GCP Logging/PubSub/IAM/STS API와 Pub/Sub 서비스 에이전트가 기존 프로젝트에 준비돼 있어야 한다.

저장소 루트에서:

```bash
python3 platform/deploy/logging/build.py
python3 services/gcp-log-collector/build.py
```

두 번째 빌드에는 `pip`가 있는 Python이 필요하다. 잠긴 의존성을 Linux x86_64 대상으로 묶는다. `terraform.tfvars` 예시:

```hcl
aws_account_id      = "616723161368"
source_aws_accounts = ["616723161368", "482840608926"]
aws_lambda_zip      = "/absolute/repository/platform/.runtime/logging/aws-collector.zip"
gcp_lambda_zip      = "/absolute/repository/services/gcp-log-collector/.build/collector.zip"
```

`backend.hcl`:

```hcl
bucket       = "delta-platform-tf-616723161368"
key          = "platform/logging.tfstate"
region       = "ap-northeast-2"
encrypt      = true
use_lockfile = true
```

```bash
cd platform/deploy/logging
AWS_PROFILE=delta-platform terraform init -backend-config=backend.hcl
terraform validate
terraform test
AWS_PROFILE=delta-platform terraform plan -out=reviewed.tfplan
# 생성·변경·삭제 대상을 확인한 뒤 적용
AWS_PROFILE=delta-platform terraform apply reviewed.tfplan
AWS_PROFILE=delta-platform terraform output
```

기존 GitHub 플랫폼 인프라 workflow는 이 root를 실행하지 않는다. 최초 Apply는 승인된 관리자 세션으로 진행하고 S3에 상태를 보관한다. CI는 코드·mock 검증을 수행한다. 향후 GitHub Apply 연결 시 AWS 역할 범위와 GCP OIDC 신뢰를 별도로 추가해야 한다.

## 검증과 운영

```bash
PYTHONPATH=services/gcp-log-collector:platform/deploy/logging \
  python3 -m unittest discover -s platform/deploy/logging/tests -v
PYTHONPATH=services/gcp-log-collector \
  python3 -m unittest discover -s services/gcp-log-collector/tests -v
```

실제 검증은 각 클라우드에 고유 마커를 기록하고 중앙 그룹에서 같은 마커·원본 정보를 확인한다. 로컬 단위 테스트, 같은 계정 구독, 다른 계정 구독 검증은 구분해서 기록한다. [현재 배포·검증 기록](../../../docs/ai-dlc/platform-central-logging.md)을 참고한다.

저장소 루트에서 `boto3`, `google-auth`, `requests`가 있는 Python으로 실행한다. 검증기는 수신 AWS 계정에만 임시 로그 그룹을 만들고 종료 시 삭제한다. GCP에는 무해한 테스트 로그 한 건을 쓰며 실제 수집기의 주기 실행을 기다린다.

```bash
AWS_PROFILE=delta-platform terraform -chdir=platform/deploy/logging output -json \
  > platform/.runtime/logging/outputs.json
python3 platform/deploy/logging/smoke.py \
  --outputs platform/.runtime/logging/outputs.json \
  --evidence platform/.runtime/logging/smoke.json
```

새 GCP Sink·IAM 설정은 전파에 시간이 걸릴 수 있다. 최초 로그가 수집되지 않으면 Sink 필터·쓰기 권한·오류를 확인하고 새 로그로 다시 검증한다. Sink는 적용 전에 놓친 로그를 자동으로 소급 복사하지 않는다. [GCP 라우팅 안내](https://docs.cloud.google.com/logging/docs/export/configure_export_v2)

CloudWatch에서 수집기 `Errors`, AWS `IteratorAge`, `FailedEnvelopes`, 실패 S3 전달 오류 알람을 확인한다. 알람은 생성하지만 이메일·Slack 수신자는 등록하지 않는다. GCP에서는 Subscription의 미확인 메시지 수·최장 대기 시간과 DLQ를 함께 확인한다. GCP 수집기는 1분마다 최대 10회×100개를 읽으며 실제 처리량은 50초 실행 예산에 따라 줄어든다. 대용량 운영 처리량을 검증한 구성은 아니다.

- AWS: 쓰기 실패는 체크포인트를 넘기지 않고 재시도한다. 3회 재시도 또는 최대 1시간 처리 지연 후 원본 이벤트 묶음을 S3에 보관한다. 장애 복구 후 원본의 시각과 ID를 보존해 재처리한다.
- GCP: 중앙 저장에 성공한 메시지만 ack한다. 반복 실패는 Pub/Sub DLQ에 보관한다. DLQ를 먼저 확인한 후 원본 형식으로 재게시한다. 수동 pull로 정상 Subscription의 ack를 소모하지 않는다.
- 일시 중지: AWS는 송신 구독 제거, GCP는 **플랫폼 Sink만** 비활성화한다. 변경 전파와 대기 메시지를 확인한 후 해당 수집기를 중지한다. Cloud Logging 원본 저장은 유지한다.
- 롤백: 새 root의 Plan을 검토해 제거한다. S3 실패 원본이 남아 있으면 `force_destroy=false`로 삭제를 막는다. 먼저 필요한 로그를 보관한다.

1 shard Kinesis에는 유휴 시간에도 비용이 있다. Lambda/PubSub/중앙 로그에도 사용량 비용이 생긴다. 해커톤 후 유지 여부와 원본·중앙 중복 보관 기간을 확인한다.

## 입력 속성

| 속성 | 타입 | 기본값 | 역할 |
|---|---|---|---|
| `aws_account_id` | string | 필수 | 수신 AWS 계정과 provider 계정 검사 |
| `aws_region` | string | `ap-northeast-2` | 수신 리전. 원본 로그 그룹과 동일해야 함 |
| `source_aws_accounts` | set(string) | 필수 | 구독을 허용할 AWS 계정 ID 목록 |
| `gcp_project_id` | string | `term2-delta` | GCP 원본 프로젝트. 현재 이 프로젝트로 제한 |
| `name` | string | `delta-platform-logs` | 전용 수신 리소스 접두어 |
| `retention_days` | number | `3` | 중앙·수집기 CloudWatch 로그 보관 기간 |
| `aws_lambda_zip` | string | 필수 | AWS 수집기 ZIP 경로 |
| `gcp_lambda_zip` | string | 필수 | GCP 수집기 ZIP 경로 |
| `gcp_log_filter` | string/null | `null` | 기본 VM·Cloud Run·네트워크 필터를 대체할 Logging 필터 |

## 출력 속성

| 속성 | 역할 |
|---|---|
| `aws_destination_arn` | AWS 팀원이 구독 필터에 입력할 ARN |
| `aws_region` | 수신 리전 |
| `allowed_source_accounts` | 실제 허용 계정 목록 |
| `central_log_groups` | AWS/GCP 중앙 로그 그룹 |
| `collector_functions` | AWS/GCP 수집 Lambda 이름 |
| `aws_failure_bucket` | AWS 실패 원본 보관 S3 |
| `gcp_sink` | 플랫폼용 Cloud Logging Sink |
| `gcp_topic` | GCP 로그 Topic |
| `gcp_subscription` | AWS 수집기 전용 Subscription |
| `gcp_failed_subscription` | GCP 실패 메시지 보관 Subscription |
| `gcp_log_filter` | 실제 적용한 로그 범위 |
| `gcp_wif_audience` | 수집 Lambda 전용 WIF Provider |

공식 동작: [CloudWatch Destination](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CreateDestination.html), [리전 제한](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CrossAccountSubscriptions.html), [Lambda 실패 원본 보관](https://docs.aws.amazon.com/lambda/latest/dg/kinesis-on-failure-destination.html), [CloudWatch 이벤트 제한](https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_PutLogEvents.html).
