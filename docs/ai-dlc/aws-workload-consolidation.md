# AWS 워크로드 VPC 통합과 기존 시연 환경 정리

기준일: 2026-11-05 (Asia/Seoul).

## 승인과 현재 상태

- 사용자 지시: `cloudfront-server` 제거, Client와 Server를 `10.10.0.0/16` 하나로 통합, 현재 두 VPC의 인스턴스를 모두 내리고 다른 곳의 `10.20.0.0/16` 라우팅 및 참조 제거.
- Ideation: 위 지시를 정리 방향과 범위의 승인으로 기록한다.
- Inception 및 Unit별 Design/Implementation Plan: 2026-11-05 사용자 `승인`으로 U1~U4 구현과 실제 적용을 승인받았다.
- 인스턴스 종료는 EC2와 루트 EBS 삭제로 승인받았다. 두 루트 EBS 모두 실제 `DeleteOnTermination=true`다.
- 완료: U1~U4 구현/Test/Review와 실제 적용 및 API readback을 완료했다. 시연 CloudFront/ALB/S3, 기존 EC2 두 대와 루트 EBS, Server VPC/Attachment 및 전용 자원을 삭제했다. 기존 `10.10` VPC와 네 서브넷/TGW/EIC ID를 유지한다.
- 검증: 공통 Workload/중앙/GCP 세 root의 최종 실제 Plan은 모두 No changes/종료0이다. AWS VPC/TGW/SG와 GCP BGP/NCC/방화벽에서 폐기 대역을 제거했다. 네 BGP가 UP이며 공통 VPC의 BGP 경로 네 개를 유지한다. 워크로드 EC2는0대라 삭제 이후 HTTP 왕복 시험은 실행하지 않았다.
- 게시: 2026-11-05 사용자 지시로 이 통합 작업을 커밋하고 푸시한다. 최종 게시 상태는 Git 이력을 따른다.
- GCP 코드 주의: 실제 GCP No changes 검증에는 별도 로그 정리 작업의 로컬 코드가 포함됐다. 그 작업이 원격에 반영되기 전에는 기존 원격 코드가 삭제된 로그 수집기를 다시 생성하도록 계획할 수 있다. GCP에서 실행하기 전 별도 로그 정리의 게시 상태와 최신 state 및 Plan을 확인한다.
- 기존 작업 트리의 GCP 로그 정리 변경(`docs/README.md`, `docs/ai-dlc/gcp-central-network.md`, `terraform/network-security-gcp/logging.tf`)을 보존한다. 같은 root를 Apply하기 전 다른 작업의 변경과 state 갱신을 다시 대조한다.
- 승인 전 사전 조회에서는 별도 로그 정리 변경이 AWS 수집기 `main.tf`와 GCP 모듈의 README/입출력/provider/예시/테스트/사전 검사로 확대되고 `logging.tf`가 삭제된 것을 확인했다. 승인 후 실제 작업 도중 해당 정리의 state 갱신도 확인했으며, 아래처럼 최신 구성으로 다시 Plan해 그 작업을 되돌리거나 추가 적용하지 않았다.

## Ideation

- 문제: 기존 두 VPC와 시연용 CloudFront를 정리하면서 폐기된 Server 대역을 목적지나 생성 기본값으로 계속 사용하면 잘못된 통신과 후속 배포 계획이 만들어질 수 있다.
- 사용자: Delta 팀의 네트워크 및 워크로드 운영자.
- 성공 기준: 공통 워크로드 VPC `10.10.0.0/16` 하나, 기존 두 EC2의 실행 종료, 시연용 CloudFront 제거, 운영 코드와 입력 및 AWS/GCP의 폐기 대역 경로와 정책 참조 제거.
- Scope: 세 워크로드 root, 중앙 TGW의 해당 spoke와 경로 및 정책, GCP NCC와 방화벽의 AWS 대역 목록, 이전 판단 기본값과 삭제될 root를 가리키는 실행 안내, 관련 테스트와 운영 문서.
- Non-goals: 새 EC2나 앱 배포, 플랫폼 및 `delta-cp` VPC 변경, 다른 CloudFront 배포 삭제, GCP VM/NLB/VPN 삭제, ANF/NGFW 검사 재활성화, 별도 로그 정리 작업의 실행.

