# AWS Workload 클라이언트 AI-DLC

## 단계와 승인 상태

- 기준일: 2026-11-01 (Asia/Seoul).
- Ideation 승인: 사용자의 `그럼 AWS Workload 먼저 만들어보자` 지시를 AWS 클라이언트 환경 준비 착수로 기록한다.
- Inception 및 U1~U3 Design / Implementation Plan: 2026-11-01 사용자 `이렇게 만들고, public 노출없이 해` 지시로 승인 완료.
- 추가 제약: Workload 공인 IPv4/IPv6, Public Subnet, IGW/NAT 및 인터넷 기본 경로를 생성하지 않는다. SSH는 EIC Endpoint SG에서만 받는다.
- U1~U3 구현/Test/Review 완료. fmt/격리 init/validate, Workload mock Plan **26개**, 중앙 회귀 mock Plan **33개**, state bootstrap 보호 테스트 **3개** 통과.
- 배포 완료: 별도 S3 state bucket, Workload 기반 20개와 Attachment 1개, 중앙 RAM principal 2개와 연결 리소스 9개 생성 및 HOME_NET 관련 2개 갱신. 삭제 없음.
- 실제 검증: 공인 주소/IGW/NAT/인터넷 경로 부재, EIC 사설 SSH 성공, Attachment available와 명시적 검사/반환 경로, Firewall/두 Primary Endpoint READY 및 IN_SYNC 확인. 두 root의 최종 Plan 모두 **No changes, 종료 코드 0**.
- GCP/VPN 및 서비스의 ANF 통과/허용/차단/로그 유입 검증은 미실행. 코드/검증/배포 문서를 아래 4개 커밋으로 나눠 origin/main에 푸시했고 원격 체크포인트 `9a4ecb0` 일치를 확인했다. 이 문서 갱신은 해당 푸시 완료 결과를 기록한다.

## Ideation

- 문제: 중앙 ANF 경유를 실트래픽으로 확인할 AWS 측 클라이언트가 필요하다.
- 사용자: 델타 중앙 네트워크 운영자와 멀티클라우드 연결 시험 담당자.
- 성공 기준: 업무 계정의 사설 EC2에 관리 접속하고, 중앙 TGW로 연결할 VPC/Attachment 계약을 준비한다. GCP 준비 후 해당 EC2에서 허용/차단 요청을 실행할 수 있어야 한다.
- 범위: 별도 Workload Terraform root/state, VPC와 Subnet, 사설 EC2, EC2 Instance Connect Endpoint, 선택적 TGW Attachment와 중앙 연결 계약.
- Non-goals: GCP/VPN 생성, ALB/서비스 배포, Workload 인터넷 출구, Workload의 보조 ANF Endpoint, 실제 멀티클라우드 통신 성공 판정, Destroy.

## Inception

### 확인한 계정과 제안값

| 항목 | 값 | 상태 |
| --- | --- | --- |
| 업무 프로필 | `sbh-delta-workload` | 로컬 프로필 및 STS 조회 확인 |
| 업무 계정 | `616723161368` | STS 조회 확인 |
| 중앙 프로필/계정 | `sbh-delta-security` / `482840608926` | 기존 중앙 구성과 연결 계약 기준 |
| Region | `ap-northeast-2` | 업무 계정에서 AZ 조회 확인 |
| VPC CIDR | `10.10.0.0/16` | 배포 완료. 조회한 업무 VPC `172.31.0.0/16` 및 중앙 Inspection `10.250.0.0/16`과 겹치지 않음 |
| 클라이언트 | AL2023 standard ARM64, `t4g.nano` 1대 | AMI `ami-029fa37c14d9a8917` 고정. running/status checks ok 및 SSH 확인 |
| EBS | 암호화한 gp3 8 GiB | 실제 Volume 조회 확인 |
| 관리 접속 | EC2 Instance Connect Endpoint 1개, SSH | create-complete 및 실제 사설 SSH 종료 코드 0 |
| state | `sbh-delta-workload-tf-616723161368` 및 별도 key | 생성/보안 설정 조회/S3 backend 초기화 완료 |

VPC `/16`, Subnet `/24`는 앞서 사용자가 지정한 주소 크기를 따른다. GCP, 다른 계정 및 온프레미스 전체 주소와의 충돌 검증은 아직 하지 않았다. GCP 연결 시 반드시 추가 확인한다.

