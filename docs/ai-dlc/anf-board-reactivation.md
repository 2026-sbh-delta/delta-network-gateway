# delta-board-vpc의 ANF 검사 재개

기준일: 2026-11-07, Asia/Seoul.

2026-11-07 후속 변경 완료: 사용자의 명시적 전체 기본 차단 해제 승인으로 SID `9000000`을 제거했다. 나머지 6개 규칙과 기존 자원을 유지했고 AWS ALB 두 IP의 외부 HTTPS200, Primary2/Secondary2 READY와 정책 IN_SYNC 및 최종 실제 Plan No changes를 확인했다. 아래 재개 당시의 최소 허용/기본 차단 기록은 당시 snapshot이며, 현재 변경 상태는 이 문서 마지막의 후속 기록을 따른다.

## 승인과 현재 단계

- Ideation: 승인 완료. 사용자의 `ㅇㅇ 지금 통신규칙 라우팅 확인하고 Workload 애들 ANF 들렀다 가야되는 거 알지?` 지시와 `delta-board-vpc만 해당` 제한을 적용한다.
- 검사 범위: 후속 선택 `인터넷 입출구와 GCP 통신 모두`로 확정했다.
- Inception: 2026-11-07 사용자 `ㄱㄱ`로 아래 요구사항과 Architecture 승인 완료.
- Construction: 같은 지시로 U1부터 U4까지의 Design/Implementation Plan, 단계별 Plan/Apply와 한시적 EIC/IAP 통신 검증 승인 완료. 구현, Test, Review와 실제 전환을 완료했다.
- 재개 당시 실행: 중앙/Workload 모두 enabled다. Primary 2개와 Secondary 2개 READY, 정책 IN_SYNC와 삭제 보호를 확인했다. 인터넷 두 AZ와 GCP 양방향 HTTP200, NAT HTTPS200, RDS TLS1.3, 미허용 8081의 실제 blocked/drop 및 FLOW/ALERT 원본 21건의 중앙 수신 일치를 확인했다. 두 최종 실제 Plan은 No changes/종료0이다.
- Operation: 임시 probe/규칙/키/터널을 정리하고 기존 Compose inactive/disabled를 유지했다. 기존 ALB DNS와 자원 ID를 보존했다. 공개 앱은 중지 상태의 기존 HTTP502로 돌아왔으며 앱 정상화나 CRUD 검증으로 보고하지 않는다. Git 게시와 원격 CI 상태는 Git 이력과 실행 결과를 따른다.
- 별도 GCP 검사 작업의 현재 상태는 [GCP 운영 기록](gcp-central-network.md#2026-11-07-operation-gcp-cloud-ngfw-검사-재개)을 따른다. 이 AWS 작업에서는 GCP 검사 설정을 변경하지 않았다.

## Ideation

- 문제: 중앙 ANF가 없고, 기존 허용 목록도 비어 있다. 현재 인터넷은 Workload의 ALB/NAT/IGW를 직접 사용하므로 중앙 TGW 검사만 켜서는 전체 요구를 충족하지 않는다.
- 사용자: Delta의 네트워크 운영자와 delta-board-vpc를 사용하는 앱 운영자.
- 성공 기준: 대상 VPC의 인터넷 입출구와 GCP 사설 통신이 양방향으로 ANF를 지나며, 필요한 통신과 관리 경로가 유지된다. 실제 허용/차단 및 원본/중앙 로그를 대조한다.
- Scope: 대상 VPC에 한정한 인터넷 검사 경로, 기존 중앙 ANF의 GCP 검사 경로, 필요한 허용 규칙/SG, 단계별 전환과 복귀, 테스트와 운영 문서.
- Non-goals: 다른 Workload/Platform VPC의 라우팅 변경, GCP 인프라/NGFW 변경, 앱/DB 교체, 앱 Compose 재시작, VPC 내부 ALB-앱/앱-DB/컨테이너 간 통신의 검사, TLS 복호화, 과거 로그 이관.

## 전환 전 실제 상태

| 항목 | 2026-11-07 사전 조회 결과 |
| --- | --- |
| 중앙/Workload 계정 | `482840608926` / `616723161368`, 둘 다 STS 확인 |
| 대상 VPC | `delta-board-vpc`, `vpc-056871f49eecfeef4`, `10.10.0.0/16` |
| 대상 Attachment | `tgw-attach-0cd5f3d3c73ee7643`, available, Spoke RT에 associated |
| TGW의 다른 연결 | 중앙 Inspection VPC 1개와 GCP VPN 2개. 다른 Workload VPC 연결은 이번 snapshot에 없음 |
| 중앙 검사 VPC | `vpc-0aedcc4463cd01655`, `10.250.0.0/16`; Attachment appliance mode enable |
| 현재 ANF | `ListFirewalls` 빈 배열, 실행 입력/기준 Plan은 disabled |
| 현재 Rule Group | ACTIVE. HTTP marker SID 2000001과 마지막 default deny SID 9000000만 있고 허용 규칙은 0개 |
| 실제 앱 호스트 | `i-0637ad326681c7480`, `10.10.1.211`, running, AZ `apne2-az1` |
| 인터넷 입구 | internet-facing ALB, listener HTTP 80, private target TCP 3000 |
| 인터넷 출구 | 앱 private RT의 `0.0.0.0/0 -> nat-05c92dc834392a6f9`; NAT는 public `10.10.10.0/24`에 있음 |
| 현재 public RT | `10.10.10.0/24`, `10.10.11.0/24` 모두 `0.0.0.0/0 -> igw-03ab99dd1d97c855c` |
| 현재 GCP VPC 경로 | 두 client subnet RT의 `10.32.1.0/24`, `10.32.2.0/24 -> TGW` |
| 실제 GCP 학습 경로 | `10.31.0.2/32`, `10.32.1.0/24`, `10.32.2.0/24`, `10.32.3.0/24`; 두 VPN의 4개 터널 UP |
| 현재 중앙 입력 차이 | `vpn_networks.gcp.cidrs`에는 `10.32.1.0/24`, `10.32.2.0/24`만 등록됨 |
| GCP BGP | Cloud Router 4개 peer UP, 각 AWS 경로 1개 수신 |
| 실제 GCP 대상 | board frontend `10.32.1.20` RUNNING. 별도 실행 앱 `10.32.2.5`도 있어 임의 변경하지 않음 |
| GCP 사설 규칙 snapshot | policy delta-lab-central의 priority 200/300은 AWS `10.10.0.0/16`에 TCP 3000/8080 allow |
| 실제 AWS 앱 SG | ALB SG의 TCP 3000과 EIC SG의 SSH 22만 ingress 허용. egress는 DB SG의 TCP 5432와 외부 TCP 443. 기존 Client 시험 SG의 8080/8081 규칙은 앱 ENI에 붙어 있지 않음 |
| 내부 통신 | ALB-앱과 앱-RDS는 local 및 기존 SG 참조를 사용. 이번 검사 범위에 포함하지 않음 |
| 관리 경로 | 기존 EIC `eice-03436798a3c0585a5` create-complete. 앱은 SSM 관리 인스턴스 목록에 없음 |
| 현재 HTTP 기준 | ALB target unhealthy / Target.FailedHealthChecks. 공개 `/`, `/api/health` 모두 HTTP 502 |
| 기준 Plan | 중앙 managed 67개, Workload managed 85개. 두 root 실제 Plan No changes / 종료 0 |

위 표는 전환 전 snapshot이다. 이후 EIC/IAP로 두 호스트의 Compose inactive/disabled와 비어 있는 시험 포트를 확인했다. 임시 probe를 이용한 HTTP200 검증 뒤 probe를 제거했으며, 최종 공개 HTTP502는 유지한 앱 중지 상태와 구분해 기록한다.

## Inception

### Functional Requirements

1. 실행 직전에 Name, VPC ID, CIDR, Account ID와 Attachment ID를 모두 대조한다. Workload 연결이 추가되거나 대상이 달라지면 기존 전환 계획을 재사용하지 않는다.
2. 인터넷은 target VPC 안의 AZ별 공유 ANF Endpoint를 거친다. 중앙 Primary Endpoint ID를 다른 VPC의 Route Target으로 쓰지 않는다.
3. GCP 통신은 기존 TGW와 중앙 Inspection VPC의 Primary Endpoint를 왕복한다. 요청/응답이 같은 AZ의 같은 상태 기반 Endpoint를 사용하도록 실제 경로를 대조한다.
4. 실제 BGP 4개 prefix와 중앙 연결 계약의 차이를 해소한다. 등록한 검사 목적지와 실제 학습 목적지가 다르면 전환하지 않는다. 기존 GCP 광고나 DB 경로를 임의로 철회하지 않는다.
5. VPN이 연결된 기존 Spoke RT의 BGP 전파를 보존하고, board만 연결할 별도 송신 RT에 원격 정적 검사 prefix를 둔다. VPN 연결 RT에 GCP 정적 대역을 넣어 되광고하지 않는다. Inspected RT에서는 두 VPN의 BGP 전파와 철회/ECMP 동작을 보존한다. 특정 VPN Attachment 하나에 GCP 목적지를 고정하지 않는다.
6. Internet HTTP 80 ingress와 현재 NAT HTTPS 443 egress를 허용한다. 기존 앱의 ALB health, DB TLS, EIC SSH, API/이미지/로그 목적지 통신이 유지돼야 한다.
7. GCP 서비스 통신은 현재 구성과 호스트 리스너를 대조한 뒤 필요한 TCP 포트만 허용한다. 우선 검증 후보는 기존 frontend 서비스 TCP 3000이며, SG와 ANF 양쪽을 같은 계약으로 맞춘다. 8080의 컨테이너 내부 포트를 원격 공개 포트로 추정하지 않는다.
8. 새 로그는 `/delta/security/nfw-flow`, `/delta/security/nfw-alert`에 저장되고 기존 구독을 통해 `/delta-cp/central/aws`로 전달돼야 한다. 실제 서비스 이벤트의 tuple, 시각, action, SID와 원문을 대조한다.
9. 임시 검증을 제외한 다른 VPC/앱/DB/보안 정책의 생성, 교체, 삭제를 Plan에 섞지 않는다.

### Non-Functional Requirements

- 먼저 준비 리소스만 생성하고 실제 전달 경로는 유지한다. 모든 Primary/Secondary Endpoint READY와 정책 IN_SYNC 및 전달 Route 확인 후에만 트래픽을 전환한다.
- AZ 이름 대신 `apne2-az1`, `apne2-az2`의 물리적 AZ ID로 중앙과 Workload를 매칭한다.
- 기존 Route의 다음 홉은 검토한 저장 Plan에 한정한 ReplaceRoute/ReplaceTransitGatewayRoute 방식으로 바꿔 삭제 후 생성 공백을 피한다. API 갱신 뒤에는 이전 Plan을 폐기하고 새 Plan으로 state를 대조한다.
- 기존 VPC/EC2/ALB/NAT/EIP/RDS/TGW/Attachment의 ID를 유지한다. 실제 Plan의 소유권과 변경 주소를 allowlist로 검토한다.
- paired ingress/egress 전환은 원자적 작업이 아니며 기존 TCP 연결은 재접속할 수 있다. 무중단을 보장한다고 기록하지 않는다. 두 방향 전환을 한 절차로 묶고 즉시 검증 및 복귀한다.
- 데이터 경로와 독립된 기존 EIC 관리 경로를 유지한다. 관리 SG를 광범위하게 열거나 장기 SSH 키를 추가하지 않는다.
- Plan/state/원본 증거는 ignored `.validation/anf-board-reactivation-20261107/`에 저장하고 민감 산출물은 0600으로 제한한다. 토큰/비밀번호/PSK를 출력하지 않는다.
- GCP 재개 작업과의 공유 상태를 전환 직전에 다시 읽는다. 상대 작업의 문서와 실제 GCP 설정을 덮어쓰지 않는다.
- 모든 변경 모듈의 README 입력/출력 표와 실제 실행 상태를 같은 작업에서 갱신한다.

### Architecture

| 통신 | 제안하는 양방향 검사 경로 |
| --- | --- |
| 인터넷 입구 | Internet -> 기존 IGW -> board VPC의 해당 AZ Secondary ANF Endpoint -> 기존 public ALB -> 기존 앱 |
| 인터넷 출구 | 앱 -> 기존 NAT -> board VPC의 해당 AZ Secondary ANF Endpoint -> 기존 IGW -> Internet |
| GCP 사설 | board VPC -> board 전용 TGW 송신 RT -> 중앙 Primary ANF Endpoint -> TGW Inspected RT의 BGP -> HA VPN -> GCP. 응답은 VPN Spoke RT의 board 반환 경로와 중앙 Endpoint 경유 |
| VPC 내부 | ALB -> 앱 TCP 3000, 앱 -> RDS TCP 5432, EIC -> 앱 TCP 22는 기존 local 경로 |

중앙 ANF 1개와 기존 정책을 사용한다. 중앙 Primary Endpoint 2개를 복구하고 board VPC에 Secondary Endpoint 2개를 추가하는 설계이며, 추가 시간 요금이 발생한다. 별도 다른 VPC에 Endpoint를 만들지 않는다.

인터넷용 Workload Endpoint subnet 후보는 미사용 `10.10.30.0/24`, `10.10.31.0/24`다. 생성 직전에 실제 Subnet/Route/계약 중복을 다시 검사한다. 각 Endpoint RT는 VPC local과 `0.0.0.0/0 -> 기존 IGW`를 갖는다. 각 public RT의 default는 같은 AZ의 Secondary Endpoint로 바꾸고, IGW edge RT는 두 public subnet CIDR을 각 AZ의 Secondary Endpoint로 보낸다. 앱 private RT의 기존 NAT default와 local 경로는 유지한다.

이 NAT 뒤 검사 모델에서는 ANF가 원래 앱 주소 대신 NAT의 사설 출발지 주소를 본다. 현재 NAT 사설 주소는 `10.10.10.249`다. 출발지 포트도 변환될 수 있으며 실제 검증에서 앱 `32954`가 NAT `16087`로 바뀌었다. 요청 시각과 고유 peer를 먼저 대조한 뒤 실제 NAT tuple/flow_id를 확인한다. 이를 앱 ENI 원본 IP/포트 로그라고 보고하지 않는다.

GCP용 중앙 subnet Route와 정적 검사 prefix는 실제 4개 광고를 포함한 명시적 계약으로 관리한다. 기존 VPN BGP 전파는 VPN Spoke/Inspected 양쪽에 보존한다. GCP 정적 목적지는 board 전용 송신 RT에만 두고 기존 VPN Spoke RT에는 AWS 반환 목적지만 정적으로 유지한다. 다른 AWS Workload Attachment가 발견되면 이 설계를 그대로 적용하지 않고 대상별 분리를 먼저 검토한다.

### Unit of Work와 Construction 제안

아래 Design과 Implementation Plan은 2026-11-07 사용자 `ㄱㄱ`로 승인됐다. 모든 Unit의 구현, 로컬 검증, 실제 적용과 Review를 완료했으며 명령별 결과는 아래 실행 기록과 구분한다.

| Unit | Design | Implementation Plan | Acceptance Criteria / 상태 |
| --- | --- | --- | --- |
| U1. 대상과 통신 계약 | 단일 VPC 고정, 4개 BGP prefix 보존, 기본 차단 앞의 최소 허용 | 중앙 허용 목록/광고 계약과 BGP 전파 보존 입력을 추가하고 실제 리스너를 대조해 AWS GCP SG를 맞춘다. README와 다른 VPC/오래된 경로 거부 검증을 갱신한다 | 다른 Workload 변경 없음, 4개 prefix 보존, API/DB/EIC/인터넷 필수 통신 확인. 구현/Test/Review 완료 |
| U2. 비전달 상태의 ANF 준비 | 중앙 bypass에서 Primary 복구, board Secondary와 미연결 edge RT 준비 | 저장 Plan 검토, 중앙 Firewall/로그/RAM 복구, Workload 전용 subnet/Endpoint/RT 준비, 실제 READY/IN_SYNC와 물리 AZ 대조 | 준비 시 기존 전달 경로 불변과 실제 Endpoint4개 READY 확인. 구현/Test/Review 완료 |
| U3. 인터넷 전환 | 기존 ALB/NAT를 보존한 대칭 IGW edge 라우팅 | 두 public default와 IGW edge Association을 한 절차로 전환하고 HTTP/HTTPS와 양방향 FLOW/ALERT를 대조한다. 실패 시 기존 IGW/Route로 복귀한다 | 입구/출구 모두 ANF 경유, AZ 일치, NAT/ALB/EIP/DB/관리 경로 보존. 실제 전환/Test/Review 완료 |
| U4. GCP 전환과 최종 검증 | board 전용 송신 RT의 4개 검사 경로와 공유 VPN RT의 board 반환 경로, 기존 BGP 전파 유지 | 미연결 전용 RT를 준비한 뒤 board Association과 반환 다음 홉을 함께 전환한다. 정확한 Association 조회로 수렴을 확인하고 refresh-only/한 주소 Import 후 새 Plan으로 대조한다 | 특정 VPN 고정/원격 대역 되광고 없음, 사설 왕복과 실제 default deny 확인, cleanup/두 최종 Plan No changes. 실제 전환/Test/Review 완료 |

### Acceptance Criteria

1. 전환 전후 대상 VPC, 기존 호스트/ALB/NAT/EIP/RDS와 모든 기존 Attachment ID가 동일하다. 다른 VPC의 Route/Association/SG는 바뀌지 않는다.
2. Primary 2개와 Secondary 2개의 READY, 정책 IN_SYNC 및 AZ별 실제 Endpoint ID를 확인한다.
3. 인터넷 요청/응답과 NAT HTTPS 요청/응답의 같은 Endpoint 통과를 실제 FLOW로 확인한다. HTTP 502나 TCP timeout만으로 검사 성공을 판단하지 않는다.
4. GCP 허용 요청과 응답이 실제 상대 응답을 받고 동일 flow/Endpoint로 기록된다. 네 VPN 터널 및 BGP가 UP이고 기존 4개 광고가 유지된다.
5. 차단은 실제 요청 tuple과 NFW blocked/drop 및 SID를 대조한다. SG/미실행 listener로 생긴 실패를 ANF 차단으로 보고하지 않는다.
6. ALB health, AWS API HTTPS, DB TCP/TLS, 관리 SSH와 기존 local 통신을 검증한다. DB 비밀값을 조회하거나 저장된 데이터를 변경하지 않는다.
7. 서비스 FLOW/ALERT의 새 원본 그룹과 플랫폼 중앙 수신을 대조한다. 기존 수동 smoke 성공을 실제 NFW 발생 성공으로 재사용하지 않는다.
8. 사용자가 중지한 Compose는 자동 재개하지 않는다. 앱이 중지돼 있으면 별도 임시 검증 응답과 원래 앱 상태를 구분하고, temporary probe를 제거한 뒤 원래 상태를 확인한다.
9. 최종 두 root의 실제 Plan No changes/종료 0, 임시 자원/키/터널/규칙 정리, rollback 자료와 운영 문서를 확인한다.

## Operation

### Deployment

1. 최신 working tree와 공유 문서 변경, 두 AWS 계정/대상 VPC, 실제 GCP 작업 상태와 BGP 광고를 다시 확인한다.
2. 기존 실제 Route/Association/정책과 state를 보관하고, 허용 계약 및 rollback 다음 홉을 검증한다.
3. 기존 EIC의 한시적 공개키 전송/터널과 GCP IAP/기존 OS Login 키를 이용한다. 실제 실행에서 GCP 등록 키를 추가하거나 삭제하지 않았으며 임시 토큰 파일만 짧게 사용 후 제거했다. 토큰/개인키를 로그나 Git에 넣지 않는다.
4. U1/U2에서 경로 전환 없는 준비를 적용하고 모든 Endpoint 상태를 확인한다. 기존 데이터 경로와 관리 경로가 유지되는지 확인한다.
5. U3/U4는 각각 최신 저장 Plan과 실제 경로 재대조 후 실행한다. 예상 밖의 리소스 변경이나 새 BGP prefix가 있으면 전환을 중단한다.
6. 각 전환 직후 실제 양방향 요청과 로그를 대조하고, 정상인 경우에만 다음 전환으로 진행한다. 실패 시 해당 전환부터 원래 경로로 되돌린다.

### 검증과 Observability

- 호스트 접속 후 먼저 현재 listener/process/Compose 상태를 읽는다. 동작 중인 포트를 빼앗거나 프로세스를 종료하지 않는다.
- 실제 앱이 중지된 경우 비어 있는 승인된 포트에 짧은 수명의 probe를 두어 HTTP/health와 사설 왕복을 검증한다. 앱 CRUD 성공으로 보고하지 않고 probe 응답으로 표시한다.
- 차단 대조에 임시 SG가 필요하면 기존 호스트의 정확한 사설 /32 및 시험 포트만 허용하고, 전환 전 성공/전환 후 NFW drop을 대조한 뒤 즉시 제거한다. 다른 GCP 실행 앱 `10.32.2.5`를 시험 대상으로 임의 사용하지 않는다.
- 각 요청의 UTC/KST 시각, probe ID, 양쪽 IP/포트, HTTP 결과와 flow_id/Endpoint/AZ, FLOW 양방향 bytes/packets, ALERT action/verdict/SID를 기록한다.
- 비밀값 없이 기존 호스트에서 NAT HTTPS, DB TCP/TLS와 관리 접속을 검사한다. 중앙 저장 시 원본 account/log group/stream/timestamp/message를 대조한다.
- 임시 probe/규칙/키/터널 제거 후 기존 앱 상태, 실제 Route와 두 최종 Plan을 다시 확인한다. BGP/Endpoint 상태와 원본/중앙 로그를 지속 점검하는 Runbook을 갱신한다.

### Rollback

- 인터넷 전환 실패: 두 public default를 기존 IGW로 복구하고 IGW edge Association을 이전 상태로 되돌린다. 양방향을 함께 복구한 뒤 공개 기준 상태와 NAT HTTPS를 재검증한다.
- GCP 전환 실패: board 반환 다음 홉을 기존 Workload Attachment로 되돌리고 board의 단일 Association을 기존 공유 Spoke RT로 복구한다. 준비한 전용 송신 RT/정적4경로는 미연결 상태로 유지할 수 있다. 네 터널과 실제 사설 요청을 재검증하고 refresh-only/정확한 Association Import로 state를 동기화한다.
- Endpoint 준비 실패: 기존 경로를 전환하지 않는다. 생성된 자원은 state에서 계속 관리하며 임의로 state를 덮어쓰거나 다른 자원을 제거하지 않는다.
- 경로 복귀와 Endpoint 비용 중단을 구분한다. Endpoint 제거는 실제 경로 참조와 Secondary Association 제거 순서를 확인한 별도 Plan으로 다룬다.

## 검증 기록

원본 증거는 ignored `.validation/anf-board-reactivation-20261107/`에 보관한다. 첫 표는 사전 조회 기록이며 재개 완료 결과는 마지막 Operation 검증 표를 따른다.

| 날짜 | 실행한 명령/API | 결과 |
| --- | --- | --- |
| 2026-11-07 | 두 프로필 `aws sts get-caller-identity`와 `ec2 describe-vpcs --filters Name=tag:Name,Values=delta-board-vpc` | 계정 2개와 단일 대상 VPC ID/CIDR 확인 |
| 2026-11-07 | `ec2 describe-{subnets,route-tables,instances,security-groups,network-acls,nat-gateways,internet-gateways,vpc-endpoints}` | 대상 VPC의 실제 subnet/RT/SG/NACL/NAT/IGW/ENI 연결을 수집. 오류 0개. inventory.json/summary.json |
| 2026-11-07 | `network-firewall describe-rule-group --rule-group-name delta-lab-stateful --type STATEFUL` | 허용 0개와 marker/default deny 실제 규칙 확인 |
| 2026-11-07 | `ec2 search-transit-gateway-routes`, `get-transit-gateway-route-table-propagations`, `describe-vpn-connections`의 비밀값 제외 query | 두 RT의 4개 GCP BGP prefix, 두 전파와 4개 UP 터널 확인. checks.json |
| 2026-11-07 | `AWS_PROFILE=sbh-delta-security terraform -chdir=terraform/network-security plan -input=false -no-color -lock-timeout=30s -detailed-exitcode -out=<ignored>/central-baseline.tfplan` | 실제 No changes, 종료 0, 변경 주소 0개 |
| 2026-11-07 | `AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload plan -input=false -no-color -lock-timeout=30s -detailed-exitcode -out=<ignored>/workload-baseline.tfplan` | 실제 No changes, 종료 0, 변경 주소 0개 |
| 2026-11-07 | `elbv2 describe-target-health`, `describe-listeners`, 공개 `/`와 `/api/health` HTTP GET | target unhealthy, HTTP 80 -> target 3000, 두 HTTP 502. public-baseline.json |
| 2026-11-07 | `ssm describe-instance-information`, `ec2 describe-instance-connect-endpoints` | SSM 목록에 대상 없음. 기존 EIC create-complete 확인. 호스트 접속은 미실행 |
| 2026-11-07 | 기존 ADC 인증을 메모리에서만 사용한 Compute REST GET instances/networks/router status/firewall policy | 두 GCP 실행 호스트, 4개 BGP UP, TCP 3000/8080 private allow 확인. gcp-readonly.json/gcp-policy.json |
| 2026-11-07 | GCP 조회 초기 시도와 수정 | gcloud 기본 계정의 instances.list 권한 부족, Python 기본 CA 오류, 잘못된 policy REST 경로 404를 구분했다. 시스템 CA로 인증서 검증을 유지하고 공식 REST 경로/기존 ADC로 위 조회 성공. 인증/정책 설정 변경 없음 |
| 2026-11-07 | `git diff --check -- docs/README.md docs/ai-dlc/anf-cost-mode.md`, Python 문서/링크/Plan 검사, 마지막 `network-firewall list-firewalls` | diff 검사 통과, 새 문서 로컬 링크 4개와 추가 문자/코드 블록 검사 통과, 동시 GCP README 변경 보존 및 두 기준 Plan 변경 0개 확인. Firewall은 여전히 빈 배열. 구현 테스트/Apply는 미실행 |

## 설계 근거

- [AWS 공유 Firewall/Secondary Endpoint 요건](https://docs.aws.amazon.com/network-firewall/latest/developerguide/creating-vpc-endpoint-association.html): 같은 물리 AZ의 Primary Endpoint와 공유 권한, 전용 subnet 및 READY 확인이 필요하다.
- [AWS ALB/NAT와 IGW 사이 검사 모델](https://aws.amazon.com/blogs/networking-and-content-delivery/deployment-models-for-aws-network-firewall/): 기존 public ALB/NAT 앞에 Endpoint를 두는 인터넷 검사와 NAT 뒤 출발지 표시 특성을 따른다.
- [AWS Network Firewall Route 구성](https://docs.aws.amazon.com/network-firewall/latest/developerguide/route-tables.html): 요청과 응답 모두 Endpoint를 경유해야 한다.
- [AWS 중앙 검사와 TGW appliance mode](https://docs.aws.amazon.com/whitepapers/latest/building-scalable-secure-multi-vpc-network-infrastructure/centralized-network-security-for-vpc-to-vpc-and-on-premises-to-vpc-traffic.html): GCP 경로는 기존 중앙 Inspection VPC 모델을 유지한다.
- [기존 ANF 비용 모드와 당시 원복](anf-cost-mode.md), [AWS NFW 원본/중앙 전달](aws-traffic-log-forwarding.md), [기존 Workload 통합](aws-workload-consolidation.md)을 현재 snapshot과 구분한다.

## 2026-11-07 Construction 실행 기록

- 승인: 사용자 `ㄱㄱ`로 Inception, U1~U4 구현과 단계별 Apply 및 한시적 EIC/IAP/probe 검증을 승인받았다.
- U1 구현: 중앙 허용 4개, 실제 GCP 광고 4개 계약과 BGP 전파 보존 입력, Workload 공유 Endpoint 준비/전환 모드와 명시적 GCP peer SG를 구현했다. 실제 입력과 증거는 ignored 경로로 관리한다.
- U2 준비: 중앙 저장 Plan의 생성17/정책 갱신2/삭제0 Apply를 완료했다. 준비 시 기존 TGW Route/Association/Attachment는 no-op이었다. 두 root의 기존 state를 백업했고 Primary2개 READY/IN_SYNC, RAM ASSOCIATED와 FLOW/ALERT 연결을 확인한 뒤 다음 단계로 진행했다.
- Workload 준비: 신규16/기존 갱신0/삭제0 Apply를 완료했다. Secondary2개, 전용 subnet/RT/미연결 edge RT와 GCP peer `10.32.1.20/32` TCP3000 ingress/egress를 준비했고 당시 public/TGW 다음 홉과 IGW edge Association은 보존했다.
- U3/U4 구현: 인터넷 helper는 두 public default와 edge Association을 함께 전환/복귀한다. GCP helper는 board 전용 RT Association과 기존 공유 RT의 board 반환 다음 홉을 함께 전환/복귀한다. 정적 VPN Route 생성/제거 지원은 일반 모드용이며 이번 전환에서는 공유 VPN RT에 GCP 정적 Route를 만들지 않았다.
- 인터넷 edge Association은 실제 IGW/RT 조합으로 Import했다. GCP는 refresh-only 저장 Plan/Apply 후 정확한 board Association 한 주소만 새 RT/Attachment 조합으로 Import했다. 이전 저장 Plan을 폐기하고 새 Plan으로 삭제 보호만 복원한 뒤 두 최종 No changes를 확인했다.

| 날짜 | 실행한 명령 | 결과 |
| --- | --- | --- |
| 2026-11-07 | `bash terraform/network-security/scripts/validate.sh` | 최종 Python22개/Terraform mock75개 통과, 종료0. 초기 fmt와 실제 API 응답/수렴 처리 보완 후 전체 재실행 |
| 2026-11-07 | `bash terraform/workload/scripts/validate.sh` | 최종 bootstrap Python3개/helper13개, root mock44개/sample-app mock12개 통과, 종료0. 초기 fixture 계산 시점과 빈 응답 처리를 수정한 뒤 전체 재실행 |
| 2026-11-07 | helper 회귀 검증 | 다른 계정/VPC, 오래된 다음 홉/잘못된 AZ, unready Endpoint, 부분 API 실패 복귀, 성공 빈 응답, Association 조회 지연과 재시도 검증 포함. 실제 패킷 결과는 아래 Operation과 별개 |
| 2026-11-07 | `operate.py plan central central-prepare`, Plan JSON review/두 state 백업/실제 VPC와 Attachment 조회 | 생성17/정책 갱신2/삭제0, 기존 TGW 전달 경로와 연결 no-op. VPC 이름/ID/CIDR 일치 및 다른 Workload 연결 없음 |
| 2026-11-07 | `operate.py apply central central-prepare` | 생성17/정책 갱신2/삭제0 완료, Primary2개 READY/IN_SYNC와 로그/RAM 확인 |
| 2026-11-07 | `operate.py plan workload workload-prepare`, `operate.py apply workload workload-prepare` | 생성16/갱신0/삭제0 완료. subnet 대역 중복 없음과 Secondary2개 READY 확인 |

### 실행 중 확인한 API와 BGP 표현의 보완

- 인터넷 dry run에서 실제 EC2 API의 Endpoint Route가 `VpcEndpointId` 대신 `GatewayId=vpce-...`로 표시되는 것을 확인하고 두 표현을 지원했다. subnet Association stale 검사도 추가했다.
- 첫 실제 인터넷 시도는 성공한 `ReplaceRoute`의 빈 응답을 JSON 오류로 해석해 중단됐다. 복귀 API 이후 실제 두 public default가 원래 IGW/active이고 edge Association이 없는 것을 즉시 확인했다. 정상 빈 응답과 CLI 실패 구분 회귀를 추가했고 재시도는 두 ALB IP의 HTTP200과 NAT HTTPS200을 확인했다. 원인과 최종 결과를 구분한다.
- 인터넷 전환 후 edge Association을 실제 IGW/RT ID로 Import했고 새 Workload Plan은 실제 No changes/종료0이었다. EIC를 통한 RDS TLS1.3/인증서 검증도 완료했다. 임시 첫 probe 종료 후 AWS/GCP 앱 inactive/disabled 및 임시 SG 회수를 확인했다.
- [AWS TGW 공식 동작](https://docs.aws.amazon.com/vpc/latest/tgw/how-transit-gateways-work.html)에 따르면 VPN에 연결된 RT의 경로가 BGP로 광고된다. GCP 정적 대역을 그 RT에 추가하지 않도록 `isolated_inspection_spokes=["aws_client"]`로 board 전용 송신 RT를 준비한다. 현재 승인한 단일 VPC/중앙 ANF 범위 안에서 BGP 광고를 보존하는 전환 방식의 보완이다. GCP 설정은 변경하지 않는다.
- 전용 RT의 4개 GCP 정적 경로는 미연결 상태로 먼저 생성한다. enabled 전환은 board의 단일 Association과 기존 VPN Spoke RT의 board 반환 다음 홉을 함께 전환한다. helper 실패 시 둘 다 이전 상태로 복구한다.
- Association을 API로 이동한 뒤에는 기존 state를 백업하고 refresh-only 저장 Plan/Apply로 실제 없어진 이전 Association과 변경된 반환 다음 홉만 동기화한다. 이어 정확히 `aws_ec2_transit_gateway_route_table_association.spoke["aws_client"]` 한 주소를 실제 새 RT/Attachment 조합으로 Import한다. refresh-only는 AWS 자원을 변경하지 않으며 accepter/Attachment/state 전체를 제거하지 않는다. 이외 모든 managed ID 보존과 새 Plan을 대조한다.
- 첫 GCP 시도는 일반 `DescribeTransitGatewayAttachments.Association`의 지연값으로 완료를 판정해 AlreadyAssociated 오류가 발생했다. 기존 공유 RT Association과 직접 반환 경로 복구 및 양방향 HTTP200을 실제 재확인했다. 정확한 `GetTransitGatewayRouteTableAssociations` 조회와 이전 바인딩 해제 지연에 한정한 재시도/회귀를 추가한 뒤 재실행해 전환을 완료했다. 무관한 RT 충돌은 재시도하지 않는다.
- NAT HTTPS 검증은 처음에 원래 앱 출발지 포트 보존을 가정해 로그를 놓쳤다. 실제 SNAT 포트 변환을 확인해 요청 시각과 고유 peer, 실제 NAT tuple로 대조했고 양방향 FLOW와 중앙 원문 일치를 확인했다. 검사 실패와 검증기의 잘못된 가정을 구분한다.

## 2026-11-07 Operation 완료와 최종 Review

실제 최종 대조 시각은 2026-11-07 02:22 KST다. 중앙/Workload 모두 enabled이며 두 기존 VPC/TGW 기반과 DNS/ALB를 유지했다. 아래 결과는 실제 API와 요청/서비스 로그를 대조한 결과다. 앱은 재시작하지 않았으며 임시 probe의 HTTP200을 앱 CRUD 성공으로 기록하지 않는다.

재개 완료 당시의 허용 계약은 인터넷에서 ALB public 대역으로 TCP80, NAT 사설 /32에서 인터넷으로 TCP443, board와 GCP frontend `10.32.1.20/32`의 TCP3000 양방향이었다. 응답은 연결 상태를 공유하며 나머지는 기본 차단했다. 이후 기본 차단 제거는 아래 후속 기록을 따른다. HTTP marker는 검사 경유 증명용 비차단 서명이며 HTTPS 본문 복호화는 구성하지 않았다.

### 실제 배포 경로

| 위치 | 실제 값과 전달 경로 |
| --- | --- |
| Firewall | `arn:aws:network-firewall:ap-northeast-2:482840608926:firewall/delta-lab-central`, Primary2 READY/IN_SYNC, delete/subnet/policy 보호 true |
| board 인터넷 AZ1 | public RT `rtb-072114d3cba91eab8` default -> `vpce-04985dd61a622aec2`; 전용 subnet `10.10.30.0/24` |
| board 인터넷 AZ2 | public RT `rtb-0583cb8f8cf47e440` default -> `vpce-0cbafda7d5340b9bc`; 전용 subnet `10.10.31.0/24` |
| IGW edge | `rtb-04483fb29ed277771`를 기존 IGW에 연결. `10.10.10.0/24`, `10.10.11.0/24`를 같은 AZ의 Secondary로 전달 |
| NAT/Endpoint 반환 | 앱 private default는 기존 NAT. 두 Firewall subnet RT default는 기존 IGW. NAT EIP `13.124.213.68` 유지 |
| GCP 송신 | 기존 board Attachment만 전용 `tgw-rtb-01c4ed16b8486a0ae`에 연결. GCP4개 정적 목적지를 중앙 Inspection Attachment로 전달 |
| GCP 반환 | VPN은 기존 `tgw-rtb-0bb68a37f3523493b`에 유지. `10.10.0.0/16` 정적 반환만 중앙 Inspection을 경유. GCP 정적 prefix 없음 |
| 검사 후 GCP 전달 | 기존 Inspected RT에서 GCP4개 BGP prefix와 두 VPN/네 터널 ECMP 유지. GCP4개 peer UP, 각 AWS prefix1개 수신, 광고4개 불변 |
| VPC 내부 | 기존 ALB-앱/앱-RDS/EIC 경로와 다른 기존 VPC RT 및 다른 SG 권한 불변 |

### 실제 실행과 검증

| 날짜 | 실행한 명령/API | 검증 결과와 증거 |
| --- | --- | --- |
| 2026-11-07 | 중앙/Workload 준비 저장 Plan 검토와 `operate.py apply` | 중앙17 add/2 change/0 destroy, Workload16 add/0 change/0 destroy. Primary2/Secondary2 준비 후 전환 |
| 2026-11-07 | `switch_internet_routes.py`를 사용하는 검토된 인터넷 전환, edge Association Import, 새 Plan | 두 public default/IGW edge 전환. 두 ALB public IP HTTP200과 NAT HTTPS200, Workload No changes. `public-after-internet-switch.json`, `internet-confirmed.summary.json` |
| 2026-11-07 | `operate.py apply central scoped-prepare` | board 전용 RT/정적4경로 생성5개와 비차단 public HTTP marker SID2000002 갱신1개, 삭제0. 기존 연결/다음 홉 no-op |
| 2026-11-07 | 검토된 `replace_inspection_routes.py ... --execute` | board Association1개와 반환 다음 홉1개 실제 전환. AWS->GCP 및 GCP->AWS TCP3000 HTTP200. 인터넷 두 IP HTTP200/NAT HTTPS200/RDS TLS1.3 회귀 확인. `gcp-switch.log`, `probe-results.json`, `public-after-gcp-switch.json` |
| 2026-11-07 | 허용/차단 대조와 `verify_logs.py` | 8081은 전환 전 HTTP200, 전환 후 timeout이며 동일 실제 tuple에 SID9000000 blocked/verdict drop. 사설 양방향/인터넷 두 AZ/NAT HTTPS의 양방향 FLOW 확인. 선택 원본21건의 account/group/stream/timestamp/message와 중앙21건 일치. `service-log-verification.json`의 모든 항목 true |
| 2026-11-07 | `operate.py plan central gcp-refresh --refresh-only`, 해당 저장 Plan Apply, board Association Import | AWS 자원 동작0. 실제 변경된 반환 Route와 없어진 이전 Association만 state 동기화 후 기존 관리 주소에 새 RT/Attachment 조합을 Import. state rm/Attachment 삭제/전체 state 덮어쓰기 없음 |
| 2026-11-07 | 새 `gcp-confirmed` Plan/Apply | 삭제 보호 false->true만 적용, 0 add/1 change/0 destroy |
| 2026-11-07 | 임시 probe stop, SG revoke 및 EIC/IAP 문맥 종료 | 두 원격 probe 종료 확인, 임시8081 /32 규칙0개, 다른 SG 변경0개. GCP 기존 OS Login 키 유지, 임시 토큰/개인키 디렉터리 제거. 두 Compose inactive/disabled 유지 |
| 2026-11-07 | `operate.py plan central central-final`, `operate.py plan workload workload-final` | 두 실제 최종 Plan No changes/종료0/변경 주소0개. 새 Plan 작성 후 Terraform 구성 digest 불변 확인 |
| 2026-11-07 | `final_snapshot.py`: 실제 Firewall/RT/Association/VPN/GCP Router/EC2/NAT/ALB/RDS 조회, 최종 state pull/사전 ID 비교, 공개 HTTP | 최종25개 대조 모두 true. 네 Endpoint READY/정책 IN_SYNC/보호 true, VPN4개 UP와 광고 보존. 중앙67->89 managed와 Workload85->102 managed, 기존 ID 변화는 의도한 board RT Association 조합1개뿐. VPC/Attachment/호스트/ALB/NAT/EIP/RDS ID 불변. 공개 `/`, `/api/health`는 기존502. `final-verification.json` |
| 2026-11-07 | 두 root `scripts/validate.sh` 최종 전체 재실행 | Terraform mock75+44+12=131개와 Python22+13+3=38개 통과, 두 명령 종료0. fmt/init/validate 포함. 실제 패킷/Plan 검증과 별개. `final-local-validation.json` |
| 2026-11-07 | `git diff --check`, Python compile/README 속성/로컬 링크/추가 문자/ignore/권한/임시 디렉터리 대조 | diff/compile 통과. 중앙 입력30/출력22, Workload 입력23/출력12, sample-app 입력14/출력2를 README와 대조. 누락 링크/추가 금지 문자/민감 산출물0개, 실제 입력과 증거5경로 Git 제외, 증거 파일0600/디렉터리0700. 임시 IAP/EIC 키 디렉터리0개. `final-static-review.json` |

실제 사설 flow_id는 AWS 시작 `1103988117274831`, GCP 시작 `881030180642994`이며 endpoint는 `primary`다. 공개 두 AZ flow_id는 `302172105129067`, `499150899428962`이고 event.endpoint는 각각 Secondary Association UUID `7765773d-0b0b-376c-86aa-e3550f84073b`, `2f2e8eba-1239-3533-b64a-45825c265041`다. NAT HTTPS는 실제 `10.10.10.249:16087 -> 54.254.92.188:443`, flow_id `936911599126268`을 확인했다. 원래 앱 소켓 `10.10.1.211:32954`와 동일 포트라고 가정하지 않는다.

Review 완료: 대상 한정 경로, 기본 차단과 최소 허용, BGP 광고/ECMP, 기존 ID/관리/DB 경로, 실제 로그 전달 및 복귀 절차를 대조했다. 부분 전환 실패는 원상 복귀와 실제 통신을 확인하고 원인별 회귀를 추가한 뒤 재시도했다. 물리 터널/AZ 장애 주입, 부하/무손실 전환, TLS 복호화, 앱 재시작/CRUD와 다른 VPC의 통신은 검증하지 않았다. Ruff는 설치돼 있지 않아 실행하지 않았으며 Python 회귀/compile과 Terraform 검증 결과만 기록한다. Git 게시와 원격 CI 상태는 Git 이력과 실행 결과를 따른다.

운영에서는 [중앙 Runbook](../../terraform/network-security/RUNBOOK.md#delta-board-vpc-인터넷과-gcp-검사-재개)과 [인터넷 Runbook](../../terraform/workload/RUNBOOK.md#공유-anf로-인터넷-검사하기)으로 현재 경로/Endpoint와 BGP 광고, 로그 수신을 확인한다. 경로 bypass와 Endpoint 비용 중단을 구분하고 Secondary를 참조하는 인터넷 경로를 복귀하기 전에 Firewall을 제거하지 않는다.

## 2026-11-07 후속: 공통 기본 차단 규칙 제거

### Ideation과 Inception

- 문제와 사용자: AWS 게시판 운영자가 인터넷에서 기존 ALB subnet `10.10.10.0/24`, `10.10.11.0/24`의 HTTPS443으로 접속해야 한다. 인용된 요청은 443 허용이며, 사용자의 직접 후속 지시는 `ANF에서 거부 규칙 빼`다.
- 변경 지시: 기존 U1의 기본 차단 요구를 변경해 SID `9000000`을 제거한다. 이 범위는 TCP443 외의 미등록 통신에도 적용된다.
- 승인: 최초 Apply 시도는 자동 승인 검토에서 전체 범위의 승인이 불명확하다는 이유로 실행 전에 거부됐다. TCP443 외의 모든 미등록 통신에도 영향을 준다는 설명과 검토한 한 자원 Plan을 제시한 뒤 사용자가 `SID 9000000 제거와 전체 기본 차단 해제를 승인`을 선택했다. 이 명시적 승인으로 본 후속 단위의 요구사항, 설계와 실제 적용 범위를 확정한다.
- Scope와 Functional Requirements: 중앙 Rule Group의 SID `9000000`만 제거하고 Terraform 입력으로 해당 선택을 보존한다. 기존 추가 서명과 flow 추적은 유지한다.
- Non-goals와 Non-Functional Requirements: Firewall/Endpoint/라우팅/SG/NACL/ALB/인증서/DNS/GCP/앱 실행 상태를 바꾸지 않는다. 기존 Rule Group ARN과 ID를 보존한다.
- Architecture와 Unit of Work: 기존 STRICT_ORDER 경로를 사용한다. U1 후속 단위로 `default_deny_enabled` 입력을 추가하며 기본값은 true, 실제 board 입력은 false로 설정한다. Stateful 기본 drop은 추가하지 않고 stream exception `DROP`는 유지한다.
- 성공 기준과 Acceptance Criteria: 실제 적용 Plan의 변경은 Rule Group 한 주소의 in-place 갱신뿐이고, 정확히 한 줄/SID만 제거한다. 적용 후 정책 IN_SYNC/Endpoint READY, 외부 HTTPS와 최종 실제 Plan을 확인한다. 실행하지 않은 검증은 성공으로 기록하지 않는다.

### Construction과 Operation

- Design/Implementation Plan: 기존 규칙/정책/Endpoint와 실행 입력을 백업하고, 조건부 기본 차단 생성 및 README 표/회귀 검증을 추가한다. mock 검증과 실제 저장 Plan을 검토한 뒤 사용자 제거 지시 범위의 한 자원만 적용하고 다시 조회한다.
- 입력: ignored `terraform/network-security/board-inspection.auto.tfvars.json`의 `default_deny_enabled=false`. 기본값을 바꾸지 않으므로 다른 배포는 기존 차단 동작을 유지한다.
- 복귀: 같은 입력을 true로 바꿔 새 저장 Plan에서 기존 SID `9000000` 한 줄의 복원만 확인한 뒤 적용한다. 기존 연결의 즉시 재평가를 가정하지 않고 새 TCP 연결로 검증한다.
- Observability: 기존 NFW FLOW/ALERT 및 중앙 구독을 보존한다. 제거 뒤에는 SID `9000000`의 신규 기본 차단 ALERT가 생성되지 않으며, 별도 실제 로그 수신을 검증하지 않은 경우 수신 성공으로 기록하지 않는다.
- 현재 상태: Construction/Test/Review와 실제 Operation 완료. 실제 Apply는 0 add/1 change/0 destroy이며 외부 HTTPS200과 최종 실제 Plan No changes/종료0을 확인했다. Git 게시와 원격 CI 상태는 Git 이력과 실행 결과를 따른다.

검증 증거는 ignored `.validation/anf-default-deny-removal-20261107/`에 저장한다.

### 실제 검증 기록

최종 확인 시각: 2026-11-07 15:21 KST.

| 날짜 | 실행한 명령/API | 결과 |
| --- | --- | --- |
| 2026-11-07 | `aws sso login --profile sbh-delta-security --no-browser`, 같은 Workload 프로필 로그인, `sts get-caller-identity` | 만료된 기존 SSO 세션을 갱신해 중앙 `482840608926`/Workload `616723161368` 확인. 새 계정/권한/자격 증명 생성 없음 |
| 2026-11-07 | `bash terraform/network-security/scripts/validate.sh` | 최종 Python22개와 Terraform mock76개 통과, 종료0. fmt/init/validate 포함. 초기 Provider 샌드박스 실행 실패와 새 시험의 AZ mock 누락을 구분해 실행 환경/fixture를 보완한 뒤 전체 재실행 |
| 2026-11-07 | `AWS_PROFILE=sbh-delta-security terraform -chdir=terraform/network-security plan -input=false -no-color -lock-timeout=30s -detailed-exitcode -out=<ignored>/remove-default-deny.tfplan`, `terraform show -json` | 종료2, 변경은 `aws_networkfirewall_rule_group.central` 한 건의 update. SID9000000 한 줄 제거/7->6개 규칙, Rule Group의 다른 필드와 ID/ARN/용량/태그 불변 확인. Plan/구성 digest 저장 |
| 2026-11-07 | 사용자 전체 범위 명시 승인 후 `AWS_PROFILE=sbh-delta-security terraform -chdir=terraform/network-security apply -input=false -no-color <ignored>/remove-default-deny.tfplan` | 종료0, 0 add/1 change/0 destroy, Rule Group 갱신2초. 적용 직전 Plan/구성 digest와 최신 AWS 규칙을 다시 대조 |
| 2026-11-07 | `network-firewall describe-rule-group`, `describe-firewall-policy`, `describe-firewall`, `list-vpc-endpoint-associations`, Workload의 `describe-vpc-endpoint-association` 2회 | SID9000000만 부재/나머지6개와 Rule Group ID 보존, Policy/Firewall와 공유 Association 불변. Primary2 READY/최신 Rule Group IN_SYNC와 Secondary2 READY 확인. 중앙 계정의 소비자 ARN 직접 조회는 리소스 정책으로 거부돼 소유자 프로필로 재조회했으며 권한 정책을 변경하지 않음 |
| 2026-11-07 15:19 KST | `curl --resolve <AWS host>:443:<ALB IP> https://<AWS host>/` 및 `/api/health` | AWS host `awsdemo.delta.mungji.com`/`aws.adajosou.site`, ALB `52.78.18.162`/`3.37.83.103` 조합의 총8개 요청이 HTTP200, curl 종료0/ssl_verify_result0. API는 environment=aws 반환. 적용 전 같은 두 IP의443은 timeout. 인증서 검증 생략 없음 |
| 2026-11-07 | `dig +short demo.delta.mungji.com` | `gcpdemo.delta.mungji.com -> gcp.adajosou.site -> 136.69.76.82` 유지. DNS/ALB/인증서를 변경하지 않음 |
| 2026-11-07 | 중앙 root의 새 실제 `terraform plan -detailed-exitcode -out=<ignored>/final.tfplan`, `terraform state pull` 전후 대조 | No changes/종료0, 변경 주소0개. managed89개 ID와 출력22개 불변. `final-summary.json` |
| 2026-11-07 | `git diff --check`, README 속성 및 추가 금지 문자/ignore/권한 검사 | diff 검사 통과, 입력31개와 outputs.tf의21개 출력 표 누락0. 추가 금지 문자0, 실제 board 입력과 검증 산출물은 Git 제외 |

Review: 실제 적용은 기본 차단 한 줄 제거에 한정되며 현재 정상적인 미등록 트래픽은 이 규칙으로 차단하지 않는다. 검사 경로와 기본 stream exception `DROP`는 유지한다. 이 작업에서 앱을 재시작하지 않았지만 외부 HTTPS의 기존 AWS 앱은 정상 응답했다. 02:22 KST의 앱 중지/502 기록을 현재 상태로 재사용하지 않는다. 원격 Compose/systemd 조회, 사설 양방향 패킷, 새 NFW 로그의 중앙 수신과 기존 TCP 연결 재평가, DNS 전환은 이번 후속에서 검증하지 않았다.

기본 동작의 근거는 [AWS STRICT_ORDER 평가와 기본 Action](https://docs.aws.amazon.com/network-firewall/latest/developerguide/suricata-rule-evaluation-order.html)과 실제 Policy 조회다. 상세 복귀는 [중앙 기본 차단 Runbook](../../terraform/network-security/RUNBOOK.md#기본-차단-규칙-제거와-복귀)을 따른다.