## Inception

### Functional Requirements

1. 기존 Client VPC `vpc-056871f49eecfeef4`와 `10.10.0.0/16`, 기존 전용 서브넷 및 TGW Attachment `tgw-attach-0cd5f3d3c73ee7643`을 공통 워크로드 기반으로 유지한다.
2. `terraform/workload-client`를 공통 네트워크 root `terraform/workload`로 정리한다. VPC와 Attachment의 기존 resource address와 backend key `delta/lab/workload-client/terraform.tfstate`를 유지해 VPC 재생성과 원격 state 이전을 피한다. 디렉터리 이름과 backend key가 다른 이유를 README에 남긴다.
3. 공통 root에서는 `aws_instance.client`, AMI 조회와 인스턴스 전용 입출력을 제거한다. 기존 EIC Endpoint와 SG, VPC/서브넷/TGW 및 GCP 목적지 `10.32.1.0/24` 경로를 유지한다. 새 Client 또는 Server EC2를 생성하지 않는다.
4. `cloudfront-server`의 시연 배포와 VPC Origin, internal ALB와 Target Group, 전용 서브넷/RT/SG/IGW, 정적 웹 S3 버킷과 두 객체를 제거한다. 현재 관리 리소스는 37개이며 `terraform_data` 1개를 포함한다.
5. Server EC2 `i-03d824c2a456e71ad`, `10.20` VPC `vpc-0dd4ef153e66803ab`, 해당 EIC/SG/서브넷/RT/TGW Attachment를 제거한다. 현재 Server root 관리 리소스는 29개이며 `terraform_data` 1개를 포함한다.
6. Client EC2 `i-077f4b16dd163998a`도 제거한다. 두 EC2의 루트 EBS 삭제가 승인 범위에 포함되어야 실제 종료한다.
7. 중앙 `spoke_attachments.aws_server`, 해당 TGW RT association과 두 TGW 정적 경로, Inspection VPC의 두 반환 경로를 제거한다. Server 목적지 허용 흐름 두 개와 종료된 인스턴스의 서비스 주소 메타데이터를 정리한다. `aws_client` 키는 기존 주소와 연결 보호를 위해 유지한다.
8. Client와 CloudFront 전용 RT의 `10.20` 경로, 관련 SG 규칙을 소유 root에서 제거한다. GCP `aws_cidrs`를 `10.10.0.0/16` 하나로 줄여 NCC의 `include_export_ranges`, private ingress/egress 범위와 handoff 출력에 반영한다.
9. AI 이전 기능의 `services/delta-ai/knowledge/mapping/defaults.yaml`에서 AWS 대상 풀을 공통 대역으로 맞춘다. 공통 대역이 AWS 대상 자신인데 전역 예약 목록에서 충돌하는 일이 없도록 클라우드별 예약 범위도 함께 조정한다. Jenkins 이전 계획기의 `terraform/workload-server` 참조를 갱신하며, 네트워크 전용 root가 컴퓨트 배포까지 지원한다고 표시하지 않는다.
10. 폐기된 root 디렉터리는 원격 리소스 정리가 끝난 뒤 제거한다. 운영용 예제와 Runbook은 새 구조를 안내한다. 과거 검증 기록과 독립적인 테스트용 주소는 실제 인프라 참조와 구분하며, 운영 명령으로 잘못 사용될 수 있는 기존 안내에 폐기 표시와 최신 문서 링크를 추가한다.

### Non-Functional Requirements

