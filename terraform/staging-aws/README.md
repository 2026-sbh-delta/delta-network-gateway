# AWS 스테이징 기반

파이프라인 스테이징의 AWS 기반을 만든다. 기존 VPC 안에 ECS Fargate 서비스 1개와 ALB를 둔다. 이미지는 [스테이징 도구](../../services/delta-staging/README.md)가 실행마다 새 태스크 정의 개정판으로 바꾼다. 이 root는 기반만 소유하며 서비스의 `task_definition`·`desired_count`는 `ignore_changes`로 넘긴다.

서비스는 태스크 0개로 시작한다. ALB는 두 모드가 있다.

- 내부(기본): 리스너 80은 `scanner_cidrs`(RFC1918만)나 `scanner_security_group_ids`에서만 받는다. DAST 실행기가 같은 네트워크에 있어야 한다.
- 공개(`alb_internet_facing = true`): ALB만 인터넷용이고, 보안 그룹은 처음에 닫혀 있다. 스테이징 도구가 배포 때 DAST 실행기의 **현재 공인 IP /32 하나만** 허용 규칙(태그 `DeltaManaged=scanner`)으로 넣고, 이전 IP는 지우고, 중지할 때 닫는다. 고정 출구 IP가 있으면 `scanner_public_cidrs`(/24 이하 공인 대역)로 미리 열 수 있다. `0.0.0.0/0`은 받지 않는다. DAST가 어느 클라우드에서 돌든 URL만 받으면 되므로 클라우드별 실행기를 두지 않는다.

 태스크는 ALB 보안 그룹에서만 컨테이너 포트를 받고 공인 IP를 받지 않는다. 인터넷 출구가 없는 Subnet을 위해 ECR API/DKR·CloudWatch Logs 인터페이스 엔드포인트와 S3 게이트웨이 엔드포인트를 기본으로 만든다. dual 모드에는 Secrets Manager interface endpoint도 추가한다. 앱/reset task 역할은 API 정책이 없으며, 서로 다른 execution role이 각자의 secret만 읽는다.

DAST는 앱 자체를 점검하므로 이 경로는 WAF를 거치지 않는다. WAF 동등성 시험은 이전 대상 환경의 실제 진입 경로로 따로 한다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `name` | `string` | `delta` | 리소스 이름 접두사 |
| `environment` | `string` | `lab` | 이름과 환경 태그 |
| `region` | `string` | `ap-northeast-2` | 스테이징 Region |
| `account_id` | `string` | 필수 | Provider가 허용할 스테이징 계정 ID |
| `vpc_id` | `string` | 필수 | 기존 사설 VPC |
| `subnet_ids` | `set(string)` | 필수 | `vpc_id` 안의 서로 다른 AZ Subnet 2개 이상. 태스크는 Subnet 기본값과 상관없이 공인 IP를 받지 않음 |
| `route_table_ids` | `set(string)` | `[]` | S3 게이트웨이 엔드포인트를 붙일 `subnet_ids`의 Route Table. 엔드포인트 생성 시 필수 |
| `create_vpc_endpoints` | `bool` | `true` | ECR API/DKR·Logs 인터페이스와 S3 gateway 생성, dual DB 모드에 SM interface 추가 |
| `endpoint_client_security_group_ids` | `set(string)` | `[]` | 같은 VPC에서 ECR·Logs를 쓰는 다른 호스트(툴링 호스트 등) 보안 그룹. 엔드포인트의 private DNS가 VPC 전체에 적용되므로 허용해야 연결된다 |
| `scanner_cidrs` | `set(string)` | `[]` | 내부 ALB에 접근할 DAST 실행기 사설 CIDR. 정규화된 RFC1918만 |
| `scanner_security_group_ids` | `set(string)` | `[]` | ALB에 접근할 DAST 실행기 보안 그룹. 내부 모드에서는 `scanner_cidrs`와 합쳐 1개 이상 필수 |
| `alb_internet_facing` | `bool` | `false` | ALB를 인터넷용으로 둔다. 실행기 IP는 도구가 배포 때 /32로 허용 |
| `scanner_public_cidrs` | `set(string)` | `[]` | 공개 모드에서 미리 열 실행기 고정 공인 대역(/24 이하). 사설·전체 대역 거부 |
| `container_name` | `string` | `app` | 단일 컨테이너 이름. dual 모드에서는 `frontend`로 고정 |
| `container_port` | `number` | `8080` | 단일 컨테이너 포트. dual 모드에서는 FE 3000으로 고정 |
| `health_check_path` | `string` | `/` | 대상 그룹 헬스 체크 경로(5초 간격, 2회 성공). 도구는 healthy 뒤에 ready로 알린다 |
| `cpu` | `number` | `256` | Fargate CPU 단위 |
| `memory` | `number` | `512` | Fargate 메모리 MiB |
| `cpu_architecture` | `string` | `X86_64` | 이미지 아키텍처. `X86_64` 또는 `ARM64` |
| `create_ecr_repository` | `bool` | `true` | 빌드 단계가 스테이징 이미지를 올릴 ECR 저장소 생성(태그 변경 불가, 푸시 시 스캔, 최근 20개 보존) |
| `bootstrap_image` | `string` | `public.ecr.aws/docker/library/busybox:stable` | 첫 개정판 자리표시 이미지. 태스크 0개라 받지 않음 |
| `allowed_image_prefixes` | `list(string)` | `[]` | 도구가 받을 이미지 저장소. 비우면 같은 계정·Region의 ECR |
| `log_retention_days` | `number` | `7` | 태스크 로그 보존 일수 |
| `tags` | `map(string)` | `{}` | 공통 태그 추가/재정의 |