| 물리 AZ ID | 업무 계정 AZ 이름 | TGW 전용 Subnet | Client Subnet |
| --- | --- | --- | --- |
| `apne2-az1` | `ap-northeast-2a` | `10.10.0.0/24` | `10.10.1.0/24` |
| `apne2-az2` | `ap-northeast-2b` | `10.10.2.0/24` | `10.10.3.0/24` |

Client와 관리 Endpoint는 우선 `apne2-az1`에 배치한다. 두 AZ의 네트워크를 준비하되 EC2는 1대로 시작하며 클라이언트 자체의 HA를 제공하지 않는다.

### 기능 요구사항

1. `terraform/workload-client`를 중앙 root와 분리한다. 계정 ID를 필수 입력으로 검증하고 Provider의 `allowed_account_ids`를 사용한다.
2. VPC 1개, AZ별 TGW/Client Subnet 각 1개, 명시적으로 연결한 Route Table 4개를 생성한다. 공인 IP, IGW, NAT 및 인터넷 기본 경로는 구성하지 않는다.
3. Client는 AL2023 standard ARM64 이미지, IMDSv2, 암호화한 root EBS, T4g Standard CPU credit 설정을 사용한다. AMI 조회 결과의 실제 ID를 입력으로 고정해 임의 이미지 변경을 막는다.
4. EC2 Instance Connect Endpoint는 client IP preservation을 끈 IPv4로 구성한다. Endpoint SG는 Client SG의 TCP 22로만 송신하고 Client SG는 Endpoint SG의 TCP 22만 수신한다. 접속 권한은 운영자의 IAM 자격 증명을 사용하며 장기 개인 키를 Terraform에서 생성하지 않는다.
5. 첫 Workload 배포는 TGW Attachment 없이도 실행할 수 있다. Attachment 활성화 입력과 원격 Route 입력을 분리하며, Attachment가 꺼진 상태에서 원격 Route를 요청하면 오류를 반환한다.
6. 공유 확인 후 업무 계정에서 TGW 전용 Subnet 두 개를 사용해 Attachment를 생성한다. 기본 Association/Propagation은 끄고 중앙이 수락 및 Spoke RT Association을 관리한다.
7. GCP CIDR은 미확정 입력으로 남긴다. 확정된 CIDR만 Client Subnet RT의 TGW Route와 시험용 SG 송신 규칙에 등록한다. 첫 허용/차단 대조군 후보는 TCP 8080/8081이며 SG는 양쪽 포트를 허용해 ANF 판정을 확인할 수 있게 한다.
8. 계정/Region/AZ, VPC, Subnet/Route Table, EC2 사설 IP, 관리 Endpoint, 선택적 Attachment ID와 중앙 전달 계약을 출력한다.

### 비기능 요구사항

- Terraform `1.16.4`, AWS Provider `6.71.0`과 lock 파일을 기존 중앙 기준에 맞춘다.
- S3 state는 업무 계정 소유 bucket을 사용한다. 제안 이름은 `sbh-delta-workload-tf-616723161368`, key는 `delta/lab/workload-client/terraform.tfstate`다. 이름 사용 가능 여부와 소유 계정 확인 후 버전 관리, 암호화, Public Access Block 및 `use_lockfile=true`를 적용한다. Bucket 준비는 root 밖의 명시적 bootstrap 절차로 기록한다.
- 중앙 state에 Workload 리소스를 섞지 않는다. 중앙 bucket의 cross-account 정책은 이번 제안의 기본 경로에 포함하지 않는다. 기존 state를 자동 이전하지 않는다.
- 실제 tfvars/backend/state/Plan과 임시 인증 자료는 Git에서 제외한다. 모든 입력/출력과 복합 입력 속성을 Workload README 표로 설명한다.
- AMI 내 기본 도구를 사용하고 인터넷에서 패키지를 설치하는 user data에 의존하지 않는다. 관리 접속과 GCP 서비스 경로를 별도로 검증한다.

### Architecture와 연결 순서