- AWS 업무 계정 `616723161368`, 중앙 계정 `482840608926`, 서울 리전과 GCP `term2-delta`를 실행 직전에 재확인한다.
- 기존 `10.10` VPC/서브넷/Attachment의 ID를 유지한다. 단순 이름 정리를 위해 교체하거나 `10.20` VPC의 CIDR만 변경하지 않는다.
- 원격 state와 backend 및 실제 입력, Plan, 임시 인증정보는 Git에 포함하지 않는다. 제한된 ignored 경로에 증거를 보존한다.
- ANF/NGFW의 현재 `disabled` 모드와 중앙 TGW/VPN, RAM 공유, 로그를 유지한다. 공통 검사 보호 장치를 전역으로 해제하지 않는다.
- 현재 시연 S3 버킷에는 `index.html`과 `verification.json` 두 객체가 있다. 삭제 전 미관리 객체나 버전이 추가됐는지 다시 확인하고 버킷 전체 강제 삭제로 범위를 확대하지 않는다.
- 각 Apply 직전 최신 state로 새 Plan을 생성하고 생성/교체/수정/삭제/관리 해제를 구분한다. 이번 사전 Destroy Plan을 의존성 정리 이후 그대로 재사용하지 않는다.

### Architecture

최종 운영 기반은 `10.10.0.0/16 공통 워크로드 VPC -> 기존 TGW -> 기존 GCP VPN/중앙 네트워크`다. 워크로드 VPC 내부의 기존 시험 EC2는 0대가 된다. 현재의 VPC/Attachment를 유지하므로 다른 환경의 `10.10` 경로는 유지할 수 있다.

현재 두 VPC는 Client `10.10`와 Server `10.20`를 별도 TGW 연결로 구성한다. 단일 VPC 통합 후 내부 통신은 VPC local 경로를 사용하므로 과거 두 VPC 간 ANF 검사 시연과 동일한 구성이 아니다. 후속 앱이나 검사 시연은 별도로 설계한다.

### Acceptance Criteria

1. CloudFront 배포 `E3H298XCGG4TSD`, VPC Origin `vo_F2fqx6DGY0HDGpcXhiDOZf`, 시연 ALB/S3 및 전용 자원이 제거되고 해당 remote state의 관리 리소스가 0개다.
2. 기존 두 EC2가 `terminated`이고 루트 EBS 두 개가 삭제된다. 두 VPC를 필터로 조회해 다른 인스턴스가 추가되지 않았는지 확인한다.
3. `10.20` VPC와 Server Attachment가 제거되고, 기존 `10.10` VPC/서브넷/Attachment는 기존 ID로 남는다.
4. 공통 root Plan에 EC2 생성이 없다. 최종 실제 Plan은 No changes로 확인한다.
5. AWS workload/central VPC RT, 두 TGW RT와 GCP NCC/Cloud Router에서 `10.20.0.0/16`과 폐기된 하위 대역의 경로가 남지 않는다. 이를 포함하는 집계 경로가 발견되면 다른 환경의 목적과 영향을 확인한다.
6. GCP 네 BGP 세션이 UP이고 `10.10` 및 GCP 대역 연결이 유지된다. EC2 삭제 이후 HTTP 왕복 검사를 실행하지 못하면 네트워크 API 검증과 구분한다.
7. GCP private 방화벽과 NCC 범위, 중앙 허용 규칙 및 서비스 메타데이터, runtime tfvars와 auto tfvars, AI 기본 설정과 실행 계획기에 폐기 대역 또는 폐기 root를 운영 대상으로 사용하는 참조가 없다.
8. 변경된 모듈 README에 전체 입력/출력 및 복합 속성 표를 유지한다. 독립 mock 및 경로/이전 판단 회귀를 실제 실행하고 결과를 기록한다.

## Unit별 Design과 Implementation Plan

아래 네 Unit은 사용자 승인 후 구현/Test/Review와 실제 적용 및 최종 readback을 완료했다. Design과 Implementation Plan은 실행 근거로 유지한다.

### U1. 시연 CloudFront 종료

- Design: 기존 Client/Server가 존재하는 동안 CloudFront 소유의 자원만 의존성 역순으로 제거한다.
- Implementation Plan: 최신 소유권/버킷/Plan 재확인, Distribution 비활성화와 배포 대기, Provider가 처리하는 Distribution/Origin/ALB/S3/전용 네트워크 삭제, API와 empty state 확인, 이후 tracked 디렉터리 제거.
- Test/Review: 실제 Destroy Plan은 `0 add, 0 change, 37 destroy`였다. 검토한 저장 Plan Apply는 종료0/37 destroyed이며 원격 state는 serial15/리소스와 출력0개다. API에서 Distribution/Origin/ALB/S3 부재를 확인했고 다른 CloudFront5개는 Enabled로 보존됐다. 소스 디렉터리를 제거하고 원본 구성과 증거는 ignored 경로에 보존했다.

