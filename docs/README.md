# Delta network gateway

## 현재 상태

- 기준일: 2026-11-03 (Asia/Seoul)
- 플랫폼 Jenkins 전환: 사용자 지시로 `feat/platform-jenkins`의 별도 worktree에서 새 빌드 실행기를 Jenkins로 연결했다. 로컬 실제 Jenkins → ECR → ECS digest/헬스/종료와 웹 표시를 검증했다. 빌드 전용 IAM 기반은 적용했고 Jenkins 운영 EC2·HTTPS·공개 플랫폼 전환은 아직이다. 구현 `337b0ec`을 푸시하고 [PR #18](https://github.com/2026-sbh-delta/delta-network-gateway/pull/18)을 열었다. 최신 CI는 PR Checks를 따른다. [구성 안내](../platform/deploy/jenkins/README.md) · [검증 기록](ai-dlc/platform-jenkins.md)
- 플랫폼 SAST 연결: 성공한 빌드의 고정 커밋 검사와 발견 사항·근거 표시를 구현했다. 실제 SAST HTTP 연결과 DB/Worker/브라우저 통합을 각각 검증했다. 검사 완료와 운영 배포 승인을 구분한다. 운영 SAST 연결·DB migration `0004`·릴리스는 아직이다. [계약](../platform/contracts/security-checks.md) · [검증 기록](ai-dlc/platform-security.md)
- 플랫폼 U3: 사용자가 PR #16을 병합했고 main `35aff4a`의 GitHub Release가 성공했다. 공개 웹에서 실제 VM 역할의 ECS 실행·digest·헬스·태스크 0개 정리를 검증했다. 이후 Jenkins 전환은 위 별도 기록을 따른다. [기록](ai-dlc/platform-u3.md)
- 플랫폼 U2: [PR #14](https://github.com/2026-sbh-delta/delta-network-gateway/pull/14) 병합 후 GitHub OIDC Plan·Apply·Release를 완료했다. [공개 플랫폼](https://d3udcz0zch0on.cloudfront.net)에서 기존 로그인·서비스를 유지하고 실제 공개 저장소 빌드 → ECR digest → 결과·로그 표시를 검증했다. 운영 역할의 CodeBuild 조회 권한 오류를 수정·적용했고 회귀 검증, 실제 역할 조회, 데스크톱·모바일 화면과 두 Terraform root의 최종 No changes를 확인했다. 권한 수정 PR #15도 main에 병합됐다. [기록](ai-dlc/platform-u2.md)
- ANF 비용 절감(2026-11-03 17:08 KST): **최종 disabled 적용 완료**. Firewall/엔드포인트 2개를 제거했고 ALB/사설 Client의 두 포트 HTTP 200과 중앙 Plan No changes를 확인했다. 실제 ANF 재생성과 검사 경로/삭제 보호 원복, 허용/차단/양방향 로그까지 검증한 뒤 다시 꺼뒀다. 정책/규칙/기존 로그와 네트워크는 보존했다. 최신 main pull 후에도 중앙 mock 41개와 helper 테스트 11개 통과. 구현/테스트/운영 문서 3개 서명 커밋을 origin/main에 게시했고 원격 `fae8ca04` 일치를 확인했다. 이 게시 확인 기록은 후속 문서 커밋으로 남긴다. [기록](ai-dlc/anf-cost-mode.md)
- AWS → GCP 매핑 판단: [`migration_mapping`](ai-dlc/migration-mapping.md) 구현 완료. 실제 AWS state 로 대조했고 GCP 생성은 안 함. 앞단은 HAProxy 기각, AWS 기능 사용 방향(ADR-AI-37), 값의 기준은 Terraform(ADR-AI-38), GCP 담당은 동규 님(2026-11-02 사용자 전달).
- 통합 시연: `deploy/demo-console/` 에서 온프레미스 증거·DAST·AI 판단·Jenkins 카나리를 한 흐름과 화면으로 돌린다. 2026-11-02 로컬 리허설 3개 시나리오 확인, 브랜치 `feat/demo-integration` 에 커밋·푸시했고 서버 배포는 안 함. 배포는 Mac Docker 대역이고 AWS·GCP 는 미연결. [기록](ai-dlc/demo-integration.md)
- Jenkins 배포·AI 판단: `deploy/jenkins-local/`에서 카나리 승격·자동 롤백·사람 확인을 로컬 시나리오로 확인했다. main 병합 후 2026-11-02 delta-ai `.venv/bin/pytest -q` 85개, 파이프라인 스크립트 51개 통과, 병합 후 Jenkins 빌드 #53~65(가짜 LLM)로 사전 확인과 시나리오 경로 확인. 커밋은 로컬 브랜치 `feat/jenkins-on-main` 까지이고 푸시·AWS 배포는 안 했다. [기록](ai-dlc/jenkins-local.md)
- 플랫폼 OIDC 연결(2026-11-03): `feat/platform-oidc`에서 Free/private 저장소에 맞춰 Environment 없이 main 전용 OIDC와 역할 3개를 구성했다. AWS CLI/SSO 인증으로 업무 계정의 IAM 기반 11개 추가를 적용했고, 정책 검증·권한 시뮬레이션 19개·최종 Plan No changes를 확인했다. 후속 권한 보완까지 로컬 배포 도구 테스트 17개·IAM mock 7개가 통과했다. GitHub 변수 9개는 사용자가 등록했고 값 일치를 확인했다. 구현 `a0714e8`을 푸시하고 PR #8의 CI 전체 성공 후 main에 병합했다(`6b404fc`). 사용자가 CI를 활성화했다. 실제 OIDC Plan에서 발견한 CloudFront prefix list 조회 권한을 해당 목록에만 추가했고, 재실행은 리소스 변경 없이 성공했다. 실제 GitHub Release도 성공했고 HTTPS·기존 로그인·기존 서비스 기록과 새 Worker 작업을 검증했다. [설정값](../platform/deploy/GITHUB.md) · [진행 기록](ai-dlc/platform-oidc.md)
- 플랫폼 CI 수정(2026-11-03): PR #7의 앱 테스트는 통과했고 Terraform은 Linux용 Provider 검증값 누락으로 실패했다. 깨끗한 Linux 환경에서 재현한 뒤 lock 파일에 해당 검증값을 추가해 `init`·`validate`·mock 3개 통과를 확인했다. 최종 PR Checks는 모두 성공했고 PR #7은 병합됐다. [원인·검증 기록](ai-dlc/platform-aws.md#ci의-linux-검증값-수정)
- 플랫폼 U1(2026-11-03): 사용자 구현 승인 후 고정 로그인·공개 GitHub 소스 확인·PostgreSQL 상태/이력·API/Worker·웹을 구현했다. 실제 PostgreSQL 통합 56개, 실제 GitHub 1개, 브라우저 3개를 통과했고 ARM64 이미지의 실제 검사·재시작 후 상태 보존도 확인했다. 추가 지시에 따라 AWS 업무 계정 616723161368/서울에 별도 기반을 배포했다. HTTPS 로그인·실제 GitHub 확인·재시작·S3 백업의 임시 DB 복원·브라우저를 검증했고 최종 Plan은 No changes다. GitHub 워크플로 3개를 게시했고 PR 테스트 실행을 시작했다. AWS OIDC 연결·GitHub를 통한 실제 배포는 미실행이다. 구현 커밋 `5390c63`을 `feat/platform`에 푸시하고 [PR #7](https://github.com/2026-sbh-delta/delta-network-gateway/pull/7)을 열었다. 최종 GitHub 테스트 상태는 PR Checks를 따른다. [U1 기록](ai-dlc/platform-u1.md) · [AWS 배포](ai-dlc/platform-aws.md)
- 플랫폼 작업 시작(2026-11-02): 로컬 `feat/platform` 브랜치와 전용 `platform/` 디렉터리를 준비했다. 웹, 백엔드 API/Worker, 계약, VM 배포 자동화, HTML 설명을 분리한다. 당시 U0는 구조·설계 문서 단계였고 기능 구현·테스트·배포는 수행하지 않았다. 현재 U1 상태는 위 항목을 따른다. 구조·링크·ignore 규칙 검증 후 사용자에게 Conventional Commit 메시지를 제시했고 로컬 커밋 승인을 받았다. U0 커밋은 `0567762`이며 이후 U1과 함께 PR #7에 게시했다. 후속 커밋도 사전에 알린다. [작업 영역](../platform/README.md) · [설계/진행 기록](ai-dlc/platform.md)
- AI 판단 서비스 SAST 경로 제거(2026-11-02): `services/delta-ai/`에서 코드 보안 판단(`code_security`, SARIF 입력, 코드 패치)을 뺐다. SAST는 별도 서비스가 SARIF와 코드 흐름으로 다시 설계한다. 환경 차이 판단과 피드백 규칙 후보·승인 API는 유지하며, 승인 규칙을 적용할 코드 검사는 아직 없다. 같은 날 `.venv/bin/pytest -q` 66개 통과, `scripts/demo.py fake` 실행 완료.
- SAST 점검 서비스: `services/delta-sast/`에 빠른 모드(Opengrep 위치 표시 → 자체 AST 경로 → 결정적 분류 → 애매한 경로만 LLM → SARIF)와 HTTP 도구 API를 구현했다. 2026-11-03 테스트 38개 통과, Claude Code 로그인으로 실제 LLM 판단 1건 확인, 게시판 예제 SARIF를 DAST에 넣어 저장형 XSS 2건 `REPRODUCED`까지 왕복 확인. 2026-11-03 보안 도구 호스트(`terraform/tooling-aws/`, `deploy/tooling/`)에서 SAST → DAST(AWS 스테이징) → SAST 피드백 체인을 LLM 없이 실행했다(합계 22.3초). 전체 LLM 검증 모드와 서버의 LLM 연동은 미구현이다. [설계/검증 기록](ai-dlc/sast.md)과 [실행 안내](../services/delta-sast/README.md)를 따른다.
- 스테이징 도구: 관리 에이전트가 호출하는 파이프라인 스테이징 도구(`services/delta-staging/`)와 AWS 기반 모듈(`terraform/staging-aws/`). 2026-11-02 mock `terraform test` 11개 통과, 2026-11-03 도구 단위 테스트 34개 통과. 2026-11-03 Apply 후 실제 ECS 배포 확인: 첫 배포 56초, 이미지 교체 57초, 내리기 3초. [설계/검증 기록](ai-dlc/staging-tool.md)을 따른다.
- AI 판단 서비스: `services/delta-ai/`에 `2026-sbh-delta/delta-ai` `007d1c6`을 subtree로 추가했다. 2026-11-01 `.venv/bin/pytest -q` 98개 통과, `scripts/demo.py fake` 실행 완료. Terraform 변경은 없고, Dockerfile/배포/다른 서비스와의 실제 연동은 미구현이다. [실행 안내](../services/delta-ai/README.md)를 따른다.
- AI DAST 점검 서비스: `services/delta-dast/`에 추가했다. 2026-11-03 Delta SAST SARIF를 입력 형식으로 받게 했다. SAST 결과를 받아 스테이징에서 저장형 XSS를 재현·판정하고 수정안·보고서를 만든다. 2026-11-02 기존 가상환경으로 주요 모듈 import와 `compileall` 성공을 확인했다. 자동 테스트는 아직 없다. 컨테이너 이미지/배포/delta-ai 연동은 미구현이다. [실행 안내](../services/delta-dast/README.md)와 [API 계약](../services/delta-dast/docs/API.md)을 따른다.
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
- 전체 Destroy: 미실행. ANF 비용 중단을 위한 부분 제거와 실제 원복 검증을 완료했고 현재 최종 disabled다. 검사 모드에서 같은 계정 두 VPC HTTP 허용/차단/로그는 검증 완료. 다른 Workload 계정 간 통신과 GCP/온프레미스/VPN, 장애 전환/부하/TLS 복호화는 미검증이다.
- 실행 프로필: `AWS_PROFILE=sbh-delta-security`. 이번 DryRun에서 STS로 중앙 계정 `482840608926`을 재확인했다. 코드에서는 계정 ID를 필수 입력으로 받아 검증한다.
- AWS Workload: 사용자 `이렇게 만들고, public 노출없이 해` 지시로 Inception과 U1~U3 승인 및 구현/Review 완료. Workload mock Plan 26개 및 state bootstrap 보호 테스트 3개 통과. 별도 S3 state와 VPC `10.10.0.0/16`, `/24` Subnet 4개, 사설 EC2 `10.10.1.143` 및 EIC Endpoint 생성 완료. 초기 배포 때 공인 IP/IPv6, IGW/NAT/인터넷 Route 부재와 EIC 사설 SSH 성공을 확인했다. 후속 CloudFront root가 Client VPC에 IGW와 ALB 전용 Subnet/RT를 추가했으며 공인 주소/인터넷 Route는 추가하지 않았다.
- AWS Server: 같은 Workload 계정의 별도 root/state에 VPC `10.20.0.0/16`, `/24` Subnet 4개, 사설 EC2 `10.20.1.10`와 EIC Endpoint/TGW Attachment를 생성했다. Server mock 31개/로컬 앱 1개, 중앙 회귀 34개, Client 회귀 26개와 기존 bootstrap 보호 3개 통과. 세 root 최종 Plan No changes와 실제 ANF 통신/로그 검증 완료. 구현/검증/배포 기록을 분할 커밋하고 origin/main 게시를 확인했다.
- GCP: 현재 무료 크레딧 프로젝트 안의 사설 서버/HA VPN 코드와 운영 문서 구현/로컬 Review 완료. Google mock Plan 25개, 실제 입력 mock Plan 1개, 앱/전달 도구 7개 통과. Project `project-5bb650b3-9c21-4c8b-a16` / Number `120643807854`, ADC 접근 및 고정 Ubuntu 이미지 조회 확인. ignored tfvars/backend 작성 완료. 마지막 실제 조회에서 연결된 Billing 계정은 `open=false`, 프로젝트 `billingEnabled=false`, Compute/IAP/OS Login API DISABLED였다. GCS bucket/실제 Plan/Apply/VPN 통신은 미실행이다. 구현/검증/준비 기록을 분할 커밋하고 origin/main 게시를 확인했다.
- 스테이징 도구: 관리 에이전트가 호출하는 파이프라인 스테이징 도구를 `services/delta-staging/`, AWS 기반을 `terraform/staging-aws/`에 추가했다. 2026-11-03 도구 단위 테스트 34개 통과, Apply 후 실제 ECS 배포 확인(첫 배포 56초, 이미지 교체 57초). GCP 구현은 미실행이다. [AI-DLC](ai-dlc/staging-tool.md)를 따른다.

## 문서

- [ANF 비용 절감과 검사 경로 원복](ai-dlc/anf-cost-mode.md)

- [AWS → GCP 매핑 판단](ai-dlc/migration-mapping.md)
- [Jenkins 배포와 AI 판단 로컬 검증](ai-dlc/jenkins-local.md)
- [개발자용 플랫폼 설계·구현 단위](ai-dlc/platform.md)
- [플랫폼 U1: 구현·검증 기록](ai-dlc/platform-u1.md)
- [플랫폼 U2: 공개 저장소 이미지 빌드](ai-dlc/platform-u2.md)
- [플랫폼 U3: 샌드박스 실행 확인](ai-dlc/platform-u3.md)
- [플랫폼 Jenkins 빌드 전환](ai-dlc/platform-jenkins.md)
- [플랫폼 SAST 결과 연결](ai-dlc/platform-security.md)
- [플랫폼 AWS 배포·운영 기록](ai-dlc/platform-aws.md)
- [플랫폼 GitHub OIDC 연결](ai-dlc/platform-oidc.md)
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
- [파이프라인 스테이징 도구 설계/검증 상태](ai-dlc/staging-tool.md)

## 다음 작업

ANF는 비용 중단 모드로 유지한다. ignored `terraform/network-security/inspection-mode.auto.tfvars`를 보존하고, 검사가 필요할 때 [전환 Runbook](../terraform/network-security/RUNBOOK.md#anf-비용-중단과-원복)의 `disabled -> bypass -> enabled` 순서로 원복한다. 우회 중에는 기존 8081 차단 시험도 HTTP 200으로 응답한다.

플랫폼 U3의 공개 Release 검증을 완료했다. Jenkins 전환·SAST 결과 연결의 구현과 로컬 검증도 완료했다. [PR #18](https://github.com/2026-sbh-delta/delta-network-gateway/pull/18)의 CI 확인·병합 후 운영 Jenkins 서버·HTTPS·SAST 설정, DB migration과 플랫폼 릴리스가 남는다. 다음 기능은 DAST를 위한 ECS 앱의 검사 수명·접근 계약 확정이다. 이후 고정 digest의 Terraform/VM 배포로 이어간다.

CloudFront 두 버튼 웹의 실제 ANF 허용/차단 대조는 확인했다. 직접 EC2 Origin/Server IGW와 SG rule/임시 ALB ingress 정리와 실제 검증 JSON 게시를 마쳤고 네 root 최종 Plan은 No changes다. 남은 화면 검증은 UI 도구가 안정된 환경에서 차단 버튼 완료 화면/최종 검증 문구를 확인하는 것이다. 페이지 하단 검증 기록은 배포 당시 로그 대조 결과이며 개별 요청의 실시간 관제 판정은 아니다.

현재 요청의 **동일 Workload 계정 두 VPC 간 ANF 왕복 검사**는 완료했다. 기존 Client `10.10.1.143`에서 새 Server `10.20.1.10`에 HTTP 200으로 도달했고, 미허용 8081은 실제 blocked/drop SID로 확인했다. 양방향 FLOW와 허용 마커 ALERT를 요청 tuple/flow_id/AZ로 대조했다. 중앙/Client/Server 최종 Plan은 모두 No changes다.

다음 연결 대상을 GCP 또는 온프레미스로 확정하면 해당 환경과 비중복 CIDR/공인 Gateway/BGP 정보를 준비해 VPN을 추가한다. 이번 두 VPC 검증 결과를 멀티클라우드나 계정 간 통신 검증으로 확대하지 않는다. 코드/실제 결과와 남은 범위는 [Server AI-DLC](ai-dlc/aws-workload-server.md)를 따른다.

GCP 연결은 후속 작업으로 남긴다. 공개 Ubuntu 이미지 이름은 조회/고정했고 GCP 기반 코드와 mock 검증은 완료했다. Billing 활성 상태와 기존 CIDR/쿼터 확인, 실제 GCP/VPN 배포와 양방향 Route/허용 정책은 미완료다. GCP 앱 로컬 성공은 GCP 연결의 ANF 통과 증거가 아니다.

중앙 실행 CIDR/AZ 입력은 작성됐고, 입력 작성 시 조회한 중앙 계정 서울 리전의 기존 VPC와 CIDR이 겹치지 않았다. 다른 계정/GCP/온프레미스의 CIDR 중복 여부는 연결 등록 시 확인한다. 소비자/VPN 계약은 준비되면 추가한다.

RAM 오류 진단은 두 Association의 실제 성공 확인으로 종료했다. 관리 계정 프로필 로그인은 더 이상 요청하지 않는다. Organizations 변경과 Destroy는 실행하지 않았다. 실제 `terraform.tfvars`, `backend.hcl`, state/Plan, 검증 로그와 Provider 캐시는 커밋 대상에서 제외했다.