1. Workload state bucket 준비 후 Workload 기반 VPC/Client/관리 Endpoint를 Plan한다. 검토한 Plan을 배포하면 GCP가 없어도 사설 EC2 관리 접속을 확인할 수 있다.
2. 중앙 `shared_account_ids`에 업무 계정을 등록하고 중앙 Plan/Apply 후, 업무 계정에서 중앙 TGW `tgw-0dafbd07013d574ed`가 보이는지 확인한다. 현재 업무 계정 조회에서는 공유 TGW가 없다.
3. Workload의 선택적 TGW Attachment를 활성화하고 해당 state에서 생성한다. 중앙으로 Attachment ID, 실제 상태와 사용 AZ IDs를 전달한다.
4. 중앙 계약은 안정된 키 `aws_client` 및 `acceptance_mode=accept`를 사용한다. 클라이언트 역할을 정확히 나타내도록 중앙 `spoke_attachments.role`에 `Client` 허용을 추가하고 관련 입력 설명/검증 테스트를 함께 갱신한다. 기존 ALB/Backend 입력은 유지한다.
5. 중앙에서 수락, Spoke Association, Inspection을 향하는 Route와 Inspected의 Workload 반환 Route를 구성한다. 수락 후에도 같은 관리 키/모드를 유지한다.
6. GCP/VPN과 실제 허용 정책은 후속 단계에서 준비한다. GCP CIDR이 확정되면 양방향 Route와 SG/ANF 허용 목록을 등록한 뒤 서비스 트래픽을 시험한다.

서비스 경로는 `Client → 업무 TGW Attachment → Spoke → 중앙 Inspection ANF → Inspected → VPN → GCP`다. 관리 SSH는 같은 VPC의 EC2 Instance Connect Endpoint와 local 경로를 사용하므로, 관리 접속 성공을 중앙 ANF 통과 증거로 취급하지 않는다.

## Construction: 승인할 Unit별 설계와 구현 계획

| Unit | Design | Implementation Plan | Approval | 구현/테스트/Review |
| --- | --- | --- | --- | --- |
| U1. 실행 기반과 VPC | 별도 root/state, 계정 검증, `/16` VPC와 두 AZ의 `/24` Subnet 4개 | 버전/Provider/입력/태그, state bootstrap 안내, VPC/Subnet/RT/Association, 예시 설정과 README 작성 | 2026-11-01 승인 | 구현/정적 Review 완료, mock/실계정 검증 통과, 배포 완료 |
| U2. 사설 클라이언트 | ARM64 EC2 1개와 EIC Endpoint, SG 간 SSH 허용 | AMI 고정, EC2/EBS/metadata/credit 설정, EIC Endpoint와 SG, 출력 및 접속 Runbook 작성 | 2026-11-01 승인 | 구현/Review 완료, public 노출 부재 및 사설 SSH 확인 |
| U3. 중앙 연결 준비 | 조건부 Workload Attachment, 중앙 소유 수락/라우팅, Client 계약 | Attachment와 목적지별 Route, 비밀값 없는 전달 출력, 중앙 Client 역할 검증과 설명/테스트 갱신, 단계별 Plan/연결 안내 작성 | 2026-11-01 승인 | 구현/회귀 검증/Review 완료, 중앙 연결 및 양쪽 최종 Plan No changes |

세 Unit은 이번 Inception 및 구현 계획 승인 대상으로 묶는다. 승인 후 코드와 로컬 검증, 확인한 실제값의 Plan을 준비한다. 실제 배포는 Plan의 변경 목록을 검토하는 단계에서 따로 기록한다.

## Acceptance Criteria와 검증 계획

- fmt, 별도 디렉터리의 `init -backend=false`, validate를 실제 실행한다.
- AWS mock provider의 Plan으로 기본 VPC 1개/Subnet 4개/명시적 RT와 Association 각 4개/EC2 1개/EIC Endpoint 1개, 공인 IP 및 기본 인터넷 Route 부재를 확인한다.
- TGW 비활성 상태, 활성 상태의 전용 Subnet 선택과 기본 Association/Propagation 비활성, 목적지별 Route를 확인한다.
- 잘못된 계정/AZ, 중복 또는 VPC 밖 Subnet, 중앙/원격 CIDR 충돌, 기본 Route 입력, Attachment 없이 Route 등록을 거부한다.
- SSH ingress가 Endpoint SG로 제한되고 GCP 시험 SG 송신 규칙이 입력 목적지만 허용하는지 확인한다. 중앙의 기존 mock 테스트와 새 Client 역할 테스트도 실제 실행한다.
- 실계정 Plan은 예상 계정에서만 실행하며 생성/수정/삭제 목록과 mock 결과를 구분한다. Apply 전에는 EC2 running, EIC Endpoint create-complete 또는 SSH 성공을 확인 완료로 기록하지 않는다.