### U2. 폐기 대역의 중앙/GCP 참조 정리

- Design: `10.20` 경로와 정책을 철회하고 중앙과 소비자 사이 중복 Attachment 관리 항목을 순서대로 정리한다.
- 중앙 accepter에는 `prevent_destroy=true`가 있으며 이 Provider의 accepter 삭제는 실제 소비자 Attachment를 삭제한다는 주석이 있다. Server accepter 하나를 정확한 주소로 백업 후 관리 해제하고, 중앙의 `aws_server` 입력과 경로/association/policy를 제거한 정상 Plan을 적용한 뒤 Server root가 실제 Attachment를 삭제하도록 한다. 나머지 accepter 보호는 유지한다.
- Implementation Plan: GCP의 `aws_cidrs`와 중앙 입력을 갱신하고 독립 mock/회귀 실행, 최신 backend/동시 작업/state 확인, Server accepter 한 항목의 명시적 관리 해제, 중앙 저장 Plan/Apply, GCP의 범위만 변경하는 Plan/Apply, BGP 수신 경로 철회와 NCC/방화벽 readback 확인.
- 별도 진행 중인 GCP 로그 정리 변경을 이 Apply에 섞지 않는다. 해당 작업과 실행을 조정하거나 승인된 범위만 반영한 종료 구성에서 Plan을 만들고 예상 밖의 변경이 있으면 적용하지 않는다.
- Test/Review: 중앙 state의 Server accepter1개만 관리 해제됐고 다른 인스턴스 내용이 동일함을 비교했다. 중앙 Plan/Apply는 `0 add, 2 change, 6 destroy`, GCP는 `0 add, 3 change, 0 destroy`로 완료했다. 폐기 대역의 AWS VPC/TGW/SG 참조와 GCP best route는0개이고, NCC/private 방화벽은 공통 대역 하나만 남는다. 네 BGP UP과 공통 VPC 경로4개를 확인했다. 중앙과 현재 GCP root의 최종 Plan은 No changes다.

### U3. 공통 네트워크와 기존 컴퓨트 종료

- Design: 기존 Client VPC를 단일 root로 운영하고 두 시험 인스턴스를 삭제한다. 공통 기반의 remote backend key와 resource address를 유지한다.
- Implementation Plan: Client root를 공통 root로 정리하고 instance/AMI 입출력과 `10.20` remote route/egress를 제거, mock/기존 ID 유지 Plan 검토, Client 변경 Apply, 중앙 참조 해제 이후 Server 최신 Destroy Plan/Apply, empty state와 actual API 확인 후 Server 코드 제거.
- Test/Review: Server 최신 Plan/Apply는 `0 add, 0 change, 29 destroy`, 공통 root는 `0 add, 0 change, 5 destroy`였다. 두 EC2 terminated와 두 루트 EBS 삭제를 API로 확인했다. Server state는 serial7/리소스와 출력0개, 공통 state는 serial7/관리24개다. VPC/네 서브넷/Attachment/EIC의 기존 ID 동일과 공통 root의 최종 No changes를 확인했다. Server 소스 디렉터리를 제거했다.

### U4. 후속 잘못된 배포 참조 방지

- Design: active 운영 기본값과 경로를 새 구조에 맞추며 과거 검증 증거를 현재 안내와 구분한다.
- Implementation Plan: AI mapping의 대상/예약 대역, Jenkins 이전 계획기의 root/지원 범위, 실제/예제 입력과 Runbook, docs 인덱스 갱신. 폐기 주소와 ID 및 디렉터리 참조를 다시 검색하고 남은 항목의 용도를 분류한다.
- Test/Review: 주소 매핑/예약 충돌, AWS root의 컴퓨트 미지원, 변경된 인벤토리/평가 fixture를 포함한 AI와 Jenkins 회귀125개가 통과했다. 현재 입력과 예제, AI 기본값, 이전 계획 인벤토리에 폐기 대역이 없고, 운영 handoff JSON도 실제 적용 출력으로 갱신했다. 남은 폐기 주소는 과거 검증 문서와 독립된 mock 테스트 값이다.

