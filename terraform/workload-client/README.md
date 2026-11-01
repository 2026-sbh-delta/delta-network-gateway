# AWS 사설 Workload 클라이언트

AWS→중앙 ANF→VPN→GCP 시험의 AWS 클라이언트를 만든다. 중앙 `network-security`와 별도 Provider/state를 사용한다. 기본값은 VPC 1개, 두 AZ의 TGW/Client Subnet 각 2개, 사설 AL2023 ARM64 `t4g.nano` 1대와 EC2 Instance Connect Endpoint 1개다.

공인 IPv4/IPv6, IGW, NAT, 인터넷 기본 Route, Public ALB 및 SSH의 CIDR 기반 ingress를 생성하지 않는다. 관리 SSH는 IAM 인증 후 같은 VPC의 EIC Endpoint SG에서만 받는다. 이것은 ANF Endpoint가 아니며 관리 접속 성공은 중앙 검사 통과 증거가 아니다. 원격 CIDR을 등록하기 전에는 시험용 SG 송신 규칙도 없다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `name` | `string` | `delta` | 리소스 이름 접두사 |
| `environment` | `string` | `lab` | 이름과 환경 태그 |
| `region` | `string` | `ap-northeast-2` | 중앙 TGW와 같은 Region |
| `workload_account_id` | `string` | 필수 | 실제 Provider/STS 계정과 비교할 업무 계정 ID |
| `central_account_id` | `string` | 필수 | 업무 계정과 다른 중앙 TGW 소유 계정 ID |
| `vpc_cidr` | `string` | 필수 | 정규화된 RFC1918 IPv4 `/16` |
| `azs` | `map(object)` | 필수 | 두 AZ ID별 TGW/Client 전용 `/24` |
| `central_az_ids` | `set(string)` | 필수 | 중앙 Inspection과 맞출 물리 AZ IDs |
| `inspection_cidrs` | `set(string)` | 필수 | 중복 검사용 중앙 Inspection CIDRs. Client Route로 만들지 않음 |
| `client_az_id` | `string` | 필수 | EC2와 관리 Endpoint를 배치할 `azs` 키 |
| `ami_id` | `string` | 필수 | 실제 조회 후 고정한 Amazon AL2023 standard ARM64 AMI |
| `transit_gateway_id` | `string`, nullable | `null` | `null`이면 Attachment 없이 독립 기반만 생성. 공유 후 TGW ID 지정 |
| `remote_cidrs` | `set(string)` | `[]` | TGW로 보낼 사설 원격 CIDRs. 중앙/업무 CIDR 및 서로 겹치지 않아야 함 |
| `test_tcp_ports` | `set(number)` | `[8080, 8081]` | 원격 CIDR에 허용할 TCP 송신 포트. ANF 차단 대조군 포트도 포함 |
| `tags` | `map(string)` | `{}` | 공통 태그 추가/재정의 |

### `azs` 내부 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| map key | `string` | 필수 | 중앙이 지원하는 물리 AZ ID. 예: `apne2-az1` |
| `tgw_subnet_cidr` | `string` | 필수 | 해당 AZ의 TGW 전용 사설 `/24` |
| `client_subnet_cidr` | `string` | 필수 | 해당 AZ의 EC2용 사설 `/24` |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| `workload_account_id` | 실제 확인한 업무 계정 ID |
| `region` | 업무 Region |
| `az_ids` | 정렬한 물리 AZ IDs |
| `vpc_id` | Workload VPC ID |
| `vpc_cidr` | Workload VPC 주소 |
| `subnet_ids_by_az_id` | AZ ID별 `tgw`/`client` Subnet IDs |
| `route_table_ids_by_az_id` | AZ ID별 `tgw`/`client` Route Table IDs |
| `client_instance_id` | 사설 EC2 ID |
| `client_private_ip` | 트래픽 및 로그를 대조할 사설 IP |
| `management_endpoint_id` | EIC Endpoint ID. ANF Endpoint와 구분 |
| `central_handoff` | Attachment 활성 시 `aws_client` 키로 제공하는 중앙 계약. 비활성 시 `{}` |

`central_handoff.aws_client`는 `account_id`, `role=Client`, `vpc_cidrs`, `attachment_id`, `acceptance_mode=accept`, `az_ids`를 포함한다. 실제 `reported_state`는 전달 직전 AWS에서 조회해 중앙 입력에 추가한다. EC2 개인 키/인증 토큰은 생성하거나 출력하지 않는다.

## 준비와 state

업무 Profile은 `AWS_PROFILE=sbh-delta-workload`를 사용한다. 중앙 명령은 `sbh-delta-security`를 별도로 지정한다. 실제 tfvars, backend, state와 Plan은 gitignore 대상이다. 예시 파일을 기존 실제 파일 위에 복사하지 않는다.

```bash
aws sts get-caller-identity --profile sbh-delta-workload
aws ssm get-parameter --profile sbh-delta-workload --region ap-northeast-2 \
  --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 \
  --query Parameter.Value --output text
# 위 AMI를 DescribeImages로 검증한 뒤 ami_id에 고정한다.
```

S3 bucket은 업무 계정 소유로 별도 준비한다. 아래 스크립트는 STS 계정과 기존 bucket의 소유권을 먼저 확인한다. `--execute`가 없으면 조회 및 예정 작업 출력만 수행한다. `--execute`를 지정하면 해당 bucket만 생성/보강하고 전체 Public Access Block, BucketOwnerEnforced, 버전 관리와 AES256 암호화를 설정한다. 중앙 bucket 정책과 기존 state는 변경하지 않는다.

