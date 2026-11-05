# 공통 AWS 워크로드 네트워크

GuardDuty는 이 계정/리전의 기본 탐지를 관리하며 기본 활성 상태는 true다. 기존 detector의 ID와 선택 보호 설정을 보존한다. 최초 적용 전 기존 detector를 Import하고, 이미 state에 등록돼 있다면 Import를 반복하지 않는다. [GuardDuty 설계와 실제 검증 상태](../../docs/ai-dlc/aws-guardduty.md), [등록과 운영 절차](RUNBOOK.md#guardduty-등록과-운영)를 따른다.

기본 구성은 기존 `10.10.0.0/16` 공통 네트워크와 계정/리전 GuardDuty를 관리한다. `application_enabled=true`를 명시하면 공개 ALB와 NAT, 사설 x86 앱 EC2와 RDS PostgreSQL을 추가한다. native는 FE/BE 각 1대, compose는 기존 frontend EC2 한 대에 두 컨테이너를 배치한다. 기존 VPC/네 서브넷/TGW/EIC의 ID와 backend key는 유지한다. [앱 설계와 실제 검증 상태](../../docs/ai-dlc/aws-sample-app.md)를 확인한다.

승인과 실행 결과는 [통합 AI-DLC](../../docs/ai-dlc/aws-workload-consolidation.md)를 따른다. 코드 변경과 실제 Apply 상태는 구분한다.

VPC의 Name 태그는 `vpc_name`으로 지정하며 기본값은 `delta-board-vpc`다. 다른 리소스의 이름은 기존 `name`/`environment` 접두사를 사용한다.

## 기존 state와의 연결

- 이전 경로: `terraform/workload-client`.
- 기존 S3 bucket과 key `delta/lab/workload-client/terraform.tfstate`를 그대로 사용한다. 디렉터리 이름을 바꿔도 원격 state를 새 key로 옮기지 않는다.
- `aws_vpc.client`, `aws_subnet.private`, Attachment의 `client` 주소, `aws_client` handoff 키와 `client` 서브넷 역할은 기존 리소스 ID와 보호 계약을 유지하기 위한 이름이다. VPC의 Name 태그는 이 주소와 별도로 지정한다.
- `client_az_id`는 EIC Endpoint의 AZ를 지정한다. 현재 EC2 개수를 의미하지 않는다.
- 기존 `ami_id` 입력과 `client_instance_id`/`client_private_ip` 출력은 제거했다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| name | string | `delta` | 기존 리소스 이름 접두사 |
| vpc_name | string | `delta-board-vpc` | 공통 접두사와 독립된 VPC Name 태그 |
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
| guardduty_enabled | bool | `true` | 계정/리전 기본 탐지. false는 detector와 findings를 보존한 일시 중지 |
| guardduty_finding_publishing_frequency | string | `SIX_HOURS` | 후속 finding 알림 주기. FIFTEEN_MINUTES/ONE_HOUR/SIX_HOURS |
| guardduty_features | map(object) | 기능별 기본값 표 참고 | 필수 7개 선택 기능과 추가 설정의 명시적 상태 |
| application_enabled | bool | `false` | 샘플 앱 기반을 명시적으로 생성하는 선택 모드 |
| application | object | `{}` 내부 기본값 사용 | 앱 세부 설정, 아래 내부 속성 표 참고 |

### azs 내부 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| map key | string | 필수 | `apne2-az1` 같은 물리 AZ ID |
| tgw_subnet_cidr | string | 필수 | TGW Attachment 전용 /24 |
| client_subnet_cidr | string | 필수 | 공통 워크로드 전용 /24, 기존 입력명 유지 |

### application 내부 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| deployment_mode | string | `native` | `native`: FE/BE 별도 EC2, `compose`: FE EC2 한 대에서 FE/BE 컨테이너 실행 |
| retain_legacy_backend | bool | `false` | compose 전환 검증 중에만 이전 BE EC2/역할/SG를 보존. 검증 후 false로 바꾸면 제거 |
| public_subnet_cidrs | map(string) | `{apne2-az1="10.10.10.0/24", apne2-az2="10.10.11.0/24"}` | ALB/NAT용 신규 public 서브넷, 기존 두 AZ 키와 일치 |
| db_subnet_cidrs | map(string) | `{apne2-az1="10.10.20.0/24", apne2-az2="10.10.21.0/24"}` | 인터넷 기본 경로가 없는 DB 전용 서브넷 |
| ami_id | string | `null` | null이면 공식 AL2023 x86_64 SSM 조회. 실제 배포 입력은 조회한 AMI를 고정 |
| instance_type | string | `t3.small` | 앱 EC2의 x86_64 유형 |
| db_engine_version | string | `18.3` | PostgreSQL 엔진 major.minor. minor 자동 갱신은 끔 |
| db_instance_class | string | `db.t3.micro` | x86 RDS 인스턴스 유형 |
| db_allocated_storage | number | `20` | 암호화 gp3 DB 용량 GiB, 정수 20 이상 |
| certificate_arn | string | `null` | 선택적 서울 ACM 인증서. 지정 시 HTTP redirect와 HTTPS listener |
| hostname | string | `null` | 인증서에 해당하는 공개 도메인. certificate_arn 사용 시 필수 |
| node_version | string | `24.19.0` | 공통 bootstrap의 Linux x64 Node.js 고정 버전. Compose 이미지 버전은 배포 도구에서 별도 고정 |
| node_sha256 | string | `14b342e71204f811bde6153be8e04b62aef63c236fef92b55f9c83154b409647` | 해당 Node.js Linux x64 tar.xz의 공식 SHA-256 |

### GuardDuty 기능별 기본값

| 기능명 | 기본 상태 | 추가 설정 |
| --- | --- | --- |
| S3_DATA_EVENTS | ENABLED | 없음 |
| EKS_AUDIT_LOGS | ENABLED | 없음 |
| EBS_MALWARE_PROTECTION | ENABLED | 없음 |
| RDS_LOGIN_EVENTS | ENABLED | RDS_DATA_RISK=ENABLED |
| LAMBDA_NETWORK_LOGS | ENABLED | 없음 |
| RUNTIME_MONITORING | DISABLED | EKS/ECS Fargate/EC2 에이전트 자동 관리 모두 DISABLED |
| AI_PROTECTION | DISABLED | 없음 |

### guardduty_features 내부 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| map key | string | 위 기능별 기본값 | 필수 7개 키를 유지한다. 해당 리전에서 지원을 확인한 AI_ANALYST만 선택적으로 추가할 수 있다 |
| status | string | 필수 | ENABLED 또는 DISABLED |
| additional_configuration | map(string) | `{}` | 기능별 추가 설정. 값은 ENABLED 또는 DISABLED. 아래 RDS/Runtime 키는 모두 명시해야 한다 |
| RDS_LOGIN_EVENTS.RDS_DATA_RISK | string | 위 기능별 기본값 | RDS 로그인 탐지의 추가 데이터 위험 탐지 설정 |
| RUNTIME_MONITORING.EKS_ADDON_MANAGEMENT | string | DISABLED | EKS 보안 에이전트 자동 관리 |
| RUNTIME_MONITORING.ECS_FARGATE_AGENT_MANAGEMENT | string | DISABLED | ECS Fargate 보안 에이전트 자동 관리 |
| RUNTIME_MONITORING.EC2_AGENT_MANAGEMENT | string | DISABLED | EC2 보안 에이전트 자동 관리 |

`guardduty_features` 입력은 전체 맵을 교체한다. 기능 하나만 바꾸더라도 필수 7개 키와 RDS/Runtime 추가 설정을 모두 유지한다. 꺼진 기능의 추가 설정도 DISABLED여야 하며 다른 기능에는 추가 설정을 넣지 않는다. 과거 EKS_RUNTIME_MONITORING은 RUNTIME_MONITORING과 함께 구성하지 않는다.

### guardduty 출력 내부 속성

| 속성명 | 역할 |
| --- | --- |
| account_id / region | 관리하는 실제 계정과 리전 |
| detector_id / arn | detector 식별자 |
| enabled | 기본 탐지 활성 상태. false는 detector 보존과 일시 중지를 뜻한다 |
| finding_publishing_frequency | 기존 finding의 후속 발생 알림 주기 |
| features | 기능명별 실제 Provider 상태와 additional_configuration의 이름/상태 맵. Findings 본문은 포함하지 않는다 |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| workload_account_id | 실제 확인한 업무 계정 |
| guardduty | 계정/리전 detector ID/ARN, 활성 상태, 게시 주기와 관리하는 기능/추가 설정 |
| region | 네트워크 리전 |
| az_ids | 물리 AZ ID 목록 |
| vpc_id | 공통 VPC ID |
| vpc_cidr | 공통 VPC 대역 |
| subnet_ids_by_az_id | AZ별 TGW와 워크로드(`client`) 서브넷 ID |
| route_table_ids_by_az_id | AZ별 TGW와 워크로드 Route Table ID |
| management_endpoint_id | 유지하는 EIC Endpoint ID |
| central_handoff | 중앙 spoke 등록용 계정/역할/CIDR/Attachment/AZ와 수락 모드, 안정된 `aws_client` 키 |
| application | 비활성화 시 null, 활성화 시 공개 URL/EC2/DB/secret ARN/S3/로그의 비밀값 없는 배포 인벤토리 |

## 실행

```bash
bash terraform/workload/scripts/validate.sh
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload init \
  -backend-config=backend.hcl -lockfile=readonly
AWS_PROFILE=sbh-delta-workload terraform -chdir=terraform/workload plan \
  -input=false -detailed-exitcode
```

기존 backend의 정확한 bucket/key를 확인한다. 새 key로 초기화해 기존 VPC를 중복 생성하지 않는다. 실제 tfvars와 backend는 Git에서 제외한다.

현재 공통 대역은 `10.10.0.0/16`이다. GCP `10.32.1.0/24` 경로를 유지하며 폐기된 Server 대역으로 향하는 경로를 등록하지 않는다. 샘플 앱은 선택적 child module [sample-app](modules/sample-app/README.md)을 사용한다. 앱 기본값은 비활성화이며 실제 배포 입력을 별도로 관리한다.

[운영과 검증](RUNBOOK.md)을 따른다. 네트워크 전용 모드에는 EIC 접속 대상 EC2가 없다. native 앱 모드는 역할별 EC2를 생성하고 compose 모드는 frontend EC2 한 대에 두 서비스를 배치한다. 기존 EIC를 관리 경로로 사용한다. application 출력의 deployment_mode/service_hosts/instances로 실행 모드와 실제 호스트를 구분한다. 기존 두 VPC의 ANF 시험은 과거 기록이며 단일 VPC local 통신의 검사 보장을 의미하지 않는다.

## 앱 실행과 상태

앱 입력 예시는 [application.tfvars.example](application.tfvars.example), 개별 빌드/DB 초기화/배포/롤백은 [배포 도구](../../deploy/workload/README.md)를 따른다. 비밀번호는 Terraform에서 관리하지 않는다. `application_enabled=false`는 앱 전체 제거 계획을 만들므로 앱 버전 롤백에는 사용하지 않는다.
