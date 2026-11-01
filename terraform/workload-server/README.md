# 사설 AWS Workload 서버

기존 Workload 계정에 별도 Server VPC를 만들고 Client VPC에서 중앙 TGW/ANF를 거쳐 접근한다. 동일 계정 두 VPC의 검사 시험이며 멀티클라우드 연결 시험과 구분한다.

- Workload: `616723161368`, `AWS_PROFILE=sbh-delta-workload`, 서울 `ap-northeast-2`.
- 새 VPC: `10.20.0.0/16`, 두 AZ의 TGW/Server 전용 `/24` Subnet 4개. EC2는 `apne2-az1`의 `10.20.1.10` 한 대다.
- EC2: AL2023 standard ARM64/t4g.nano, 암호화한 8 GiB gp3, IMDSv2, T4g Standard credits. 이미지에 있는 Python으로 두 HTTP listener를 실행한다.
- 공인 IPv4/IPv6, IGW/NAT/LB 및 인터넷 기본 Route를 생성하지 않는다. SSH는 같은 VPC의 EIC Endpoint SG에서만 허용한다. 서비스 ingress는 Client `/32`의 TCP 8080/8081만 수용하고 서버의 새 outbound 연결은 허용하지 않는다.
- 별도 S3 key: 기존 Workload bucket의 `delta/lab/workload-server/terraform.tfstate`. 중앙/Client state와 섞거나 이전하지 않는다.

승인/실제 Plan/Apply/검증 상태는 [AI-DLC](../../docs/ai-dlc/aws-workload-server.md)에 기록한다. 현재 구성값과 후속 실행 결과를 구분해서 확인한다.

2026-11-01 실제 배포: VPC `vpc-0dd4ef153e66803ab`, EC2 `i-03d824c2a456e71ad`/`10.20.1.10`, Attachment `tgw-attach-08255cc8fcac5df2c`, EIC `eice-008f5b3a537d716bd`. Client의 8080 HTTP 200과 양방향 FLOW, 8081 blocked/drop SID 9000000, 허용 마커 alert SID 2000001을 확인했다. 세 root 최종 Plan은 No changes다. 로컬 검증은 Server mock 31개/앱 1개, 중앙 회귀 34개, Client 회귀 26개 및 기존 bootstrap 보호 3개 통과다.

후속 [CloudFront ANF 웹](../cloudfront-server/README.md)은 별도 state에서 Client VPC의 internal ALB를 추가했다. 이 Server SG에는 ALB 전용10.10.4/24와10.10.5/24의 TCP8080/8081 standalone ingress4개만 추가한다. 중앙 ANF가8080만 허용하고8081은 차단한다. 이전 직접 Origin/Server IGW/직접 서비스 SG rule은 제거했다. 서버 공인 주소/인터넷 Route는 없고 이 root의 최종 Plan은 No changes다.

## 준비와 단계별 실행

Terraform `1.16.4`, AWS Provider `6.71.0`과 lock을 고정한다. `terraform.tfvars.example`의 계정/AMI는 mock fixture다. 실제 입력은 ignored `terraform.tfvars`/`backend.hcl`에 0600으로 저장한다. AMI/계정/AZ/TGW 조회로 실제값을 검증한다.

기존 S3 bucket은 버전 관리, 암호화, Public Access Block과 bucket owner를 확인한다. Server key가 없음을 확인하고 backend를 초기화한다. 기존 key가 발견되거나 조회가 403이면 새 state라고 가정하지 않는다. `-migrate-state`는 사용하지 않는다.

```bash
terraform/workload-server/scripts/validate.sh
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload-server init \
  -backend-config=backend.hcl -input=false -lockfile=readonly
```

1. `enable_remote_routes=false`로 VPC/서버/EIC/Attachment를 계획한다. `remote_cidrs=["10.10.0.0/16"]`, `allowed_client_cidrs=["10.10.1.143/32"]`는 미리 등록할 수 있다. 두 서비스 ingress가 있어도 이 단계의 반환 Route는 아직 없다.
2. 저장 Plan의 계정/생성 목록/공인 주소 부재/기존 리소스 삭제 부재를 검토하고 적용한다. `central_handoff.aws_server`를 중앙 계약에 병합한다. 중앙에서 실제 Attachment 소유 계정/상태를 확인하고 `role=Backend`, `acceptance_mode=accept`로 수락한다. 수락 후에도 같은 키/모드를 유지한다.
3. 중앙의 Spoke Association/Inspection 경로/Inspected 반환 경로를 적용한다. 같은 Workload principal의 RAM 공유를 재사용한다. 두 VPC를 직접 연결하는 Peering이나 Spoke 직결 Route는 추가하지 않는다.
4. `enable_remote_routes=true`로 Server 반환 Route 두 개를 생성한다. 기존 Client의 `remote_cidrs`에도 Server `/16`을 등록해 두 Client RT의 TGW Route 및 두 시험 포트 egress를 추가한다.
5. 중앙 ANF에는 Client `/32` → Server `/32` TCP 8080만 허용한다. 8081은 마지막 drop SID 9000000으로 차단한다. HTTP `/delta-test-marker` 관측 alert SID 2000001은 허용 트래픽에서 별도로 확인한다.