```bash
cd terraform/workload-client
python3 scripts/bootstrap_state.py --profile sbh-delta-workload \
  --account-id 616723161368 --bucket sbh-delta-workload-tf-616723161368
# 승인된 state bootstrap 실행 시 위 명령에 --execute를 추가한다.
```

Bucket 이름이 다른 계정에서 사용 중이거나 소유권을 확인할 수 없으면 중단한다. 새 이름을 결정하고 `backend.hcl`에 반영한다. state/lockfile 접근 권한은 해당 key의 GetObject/PutObject, lockfile DeleteObject 및 bucket ListBucket으로 제한할 수 있다. 운영자의 SSO 세션을 쓰고 인증정보를 파일에 넣지 않는다.

```bash
export AWS_PROFILE=sbh-delta-workload
terraform init -backend-config=backend.hcl -input=false
terraform fmt -check -recursive
terraform validate
umask 077
terraform plan -input=false -out=workload.tfplan
terraform show -no-color workload.tfplan
# 생성/수정/삭제 및 public 노출 부재를 검토한 실제 Plan만 적용한다.
terraform apply workload.tfplan
```

`backend.hcl`은 자동 로드되지 않는다. 기존 state/backend를 자동 migrate하지 않는다. Bucket은 이 root 밖에서 관리하며 Workload 해체 후에도 기본 보존한다.

## 중앙 TGW를 연결하기

1. 첫 Workload Plan/Apply는 `transit_gateway_id=null`, `remote_cidrs=[]`로 수행할 수 있다.
2. 중앙 실제 tfvars의 `shared_account_ids`에 업무 계정을 **기존 값과 함께** 등록한다. 중앙 Plan에서 두 RAM principal 추가를 검토하고 Apply한다. Organizations는 변경하지 않는다.
3. 업무 프로필의 `aws ec2 describe-transit-gateways`에 중앙 TGW와 예상 소유 계정이 나타나는지 확인한다.
4. 업무 tfvars의 `transit_gateway_id`를 지정하고 Plan/Apply한다. 공유 TGW Attachment의 기본 Association/Propagation은 false다. `remote_cidrs`는 GCP 주소와 VPN 준비 전까지 비워둔다.
5. `central_handoff`와 실제 Attachment 상태를 중앙 `spoke_attachments.aws_client`에 등록한다. 중앙은 `Client` 역할을 지원한다. `acceptance_mode=accept`는 수락 후에도 유지한다.
6. 중앙 Plan/Apply에서 수락, Spoke Association과 검사/반환 Route를 검토한다. 중앙 `allowed_flows`는 자동 추가하지 않는다.

TGW Attachment 소유자는 Workload, accepter와 TGW RT Association/Route 소유자는 중앙이다. 서로의 state에서 중복 관리하지 않는다. 중앙 accepter에 `prevent_destroy`가 있으므로 철거 전에 두 state의 소유권과 삭제 순서를 확인한다.

공유 TGW의 기본 Association/Propagation은 Workload Provider에서 설정하거나 drift를 검사할 수 없다. 해당 Attachment 속성 두 개는 `ignore_changes`로 중앙 관리와 분리한다. 대신 공유 TGW data source가 실제 기본 Association/Propagation/자동 수락이 모두 disable인지 검사하며 중앙 accepter와 실제 RT 조회도 확인한다. 이 제외는 Subnet, TGW ID, 태그 등 다른 Attachment 속성에는 적용하지 않는다. [Provider의 공유 TGW 제한](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ec2_transit_gateway_vpc_attachment)

## Route Table 표

| 위치 | 목적지 | 다음 홉 |
| --- | --- | --- |
| Client Subnet RT 2개 | 같은 VPC | local |
| Client Subnet RT 2개 | 등록한 `remote_cidrs`만 | 공유 TGW |
| TGW 전용 Subnet RT 2개 | 같은 VPC의 EC2 | local |
| 중앙 Spoke RT | 등록한 Workload/원격 CIDR | Inspection Attachment |
| 중앙 Inspected RT | Workload CIDR | Workload Attachment |

관리 Endpoint와 EC2는 같은 AZ/Client Subnet을 사용한다. GCP CIDR은 RFC1918 전체 포함 여부와 중복을 검증하고, 인터넷 기본 Route와 공인 목적지를 거부한다. AWS와 GCP 전체 환경의 미등록 CIDR 충돌은 코드가 자동 발견하지 않는다.

## 검증과 운영

```bash
DELTA_KEEP_VALIDATION=1 scripts/validate.sh
```

별도 임시 디렉터리에서 backend 없이 init/validate 및 AWS mock Plan 테스트만 실행한다. 실제 tfvars/backend를 복사하지 않고 예시/가짜 값만 사용한다. 실제 Plan/Apply나 AWS 리소스 생성은 이 스크립트에 없다.

관리 접속, 실제 public 노출 점검 및 해체 순서는 [Runbook](RUNBOOK.md)을 따른다. EC2/EBS, 활성화한 TGW Attachment/트래픽, S3 저장/요청이 비용 항목이다. 인터넷 출구가 없으므로 외부 패키지 다운로드는 준비되지 않는다. GCP/VPN/실제 ANF 허용 및 차단 통신은 후속 단계다.

## 공식 근거

- [EIC Endpoint의 사설 접속과 제한](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/connect-with-ec2-instance-connect-endpoint.html)
- [Endpoint/EC2 Security Group 참조](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/eice-security-groups.html)
- [AL2023 standard의 EIC 사전 설치](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-connect-set-up.html)
- [공유 TGW의 소유 경계](https://docs.aws.amazon.com/vpc/latest/tgw/working-with-transit-gateways.html)
- [S3 backend와 lockfile](https://developer.hashicorp.com/terraform/language/backend/s3)
