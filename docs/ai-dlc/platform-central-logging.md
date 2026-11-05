# AWS·GCP 플랫폼 중앙 로그 수신

기준일: 2026-11-04. 사용자의 “그러면 띄워”, CloudWatch Logs 전달 선택, “GCP도 고려” 지시로 설계·구현·배포를 승인받았다.

## 목적과 범위

팀원이 연결할 실제 로그 수신 경로를 만든다. AWS/GCP 원본 로그는 유지하고 플랫폼 AWS 계정(616723161368, 서울)에 복사한다. 기존 네트워크 계정(482840608926)의 수집기와 GCP Sink는 변경하지 않는다. 서버 IP·VPN이 필요한 방식이 아니며 OTel 앱 메트릭/트레이스 수신과 구분한다.

## 요구사항과 설계

- AWS: CloudWatch Logs 구독 → 계정 허용 목록이 있는 Destination → 암호화된 Kinesis 1 shard → Lambda → 중앙 CloudWatch Logs.
- GCP: term2-delta의 Cloud Logging → 별도 Sink/Pub/Sub → AWS Lambda의 WIF 인증 → 중앙 CloudWatch Logs. 장기 SA 키는 만들지 않는다.
- 원본 계정/프로젝트·로그 이름·시각·이벤트 ID를 보존한다. 중복 전달은 가능하며 exactly-once를 주장하지 않는다.
- AWS 실패 원본은 S3, GCP 실패 메시지는 Pub/Sub DLQ에 7일 보관한다. 중앙 로그는 기본 3일, 대기 큐는 1일이다.
- Terraform root/state를 플랫폼 디렉터리에 분리한다. 기존 공유 네트워크/VM/앱/DB는 수정하지 않는다.
- 구독을 추가하지 않은 AWS 로그와 GCP 필터 외 로그는 자동 수집하지 않는다. VM 내부 로그 에이전트 설치는 이번 범위가 아니다.

## 구현 단위와 완료 조건

1. 수신 코드·최소 권한·실패 보관·소규모 Terraform 구성.
2. 중복/부분 실패/크기/계정 경계 테스트, Terraform validate·mock test·Plan 검토.
3. 실제 Apply, AWS 원본 로그와 GCP Cloud Logging 테스트 로그의 중앙 도착 확인.
4. 수신 ARN·GCP Sink/Topic·팀원 명령·검증 한계 문서화.

## 상태

