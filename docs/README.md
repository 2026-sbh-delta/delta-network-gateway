# Delta network gateway

## 현재 상태

- 기준일: 2026-11-03 (Asia/Seoul)
- 플랫폼 U1(2026-11-03): 사용자 구현 승인 후 고정 로그인·공개 GitHub 소스 확인·PostgreSQL 상태/이력·API/Worker·웹을 구현했다. 실제 PostgreSQL 통합 56개, 실제 GitHub 1개, 브라우저 3개를 통과했고 ARM64 이미지의 실제 검사·재시작 후 상태 보존도 확인했다. 추가 지시에 따라 AWS 업무 계정 616723161368/서울에 별도 기반을 배포했다. HTTPS 로그인·실제 GitHub 확인·재시작·S3 백업의 임시 DB 복원·브라우저를 검증했고 최종 Plan은 No changes다. GitHub 배포 워크플로 3개는 로컬 준비·검증했으며 OIDC 연결·원격 실행 전이다. 커밋·푸시는 미실행이다. [U1 기록](ai-dlc/platform-u1.md) · [AWS 배포](ai-dlc/platform-aws.md)
- 플랫폼 작업 시작(2026-11-02): 로컬 `feat/platform` 브랜치와 전용 `platform/` 디렉터리를 준비했다. 웹, 백엔드 API/Worker, 계약, VM 배포 자동화, HTML 설명을 분리한다. 당시 U0는 구조·설계 문서 단계였고 기능 구현·테스트·배포는 수행하지 않았다. 현재 U1 상태는 위 항목을 따른다. 구조·링크·ignore 규칙 검증 후 사용자에게 Conventional Commit 메시지를 제시했고 로컬 커밋 승인을 받았다. 실제 커밋은 Git 이력을 기준으로 하며 원격 푸시는 미실행이다. 후속 커밋도 사전에 알린다. [작업 영역](../platform/README.md) · [설계/진행 기록](ai-dlc/platform.md)
- AI 판단 서비스 SAST 경로 제거(2026-11-02): `services/delta-ai/`에서 코드 보안 판단(`code_security`, SARIF 입력, 코드 패치)을 뺐다. SAST는 별도 서비스가 SARIF와 코드 흐름으로 다시 설계한다. 환경 차이 판단과 피드백 규칙 후보·승인 API는 유지하며, 승인 규칙을 적용할 코드 검사는 아직 없다. 같은 날 `.venv/bin/pytest -q` 66개 통과, `scripts/demo.py fake` 실행 완료.
- SAST 점검 서비스(설계): 빠른 모드(파이프라인, Opengrep 위치 표시 + 자체 AST 경로 + 애매한 경로만 LLM)와 전체 LLM 검증 모드(파이프라인 밖)를 둘 다 두는 설계 초안을 작성했다(2026-11-02). 구현은 미착수다. [설계](ai-dlc/sast.md)를 따른다.
- 스테이징 도구(설계·검증 기록): 관리 에이전트가 호출하는 파이프라인 스테이징 도구와 AWS 기반 모듈은 `feat/add-staging-tool` 브랜치에 있고 main에는 아직 병합하지 않았다. 2026-11-02 mock `terraform test` 11개와 도구 단위 테스트 29개 통과, Workload 계정 기본 VPC 대상 실제 Plan(25개 추가) 저장. Apply와 ECS 배포는 미실행이다. [설계/검증 기록](ai-dlc/staging-tool.md)을 따른다.
- AI 판단 서비스: `services/delta-ai/`에 `2026-sbh-delta/delta-ai` `007d1c6`을 subtree로 추가했다. 2026-11-01 `.venv/bin/pytest -q` 98개 통과, `scripts/demo.py fake` 실행 완료. Terraform 변경은 없고, Dockerfile/배포/다른 서비스와의 실제 연동은 미구현이다. [실행 안내](../services/delta-ai/README.md)를 따른다.
- AI DAST 점검 서비스: `services/delta-dast/`에 추가했다. SAST 결과를 받아 스테이징에서 저장형 XSS를 재현·판정하고 수정안·보고서를 만든다. 2026-11-02 기존 가상환경으로 주요 모듈 import와 `compileall` 성공을 확인했다. 자동 테스트는 아직 없다. 컨테이너 이미지/배포/delta-ai 연동은 미구현이다. [실행 안내](../services/delta-dast/README.md)와 [API 계약](../services/delta-dast/docs/API.md)을 따른다.
- CloudFront ANF 웹: 사용자 지시로 private S3/OAC의 두 버튼 페이지와 Client VPC internal ALB/ALB VPC Origin을 배포했다. URL은 https://d228fpdue5s0by.cloudfront.net/ 이다. API는 ALB→TGW→중앙 ANF→기존 사설 Server로 연결한다. 웹200/허용8080 HTTP200/차단8081 HTTP504와 SID9000000 blocked/drop, 허용 마커 SID2000001 및 양방향 FLOW를 실제 대조했다. 새 경로 mock21개/중앙 회귀35개 통과. 직접 Origin/Server IGW/임시 ingress 정리와 검증 JSON 게시, 네 root No changes까지 확인했다. 브라우저 차단 버튼 완료 화면 재확인은 도구 오류로 미검증이며 사용자 지시로 이번 변경을 분할 커밋하고 origin/main 게시 성공을 확인했다. [승인/실제 결과](ai-dlc/cloudfront-anf-demo.md)를 따른다.
- CloudFront Git/푸시: 구현 `5c807f58`, 회귀 검증 `1801463e`, 운영 문서 `12587b42`로 분할 서명 커밋했다. 2026-11-01 21:00 KST `git push origin main` 성공과 `git ls-remote --heads origin main`의 `12587b42600ba05fe3aa6564e427cd52edd4f6c6` 일치를 확인했다. 당시 작업 트리는 깨끗했으며 이번 문서 갱신은 해당 게시 결과를 기록한다. 실제 입력/backend/state/Plan/원본 증거는 제외했다.
- 이전 CloudFront 직접 EC2 Origin 시험은 중앙 ANF를 경유하지 않았다. 당시 배포/HTTPS/보안 검증은 [과거 기록](ai-dlc/cloudfront-server.md)에 보존하고, 현재 구성과 구분한다.
- Ideation: 사용자 승인 완료.
- Inception 및 U1~U5 Design / Implementation Plan: 사용자의 `PLEASE IMPLEMENT THIS PLAN` 지시로 승인 완료.
- Construction: 중앙 U1~U5와 후속 Client 역할 구현/Review 완료. 중앙 mock Plan은 같은 계정 Client/Backend 사례를 포함한 최신 회귀 실행에서 34개 통과했다.
- AWS Rule Group DryRun: SCP 수정 후 재시도한 두 규칙 집합 모두 통과. 초기 예제 소비 용량은 기본 차단 1, TCP/UDP/ICMP 및 추가 서명 5. 실제 Server 시험 규칙도 DryRun 소비 용량 3으로 수락됐고 두 VPC HTTP 허용/차단을 실제 검증했다.
- 중앙 실행 입력: `terraform.tfvars` 작성 완료. VPC `10.250.0.0/16`, Subnet 4개는 `/24`, AZ IDs는 `apne2-az1`, `apne2-az2`. 실제 입력으로 격리된 중앙 기반 mock Plan 1개 통과.
- Backend: S3 초기화 메타데이터 확인. Bucket `sbh-delta-central-tf`, encrypt/use_lockfile=true, 허용 계정 `482840608926`. 현재 `backend.hcl`과 초기화된 bucket/key/region이 일치한다.
- 배포: 중앙 Inspection과 Workload 계정의 사설 Client/Server 두 VPC 배포 완료. 이번 Server 기반 23개와 반환 Route 2개, 중앙 연결 9개/정책 2개 갱신, Client 원격 Route/egress 4개를 검토 후 적용했다. 기존 Client 교체/리소스 삭제는 없다. Attachment available/Spoke associated, Firewall/두 Primary Endpoint READY 및 IN_SYNC, 검사/반환 Route active를 실제 확인했다.
- RAM: 두 Share와 Resource Association에 이어 업무 계정 `616723161368`의 두 principal 연결을 추가했다. 업무 계정에서 중앙 TGW available을 조회했다. 조직 외 공유는 비활성화다.
- 로그/통신: Client `10.10.1.143` → Server `10.20.1.10:8080` HTTP 200 및 같은 flow_id/AZ의 양방향 FLOW 확인. 8081은 timeout과 SID 9000000 blocked/verdict=drop, 마커 SID 2000001은 HTTP 200과 allowed/verdict=alert를 실제 요청 source port와 대조했다. 같은 계정 두 VPC의 ANF 왕복 검증 완료다.
- 실계정 Plan: 실제 저장 Plan의 계정/변경 목록/보안 속성을 검토한 뒤 적용했다. 배포 후 중앙/Client/Server 세 root의 최종 Plan 모두 No changes, 상세 종료 코드 0이다. Terraform SSO refresh 오류는 유효한 CLI 역할 자격 증명을 메모리에서만 전달해 재검증했다.
- 기존 기반 Git/푸시: 기존 Client 배포의 원격 체크포인트는 `9033794`다. 후속 변경을 GCP 구현 `7c31c885`, GCP 검증 `fd9abcd1`, AWS Server 구현 `d62eaae8`, Server/중앙 회귀 검증 `86659848`, 배포 기록 `14ed879c`로 분할 커밋했다. 2026-11-01 19:25 KST `git push origin main` 성공과 `git ls-remote --heads origin main`의 `14ed879c37aa29b1b6582299a24a623b6f770ec2` 일치를 확인했다. 당시 작업 트리는 깨끗했으며 이번 문서 갱신은 해당 게시 결과를 기록한다. 실제 tfvars/backend/state/Plan은 제외한다.
- Destroy: 미실행. 같은 계정 두 VPC HTTP 허용/차단/로그는 검증 완료. 다른 Workload 계정 간 통신과 GCP/온프레미스/VPN, 장애 전환/부하/TLS 복호화는 미검증이다.
- 실행 프로필: `AWS_PROFILE=sbh-delta-security`. 이번 DryRun에서 STS로 중앙 계정 `482840608926`을 재확인했다. 코드에서는 계정 ID를 필수 입력으로 받아 검증한다.
- AWS Workload: 사용자 `이렇게 만들고, public 노출없이 해` 지시로 Inception과 U1~U3 승인 및 구현/Review 완료. Workload mock Plan 26개 및 state bootstrap 보호 테스트 3개 통과. 별도 S3 state와 VPC `10.10.0.0/16`, `/24` Subnet 4개, 사설 EC2 `10.10.1.143` 및 EIC Endpoint 생성 완료. 초기 배포 때 공인 IP/IPv6, IGW/NAT/인터넷 Route 부재와 EIC 사설 SSH 성공을 확인했다. 후속 CloudFront root가 Client VPC에 IGW와 ALB 전용 Subnet/RT를 추가했으며 공인 주소/인터넷 Route는 추가하지 않았다.
- AWS Server: 같은 Workload 계정의 별도 root/state에 VPC `10.20.0.0/16`, `/24` Subnet 4개, 사설 EC2 `10.20.1.10`와 EIC Endpoint/TGW Attachment를 생성했다. Server mock 31개/로컬 앱 1개, 중앙 회귀 34개, Client 회귀 26개와 기존 bootstrap 보호 3개 통과. 세 root 최종 Plan No changes와 실제 ANF 통신/로그 검증 완료. 구현/검증/배포 기록을 분할 커밋하고 origin/main 게시를 확인했다.
- GCP: 현재 무료 크레딧 프로젝트 안의 사설 서버/HA VPN 코드와 운영 문서 구현/로컬 Review 완료. Google mock Plan 25개, 실제 입력 mock Plan 1개, 앱/전달 도구 7개 통과. Project `project-5bb650b3-9c21-4c8b-a16` / Number `120643807854`, ADC 접근 및 고정 Ubuntu 이미지 조회 확인. ignored tfvars/backend 작성 완료. 마지막 실제 조회에서 연결된 Billing 계정은 `open=false`, 프로젝트 `billingEnabled=false`, Compute/IAP/OS Login API DISABLED였다. GCS bucket/실제 Plan/Apply/VPN 통신은 미실행이다. 구현/검증/준비 기록을 분할 커밋하고 origin/main 게시를 확인했다.