## Operation 계획

- 배포 후 EC2 running/status checks, EIC Endpoint create-complete, 실제 SSH의 `uname`/사설 IP/기본 도구를 확인한다. TGW는 공유 노출, pendingAcceptance/available, Spoke Association과 경로를 단계별 확인한다.
- EC2/EBS, 활성화 후 TGW Attachment/데이터 처리, S3 state 저장과 요청이 비용 항목이다. EIC Endpoint 자체는 추가 사용료가 없으며 다른 AZ로 접속하면 AZ 간 데이터 전송 요금이 적용될 수 있다.
- 인터넷 출구가 없으므로 외부 패키지 다운로드와 일반 인터넷 접속은 준비되지 않는다. 해당 기능이 필요해지면 별도 출구 설계를 결정한다.
- 해체는 원격 Route/중앙 계약 정리와 accepter의 삭제 보호 및 실제 Attachment 소유권을 먼저 검토한다. Workload Attachment/EC2/EIC Endpoint, Subnet/VPC 순서로 의존 관계를 따르고 state bucket은 기본 보존한다. 이번 작업에서 Destroy는 실행하지 않는다.

## 실제 확인 기록

| 날짜 | 명령 또는 확인 | 실제 결과 |
| --- | --- | --- |
| 2026-11-01 15:55~15:57 KST | `aws configure list-profiles` | `sbh-delta-workload` 프로필 존재 |
| 2026-11-01 15:55~15:57 KST | `aws sts get-caller-identity --profile sbh-delta-workload --region ap-northeast-2` | 업무 계정 `616723161368`, IAM Identity Center AdministratorAccess 세션 확인 |
| 2026-11-01 15:55~15:57 KST | 같은 프로필의 `ec2 describe-vpcs` | 기본 VPC `vpc-099fdce3996ac8be7`, CIDR `172.31.0.0/16` 1개. 제안 `10.10.0.0/16`과 겹치지 않음 |
| 2026-11-01 15:55~15:57 KST | 같은 프로필의 `ec2 describe-availability-zones --all-availability-zones` | `apne2-az1`/`apne2-az2`가 각각 `ap-northeast-2a`/`ap-northeast-2b`, available |
| 2026-11-01 15:55~15:57 KST | 같은 프로필의 `ec2 describe-instance-types --instance-types t4g.nano` | ARM64, 2 vCPU, 512 MiB 확인. 실제 인스턴스 생성/가용 용량 검증은 아님 |
| 2026-11-01 15:55~15:57 KST | 같은 프로필의 `ec2 describe-transit-gateways` | 빈 목록. 공유 활성화 전 Workload 기반을 독립적으로 준비해야 함 |
| 2026-11-01 15:58 KST | `git diff --check` 및 변경 문서 3개의 로컬 Markdown 링크/금지 문자 검사 | 통과. 문서만 추가/갱신했으며 Terraform 코드 검증 또는 AWS 배포 검증 결과가 아님 |
| 2026-11-01 16:00~16:03 KST | 설치된 Provider 6.71.0의 격리 `providers schema -json`, 실제 업무 계정의 public SSM AMI Parameter/DescribeImages, DescribeInstanceTypeOfferings | 공식 Provider 스키마 확인. AMI `ami-029fa37c14d9a8917`은 Amazon AL2023 standard ARM64, available, root 8 GiB. t4g.nano는 두 선택 AZ 모두 제공. 설치 Provider 실행은 샌드박스 소켓 제약으로 권한을 올린 로컬 검증에서 수행 |
| 2026-11-01 16:03~16:05 KST | `DELTA_KEEP_VALIDATION=1 DELTA_VERBOSE_PLAN=1 terraform/workload-client/scripts/validate.sh` | fmt/격리 init/validate 통과. 최초 두 mock 실행은 Plan 시점의 계산된 ID fixture 문제로 실패했으며 override를 보완한 최종 실행은 **23 passed, 0 failed**. 실제 AWS Apply를 테스트에 사용하지 않음 |
| 2026-11-01 16:03 KST | `DELTA_KEEP_VALIDATION=1 terraform/network-security/scripts/validate.sh` | 중앙 Client 역할 추가와 기존 입력/라우팅/방화벽 회귀 테스트 **33 passed, 0 failed**. 격리 init/validate 및 fmt 통과 |
| 2026-11-01 16:05 KST | bootstrap Python compile/모의 subprocess 검사, Workload README 입력/출력/링크 검사 | 잘못된 계정, bucket 403 소유권 미확인, preview의 bucket 부재 3가지에서 AWS 변경 명령이 실행되지 않음 확인. 입력 15개/출력 11개 모두 문서화 |
| 2026-11-01 16:05~16:06 KST | `python3 terraform/workload-client/scripts/bootstrap_state.py --profile sbh-delta-workload --account-id 616723161368 --bucket sbh-delta-workload-tf-616723161368` 및 동일 명령 `--execute` | preview에서 bucket 부재 확인 후 사용자의 생성 지시에 따라 별도 state bucket 생성. 재조회에서 Public Access Block 4개 true, BucketOwnerEnforced, versioning Enabled, AES256 확인 |
| 2026-11-01 16:06 KST | 업무 Profile의 `terraform -chdir=terraform/workload-client init -backend-config=backend.hcl -input=false -lockfile=readonly -plugin-dir=<existing-provider-cache>` | 별도 S3 backend 초기화 성공. state 이전 없음. 설치된 Provider는 기존 lock checksum과 일치하며 로컬 캐시에서 재사용 |
| 2026-11-01 16:06 KST | 중앙 Profile의 `terraform -chdir=terraform/network-security plan -input=false -out=.validation/workload-baseline.tfplan` | **No changes**. Workload 연결 전 기존 중앙 state와 실계정 정합성 확인 |
| 2026-11-01 16:06 KST | 업무 Profile의 `terraform -chdir=terraform/workload-client plan -input=false -out=.validation/foundation.tfplan`, `show -json` 및 변경/보안 속성 검사 | **20 add, 0 change, 0 destroy**. 예상 리소스 타입/개수와 일치. 공인 주소 할당 비활성, IGW/NAT/LB/인터넷 Route 선언 없음, SSH CIDR ingress 없음 확인. review JSON은 `.validation/foundation-plan-review.json`에 보관 |
| 2026-11-01 16:07~16:09 KST | 업무 Profile의 `terraform apply -input=false .validation/foundation.tfplan` | 검토한 실제 Plan 적용 착수. VPC/Subnet/RT/SG/EC2 생성 완료, EIC Endpoint는 create-in-progress. 이 시점에는 전체 Apply 및 SSH 성공으로 기록하지 않음 |
| 2026-11-01 16:07~16:09 KST | 중앙 `shared_account_ids`에 업무 계정 추가 후 `plan -out=.validation/workload-sharing.tfplan`, `show -json` 검사, 해당 Plan `apply` | 기존 실제 입력 보존. **2 add, 0 change, 0 destroy**이며 두 RAM principal만 추가하는 것을 확인 후 적용 성공. 업무 프로필에서 TGW `tgw-0dafbd07013d574ed`, owner `482840608926`, available 확인 |
| 2026-11-01 16:10 KST | 업무 계정의 DescribeInstances/InstanceStatus/Subnets/RouteTables/InternetGateways/NatGateways/SecurityGroups/Volumes/InstanceConnectEndpoints 읽기 전용 검사 | 실제 public 노출 검사 통과. EC2 running, status checks ok, 사설 IP `10.10.1.143`, 공인 IP/IPv6 없음, Subnet 4개 자동 공인 할당 false, IGW/NAT 없음, VPC RT는 local만 존재. Client egress 없음, SSH는 관리 SG만 허용, EBS 8 GiB 암호화. 관리 Endpoint 준비 중. `.validation/private-deployment-inventory.json`에 보관 |
| 2026-11-01 16:12~16:13 KST | Workload foundation의 저장된 Plan `apply` 완료 | **20 added, 0 changed, 0 destroyed**. EIC Endpoint는 5분 37초 후 create-complete. VPC `vpc-056871f49eecfeef4`, EC2 `i-077f4b16dd163998a`, 관리 Endpoint `eice-03436798a3c0585a5` 생성 |
| 2026-11-01 16:13 KST | 업무 계정 확인 후 임시 SSH 키의 `ec2-instance-connect send-ssh-public-key`, `ssh`의 `ProxyCommand=aws ec2-instance-connect open-tunnel ...` | 실제 EIC 사설 SSH 종료 코드 **0**. `uname -m=aarch64`, `hostname -I=10.10.1.143`, `/usr/bin/curl` 및 OS Route 확인. 임시 키는 임시 디렉터리 종료 시 삭제. OS 기본 gateway는 VPC router이며 VPC RT의 인터넷 Route 부재와 구분한다. `.validation/private-ssh-check.json`에 보관 |
| 2026-11-01 16:13 KST | Workload의 `transit_gateway_id` 지정 후 `plan -out=.validation/attachment.tfplan`, `show -json`의 타입/주소/전용 Subnet 검사, 해당 Plan `apply` | **1 add, 0 change, 0 destroy** 검토 후 **1 added** 성공. Attachment `tgw-attach-0cd5f3d3c73ee7643`. Client/EC2 교체 및 원격 Route/송신 규칙 추가 없음 |
| 2026-11-01 16:14~16:18 KST | 중앙에서 실제 Attachment pendingAcceptance/소유 계정/전용 Subnet 확인 후 `plan -out=.validation/client-onboarding.tfplan`, JSON 대조, 저장 Plan `apply` | **9 add, 2 change, 0 destroy** 검토 후 **9 added, 2 changed, 0 destroyed**. 추가 Route는 같은 AZ Primary Endpoint/중앙 TGW/Inspected 반환 Attachment만 사용. 기존 Suricata 및 정책 동작은 HOME_NET에 Workload CIDR 추가 외 동일. Attachment 수락, Spoke Association 완료 |
| 2026-11-01 16:18~16:20 KST | 첫 최종 Plan 및 공유 TGW 공식 Provider 제한 대조, 소비자 `DescribeTransitGateways.Options` 확인 | 중앙 No changes. Workload는 Provider가 공유 Attachment의 기본 플래그를 true로 읽어 1 update를 제안했으며 **해당 Plan은 적용하지 않음**. 해당 두 속성만 ignore_changes로 중앙 소유와 분리. TGW data source는 실제 기본 Association/Propagation/자동 수락 disable을 검사하도록 보강. 업무 계정 API에서 세 값 disable 확인 |
| 2026-11-01 16:20 KST | `DELTA_KEEP_VALIDATION=1 DELTA_VERBOSE_PLAN=1 terraform/workload-client/scripts/validate.sh` | 소유권 보완 및 위험한 중앙 기본값 거부 3개 추가 후 fmt/격리 init/validate와 mock Plan **26 passed, 0 failed**, bootstrap 보호 테스트 **3개 OK**. 이전 23개 결과를 최신 26개 결과로 갱신 |
| 2026-11-01 16:20~16:21 KST | 두 Profile의 각 root `plan -input=false -detailed-exitcode -out=<ignored-final-plan>` | Workload와 중앙 모두 **No changes, 종료 코드 0**. 중앙 입력은 reported_state=available로 갱신하고 aws_client 키와 acceptance_mode=accept 유지 |
| 2026-11-01 16:20~16:21 KST | 중앙 Describe/Get/Search TGW 및 RT/Firewall, 업무 DescribeInstances/InstanceConnectEndpoints와 Python 대조 | Attachment available, Spoke/Inspected 명시 Association, TGW Propagation 0개, 등록한 Workload 경로 active/같은 AZ Endpoint 대상, Firewall READY/IN_SYNC 및 Primary Endpoint 2개 READY, EC2 공인 IPv4/IPv6 없음, EIC create-complete. 최초 대조 스크립트의 Endpoint Route API 필드 오인(VpcEndpointId 대신 GatewayId 반환)을 보완해 최종 통과. `.validation/final-topology.json` 보관 |
| 2026-11-01 16:21~16:22 KST | `terraform fmt -check -recursive`, `git diff --check`, 문서 링크/금지 문자/입출력 표 검사 및 `git ls-files --cached --others --exclude-standard` 대상 자격 증명 패턴 검사 | 통과. Workload 입력 15개/출력 11개, 중앙 입력 20개/출력 16개 문서화 확인. Git-visible 파일에 일반 개인 키/액세스 토큰 패턴 없음. 실제 tfvars/backend/state/Plan/검증 산출물 제외 유지 |
| 2026-11-01 16:24~16:25 KST | `git ls-remote origin refs/heads/main`, `terraform fmt -check -recursive`, `git diff --check`, `bash -n`, Python compile/문서 표/자격 증명 패턴 검사, `git check-ignore` | 원격 main의 `d6b9545`와 기존 로컬 기준 일치. 형식/구문/문서/제외 검사 통과. 기존 이벤트에서 Workload 26개/중앙 33개 mock Plan 성공 결과를 재확인했으며 Terraform 테스트/실계정 Plan/Apply는 이번 커밋 작업에서 재실행하지 않음 |
| 2026-11-01 16:25~16:26 KST | 단위별 `git add`, `git diff --cached --check`, `git commit -m <unit>`, `git push origin main`, `git ls-remote origin refs/heads/main` | 서명된 분할 커밋 4개 생성 및 staged 공백 검사 통과. `d6b9545..9a4ecb0 main -> main` 푸시 종료 코드 0. 원격 main은 `9a4ecb011c0e2f12190c73000f1095559db5c024`로 검증 체크포인트와 일치. 실제 설정/state/Plan/검증 로그는 제외 |