```bash
umask 077
mkdir -p terraform/workload-server/.validation
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload-server plan \
  -input=false -out=.validation/reviewed.tfplan
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload-server show \
  -no-color .validation/reviewed.tfplan
# 변경 목록을 검토한 저장 Plan만 적용한다.
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload-server apply \
  -input=false .validation/reviewed.tfplan
```

빈 연결 기반은 20리소스다. 이번 실제 입력처럼 Attachment 1개와 Client 두 포트 ingress가 있으면 첫 Plan은 23리소스, 수락 후 반환 Route 2개를 추가한다. 실제 변경 목록은 매번 Plan으로 다시 확인한다. `validate.sh`는 실제 backend/입력을 복사하지 않고 별도 디렉터리에서 init/validate, mock Plan과 로컬 두 앱 포트 응답을 검사한다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| name | string | `delta` | 이름 접두사 |
| environment | string | `lab` | 환경 이름/태그 |
| region | string | `ap-northeast-2` | 중앙 TGW와 같은 Region |
| workload_account_id | string | 필수 | Provider/STS로 검증할 Workload 계정 |
| central_account_id | string | 필수 | 다른 계정인 공유 TGW 소유자 |
| vpc_cidr | string | 필수 | RFC1918 정규 IPv4 `/16` |
| azs | map(object) | 필수 | 중앙과 공통인 정확히 두 물리 AZ 및 전용 Subnet |
| central_az_ids | set(string) | 필수 | 중앙 Primary Endpoint가 지원하는 물리 AZ IDs |
| inspection_cidrs | set(string) | 필수 | 중복 검사에 사용할 중앙 Inspection CIDRs |
| server_az_id | string | 필수 | 단일 서버/EIC를 배치할 azs의 키 |
| ami_id | string | 필수 | 고정 AL2023 standard ARM64 HVM/EBS 이미지 ID |
| transit_gateway_id | string | `null` | 선택적 공유 TGW. null이면 Attachment 없음 |
| remote_cidrs | set(string) | `[]` | Client 등 사설 원격망. 중복 검사와 반환 Route 목적지 |
| tags | map(string) | `{}` | 추가 공통 태그 |
| server_private_ip | string | 필수 | 선택한 Server `/24`의 AWS 예약 주소를 제외한 고정 IPv4 |
| allowed_client_cidrs | set(string) | `[]` | 두 시험 포트 ingress source `/32`. remote_cidrs 안에 있어야 함 |
| enable_remote_routes | bool | `false` | 중앙 수락 후 Server의 TGW 반환 Route 활성화 |

`azs` 내부 속성:

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| map key | string | 필수 | `apne2-az1` 같은 물리 AZ ID. 리소스 반복 키로 유지 |
| tgw_subnet_cidr | string | 필수 | VPC 안의 비중복 정규 `/24` TGW 전용 Subnet |
| server_subnet_cidr | string | 필수 | VPC 안의 비중복 정규 `/24` Server Subnet |

이름/태그는 서버용 접미사 `-server`를 사용한다. 공유 Attachment의 기본 Association/Propagation 두 필드만 중앙이 소유하며 소비자 `ignore_changes`로 분리한다. TGW data source가 실제 기본값 비활성을 검증한다.

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| workload_account_id | 실제 STS 계정 |
| region | Workload Region |
| az_ids | 두 물리 AZ ID |
| vpc_id | Server VPC ID |
| vpc_cidr | Server VPC `/16` |
| subnet_ids_by_az_id | 물리 AZ별 TGW/Server Subnet IDs |
| route_table_ids_by_az_id | 물리 AZ별 TGW/Server Route Table IDs |
| server_instance_id | 사설 EC2 ID |
| server_private_ip | 서버 내부 IPv4 |
| management_endpoint_id | 관리용 EIC Endpoint ID. ANF Endpoint와 다름 |
| central_handoff | 중앙 `aws_server` 계약. account_id/Backend role/vpc_cidrs/attachment_id/acceptance_mode/az_ids이며 reported_state는 실제 조회로 추가 |
| test_ports | 앱이 실제 listen하는 8080/8081 |

## 운영과 비용

서버 앱은 user data로 설치하므로 앱/startup 변경은 `user_data_replace_on_change=true`에 따라 Server EC2 교체를 제안한다. 변경 Plan에서 중단/교체 범위를 먼저 검토한다. 기존 Client에는 이 앱을 설치하지 않는다.

[Runbook](RUNBOOK.md)에서 사설 SSH, 로컬 앱, ANF 왕복과 로그, 롤백을 확인한다. EIC 관리 경로는 같은 VPC local이며 ANF 서비스 시험과 구분한다. EC2/EBS, TGW Attachment/데이터 처리, 기존 ANF/CloudWatch/S3 state가 비용 항목이다. 단일 서버의 HA와 장시간 부하/장애 시험은 포함하지 않는다.

해체는 Client 원격 경로/중앙 정책/연결 의존 관계를 먼저 검토한다. 중앙 accepter에는 실제 Attachment 삭제 방지를 위한 prevent_destroy가 있어 소유권을 확인한 뒤 별도 작업으로 정리한다. state bucket/버전과 중앙 로그는 기본 보존한다. 이 root는 bucket을 생성/삭제하지 않는다.