## Operation

- Deployment: U1 삭제, U2 중앙/GCP 갱신, U3 컴퓨트와 Server VPC 삭제, U4 참조와 문서 갱신을 완료했다. U1의 마지막 전용 서브넷 해제 대기 중 독립된 중앙 accepter의 관리 해제를 수행했고, 실제 중앙/GCP 리소스 Apply는 U1 완료 이후 실행했다.
- Observability: AWS EC2/Volume/VPC/ELB/CloudFront API, VPC/TGW route, GCP Router BGP/NCC/firewall과 각 remote state를 대조한다. 인스턴스가 0대인 상태에서는 HTTP 성공을 검증했다고 기록하지 않는다.
- Rollback: 기존 VPC ID와 state를 보존하는 변경을 우선한다. 삭제한 EC2의 로컬 디스크와 Server VPC/CloudFront는 state 백업만으로 데이터나 동일 ID를 복원할 수 없다. 실제 삭제 전이라면 Plan 폐기로 중단할 수 있으며, 삭제 이후 재생성에는 별도 요청과 검토가 필요하다.
- Runbook: 실행 직전 state/계정/병행 작업을 재확인하고 각 Unit의 새 Plan을 검토한다. 최종 empty state와 원본 backend의 감사 이력을 보존한 뒤 폐기 root 코드를 제거한다.

## 검증 기록

| 날짜 | 실행한 명령 또는 조회 | 결과 |
| --- | --- | --- |
| 2026-11-05 | `git status --short --branch`, 관련 AI-DLC/README, `rg`로 코드와 ignored 실제 입력 검색 | 기존 GCP 로그 작업 3파일 변경 확인. 중앙/GCP 경로, AI defaults와 Jenkins의 Server root 참조 발견 |
| 2026-11-05 | 업무/중앙 프로필 `aws sts get-caller-identity` | 각각 `616723161368`, `482840608926` 확인 |
| 2026-11-05 | 두 VPC `describe-instances`, `describe-network-interfaces`, `describe-route-tables`, `get-distribution`, S3 `list-objects-v2` | 기존 EC2 두 대 running, 루트 EBS 두 개 delete-on-termination, 시연 CloudFront Enabled/Deployed와 S3 객체 두 개 확인. Client VPC의 Server 목적지 TGW 경로 4개 확인 |
| 2026-11-05 | 기존 backend metadata 대조와 S3 `cp ... -` 읽기 백업 | CloudFront serial11/37개, Client serial5/29개, Server serial4/29개, 중앙 serial27/55개, GCP serial2/67개. 원격 state 수정 없음 |
| 2026-11-05 | CloudFront/Server `terraform plan -destroy -detailed-exitcode -input=false -no-color -lock-timeout=15s -out=<ignored plan>`, `terraform show -json` | 각각 종료2, 37개/29개 delete만 존재. 생성/교체/수정0. `terraform_data` 각1개 포함. Apply 아님 |
| 2026-11-05 | 중앙 `describe-route-tables`, 두 TGW RT `search-transit-gateway-routes` | `10.20` 반환 경로 2개와 TGW 정적 경로 각1개 active 확인 |
| 2026-11-05 | `gcloud compute routers get-status` | 일반 CLI 계정 권한 부족. 성공으로 기록하지 않음 |
| 2026-11-05 | 기존 ADC로 Google Router/NCC/firewall GET | 시스템 신뢰 CA 적용 후 HTTP200. BGP4개 UP/Established, `10.20` best route4개와 NCC/private ingress/egress 참조 확인. 최초 Python 기본 CA 조회 실패는 이 읽기 재시도로 해결 |
| 2026-11-05 | `git diff --check`, 문서/Plan 요약/권한/Git ignore 검사 | 공백 검사 통과. 금지 문자 없음, 기록된 delete37개/29개와 원본 요약 일치, 증거 파일0600 및 Git 제외 확인. 애플리케이션이나 구현 mock 테스트 결과가 아님 |
| 2026-11-05 | 최종 `git status --short` | 같은 checkout의 별도 GCP 로그 정리 변경 확대 확인. 통합의 Terraform 구현/Apply와 구분하며, 이전 snapshot과 Plan을 실행 시점의 최신 상태로 간주하지 않음 |