최종 mock 이벤트는 Workload `.validation/test-events.jsonl`과 `.validation/central-test-events.jsonl`에 보관한다. 이 디렉터리 및 실제 tfvars/backend/Plan은 Git에서 제외한다. 코드의 public 노출 제약 검증은 실제 배포 후 AWS 리소스 조회 및 SSH 검증과 구분한다.

## 현재 배포 출력

| 항목 | 값 |
| --- | --- |
| 업무 VPC | `vpc-056871f49eecfeef4` / `10.10.0.0/16` |
| EC2 | `i-077f4b16dd163998a` / `10.10.1.143` |
| 관리 Endpoint | `eice-03436798a3c0585a5` |
| TGW Attachment | `tgw-attach-0cd5f3d3c73ee7643` |
| 중앙 TGW | `tgw-0dafbd07013d574ed` |
| 배포/검증 상태 | 생성 완료, 사설 SSH 성공, 중앙 연결 완료, 양쪽 최종 Plan No changes |

공인 주소와 IGW/NAT/인터넷 Route는 없다. Workload의 remote_cidrs와 중앙 VPN/allowed_flows는 비어 있어 GCP 서비스 통신은 아직 구성하지 않았다. 다음 단계는 GCP 프로젝트/리전/CIDR 확정과 HA VPN/서버 준비다.

