# Delta network gateway

## 현재 상태

- 기준일: 2026-11-01 (Asia/Seoul)
- Ideation: 사용자 승인 완료.
- Inception 및 U1~U5 Design / Implementation Plan: 사용자의 `PLEASE IMPLEMENT THIS PLAN` 지시로 승인 완료.
- Construction: U1~U5 구현 및 정적 Review 완료. fmt/init/validate와 mock Plan 32개 통과.
- AWS Rule Group DryRun: SCP 수정 후 재시도한 두 규칙 집합 모두 통과. 소비 용량은 기본 차단 1, TCP/UDP/ICMP 및 추가 서명 예제 5. 실제 패킷 검증은 미완료.
- 중앙 실행 입력: `terraform.tfvars` 작성 완료. VPC `10.250.0.0/16`, Subnet 4개는 `/24`, AZ IDs는 `apne2-az1`, `apne2-az2`. 실제 입력으로 격리된 중앙 기반 mock Plan 1개 통과.
- Backend: S3 초기화 메타데이터 확인. Bucket `sbh-delta-central-tf`, encrypt/use_lockfile=true, 허용 계정 `482840608926`. 현재 `backend.hcl`과 초기화된 bucket/key/region이 일치한다.
- 배포: 중앙 단독 기반의 사용자 Apply 완료. 마지막 재시도는 2 added, 0 changed, 0 destroyed. Firewall/두 AZ Primary Endpoint READY, Firewall IN_SYNC, TGW와 Inspection Attachment available 및 appliance mode enable을 읽기 전용으로 확인했다.
- RAM: 두 Share ACTIVE, 두 Resource Association ASSOCIATED. 조직 외 공유는 계속 비활성화다. 이전 Organizations 온보딩 관련 오류는 해소됐다.
- 로그: FLOW/ALERT CloudWatch Logs 목적지 설정 확인. 실제 로그 유입 및 통신 검증은 미완료다.
- 실계정 Plan: 사용자 Apply 로그에서 2 to add, 0 to change, 0 to destroy 확인. 에이전트가 직접 init/Plan/Apply를 실행하지 않았다.
- Git: 초기 분할 커밋 4개와 후속 문서 커밋 `f7b6244`, `9252a76` 완료. backend/RAM 운영 안내, 배포 검증 및 AWS/GCP 연결 제안을 기록했다.
- 푸시: 2026-11-01 15:53~15:54 KST `git push origin main` 성공 후 원격 `main`이 검증 체크포인트 `9252a76`과 일치함을 확인했다. 이 문서 갱신은 해당 푸시 완료 결과를 기록한다.
- Destroy: 이 작업에서 미실행. 로그 전달, 계정 간 연결 및 실제 통신은 미검증.
- 실행 프로필: `AWS_PROFILE=sbh-delta-security`. 이번 DryRun에서 STS로 중앙 계정 `482840608926`을 재확인했다. 코드에서는 계정 ID를 필수 입력으로 받아 검증한다.

## 문서

- [승인된 요구사항과 구현 단위](ai-dlc/central-network.md)
- [실행 및 연결 안내](../terraform/network-security/README.md)

## 다음 작업

업무 계정 공유 대상과 VPC Attachment 계약, 필요한 VPN 및 실제 허용 정책을 입력해 후속 연결을 구성한다. 현재 입력은 빈 상태여서 업무/VPN 연결 및 서비스 트래픽 허용은 구성하지 않았다. 연결 후 왕복 경로, 허용/차단과 실제 로그 유입을 검증한다.

첫 실트래픽 검증 후보는 AWS 업무 VPC의 EC2 클라이언트와 GCP VM 서버를 HA VPN으로 연결하는 시나리오다. AWS→중앙 ANF→VPN→GCP 사설 IP 및 응답 경로를 검사한다. 테스트에 사용할 업무 계정, GCP 프로젝트/리전, 기존 리소스와 주소 대역은 미확정이며 추가 리소스는 아직 생성하지 않았다.

중앙 실행 CIDR/AZ 입력은 작성됐고, 입력 작성 시 조회한 중앙 계정 서울 리전의 기존 VPC와 CIDR이 겹치지 않았다. 다른 계정/GCP/온프레미스의 CIDR 중복 여부는 연결 등록 시 확인한다. 소비자/VPN 계약은 준비되면 추가한다.

RAM 오류 진단은 두 Association의 실제 성공 확인으로 종료했다. 관리 계정 프로필 로그인은 더 이상 요청하지 않는다. 에이전트의 Organizations 변경, Apply와 Destroy는 실행하지 않았다. 실제 `terraform.tfvars`, `backend.hcl`, state/Plan, 검증 로그와 Provider 캐시는 커밋 대상에서 제외했다.