사전 state/Plan/로그와 GCP readback은 ignored `.validation/workload-consolidation-20261105/`에 제한된 권한으로 보존한다. 최신 진행은 아래 승인 후 기록을 따른다.

## 승인 후 구현과 적용 기록

- 2026-11-05: 사용자 `승인`으로 U1~U4의 설계, 구현, 기존 EC2 두 대와 루트 EBS 삭제 및 실제 적용을 승인받았다.
- 기존 다섯 root의 실제 state를 다시 백업하고 S3 lock 부재를 확인했다. CloudFront11/Client5/Server4/중앙27/GCP2 serial은 사전 확인과 같았다. 이후 별도 GCP 로그 작업이 state를 serial4/관리55개로 갱신했다. 처음 준비한 HEAD 기반 Plan은 삭제된 로그 자원12개 재생성을 제안해 폐기했다. 최신 소스와 실제 입력을 다시 격리해 NCC와 두 private 방화벽의 갱신3개만 있는 Plan을 검토한 뒤 적용했다. active backend key는 동일하며, 현재 checkout의 최종 GCP Plan도 No changes다.
- U1: 시연 버킷의 객체와 버전을 재조회해 승인한 두 파일만 확인했다. 최신 CloudFront Plan의 delete37개를 검토하고 Apply를 완료했다. Distribution/Origin/전용 네트워크 삭제에는 약11분27초가 걸렸다. empty state와 실제 API 부재를 확인한 뒤 소스 디렉터리를 제거했다.
- U3: `terraform/workload-client`를 `terraform/workload`로 이동했다. 기존 resource address/backend key/태그를 유지하고 `aws_instance.client`, AMI 조회/입력 및 EC2 출력 두 개를 제거했다. 실제/auto 입력에서 Server 목적지 Route와 egress를 제거하며 GCP 목적지를 유지했다.
- U4: AWS mapping 기본 풀을 공통 VPC로 변경하고 기존 네 서브넷을 AWS 대상 예약 범위로 추가했다. GCP 대상에는 공통 AWS VPC 전체를 예약한다. Jenkins 계획기는 공통 root를 안내하며 별도 컴퓨트 구성이 필요함을 명시한다.