별도 앱, 웹서버 또는 컨테이너는 배포하지 않았다. AL2023 기본 이미지와 사전 설치된 curl을 사용해 GCP에 요청을 보낼 클라이언트를 준비한 상태다.

## 버전 관리

- 승인: 2026-11-01 사용자 `일단 커밋 단위 쪼개서 커밋 푸시 ㄱㄱ` 지시.
- 원격: `git@github.com:2026-sbh-delta/delta-network-gateway.git`, 브랜치 `main`.

| 단위 | 커밋 | 메시지 |
| --- | --- | --- |
| 중앙 Client 연결 계약 | `6a20961` | `feat: support client spokes in central inspection contracts` |
| 사설 Workload 구현 | `ad1b13a` | `feat: add private AWS workload client infrastructure` |
| 검증 도구와 보호 테스트 | `6921a02` | `test: cover private workload plans and state bootstrap guards` |
| 배포 및 검증 기록 | `9a4ecb0` | `docs: record private workload deployment and verification` |

위 4개 커밋은 생성 및 origin/main 푸시를 완료했다. 원격 조회로 확인한 체크포인트는 `9a4ecb0`이며 이 문서 갱신은 해당 완료 결과를 기록한다. 실제 tfvars/backend/state/Plan/검증 로그/Provider 캐시는 포함하지 않는다.

## 공식 근거

- [EC2 Instance Connect Endpoint 동작, 제한 및 비용](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/connect-with-ec2-instance-connect-endpoint.html)
- [EIC Endpoint와 인스턴스의 SG 규칙](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/eice-security-groups.html)
- [AL2023 standard AMI의 EC2 Instance Connect 사전 설치](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-connect-set-up.html)
- [EIC Endpoint의 콘솔 및 AWS CLI 접속](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/connect-using-eice.html)
- [TGW 공유와 계정별 소유 경계](https://docs.aws.amazon.com/vpc/latest/tgw/working-with-transit-gateways.html)