| `dual_container` | `bool` | `false` | 단일 `app:8080` 호환. `true`면 `frontend:3000`·`backend:8080` 및 Node reset 사용 |
| `bootstrap_backend_image` | `string` | `public.ecr.aws/docker/library/busybox:stable` | 초기 BE 이미지. dual 배포 전 `/app/scripts`·migration이 포함된 실제 digest로 교체 필수 |
| `schema_prepared` | `bool` | `false` | provider의 최초 reset 순차 실행. 실제 스키마·역할 준비 검증 후 `true`로 병렬 실행 허용 |
| `existing_nat_egress` | `bool` | `false` | dual에서 endpoint를 끌 경우 기존 NAT·HTTPS route 존재 확인. 443 egress만 추가하며 NAT는 생성하지 않음 |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| `region` | 스테이징 Region |
| `cluster_name` | ECS 클러스터 이름 |
| `service_name` | 도구가 갱신할 ECS 서비스 |
| `task_family` | 도구가 개정할 태스크 정의 family |
| `target_group_arn` | 도구가 healthy를 기다릴 대상 그룹 |
| `internal_url` | 스캐너 출처만 닿는 스테이징 주소(내부 모드면 사설 DNS) |
| `ecr_repository_url` | 스테이징 이미지 ECR 저장소 주소. 만들지 않으면 `null` |
| `alb_security_group_id` | 내부 ALB 보안 그룹 |
| `log_group_name` | 태스크 로그 그룹 |
| `tool_config` | 스테이징 도구 설정 파일에 그대로 넣는 객체 |
| `operator_handoff` | 비밀번호 없이 DB·master/runtime/reset secret 참조·실행 주체·로그/IAM 준비 정보를 인계하는 별도 객체 |

`tool_config`는 `cloud`, `region`, `account_id`, `cluster`, `service`, `task_family`, `container_name`, `target_group_arn`, `alb_security_group_id`, `dynamic_scanner_access`, `allowed_image_prefixes`와 아래 계약 필드를 담는다. `url`은 넣지 않고 provider가 ALB에서 조회한다.

| 내부 필드 | 값·의미 |
| --- | --- |
| `environment` | 항상 `staging` (`environment` 입력은 이름·태그용) |
| `backend_container_name` | dual일 때 `backend`, 단일 모드 `null` |
| `reset_task_family` | dual일 때 별도 `<prefix>-db-reset`, 단일 모드 `null` |
| `reset_container_name` | `reset` |
| `expected_database_major` | `18`, 실제 DB 버전 관측을 대신하지 않음 |
| `database_name` | `board_staging` |
| `schema_prepared` | 입력 그대로. 기본 `false`, 첫 reset 성공 뒤 앱 시작; 준비 확인 후 `true`가 병렬 실행 opt-in |