| 날짜 | 명령 또는 확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-05 | `bash terraform/workload/scripts/validate.sh` | init/validate/fmt 및 mock24개, bootstrap 보호 Python3개 통과 |
| 2026-11-05 | `bash terraform/network-security/scripts/validate.sh` | 최초에는 실제 tfvars 형식 검사에서 종료3. `terraform fmt terraform/network-security/terraform.tfvars` 후 재실행해 mock44개와 경로 helper Python11개 통과 |
| 2026-11-05 | `bash terraform/network-security-gcp/scripts/validate.sh` | 변경 중인 로그 코드 보존 상태에서 init/validate/fmt 및 mock14개, Python10개 통과. PSK 미노출 확인 |
| 2026-11-05 | 공통 root 실제 `plan -detailed-exitcode`와 `show -json` | delete5개만 존재: 기존 Client EC2, Server 목적지 Route2개, egress2개. EC2 출력2개 제거. VPC/서브넷/Attachment/관리 Endpoint의 생성/교체/수정 없음 |
| 2026-11-05 18:06~18:18 KST | 검토한 CloudFront 저장 Plan `terraform apply` | 종료0/37 destroyed. 원격 state serial15/관리 및 출력0. 실제 CF Distribution/Origin/ALB/S3 부재 확인 |
| 2026-11-05 18:17 KST | 중앙 `terraform state rm -dry-run`, 정확한 Server accepter1개 `state rm`, S3 state 전체 인스턴스 대조 | 관리 해제1개, 중앙 serial28. 다른 리소스 인스턴스 내용 동일. AWS Attachment 실제 삭제는 Server root에서 수행 |
| 2026-11-05 18:19 KST | 중앙 Plan/JSON 검토와 저장 Plan Apply | 종료0/2 changed/6 destroyed. 경로4개, Server association과 계약 제거, HOME_NET 및 시험 허용 규칙 갱신. 중앙 state serial30/관리48개 |
| 2026-11-05 18:19 KST | 최신 GCP 격리 구성의 Plan/JSON 검토와 저장 Plan Apply | NCC 범위와 private ingress/egress의 CIDR만 갱신3개, 종료0. 공유 로그 정리 결과와 VPN/VM 보존. 현재 state serial6/관리55개 |
| 2026-11-05 18:20 KST | 공통 Workload 최신 Plan/JSON 검토와 저장 Plan Apply | 종료0/5 destroyed. Client EC2와 폐기 Route2개/egress2개 삭제. 기존 VPC/서브넷/TGW/EIC ID 동일 |
| 2026-11-05 18:21~18:22 KST | Server 최신 Destroy Plan/JSON 검토와 저장 Plan Apply | 종료0/29 destroyed. 원격 state serial7/관리 및 출력0 확인 후 Server 소스 디렉터리 제거 |
| 2026-11-05 18:21~18:22 KST | 공통 Workload/중앙/현재 GCP root의 최종 실제 `plan -detailed-exitcode`와 `show -json` | 세 root 모두 종료0/No changes. 리소스와 출력 변경0. 공통 root에 EC2 생성 없음 |
| 2026-11-05 | locked 테스트 환경에서 `pytest -q services/delta-ai/tests/test_mapping.py services/delta-ai/tests/test_eval.py deploy/jenkins-local/tests/test_migration.py` | 변경된 기본값과 인벤토리 및 평가 fixture 포함125 passed. 최초 fixture 갱신 전의 mapping/Jenkins 검사는111 passed |
| 2026-11-05 18:23 KST | ignored `verify_live.py`: AWS EC2/Volume/VPC/TGW/RT/SG/CloudFront/ELB/S3/ANF와 GCP Router/NCC/firewall GET | 두 EC2 terminated, 두 EBS deleted, 공통 워크로드 활성 EC2 0대, `10.20` VPC와 CF 시연 부재. 업무/중앙 RT와 SG 및 두 TGW RT의 폐기 CIDR 0개. GCP BGP4개 UP, 공통 VPC best route4개/폐기 route0, NCC/private firewall 범위는 공통 /16 하나. 다른 CloudFront5개 Enabled, 플랫폼/CP VPC 보존, ANF disabled |
| 2026-11-05 | `terraform fmt -check -recursive terraform`, `git diff --check`, README/운영 입력/로컬 링크 검사, 다섯 state의 S3 lock HEAD | 형식/공백 검사 통과. 공통 모듈14입력/9출력과 README 표 일치, retired root3개 부재, 운영 입력의 폐기 CIDR 없음, 관련 로컬 링크157개 정상. 다섯 대상 state lock 모두 부재이며 증거 파일0600 유지 |
| 2026-11-05 | 원격 main 기반 격리 게시 후보의 세 root `scripts/validate.sh`, `terraform fmt -check -recursive terraform`, AI 전체 및 Jenkins 이전 테스트 | 별도 GCP 로그 정리 변경을 제외한 게시 후보에서 Terraform mock82개/Python24개, AI 및 Jenkins323개 통과. 실제 GCP Plan/Apply를 재실행한 결과는 아님 |

## 최종 Review

- 승인한 제거와 통합 범위를 실제 적용했다. 공통 네트워크의 기존 ID와 backend key를 유지했고 새 컴퓨트나 VPC를 생성하지 않았다.
- CloudFront/Server의 empty remote state와 원본 state/config/Plan/로그는 ignored 경로에 남긴다. 기존 S3 state bucket이나 감사 이력을 삭제하지 않는다.
- 현재 운영용 설정/예제/handoff와 AI/이전 계획에는 폐기 대역을 사용하지 않는다. 과거 AI-DLC와 일반 mock의 주소는 검증 증거로 남기며 현재 실행 안내와 구분했다.
- 실행하지 않은 검증: 삭제 후 EC2 HTTP 왕복 시험, 새 앱 배포, 부하/가용성 시험. Git 게시 상태는 원격 이력을 따른다.
