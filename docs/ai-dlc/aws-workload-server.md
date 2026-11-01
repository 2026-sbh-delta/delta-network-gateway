# 동일 Workload 계정의 두 VPC ANF 검사

## 단계와 승인 상태

- 기준일: 2026-11-01 (Asia/Seoul).
- Ideation: 새 AWS 계정 생성 제약을 피하고 기존 Workload 계정 안의 두 VPC 사이에서 중앙 ANF의 실제 허용/차단을 검증한다.
- Inception 및 U1~U3 Design / Implementation Plan 승인: 사용자 `aws workload 계정에 다른 VPC 만들어서 ANF 통과해서 도달하게 만들자` 지시를 아래 사설 Server VPC 생성, 중앙 연결 및 실제 시험의 승인으로 기록한다. 주소 크기와 public 비노출은 기존 사용자 지시를 유지한다.
- 현재 단계: U1~U3 구현/Test/Review와 실제 배포/관제 검증 완료. Server mock Plan 31개와 앱 두 리스너 로컬 시험 1개, 중앙 회귀 34개, Client 회귀 26개 및 기존 state bootstrap 보호 3개 통과. 실제 HTTP 허용/차단과 정확한 요청 tuple의 ALERT, 허용 연결의 양방향 FLOW를 확인했다.
- 범위: Workload `616723161368`에 별도 Server VPC/사설 EC2/EIC Endpoint/TGW Attachment, 중앙 `482840608926`의 수락/경로/정책과 Client 원격 경로/시험 송신 규칙, 실제 ANF 왕복 통신과 로그.
- 범위 밖: 새 계정, GCP/온프레미스/VPN 배포, 공개 IP/IGW/NAT/LB, 기존 Client 교체, 기존 state 이전, Destroy. 기존 GCP 코드 변경은 보존한다.
- Git: 기존 Server 작업은 아래 Git 게시 기록처럼 분할 커밋/푸시 완료했다. 후속 CloudFront ALB/ANF 웹 변경도 사용자 지시로 분할 커밋/푸시를 완료했으며 [게시 기록](cloudfront-anf-demo.md#git-게시)을 따른다.

## Ideation

- 문제: 원격 클라우드/온프레미스의 준비 상태에 의존하지 않고 중앙 검사 경로를 실제 트래픽으로 확인해야 한다.
- 사용자: 델타 네트워크/관제 운영자.
- 성공 기준: Client에서 새 Server 사설 IP의 TCP 8080 HTTP 응답, 8081 ANF 차단과 SID 9000000, 실제 FLOW/ALERT 및 양방향 Route 증거. 서버 로컬에서는 두 포트 모두 응답해야 한다.
- 같은 계정이어도 서로 다른 VPC이므로 각 VPC의 TGW Route를 중앙 검사 경로로 보낼 수 있다. 계정 분리/멀티클라우드 검증과는 구분한다.

## Inception

### 실제 확인과 입력

| 항목 | 값 | 상태 |
| --- | --- | --- |
| Workload / 중앙 | `616723161368` / `482840608926` | 2026-11-01 19:01~19:02 KST STS 확인 |
| 프로필 / Region | `sbh-delta-workload`, `sbh-delta-security` / `ap-northeast-2` | 기존 세션 정상 |
| 기존 Client | VPC `10.10.0.0/16`, EC2 `i-077f4b16dd163998a`, IP `10.10.1.143` | running 재조회 |
| 중앙 Inspection | `10.250.0.0/16`, TGW `tgw-0dafbd07013d574ed` | Firewall/두 Endpoint READY 및 IN_SYNC 재조회 |
| 새 VPC | `10.20.0.0/16` | 조회한 Workload Client/기본 VPC와 비중복 |
| 새 EC2 | `10.20.1.10`, t4g.nano, AL2023 standard ARM64 | 기존 검증 AMI `ami-029fa37c14d9a8917` 사용. 배포 전 데이터 조회 검증 |
| 별도 state | 기존 Workload S3 bucket, key `delta/lab/workload-server/terraform.tfstate` | Client/중앙과 다른 key. 신규 state로 초기화하며 이전하지 않음 |

| 물리 AZ ID | TGW 전용 /24 | Server /24 |
| --- | --- | --- |
| apne2-az1 | `10.20.0.0/24` | `10.20.1.0/24` |
| apne2-az2 | `10.20.2.0/24` | `10.20.3.0/24` |

### Functional / Non-Functional Requirements

1. `terraform/workload-server`를 별도 root/state로 구성한다. 계정/Region/AZ/TGW 소유자 및 기본 Association/Propagation/수락 비활성을 검증한다.
2. `/16` VPC, 두 AZ의 TGW/Server 전용 `/24` Subnet과 명시적 RT 각 4개를 생성한다. 공인 IPv4/IPv6, IGW/NAT/LB와 인터넷 기본 Route는 생성하지 않는다.
3. 사설 EC2 1대, 암호화한 8 GiB gp3/IMDSv2/T4g Standard credits, 같은 VPC의 EIC Endpoint와 SG 간 제한 SSH를 구성한다. 서버 SG의 서비스 ingress는 기존 Client `/32`의 8080/8081만 허용한다. 서버의 새 outbound 연결은 허용하지 않는다.
4. AMI의 Python 표준 라이브러리로 systemd HTTP 앱을 실행한다. apt/pip/인터넷 다운로드는 사용하지 않는다. 앱은 두 포트 모두 listen한다.
5. Server Attachment를 생성하고 중앙에서 안정된 `aws_server` 키, Backend 역할, acceptance_mode=accept로 수락/Spoke Association을 관리한다. 같은 Workload account principal을 재사용한다.
6. 중앙 수락 후 Server의 명시적 Client 반환 Route를 활성화한다. Client도 Server VPC CIDR을 TGW로 라우팅하고 두 시험 포트 egress를 수용한다.
7. 중앙 ANF는 Client `/32` → Server `/32` TCP 8080만 허용한다. 미허용 8081은 마지막 SID 9000000으로 차단한다. 관측용 HTTP `/delta-test-marker` alert SID 2000001은 차단과 구분한다. 기존 `pass` 없는 flowbit/STRICT_ORDER 정책을 유지한다.
8. Terraform/Provider는 기존 1.16.4/6.71.0과 lock을 사용한다. 기존 공유 Attachment의 기본 플래그는 중앙이 소유하고 소비자에서는 해당 두 필드만 ignore_changes한다.
9. 입력/출력과 복합 입력 속성, 운영/해체 절차를 README에 작성한다. 실제 tfvars/backend/state/Plan/임시 SSH 키/증거 파일은 커밋에서 제외한다.

### Architecture와 Route

`Client VPC → TGW Spoke RT → Inspection Attachment → 같은 AZ ANF → TGW Inspected RT → Server Attachment → Server VPC`

응답은 반대로 동일 검사 단계를 거친다. TGW는 하나이며 Spoke/Inspected는 그 안의 서로 다른 Route Table이다. 두 VPC 사이 Peering이나 직접 우회 Route는 만들지 않는다.

| 위치 | 목적지 | 다음 홉 |
| --- | --- | --- |
| Client Subnet RT | Server VPC /16 | 중앙 TGW |
| Server Subnet RT | Client VPC /16 | 중앙 TGW |
| TGW Spoke | 두 VPC /16 | Inspection Attachment |
| Inspection TGW Subnet RT | 두 VPC /16 | 같은 AZ Primary Endpoint |
| Inspection Firewall Subnet RT | 두 VPC /16 | TGW |
| TGW Inspected | 각 VPC /16 | 해당 VPC Attachment |

## Construction

| Unit | Design | Implementation Plan | Approval | 상태 |
| --- | --- | --- | --- | --- |
| U1. 사설 Server 기반 | 별도 root/state, 2 AZ 전용 Subnet, 사설 EC2/EIC | 기존 Workload 검증 패턴 재사용, 고정 내부 IP/서버 앱/SG ingress 추가, 격리 mock 및 로컬 앱 검증 | 2026-11-01 사용자 생성 지시 | 구현/mock/Review/실제 23 add 적용 완료. EIC 사설 SSH/앱 두 리스너 확인 |
| U2. 중앙 검사 연결 | Backend Attachment, 양방향 TGW 경로, 8080 허용과 8081 차단 | Server 기반/Attachment 생성, 중앙 수락/경로/정책, Server 반환 Route와 Client 송신 경로를 단계별 Plan 검토 후 적용 | 같은 지시 범위 | 중앙 9 add/2 change, Client 4 add, Server 반환 Route 2 add 적용 완료. 우회 없는 Route audit 통과 |
| U3. 실제 관제 검증 | SSH/서버 로컬과 ANF 서비스 경로 분리 | 공인 노출 부재, 앱 양쪽 리스너, Route/Endpoint, 실제 HTTP 허용/차단/FLOW/ALERT 및 최종 Plan 확인 | 같은 지시 범위 | public 노출/HTTP 200/8081 blocked/drop SID/허용 마커 alert와 양방향 FLOW 확인. 세 root 최종 Plan No changes. Review 완료 |

## Acceptance Criteria / Operation

- fmt, 격리 init/validate, AWS mock Plan에서 기반/주소/계정/정책/수락/우회 부재를 검사한다. 중앙/Client 기존 테스트도 회귀 실행한다.
- 모든 실제 저장 Plan의 계정/변경 목록/삭제 부재를 확인한 뒤 적용한다. Server state는 새로운 key를 사용한다.
- EIC SSH에서 앱 두 리스너를 확인한다. 관리 경로 성공은 ANF 통과 성공으로 보지 않는다.
- Client의 8080 HTTP 응답과 양방향 FLOW, 8081 실패와 실제 blocked action/SID 9000000, 마커 SID 2000001의 alert 및 허용 응답을 대조한다. ALERT만으로 차단을 판정하지 않는다.
- 최종 세 root의 Plan이 No changes인지 확인한다. 실계정 Plan/Apply, 실제 패킷, 로그 조회, 커밋/푸시를 분리해 기록한다.
- 롤백은 추가 Client Route/egress와 중앙 정책/등록을 되돌리는 변경 목록부터 검토한다. accepter는 실제 소비자 Attachment 삭제 위험과 prevent_destroy가 있으므로 state 소유권/의존 관계를 확인한다. 기존 state와 로그를 보존하고 이번 작업에서는 Destroy를 실행하지 않는다.

## 실제 확인 기록

| 날짜 | 명령/확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-01 19:01~19:02 KST | 두 프로필 STS, Workload DescribeVpcs/DescribeInstances, 중앙 DescribeFirewall | 예상 계정/Client running 확인. 새 10.20/16은 기존 10.10/16과 172.31/16에 비중복. Firewall/두 Primary Endpoint READY/IN_SYNC |
| 2026-11-01 19:02 KST | 중앙/Client `terraform plan -input=false -detailed-exitcode` | 모두 No changes, 종료 코드 0. 새 Server 연결 전 기준선 확인 |
| 2026-11-01 19:02~19:03 KST | 임시 키/EIC를 통한 기존 Client SSH | Python 3.9.25, curl, nobody 사용자/그룹 확인. 동일 AL2023 이미지의 인터넷 다운로드 없는 앱 실행 기반 확인. 임시 키 삭제 |
| 2026-11-01 19:04~19:06 KST | 각 root `DELTA_KEEP_VALIDATION=1 scripts/validate.sh` | Server 격리 init/validate/fmt/mock **31 passed**, 로컬 HTTP 두 포트 시험 **1 passed**. 중앙 같은 계정 Client/Backend 사례 포함 **34 passed**. Client **26 passed**, 기존 bootstrap 보호 **3 passed** |
| 2026-11-01 19:05~19:06 KST | S3 versioning/PublicAccessBlock/신규 key HeadObject, Server `init -backend-config=backend.hcl` | versioning Enabled/PAB 네 항목 true. Server key는 명확한 404 후 신규 초기화. bucket/state 이전 없음. 실제 tfvars/backend 0600 |
| 2026-11-01 19:06 KST | Server 저장 Plan 및 JSON 목록/보안 검사, 저장 Plan apply 착수 | **23 add, 0 change, 0 destroy**. 예상 타입/개수와 일치, 공인 주소/IPv6와 인터넷 Route 없음. EC2/VPC/Attachment 생성 후 EIC 준비 중 |
| 2026-11-01 19:07~19:08 KST | 중앙 저장 Plan/Route target 검사, 실제 compiled RuleGroup `create-rule-group --dry-run` | **9 add, 2 change, 0 destroy** 검토. 같은 AZ Primary Endpoint/Inspection/TGW 반환 경로만 사용. DryRun 수락, consumed capacity=3, 실제 RuleGroup 새 생성 없음. 저장 Plan apply 착수 |
| 2026-11-01 19:08~19:09 KST | Client 저장 Plan/JSON 검사 및 apply | Server `/16`에 대한 Client Route 두 개 및 두 시험 포트 egress만 **4 add, 0 change, 0 destroy** 검토 후 적용 |
| 2026-11-01 19:10~19:13 KST | Server/중앙 apply 완료, DescribeTransitGatewayAttachments, 서버 공인 노출 inventory | Server **23 added**, 중앙 **9 added/2 changed**, 삭제 없음. Attachment available/Spoke associated, 공인 IP/IPv6/IGW/NAT/인터넷 Route 부재, 서버 egress 없음 및 Client `/32` 두 포트 ingress 확인. EIC는 약 5분 30초 후 create-complete |
| 2026-11-01 19:13~19:14 KST | Server `enable_remote_routes=true`, 저장 Plan/JSON 검사 및 apply | **2 add, 0 change, 0 destroy**. 두 Server Subnet RT의 Client `/16` → TGW Route만 추가. 최종 Server root 25리소스 |
| 2026-11-01 19:13~19:16 KST | 최종 Terraform Plan과 CLI 역할 자격 증명 처리 | 일반 Profile의 Terraform SSO refresh가 InvalidGrantException으로 실패했지만 AWS CLI STS는 정상. CLI의 유효한 역할 자격 증명을 subprocess 메모리 환경으로만 전달하고 계정을 재검증해 재시도. 중앙/Client/Server 세 root 모두 **No changes, 상세 종료 코드 0**. 키/토큰을 파일이나 출력에 저장하지 않음 |
| 2026-11-01 19:14 KST | 임시 키/EIC의 Server SSH, systemd/ss/localhost HTTP 확인 | SSH 종료 코드 0, delta-test active, 8080/8081 모두 실제 HTTP 200과 cloud=aws/service=delta-private-server JSON. 관리 경로 결과와 중앙 ANF 시험을 구분 |
| 2026-11-01 19:15~19:16 KST | Client EIC SSH의 curl 8080 health/marker 및 8081 대조군 | 첫 수집 도구는 curl 코드 000의 JSON 숫자 표현 오류로 실패해 수정/재실행. 최종 SSH 종료 코드 0, 8080 두 요청 HTTP 200, 8081 curl 종료 코드 28/HTTP 0. 각 실제 source port 기록 |
| 2026-11-01 19:16 KST | 실제 TGW routes/associations/propagations, 두 VPC RT/Inspection RT/Firewall API audit | 두 Client/두 Server 원격 Route active, Inspection Subnet 검사/반환 8경로 active, Spoke 연결 2/Inspected 연결 1, 직접 Propagation 0. 같은 AZ Endpoint target, Inspection appliance mode enable, Firewall/Endpoint READY/IN_SYNC |
| 2026-11-01 19:17 KST | CloudWatch `filter-log-events`의 실제 source port/IP/port/SID/action 대조 | Client :53331 → Server :8080, SID 2000001 **allowed**, verdict=alert, flow_id 1532669018529335. Client :38275 → Server :8081, SID 9000000 **blocked**, verdict=drop, flow_id 1611409625118249. 실제 curl 요청 포트와 일치. 이 시점 FLOW는 아직 0개 |
| 2026-11-01 19:19 KST | CloudWatch FLOW 재조회 및 요청/응답 tuple/flow_id/AZ 검증 | FLOW 8개 유입. 최종 health source :51787의 flow_id **1451453943784869**, 마커 source :53331의 flow_id **1532669018529335**에 대해 각각 요청/응답 두 방향과 같은 AZ를 확인. 마커 FLOW는 같은 flow_id의 SID 2000001 ALERT와도 일치. 실제 HTTP/FLOW/ALERT 검증 완료 |
| 2026-11-01 19:21 KST | `terraform fmt -check -recursive`, `git diff --check`, shell/Python 문법과 문서/입력/출력/링크 검사, 실제 증거 재대조 | 모두 통과. 새 Server 입력 17개/출력 12개와 azs 내부 속성 문서화. 새 Server tfvars/backend 및 변경한 실제 tfvars 0600, 실제 입력/backend Git 제외 확인. 기존 사용자 backend 파일의 권한은 변경하지 않음. 세 No-change Plan과 정확한 ALERT/양방향 TCP FLOW 증거 확인. 커밋/푸시 없음 |

## 현재 배포와 실제 결과

| 항목 | 실제값/결과 |
| --- | --- |
| Server VPC | `vpc-0dd4ef153e66803ab`, `10.20.0.0/16` |
| Server EC2 | `i-03d824c2a456e71ad`, `10.20.1.10`, apne2-az1 |
| Server EIC Endpoint | `eice-008f5b3a537d716bd` |
| Server TGW Attachment | `tgw-attach-08255cc8fcac5df2c`, available/Spoke associated |
| Client | 기존 `i-077f4b16dd163998a`, `10.10.1.143` 유지 |
| 허용 health | TCP 8080, source port 51787, HTTP 200 |
| 허용 마커 | TCP 8080, source port 53331, HTTP 200 및 SID 2000001 allowed |
| 차단 대조군 | TCP 8081, source port 38275, timeout 및 SID 9000000 blocked/drop |
| FLOW | 두 허용 연결 모두 같은 flow_id/AZ의 요청/응답 확인. health=1451453943784869, marker=1532669018529335 |
| 최종 Plan | 중앙/Client/Server 모두 No changes |

증거는 Server root의 ignored `.validation/`에 `private-inventory.json`, `server-probe.json`, `client-probe.json`, `final-topology.json`, `firewall-logs.json`, `log-proof.json`과 Plan/Apply 기록으로 보관한다. 실제 tfvars/backend는 Git에서 제외하며 S3 state key는 Server 전용이다.

## Review와 검증 범위

- Workload 한 계정에 서로 다른 Client/Server VPC를 구성했고 기존 Client 인스턴스를 교체하지 않았다. 새 계정과 GCP/온프레미스/VPN은 생성하지 않았다.
- 실제 Server public 비노출, Endpoint SG 기반 사설 SSH, Server의 양쪽 리스너/SG 수용, Client의 양쪽 포트 송신을 대조해 차단 원인을 구분했다.
- 중앙 Route audit와 같은 flow_id/AZ의 양방향 FLOW, 실제 HTTP 응답, 정확한 source port의 blocked/drop ALERT로 ANF 왕복 검사와 차단을 검증했다. 마커 ALERT는 allowed이며 ALERT 발생 자체가 차단이라는 해석을 하지 않는다.
- 별도 Server state의 최종 리소스는 25개이며, 이번 중앙 9개 생성/2개 갱신 및 Client 4개 생성 외 기존 리소스 삭제는 없다. 모든 실제 저장 Plan은 적용 전 JSON 목록/보안 속성을 검사했다.
- 같은 계정의 두 VPC HTTP 시험 결과다. 별도 Workload 계정 간 통신, GCP/온프레미스/VPN, 장시간 부하/장애 전환/전체 IPS 서명/TLS 복호화는 미검증이다.
- Server 구현과 검증/배포 기록을 아래 별도 커밋으로 나눠 origin/main에 푸시했고 원격 체크포인트 `14ed879c` 일치를 확인했다. Destroy는 실행하지 않았다.

## Git 게시 기록

후속 2026-11-01 20:15~20:16 KST: [CloudFront 직접 Origin](cloudfront-server.md)이 별도 state에서 이 VPC에 필수 IGW와 서비스 SG의 TCP 8080 ingress를 추가했다. 기존 Server EC2/앱/Route는 유지하고 공인 주소/인터넷 Route 부재 및 이 root의 No changes를 재확인했다. 직접 Origin 경로는 중앙 ANF를 거치지 않는다. 기존 두 VPC 관제 검증과 구분한다.

2026-11-01 사용자의 `커밋 단위로 쪼개서 커밋 푸시` 지시로 현재 변경 전체의 분할 커밋과 origin/main 푸시를 승인받았다. 기존 SSH 서명 설정을 사용하며 실제 tfvars/backend/state/Plan, 검증 증거와 캐시는 제외한다.

| 구분 | 커밋 | 내용 |
| --- | --- | --- |
| AWS 구현 | `d62eaae8` | 별도 Server VPC/사설 EC2/앱/EIC/TGW 계약, Client 원격 연결 안내 |
| AWS 검증 | `86659848` | Server mock/로컬 앱 검증 및 같은 계정 두 VPC의 중앙 검사 경로 회귀 |
| 배포 기록 | `14ed879c` | 실제 HTTP/FLOW/ALERT, 최종 Plan 및 GCP 준비 상태 구분 |

| 날짜 | 명령/확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-01 19:24 KST | `git ls-remote --heads origin main` | 원격 main이 작업 시작 기준 `9033794be07eccfb84ea8f74f222c9be08969b08`과 일치 |
| 2026-11-01 19:24~19:25 KST | `git ls-files --cached --others --exclude-standard`, 자격 증명 패턴 검사, `terraform fmt -check -recursive`, `git diff --check`, 명시적 경로의 `git add`/`git commit` | 민감 입력/검증 캐시 후보와 자격 증명 패턴 검출 없음. fmt/공백 검사 통과. GCP와 AWS 기능/검증 4개 커밋 완료. 배포 명령이나 기존 테스트는 이번 게시 작업에서 재실행하지 않음. 문서 커밋과 푸시 대기 |
| 2026-11-01 19:25 KST | 문서 커밋, `git push origin main`, `git ls-remote --heads origin main`, `git status --short` | 기능/검증/문서 5개 커밋 게시 성공. 원격 main은 `14ed879c37aa29b1b6582299a24a623b6f770ec2`와 일치하고 당시 작업 트리는 깨끗함. 이번 문서 갱신은 해당 검증 결과를 기록 |

후속 2026-11-01 20:54 KST: [CloudFront ANF 웹](cloudfront-anf-demo.md)으로 전환 완료. 이전 직접 Origin/Server IGW/직접 서비스 SG ingress를 제거하고 Client VPC ALB source /24 두 개의8080/8081 ingress4개를 별도 state에서 관리한다. 중앙 ANF의8080 허용/8081 blocked-drop을 실제 검증했으며 서버 공인 주소/인터넷 Route 부재와 이 root No changes를 재확인했다. 기존 앱은 client/port/method만 journal에 기록하므로 고유 path/query는 HTTP 응답과 ANF 마커에서 대조했다.
