# AWS·GCP 플랫폼 중앙 로그 수신

기준일: 2026-11-04. 사용자의 “그러면 띄워”, CloudWatch Logs 전달 선택, “GCP도 고려” 지시로 설계·구현·배포를 승인받았다.

2026-11-06 현재 연결 정보: 실제 중앙 그룹은 `/delta-cp/central/aws`, `/delta-cp/central/gcp`이고 각각 보관 3일이다. AWS 수집기의 실제 저장 그룹과 원본 계정 허용도 재조회했다. 아래 최초 배포 기록과 당시 Plan 결과는 보존하며, 현재 tracked 플랫폼 Terraform의 `/delta-platform/central/*` 정의와 실제 이름의 차이는 아래 후속 기록을 따른다.

2026-11-07 후속: 현재 tracked 코드와 원격 state 및 실제 중앙 이름은 `/delta-cp/central/*`로 일치한다. 사용자 승인으로 두 GCP 방화벽 로그만 Security 우선 저장으로 전환했고 나머지 GCP 직접 수집은 유지한다. 아래 최신 후속 절과 [별도 실제 검증](gcp-security-log-consolidation.md)을 따른다.

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
| 중앙 AWS 로그 | `/delta-cp/central/aws` (2026-11-06 실제 조회) |
| 중앙 GCP 로그 | `/delta-cp/central/gcp` (2026-11-06 실제 조회) |
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

2026-11-04 최초 검증 당시 AWS **다른 계정(482840608926)의 실제 원본 그룹 구독**은 미실행이었다. 당시 정책 배포와 같은 계정 e2e 검증을 타 계정 실제 수신 완료로 표현하지 않는다. 이후 GuardDuty 원본 그룹의 실제 연결/수신은 아래 2026-11-06 후속 기록을 따른다. GCP는 실제 프로젝트의 전용 경로를 검증했다. 전체 서비스의 앱 로그 에이전트, 플랫폼 웹 조회, 부하/장기 장애/실제 DLQ 재처리/알림 수신자 구성은 이 검증에 포함하지 않는다.

운영·중지·실패 보관·전체 입력/출력은 [배포 안내](../../platform/deploy/logging/README.md)를 따른다.

## 2026-11-05 네트워크 계정의 중복 GCP 수집 정리

