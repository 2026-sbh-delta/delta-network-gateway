# 선택적 x86 Sample App 기반

기존 workload VPC/사설 서브넷/EIC를 참조하는 child module이다. 별도 state나 VPC를 만들지 않는다. 공개 ALB -> FE:3000 -> BE:8080 -> 사설 RDS:5432로 연결한다. native는 FE/BE 별도 EC2, compose는 frontend EC2의 두 컨테이너를 사용하고 선택 AZ의 기존 workload RT 하나에 단일 NAT default route를 추가한다. DB RT와 TGW 전용 RT에는 인터넷 경로를 넣지 않는다.

기본 native 모드는 AL2023 x86_64, Node.js 24와 systemd를 사용한다. compose 모드는 기존 frontend EC2 한 대와 호스트 역할을 사용하며 Docker/Compose는 별도 배포 도구로 준비한다. user_data는 호스트 교체를 피하기 위해 그대로 유지한다. 초기화는 런타임/로그/설치 도구만 준비하며 앱은 산출물 설치 후 시작한다. RDS 마스터 암호는 AWS가 생성하고 앱 secret은 metadata만 Terraform으로 관리한다. 실제 secret 값은 운영자 초기화 도구가 저장한다. compose에서는 frontend 호스트 역할이 앱 secret을 읽고 BE 컨테이너에만 파일로 전달한다. 두 서비스의 로그 그룹은 호스트 수와 무관하게 보존한다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| prefix | string | 필수 | 이름 접두사, 리소스에 맞게 최대 24자 사용 |
| region | string | 필수 | AWS 리전 |
| account_id | string | 필수 | 업무 계정 ID와 산출물 버킷 이름 |
| vpc_id | string | 필수 | 기존 VPC ID |
| vpc_cidr | string | 필수 | 신규 서브넷 포함/충돌 검사의 기준 대역 |
| existing_subnet_cidrs | list(string) | 필수 | 충돌을 금지할 모든 기존 서브넷 |
| management_security_group_id | string | 필수 | 기존 EIC SG. 새 역할 SG로 향하는 SSH rule만 추가 |
| management_endpoint_id | string | 필수 | 배포 도구가 사용할 기존 EIC ID |
| instance_az_id | string | 필수 | 앱 EC2/DB primary/NAT를 둘 물리 AZ ID |
| workload_subnets | map(object) | 필수 | 두 기존 workload 서브넷의 ID/RT/실제 AZ 이름 |
| config | object | `{}` 내부 기본값 사용 | 아래 앱 설정 |
| internet_firewall_endpoints | map(string) | `{}` | 물리 AZ ID별 READY Secondary Endpoint ID. 비어 있으면 기존 public IGW default 유지 |
| remote_service_cidrs | set(string) | `[]` | frontend의 명시적 원격 peer IPv4 CIDR, default route 금지 |
| remote_service_ports | set(number) | `[3000]` | peer와 양방향으로 허용할 TCP 서비스 포트 |

`internet_firewall_endpoints`의 각 키는 public subnet의 물리 AZ ID이고 값은 같은 AZ의 실제 Secondary Endpoint ID다. 두 AZ를 모두 제공해야 한다. 다른 VPC의 Primary Endpoint ID를 넣지 않는다.

### workload_subnets 내부 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| map key | string | 필수 | 기존 두 물리 AZ ID |
| id | string | 필수 | 기존 사설 workload subnet ID |
| route_table_id | string | 필수 | 기존 workload RT. 선택한 AZ에만 NAT 경로 추가 |
| az_name | string | 필수 | 해당 계정의 AZ 이름. RDS primary 배치에 사용 |

### config 내부 속성

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

map(string)의 각 키는 기존 두 AZ ID, 값은 겹치지 않는 정규화된 VPC 내부 /24 CIDR이다. 인증서 ARN/공개 hostname은 쌍으로 지정한다. 실제 입력에서 AMI를 고정해 원치 않는 후속 교체를 피한다.

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| deployment | 아래 비밀값 없는 배포/관리 인벤토리. 비밀번호를 반환하지 않음 |
| inspection_network | 기존 internet_gateway_id와 public_subnets(AZ별 id/cidr/route_table_id). runtime 자원과 분리한 인터넷 검사 연결 정보 |

### deployment 내부 속성

| 속성명 | 역할 |
| --- | --- |
| name / region / account_id / vpc_id | 리소스 식별 및 운영자의 대상 계정 확인 |
| management_endpoint_id | EIC 접속에 사용할 endpoint |
| url | HTTP ALB DNS 또는 인증서에 해당하는 지정 HTTPS hostname |
| artifact_bucket | 전용 비공개/암호화/버전 관리 S3 버킷 |
| instance_architecture | 검증한 AMI 아키텍처, x86_64 |
| deployment_mode | 실제 선택한 native/compose 실행 모드 |
| service_hosts.frontend / service_hosts.backend | 각 서비스가 실행되는 instances 키. compose는 둘 다 frontend |
| instances.frontend / instances.backend | 실제 존재하는 EC2만 포함. 최종 compose는 frontend 한 개. 각 id/private_ip/az/instance_type/public_ip_enabled |
| database.identifier / endpoint / port / name / instance_class | DB 식별자와 비밀값 아닌 연결 설정 |
| database.master_secret_arn / app_secret_arn / app_user | 운영자 초기화 및 BE 역할의 별도 credential 식별자 |
| database.publicly_accessible / encrypted | DB 공개/암호화 설정의 Plan 값, 실제 API 검증과 구분 |
| logs.frontend / logs.backend | 역할별 CloudWatch 로그 그룹 |

## 운영

전체 root에서만 Plan/Apply한다. [배포 도구](../../../../deploy/workload/README.md)와 [승인/검증 기록](../../../../docs/ai-dlc/aws-sample-app.md)을 따른다. 선택 모드 해제는 전체 앱 자원 제거를 의미하며 버전 rollback에 사용하지 않는다. RDS 삭제 보호와 최종 snapshot을 유지한다. 전체 제거는 데이터/의존성을 별도로 검토한다.