## 문서

- [개발자용 플랫폼 설계·구현 단위](ai-dlc/platform.md)
- [플랫폼 U1: 구현·검증 기록](ai-dlc/platform-u1.md)
- [플랫폼 AWS 배포·운영 기록](ai-dlc/platform-aws.md)
- [플랫폼 구성 설명 HTML](../platform/docs/overview.ko.html)
- [SAST 점검 서비스 설계(빠른 모드·전체 모드)](ai-dlc/sast.md)
- [파이프라인 스테이징 도구 설계/검증 상태](ai-dlc/staging-tool.md)
- [CloudFront 웹의 중앙 ANF 허용/차단 시험](ai-dlc/cloudfront-anf-demo.md)
- [CloudFront 직접 EC2 VPC Origin](ai-dlc/cloudfront-server.md)
- [CloudFront 실행 및 전체 입력/출력](../terraform/cloudfront-server/README.md)
- [승인된 요구사항과 구현 단위](ai-dlc/central-network.md)
- [AWS Workload 클라이언트 구성안과 승인 상태](ai-dlc/aws-workload-client.md)
- [같은 Workload 계정의 Server VPC와 실제 ANF 검증](ai-dlc/aws-workload-server.md)
- [GCP 사설 서버와 HA VPN 설계/실제 상태](ai-dlc/gcp-server-vpn.md)
- [실행 및 연결 안내](../terraform/network-security/README.md)
- [Workload 실행 및 전체 입력/출력](../terraform/workload-client/README.md)
- [Server 실행 및 전체 입력/출력](../terraform/workload-server/README.md)
- [두 VPC 실제 관제 Runbook](../terraform/workload-server/RUNBOOK.md)
- [GCP 실행 및 전체 입력/출력](../terraform/gcp-server/README.md)
- [GCP와 AWS VPN 연결/관제 검증](../terraform/gcp-server/RUNBOOK.md)

