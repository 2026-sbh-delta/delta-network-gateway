# AWS 중앙 계정의 GCP 로그 수집기

기존 ANF와 분리한 state로 Lambda, 최소 IAM 역할, 1분 poll rule과 CloudWatch 로그 그룹을 만든다. GCP Pub/Sub를 TLS API로 pull하므로 업무 VPN이 중단돼도 수집기를 별도로 운영할 수 있다. AWS ANF를 생성하거나 원복하지 않는다. GCP 쪽 준비는 [GCP 모듈](../network-security-gcp/README.md), 수집 코드는 [서비스 안내](../../services/gcp-log-collector/README.md)를 따른다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `central_account_id` | `string` | 필수 | Provider와 STS로 검증할 중앙 계정 |
| `region` | `string` | `ap-northeast-2` | Lambda/CloudWatch 리전 |
| `name` | `string` | `delta-lab` | 역할과 함수 접두사. GCP WIF 신뢰 역할과 일치시킴 |
| `subscription` | `string` | 필수 | `term2-delta`의 정확한 pull subscription |
| `wif_audience` | `string` | 필수 | GCP `log_handoff`에서 받은 숫자 Project Number 기반 WIF audience |
| `collector_zip` | `string` | 필수 | 빌드한 Linux x86_64 Python 3.13 Lambda ZIP |
| `log_retention_days` | `number` | `30` | 원본/수집기 로그의 유한 보존 일수 |

복합 object 입력은 없다. `subscription`과 `wif_audience`는 비밀값이 아니다. IAM/서비스 계정의 영구 키를 환경 변수에 넣지 않는다. Lambda의 임시 실행 역할 자격 증명을 GCP WIF로 교환한다.

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| `collector_role_arn` | GCP에서 신뢰할 정확한 역할 ARN |
| `log_group` | GCP 원본 로그 목적지 |
| `collector_log_group` | 실행 로그와 수집 카운터 |
| `function_name` | 수동 상태 확인에 사용할 함수 이름 |
| `poll_rule_name` | 수집 중지/재개에 사용할 EventBridge rule |

## 준비와 검증

1. [수집기 빌드](../../services/gcp-log-collector/README.md)를 실행한다. macOS 가상환경을 그대로 ZIP에 복사하지 않는다.
2. 기존 중앙 계정의 S3 backend에서 이 root 전용 key를 사용한다. 중앙 네트워크와 같은 state key를 쓰지 않는다. 예시 ID/Backend를 실제값으로 바꾼 뒤 인증 주체와 저장 Plan을 확인한다.
3. GCP의 `aws_collector_role_arn`과 이 root의 역할 이름을 일치시키고 `log_handoff`를 전달한다. bootstrap 전에 역할 ARN은 검증된 계정과 고정 이름으로 명시할 수 있다.
4. 다음 로컬 검증은 별도 임시 디렉터리에서 mock Plan만 실행한다. 실제 배포는 아니다.

```bash
bash terraform/gcp-log-ingest-aws/scripts/validate.sh
```

검증 스크립트는 중앙 모듈에 초기화된 AWS Provider와 이 root의 lock 파일을 사용한다. Provider가 없으면 먼저 공식 패키지를 초기화한다. Lambda는 Python 3.13/x86_64, 메모리 256MB, timeout 50초, 예약 동시성은 설정하지 않는다. 실제 계정의 지역 한도가 10이고 AWS는 예약 후 미예약 용량 100을 요구해 예약 1 설정을 사용할 수 없었다. 1분 주기/50초 timeout과 EventBridge 및 Lambda 비동기 재시도 0회/최대 이벤트 수명 60초로 호출 적체를 제한한다. 중첩 호출 가능성은 남으며 Pub/Sub lease와 at-least-once 처리를 사용한다. IAM은 원본/실행 로그 그룹의 CreateLogStream/PutLogEvents만 허용한다. 생성한 EventBridge rule의 중앙 계정 호출만 허용한다.

## 운영

원본은 `/delta/lab/gcp/term2-delta/network-security`, 실행 기록은 `/aws/lambda/<name>-gcp-log-collector`에 보관한다. 두 그룹은 해체 시 기본 보존하므로 다시 관리하려면 기존 그룹을 import한다. 기존 ANF FLOW/ALERT 그룹과 전달 정책은 변경하지 않는다.

Lambda Errors 경보는 콘솔에서 확인할 수 있으며 SNS 알림 수신자는 자동 등록하지 않는다. poll rule 중지는 추가 수집만 중지하며 기존 Pub/Sub 메시지는 보존 기간을 따른다. GCP 원본과 `insert_id`/`event_id`를 대조하고 같은 이벤트의 중복 가능성을 고려한다. [GCP 조회](queries/network.cwli)와 [AWS ANF 함께 조회](queries/combined-network.cwli)를 제공한다. 함께 조회할 때는 Logs Insights에서 GCP 그룹과 기존 ANF flow/alert 그룹을 모두 선택한다. ANF disabled 중 신규 ANF 검사 로그가 없는 것은 정상이며 과거 기록의 시각 범위를 지정한다. 실제 인증/저장/장애 복구 결과는 [AI-DLC](../../docs/ai-dlc/gcp-central-network.md)에 기록한다.