`operator_handoff`는 dual일 때만 객체다. DB host/name, 마스터·runtime·reset secret ARN, runtime/reset execution/task role ARN, cluster/service/reset task ARN과 `reset/reset/*` 로그 stream ARN을 제공한다. 도구 대상 JSON에 병합하지 않는다.

## 준비와 검증

환경은 시연 전에 한 번 apply해 두고, 파이프라인 실행 중에는 이미지만 바꾼다. 실제 tfvars, backend, state와 Plan은 gitignore 대상이다. 예시 파일을 실제 파일 위에 복사하지 않는다. VPC와 Subnet은 기존 Workload 네트워크와 맞춰 정하며, 이 root는 VPC·Route·TGW를 만들지 않는다.

```bash
TERRAFORM_BIN=terraform terraform/staging-aws/scripts/validate.sh
```

검증 스크립트는 `.tf`·lock·기존 tests만 임시 디렉터리에 복사한 뒤 그 안에서 `fmt -check`, `validate`, mock Provider `terraform test`(전부 `command = plan`)만 실행한다. 실제 tfvars·plan·state는 복사/로드하지 않는다. 실제 Plan/Apply와 backend 초기화는 하지 않는다.

## dual DB 구성과 적용 경계

`dual_container=true`면 같은 `awsvpc` 태스크에 FE·BE를 함께 두고, ALB/SG는 FE3000에만 연결한다. FE는 `API_HOST=127.0.0.1`, `API_PORT=8080`으로 BE를 호출하며, BE `/healthz` 성공 뒤 FE를 시작한다. 두 컨테이너는 `CLOUD_PROVIDER=aws`, `APP_ENV=staging`을 받는다. 서비스 태스크 수는 최초 0이며 도구가 점검 때 1로 올린다.

기존 VPC/subnet에 별도 RDS PostgreSQL18 `board_staging`을 만든다. `db.t4g.micro`, single AZ, gp3 20GiB 암호화, 공인 접근 없음, 강제 TLS, 백업 1일이며 HA가 아니다. 새 VPC/VM/NAT·운영 DB는 만들지 않는다. Region의 PG18·해당 클래스 가용성은 실제 계획 전 별도 확인해야 한다.

Task ENI→endpoint SG의 443 egress/ingress로 Secrets Manager를 사용한다. 기본 경로는 기존 ECR/Logs endpoint와 S3 gateway에 추가한 SM endpoint다. endpoint를 끄려면 `existing_nat_egress=true`로 기존 NAT route를 확인해야 하며, 해당 모드만 task 443 egress를 추가한다. RDS5432는 staging service SG 및 명시한 기존 provisioning SG에서만 허용한다. reset task는 provider가 service의 사설 network configuration을 재사용한다.

**DB는 사람이 준비하는 단계가 없다(11/6 단순화).** Terraform이 RDS 사용자 `board_app`과 비밀번호를 만들고, 접속 비밀값 JSON(`DATABASE_URL`, `PGHOST`, `PGPASSWORD`, `PGSSLROOTCERT_PEM`)을 직접 채운다. CA는 AWS 공개 서울 리전 RDS 신뢰 묶음(`rds-ca-ap-northeast-2.pem`)이다. `NODE_OPTIONS` prelude가 `/tmp/rds-ca.pem`에 로그 없이 저장하고 TLS hostname 검증은 유지한다. FE에는 DB secret을 주입하지 않는다.

reset family는 같은 BE digest의 `node scripts/reset-db.mjs`를 `/app`에서 실행한다. `APP_RESET_DATABASE=PGDATABASE=board_staging`이며 앱과 같은 실행 역할·DB 사용자를 쓴다. 스키마 생성과 비우기를 한 번에 하므로 첫 배포 전 별도 준비가 없다.