- 구현: 완료. 전용 Terraform root, AWS 수집기, 기존 GCP 수집기의 선택형 스트림 이름, CI 검사, 실제 smoke 도구와 연결 안내를 추가했다.
- 로컬 테스트: Python 24개(AWS 9/GCP 15), Terraform mock 3개, validate, Python lint·format, git diff 검사를 통과했다.
- Plan: 43개 생성, 기존 변경·삭제 0개를 검토했다.
- Apply: 완료. 첫 Apply에서 AWS Destination Principal에 계정 ARN 대신 계정 ID가 필요하다는 API 오류가 발생했다. 이를 수정하고 남은 정책 1개를 재계획·적용했다. 기존 리소스는 변경하지 않았다.
- 실제 수신: AWS 같은 계정 임시 로그 그룹 → 논리 Destination → 중앙 로그, GCP Cloud Logging → Pub/Sub → 실제 WIF Lambda → 중앙 로그를 확인했다. 테스트 원본 계정/프로젝트·시각·본문·event_id가 보존됐다. 임시 AWS 원본 그룹은 제거했다.
- GCP 초기 주의: Sink 생성 직후 첫 테스트 항목은 목적지에서 확인되지 않았다. 필터 일치·쓰기 권한·수집기 정상 동작과 실제 네트워크 로그 수신을 확인하고 새 테스트 로그로 중앙 도착을 검증했다. 설정 전파 시점에 관한 추정이며 첫 항목이 누락된 원인을 확정하지는 않았다. 과거 로그의 자동 재전송을 주장하지 않는다.
- 후속 독립 재검증: 새 마커를 사용한 `smoke.py` 전체 실행도 추가 수동 전송 없이 AWS·GCP 모두 통과했다. `smoke-confirmed.json`에 원본 정보와 중앙 이벤트 ID, 임시 AWS 그룹 삭제 결과를 기록했다.
- 최종 실제 Plan: `No changes`, 상세 종료 코드 0.
- 커밋 / 푸시 / 병합: 구현 `bb1f2f0`과 Linux lock 수정 `aa1cac5`를 게시했다. [PR #34](https://github.com/2026-sbh-delta/delta-network-gateway/pull/34)는 전체 CI 통과 후 main `660d16b`로 병합했다. 최신 main의 발표 화면 변경을 반영하면서 `docs/README.md`의 양쪽 진행 기록도 보존했다. 원래 사용자 작업 디렉터리의 branch·파일은 바꾸지 않았다.
- CI: [41838815926](https://github.com/2026-sbh-delta/delta-network-gateway/actions/runs/41838815926)의 플랫폼 test job과 Terraform 9개 root가 모두 통과했다. 중앙 수집 테스트·기존 백엔드/DAST/AI·브라우저·PostgreSQL 이전/세션 회귀 검사를 포함한다.
- 첫 CI의 새 logging Terraform 작업은 Linux provider 체크섬 누락으로 실패했다. `terraform providers lock -platform=darwin_arm64 -platform=linux_amd64`로 양쪽 패키지의 공식 서명·체크섬을 기록해 수정했고 최신 CI에서 통과했다. 실패가 확인된 이전 중복 실행은 중지했다. AWS/GCP 실제 수신 실패와는 별개다.
- GitHub 기존 플랫폼 인프라 workflow는 이 새 root를 실행하지 않는다. 최초 배포는 승인된 관리자 세션과 GCP ADC로 수행하며 원격 S3 state에 기록한다.

## 실제 연결 정보

| 항목 | 값 |
|---|---|
| AWS Destination | `arn:aws:logs:ap-northeast-2:616723161368:destination:delta-platform-logs` |
| 허용 AWS 계정 | `482840608926`, `616723161368` |
| 중앙 AWS 로그 | `/delta-platform/central/aws` |
| 중앙 GCP 로그 | `/delta-platform/central/gcp` |
| GCP Sink / Topic | `delta-platform-logs` / `projects/term2-delta/topics/delta-platform-logs` |
| GCP Subscription | `projects/term2-delta/subscriptions/delta-platform-logs-aws` |
| GCP WIF Pool | `delta-platform-logs-aws` (수집기 역할만 허용) |
| 상태 파일 | `s3://delta-platform-tf-616723161368/platform/logging.tfstate` |

## 검증 명령과 한계

2026-11-04에 실행했다. 원문 결과와 테스트 마커는 로컬 ignored `platform/.runtime/logging/`에 남겼다.

- `PYTHONPATH=services/gcp-log-collector:platform/deploy/logging python3 -m unittest discover -s platform/deploy/logging/tests -v`: 9개 통과. 권한 경계, UTF-8 크기, 부분 실패, 중복 ID, 재귀 방지 검증.
- GCP 수집기 의존성이 있는 Python으로 `PYTHONPATH=services/gcp-log-collector python -m unittest discover -s services/gcp-log-collector/tests -v`: 15개 통과. 기존 기본 스트림과 새 플랫폼 스트림 옵션도 확인.
- `terraform validate`, `terraform test`: 유효, 3개 통과. 최초 mock의 EventBridge ARN 누락은 수정 후 재실행했다.
- 두 수집기 ZIP 빌드 성공. GCP ZIP의 Linux x86_64 네이티브 파일·현재 소스 일치를 확인했다. 최초 uv 환경에는 pip가 없어 빌드가 실패했고 pip가 있는 Python으로 정상 빌드했다.
- `AWS_PROFILE=delta-platform terraform plan/apply`: 위의 실제 결과. 최종 `plan -detailed-exitcode`: 0.
- `smoke.py --outputs ... --evidence ...`: 양쪽 원본→중앙 저장 검증. 별도 GCP 새 로그 쓰기도 수행했으므로 첫 시험은 단일 전송만으로 성공했다고 표현하지 않는다.
- GCP 기존 `delta-lab-network-security` Sink와 새 Sink가 각각 원래/전용 Topic으로 연결돼 있음을 확인했다. 기존 수집기·기본 로그 버킷은 변경하지 않았다.

AWS **다른 계정(482840608926)의 실제 원본 그룹 구독**은 아직 하지 않았다. 해당 그룹 정보가 없고 팀원이 원본에서 추가할 작업이다. 정책 배포와 같은 계정 e2e 검증을 타 계정 실제 수신 완료로 표현하지 않는다. GCP는 실제 프로젝트의 전용 경로를 검증했다. 전체 서비스의 앱 로그 에이전트, 플랫폼 웹 조회, 부하·장기 장애·실제 DLQ 재처리·알림 수신자 구성은 이번 검증에 포함하지 않는다.

운영·중지·실패 보관·전체 입력/출력은 [배포 안내](../../platform/deploy/logging/README.md)를 따른다.
