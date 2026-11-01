# Delta network gateway

## 현재 상태

- 기준일: 2026-11-01 (Asia/Seoul)
- Ideation: 사용자 승인 완료.
- Inception 및 U1~U5 Design / Implementation Plan: 사용자의 `PLEASE IMPLEMENT THIS PLAN` 지시로 승인 완료.
- Construction: U1~U5 구현 및 정적 Review 완료. fmt/init/validate와 mock Plan 32개 통과.
- AWS Rule Group DryRun: SCP 수정 후 재시도한 두 규칙 집합 모두 통과. 소비 용량은 기본 차단 1, TCP/UDP/ICMP 및 추가 서명 예제 5. 실제 패킷 검증은 미완료.
- 중앙 실행 입력: `terraform.tfvars` 작성 완료. VPC `10.250.0.0/16`, Subnet 4개는 `/24`, AZ IDs는 `apne2-az1`, `apne2-az2`. 실제 입력으로 격리된 중앙 기반 mock Plan 1개 통과.
- Backend 설정: 사용자 작성 `backend.hcl` 존재. `allowed_account_ids=["437358133988"]`가 지정 프로필의 중앙 계정 `482840608926`과 달라 현재 프로필로 backend 인증을 허용하지 않는다. 파일은 변경하지 않았다.
- 실계정 Plan 및 S3 backend init: 미실행.
- Git: 저장소 기본 설정, Terraform 구현, 검증 도구, 운영 문서의 단위로 커밋한다. 코드와 검증 도구의 로컬 커밋 3개는 완료했고 운영 문서는 이번 docs 커밋에 포함한다.
- 푸시: 자동 승인 검토가 원격 대상의 명시적 확인을 요구해 실행 전에 차단했다. 대상은 `2026-sbh-delta/delta-network-gateway`의 `main`이다.
- Apply / Destroy / 배포 / 실제 통신 검증: 미실행.
- 실행 프로필: `AWS_PROFILE=sbh-delta-security`. 이번 DryRun에서 STS로 중앙 계정 `482840608926`을 재확인했다. 코드에서는 계정 ID를 필수 입력으로 받아 검증한다.

## 문서

- [승인된 요구사항과 구현 단위](ai-dlc/central-network.md)
- [실행 및 연결 안내](../terraform/network-security/README.md)

## 다음 작업

Backend의 허용 계정을 실행 프로필과 일치시키고 실제 S3 bucket과 접근 권한을 확인한 뒤 backend init 및 실계정 Plan을 검토한다. 중앙 실행 CIDR/AZ 입력은 작성됐고, 조회한 중앙 계정 서울 리전의 기존 VPC와 CIDR이 겹치지 않는다. 다른 계정/GCP/온프레미스의 CIDR 중복 여부는 연결 등록 시 확인한다. 소비자/VPN 계약은 준비되면 추가한다. Apply와 Destroy에는 별도 실행 지시가 필요하다.

Git 푸시는 원격 대상 확인 후 재시도한다. 실제 `terraform.tfvars`, `backend.hcl`, state/Plan, 검증 로그와 Provider 캐시는 커밋 대상에서 제외했다.
