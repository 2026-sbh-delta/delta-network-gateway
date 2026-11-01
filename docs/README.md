# Delta network gateway

## 현재 상태

- 기준일: 2026-11-01 (Asia/Seoul)
- Ideation: 사용자 승인 완료.
- Inception 및 U1~U5 Design / Implementation Plan: 사용자의 `PLEASE IMPLEMENT THIS PLAN` 지시로 승인 완료.
- Construction: 중앙 U1~U5와 후속 Client 역할 구현/Review 완료. 중앙 mock Plan은 최신 회귀 실행에서 33개 통과했다.
- AWS Rule Group DryRun: SCP 수정 후 재시도한 두 규칙 집합 모두 통과. 소비 용량은 기본 차단 1, TCP/UDP/ICMP 및 추가 서명 예제 5. 실제 패킷 검증은 미완료.
- 중앙 실행 입력: `terraform.tfvars` 작성 완료. VPC `10.250.0.0/16`, Subnet 4개는 `/24`, AZ IDs는 `apne2-az1`, `apne2-az2`. 실제 입력으로 격리된 중앙 기반 mock Plan 1개 통과.
- Backend: S3 초기화 메타데이터 확인. Bucket `sbh-delta-central-tf`, encrypt/use_lockfile=true, 허용 계정 `482840608926`. 현재 `backend.hcl`과 초기화된 bucket/key/region이 일치한다.
- 배포: 중앙 기반은 사용자 Apply로 완료했다. 후속 승인으로 에이전트가 사설 Workload 기반 20개와 Attachment 1개, 중앙 RAM principal 2개 및 수락/검사/반환 리소스 9개를 생성하고 HOME_NET 관련 2개를 갱신했다. 삭제는 없다. Attachment available, Firewall/두 Primary Endpoint READY 및 IN_SYNC, 검사/반환 Route active를 실제 확인했다.
- RAM: 두 Share와 Resource Association에 이어 업무 계정 `616723161368`의 두 principal 연결을 추가했다. 업무 계정에서 중앙 TGW available을 조회했다. 조직 외 공유는 비활성화다.
- 로그: FLOW/ALERT CloudWatch Logs 목적지 설정 확인. 실제 로그 유입 및 통신 검증은 미완료다.
- 실계정 Plan: Workload 20 add 및 Attachment 1 add, 중앙 공유 2 add와 등록 9 add/2 change를 검토 후 적용했다. 배포 후 두 root의 최종 Plan은 모두 No changes, 상세 종료 코드 0이다.
- Git/푸시: 사용자의 후속 지시로 중앙 Client 계약 `6a20961`, Workload 구현 `ad1b13a`, 검증 도구 `6921a02`를 분할 커밋했다. 배포/진행 기록은 이번 문서 커밋에 포함한다. 현재 푸시는 미실행이며 원격 main의 기존 `d6b9545`를 조회 확인했다. 실제 tfvars/backend/state/Plan은 제외한다.
- Destroy: 미실행. GCP/VPN과 실제 ANF 허용/차단 통신 및 로그 유입은 미검증.
- 실행 프로필: `AWS_PROFILE=sbh-delta-security`. 이번 DryRun에서 STS로 중앙 계정 `482840608926`을 재확인했다. 코드에서는 계정 ID를 필수 입력으로 받아 검증한다.
- AWS Workload: 사용자 `이렇게 만들고, public 노출없이 해` 지시로 Inception과 U1~U3 승인 및 구현/Review 완료. Workload mock Plan 26개 및 state bootstrap 보호 테스트 3개 통과. 별도 S3 state와 VPC `10.10.0.0/16`, `/24` Subnet 4개, 사설 EC2 `10.10.1.143` 및 EIC Endpoint 생성 완료. 공인 IP/IPv6, IGW/NAT/인터넷 Route 부재와 EIC 사설 SSH 성공을 실제 확인했다.

## 문서

- [승인된 요구사항과 구현 단위](ai-dlc/central-network.md)
- [AWS Workload 클라이언트 구성안과 승인 상태](ai-dlc/aws-workload-client.md)
- [실행 및 연결 안내](../terraform/network-security/README.md)
- [Workload 실행 및 전체 입력/출력](../terraform/workload-client/README.md)

## 다음 작업

AWS Workload와 중앙 수락/경로 연결 및 최종 검증은 완료했다. 중앙에는 업무 계정과 `aws_client` 계약을 등록했고 VPN/실제 허용 정책은 비어 있다. Workload의 원격 CIDR과 시험용 SG 송신 규칙도 아직 비어 있다.

다음 멀티클라우드 단계는 GCP 프로젝트/리전과 비중복 서버 CIDR을 확정하고 HA VPN/서버를 준비하는 것이다. AWS 사설 클라이언트는 배포됐으며 AWS→중앙 ANF→VPN→GCP 사설 IP 및 응답 경로는 GCP/VPN과 양방향 Route/허용 정책을 등록한 후 검증한다. 현재 SSH 성공은 VPC local 관리 경로의 검증이다.

중앙 실행 CIDR/AZ 입력은 작성됐고, 입력 작성 시 조회한 중앙 계정 서울 리전의 기존 VPC와 CIDR이 겹치지 않았다. 다른 계정/GCP/온프레미스의 CIDR 중복 여부는 연결 등록 시 확인한다. 소비자/VPN 계약은 준비되면 추가한다.

RAM 오류 진단은 두 Association의 실제 성공 확인으로 종료했다. 관리 계정 프로필 로그인은 더 이상 요청하지 않는다. Organizations 변경과 Destroy는 실행하지 않았다. 실제 `terraform.tfvars`, `backend.hcl`, state/Plan, 검증 로그와 Provider 캐시는 커밋 대상에서 제외했다.