## 다음 작업

플랫폼은 `feat/platform`의 `platform/`에서 별도로 진행한다. U1은 고정 로그인 → 서비스 등록 → 공개 GitHub 사전 검사 → 결과 조회까지 구현·로컬 검증했다. [AWS 배포](ai-dlc/platform-aws.md)와 HTTPS 운영 검증을 완료했다. 다음은 커밋·푸시 범위 확인과 GitHub OIDC·Environment 연결, 이후 U2 공개 소스 격리 빌드다. ECS 연동은 후속 단위에서 해당 도구의 실제 병합·실행 상태를 확인한 뒤 연결한다.

CloudFront 두 버튼 웹의 실제 ANF 허용/차단 대조는 확인했다. 직접 EC2 Origin/Server IGW와 SG rule/임시 ALB ingress 정리와 실제 검증 JSON 게시를 마쳤고 네 root 최종 Plan은 No changes다. 남은 화면 검증은 UI 도구가 안정된 환경에서 차단 버튼 완료 화면/최종 검증 문구를 확인하는 것이다. 페이지 하단 검증 기록은 배포 당시 로그 대조 결과이며 개별 요청의 실시간 관제 판정은 아니다.

현재 요청의 **동일 Workload 계정 두 VPC 간 ANF 왕복 검사**는 완료했다. 기존 Client `10.10.1.143`에서 새 Server `10.20.1.10`에 HTTP 200으로 도달했고, 미허용 8081은 실제 blocked/drop SID로 확인했다. 양방향 FLOW와 허용 마커 ALERT를 요청 tuple/flow_id/AZ로 대조했다. 중앙/Client/Server 최종 Plan은 모두 No changes다.

