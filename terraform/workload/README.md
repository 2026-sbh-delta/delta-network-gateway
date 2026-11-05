# 공통 AWS 워크로드 네트워크

Client와 Server의 기존 시험 환경을 `10.10.0.0/16` VPC 하나로 통합한 네트워크 전용 root다. EC2와 앱을 생성하지 않는다. 기존 VPC, 두 AZ의 TGW/워크로드 서브넷, Route Table, SG와 EIC Endpoint를 관리한다.

승인과 실행 결과는 [통합 AI-DLC](../../docs/ai-dlc/aws-workload-consolidation.md)를 따른다. 코드 변경과 실제 Apply 상태는 구분한다.

## 기존 state와의 연결

- 이전 경로: `terraform/workload-client`.
- 기존 S3 bucket과 key `delta/lab/workload-client/terraform.tfstate`를 그대로 사용한다. 디렉터리 이름을 바꿔도 원격 state를 새 key로 옮기지 않는다.
- `aws_vpc.client`, `aws_subnet.private`, Attachment의 `client` 주소, `aws_client` handoff 키, 태그와 `client` 서브넷 역할은 기존 리소스 ID와 보호 계약을 유지하기 위한 이름이다.
- `client_az_id`는 EIC Endpoint의 AZ를 지정한다. 현재 EC2 개수를 의미하지 않는다.
- 기존 `ami_id` 입력과 `client_instance_id`/`client_private_ip` 출력은 제거했다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| name | string | `delta` | 기존 리소스 이름 접두사 |
| environment | string | `lab` | 환경과 태그 |
| region | string | `ap-northeast-2` | 중앙 TGW와 같은 리전 |
| workload_account_id | string | 필수 | 실제 STS 및 Provider 허용 업무 계정 |
| central_account_id | string | 필수 | 공유 TGW의 예상 소유 계정 |
| vpc_cidr | string | 필수 | 정규화된 사설 IPv4 /16 |
| azs | map(object) | 필수 | 두 물리 AZ ID와 전용 사설 /24 서브넷 |
| central_az_ids | set(string) | 필수 | 중앙 네트워크가 지원하는 AZ ID |
| inspection_cidrs | set(string) | 필수 | 중앙 VPC와 주소 중복 검사 |
| client_az_id | string | 필수 | 유지할 EIC Endpoint의 AZ, 기존 입력명 유지 |
| transit_gateway_id | string | `null` | 공유 TGW ID, null이면 Attachment 없음 |
| remote_cidrs | set(string) | `[]` | 반환 경로가 준비된 원격 사설망의 TGW 목적지 |
| test_tcp_ports | set(number) | `[8080, 8081]` | 유지하는 워크로드 SG의 원격망 송신 포트 |
| tags | map(string) | `{}` | 추가 공통 태그 |

### azs 내부 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| map key | string | 필수 | `apne2-az1` 같은 물리 AZ ID |
| tgw_subnet_cidr | string | 필수 | TGW Attachment 전용 /24 |
| client_subnet_cidr | string | 필수 | 공통 워크로드 전용 /24, 기존 입력명 유지 |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| workload_account_id | 실제 확인한 업무 계정 |
| region | 네트워크 리전 |
| az_ids | 물리 AZ ID 목록 |
| vpc_id | 공통 VPC ID |
| vpc_cidr | 공통 VPC 대역 |
| subnet_ids_by_az_id | AZ별 TGW와 워크로드(`client`) 서브넷 ID |
| route_table_ids_by_az_id | AZ별 TGW와 워크로드 Route Table ID |
| management_endpoint_id | 유지하는 EIC Endpoint ID |
| central_handoff | 중앙 spoke 등록용 계정/역할/CIDR/Attachment/AZ와 수락 모드, 안정된 `aws_client` 키 |

## 실행

```bash
bash terraform/workload/scripts/validate.sh
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload init \
  -backend-config=backend.hcl -lockfile=readonly
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload plan \
  -input=false -detailed-exitcode
```

기존 backend의 정확한 bucket/key를 확인한다. 새 key로 초기화해 기존 VPC를 중복 생성하지 않는다. 실제 tfvars와 backend는 Git에서 제외한다.

현재 공통 대역은 `10.10.0.0/16`이다. GCP `10.32.1.0/24` 경로를 유지하며 폐기된 Server 대역으로 향하는 경로를 등록하지 않는다. 향후 새 앱은 별도 컴퓨트 구성에서 생성하고 이 root의 네트워크를 참조한다.

[운영과 검증](RUNBOOK.md)을 따른다. EIC 접속 대상 EC2는 이 root가 제공하지 않는다. 기존 두 VPC의 ANF 시험은 과거 기록이며 단일 VPC local 통신의 검사 보장을 의미하지 않는다.
