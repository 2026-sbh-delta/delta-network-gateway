# GCP 로그를 AWS로 전달

Pub/Sub pull subscription의 원래 LogEntry를 CloudWatch Logs에 저장한 뒤 ack한다. 부분 저장 실패, 실패한 ack, 잘못된 JSON, 다른 프로젝트와 oversized 메시지는 재시도/DLQ를 위해 ack하지 않는다. at-least-once이므로 저장 후 ack 실패 시 중복될 수 있고 `event_id`는 같은 원본에 대해 유지된다. 원문을 자르거나 성공하지 않은 저장을 성공으로 기록하지 않는다.

CloudWatch event timestamp는 수집 시각이며 원본 발생 시각은 `source_timestamp`와 `original.timestamp`에 보존한다. 원본 project/logName/insertId와 JSON을 함께 저장한다. 이 수집기는 트래픽 검사나 AWS ANF를 실행하지 않는다.

현재 [플랫폼 중앙 로그 수집](../../platform/deploy/logging/README.md)에서 사용하는 공통 코드다. 네트워크 계정 전용 전달 경로 정리와 별개로 플랫폼의 빌드와 테스트에서 계속 사용한다.

## 실행과 패키징

`requirements.txt`는 직접 의존성, `requirements.lock`은 실제 설치하여 고정한 전체 버전이다. Python 3.13 Lambda용 Linux x86_64 wheel로 빌드하며 macOS native 라이브러리를 복사하지 않는다. 산출물은 ignored `.build/collector.zip`이고 빌드는 업로드/배포를 수행하지 않는다.

```bash
python3 -m venv /tmp/delta-gcp-collector-venv
/tmp/delta-gcp-collector-venv/bin/python -m pip install -r services/gcp-log-collector/requirements.lock
PYTHONPATH=services/gcp-log-collector /tmp/delta-gcp-collector-venv/bin/python -m unittest discover -s services/gcp-log-collector/tests -v
/tmp/delta-gcp-collector-venv/bin/python services/gcp-log-collector/build.py
```

| 환경 변수 | 역할 |
| --- | --- |
| `PUBSUB_SUBSCRIPTION` | `projects/term2-delta/subscriptions/...` 경로 |
| `WIF_AUDIENCE` | 숫자 Project Number의 `.../providers/aws-collector` audience |
| `DESTINATION_LOG_GROUP` | 중앙 GCP 원본 로그 그룹 |

AWS Lambda가 제공하는 임시 역할 자격 증명과 Region을 Google AWS WIF가 사용한다. Google 영구 서비스 계정 키, AWS 영구 키와 사용자 ADC를 패키지에 넣지 않는다. 로컬 테스트는 주입한 fake client와 실제 Google SDK의 오프라인 AWS 서명 생성을 검증한다. 네트워크/metadata 호출 없이 Lambda 환경 자격 증명을 사용하는지 확인하며, 실제 Google/AWS 인증 성공을 의미하지 않는다.

한 pull은 최대 100개를 요청하지만 실제 반환 수가 더 적을 수 있다. 한 호출에서 최대 10회 pull하며, 추가 pull은 처음 10초 안이고 Lambda 잔여 시간이 40초 이상일 때만 시작한다. 빈 배치나 저장/ack 실패가 나오면 중단한다. 30초 long poll과 후속 저장/ack 시간을 남기며 CloudWatch byte 제한에 맞춰 저장 배치를 나눈다. 전체 저장 실패와 부분 거부를 구분하고 받아들인 이벤트의 ack ID만 처리한다. Lambda timeout 직전의 실패나 ack 유실도 중복 재처리로 복구할 수 있으며 exactly-once를 보장하지 않는다.

운영 중 `ingest_summary`의 pulled/stored/acked/failed와 Lambda Errors, GCP subscription backlog/DLQ를 함께 확인한다. subscription pull 자체가 실패하면 빈 배치 성공으로 처리하지 않는다. 실제 end-to-end 검증은 [플랫폼 실행 안내](../../platform/deploy/logging/README.md#검증과-운영)를 따른다.