별도 승인된 [네트워크 로그 정리](gcp-central-network.md#2026-11-05-gcp-로그-전달-경로-정리-검토)에서 네트워크 계정 전용 전달 경로를 제거했다. 이 플랫폼 root와 공통 `services/gcp-log-collector`는 유지한다. 현재 운영 안내는 위 2026-11-04 초기 배포 때의 기존 수집기 보존 설명과 구분한다.

2026-11-05 `evidence.py receive before/after`로 실제 GCP 방화벽 원본과 플랫폼의 저장 JSON을 삭제 전35건, 삭제 후4건 대조했다. `verify.py final`로 플랫폼 GCP Lambda와 Sink 설정 불변, 실행 규칙 활성, AWS 수신 Lambda와 event source mapping 활성도 확인했다. 이 후속 작업에서 플랫폼 Apply나 AWS 원본 송신 구독 추가는 하지 않았다. 원본 증거와 명령 및 한계는 네트워크 정리 기록을 따른다.

## 2026-11-06 GuardDuty 송신 연결 설계와 현재 로그 이름

사용자는 중앙 계정 `482840608926`의 GuardDuty Finding 전달 Inception과 구현 및 실제 적용/샘플 검증을 승인했다. 중앙 root에 `/delta/security/guardduty`와 EventBridge 규칙/타깃/쓰기 정책 및 그룹 구독을 실제 적용했다. 신규5개/기존 변경과 삭제0개이며 자세한 승인과 실행 명령은 [별도 기록](aws-guardduty-logging.md)을 따른다.

2026-11-06 STS로 계정을 확인한 후 `describe-destinations`, `get-function-configuration`의 필요한 필드, `describe-log-groups --log-group-name-prefix /delta-cp/central/`를 읽기 전용으로 실행했다. Destination ARN과 Kinesis 타깃은 기존과 같고, 정책과 AWS 수집기 모두 `482840608926`, `616723161368`을 허용한다. 수집기는 `Active` / `Successful`이며 `DESTINATION_LOG_GROUP=/delta-cp/central/aws`다. AWS/GCP 중앙 그룹의 현재 이름과 보관 3일도 확인했다.

tracked `platform/deploy/logging/aws.tf`는 여전히 `/delta-platform/central/*`를 정의한다. 이번에는 현재 연결 안내만 실제 값으로 정정하며 플랫폼 코드, state 또는 운영 설정을 바꾸지 않는다. 현재 플랫폼 root의 Plan/Apply와 코드 수렴 검증은 미실행이므로 2026-11-04의 No changes를 현재 root의 결과로 해석하지 않는다. GuardDuty 경로 추가를 위해 이 플랫폼 root를 그대로 Apply하지 않는다.

2026-11-06 00:37 KST에 GuardDuty 샘플 `Backdoor:EC2/DenialOfService.Tcp` 1종/1건의 원본 그룹 저장과 `/delta-cp/central/aws` 도착을 확인했다. 플랫폼 레코드의 `account_id=482840608926`, 원본 그룹/스트림/시각, 전체 message 문자열과 원본 로그 ID 기반 event_id를 대조했다. 최초 같은 계정 smoke와 구분되는 실제 교차 계정 원본 수신 검증이다. 수신 Destination 정책과 AWS Lambda의 CodeSha256/RevisionId 및 조회한 운영 설정은 전후 동일했다. 중앙 송신 root의 최종 실제 Plan은 No changes/종료0이며, 플랫폼 root의 Plan 결과는 아니다. 대시보드 UI와 장기 부하/장애는 검증하지 않았다.

## 2026-11-06 Network Firewall FLOW와 ALERT 송신 연결

중앙 계정 `482840608926`에 기업 원본 그룹 `/delta/security/nfw-flow`, `/delta/security/nfw-alert`와 각 `delta-platform-central` 구독을 실제 적용했다. 두 구독은 같은 서울 리전 Destination, 빈 패턴과 `Random` 배포 방식을 사용한다. 기존 `/aws/vendedlogs/network-firewall/delta-lab/flow`, `/aws/vendedlogs/network-firewall/delta-lab/alert`와 과거 로그는 유지했다. 자세한 승인, Plan, Apply와 검증 명령은 [별도 기록](aws-traffic-log-forwarding.md)을 따른다.

2026-11-06 23:55:57 KST에 새 두 그룹에 수동 smoke를 각각 1개 기록했다. `/delta-cp/central/aws`에서 두 이벤트의 `account_id=482840608926`, 리전, 원본 그룹, 스트림, 원본 시각과 전체 message가 일치했고 FLOW는 3922ms, ALERT는 4151ms 뒤 수집됐다. Destination 정책과 수집 Lambda의 CodeSha256 및 RevisionId는 적용 전후 같았다. 이는 교차 계정 구독과 중앙 수집기의 검증이다. ANF가 `disabled`여서 Network Firewall 서비스가 실제 생성한 FLOW와 ALERT는 검증하지 않았다.

중앙 송신 root의 실제 Apply는 신규4개, 기존 정책 변경1개, 삭제0개였고 최종 Plan은 No changes, 종료0이다. 플랫폼 root에는 Plan이나 Apply를 실행하지 않았으며 tracked 이름과 실제 중앙 그룹 이름의 기존 차이도 바꾸지 않았다.

## 2026-11-07 GCP 방화벽 로그의 Security 우선 수집

`compute.googleapis.com/firewall`, `networksecurity.googleapis.com/firewall_threat`는 전용 Sink와 Pub/Sub 및 WIF로 Security 계정 `482840608926`의 `/delta/security/gcp-firewall`, `/delta/security/gcp-firewall-threat`에 먼저 저장한다. 각 빈 구독이 기존 Destination을 사용하고 플랫폼 AWS 수신기는 지정한 계정과 그룹 및 GCP 원본 식별자를 검증해 `/delta-cp/central/gcp`로 분기한다. 기존 cloud=gcp, event_id와 original을 보존하고 `relay`에 중계 정보를 추가한다.

전용 리소스24개 생성과 플랫폼 수신 준비3개 갱신 후 합성2종 및 실제 새 규칙 로그19건의 원본 일치를 확인했다. 이어 2026-11-07 00:34:12 KST에 기존 플랫폼 Sink에서 두 logName을 전체 필터 바깥의 제외 조건으로 제거했다. 전환 후 새 규칙 로그17건과 나머지 GCP 직접 수집 및 AWS 구독 회귀 마커 각1건도 확인했다. Security와 플랫폼의 raw original은 같으며 GCP 조회 시각은 소수점 끝 0의 표기만 정규화해 나노초 단위로 대조했다.

기존 플랫폼 리소스43개의 ID, Destination, 중앙 그룹과 나머지 GCP 직접 수집을 유지했다. 기존 GCP 수집기의 ZIP 경로는 state의 다른 로컬 경로에서 이번 실행 경로로 갱신했지만 배포 코드 해시와 환경은 같다. 두 root의 최종 실제 Plan은 No changes, 종료0이다. 양쪽 검사는 disabled이며 새 NGFW 위협 발생과 대시보드 UI는 검증하지 않았다. 승인, 명령과 복귀는 [Security 수집 기록](gcp-security-log-consolidation.md)과 [전용 Runbook](../../terraform/gcp-security-log-ingest/README.md)을 따른다.