다음 연결 대상을 GCP 또는 온프레미스로 확정하면 해당 환경과 비중복 CIDR/공인 Gateway/BGP 정보를 준비해 VPN을 추가한다. 이번 두 VPC 검증 결과를 멀티클라우드나 계정 간 통신 검증으로 확대하지 않는다. 코드/실제 결과와 남은 범위는 [Server AI-DLC](ai-dlc/aws-workload-server.md)를 따른다.

GCP 연결은 후속 작업으로 남긴다. 공개 Ubuntu 이미지 이름은 조회/고정했고 GCP 기반 코드와 mock 검증은 완료했다. Billing 활성 상태와 기존 CIDR/쿼터 확인, 실제 GCP/VPN 배포와 양방향 Route/허용 정책은 미완료다. GCP 앱 로컬 성공은 GCP 연결의 ANF 통과 증거가 아니다.

중앙 실행 CIDR/AZ 입력은 작성됐고, 입력 작성 시 조회한 중앙 계정 서울 리전의 기존 VPC와 CIDR이 겹치지 않았다. 다른 계정/GCP/온프레미스의 CIDR 중복 여부는 연결 등록 시 확인한다. 소비자/VPN 계약은 준비되면 추가한다.

RAM 오류 진단은 두 Association의 실제 성공 확인으로 종료했다. 관리 계정 프로필 로그인은 더 이상 요청하지 않는다. Organizations 변경과 Destroy는 실행하지 않았다. 실제 `terraform.tfvars`, `backend.hcl`, state/Plan, 검증 로그와 Provider 캐시는 커밋 대상에서 제외했다.
