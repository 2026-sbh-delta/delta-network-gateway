# AWS 스테이징 기반

파이프라인 스테이징의 AWS 기반을 만든다. 기존 사설 VPC 안에 ECS Fargate 서비스 1개와 내부 ALB를 둔다. 이미지는 [스테이징 도구](../../services/delta-staging/README.md)가 실행마다 새 태스크 정의 개정판으로 바꾼다. 이 root는 기반만 소유하며 서비스의 `task_definition`·`desired_count`는 `ignore_changes`로 넘긴다.

서비스는 태스크 0개로 시작한다. ALB는 `internal`이고 리스너 80은 `scanner_cidrs`(RFC1918만)나 `scanner_security_group_ids`에서만 받는다. 태스크는 ALB 보안 그룹에서만 컨테이너 포트를 받고 공인 IP를 받지 않는다. 인터넷 출구가 없는 Subnet을 위해 ECR API/DKR·CloudWatch Logs 인터페이스 엔드포인트와 S3 게이트웨이 엔드포인트를 기본으로 만든다. 태스크 역할에는 정책이 없다.

DAST는 앱 자체를 점검하므로 이 경로는 WAF를 거치지 않는다. WAF 동등성 시험은 이전 대상 환경의 실제 진입 경로로 따로 한다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `name` | `string` | `delta` | 리소스 이름 접두사 |
| `environment` | `string` | `lab` | 이름과 환경 태그 |
| `region` | `string` | `ap-northeast-2` | 스테이징 Region |
| `account_id` | `string` | 필수 | Provider가 허용할 스테이징 계정 ID |
| `vpc_id` | `string` | 필수 | 기존 사설 VPC |
| `subnet_ids` | `set(string)` | 필수 | 서로 다른 AZ의 사설 Subnet 2개 이상. 공인 IP 자동 할당이 없어야 함 |
| `route_table_ids` | `set(string)` | `[]` | S3 게이트웨이 엔드포인트를 붙일 `subnet_ids`의 Route Table. 엔드포인트 생성 시 필수 |
| `create_vpc_endpoints` | `bool` | `true` | ECR API/DKR·Logs 인터페이스와 S3 게이트웨이 엔드포인트 생성 |
| `scanner_cidrs` | `set(string)` | `[]` | 내부 ALB에 접근할 DAST 실행기 사설 CIDR. 정규화된 RFC1918만 |
| `scanner_security_group_ids` | `set(string)` | `[]` | 내부 ALB에 접근할 DAST 실행기 보안 그룹. `scanner_cidrs`와 합쳐 1개 이상 필수 |
| `container_name` | `string` | `app` | 도구가 이미지를 바꿀 컨테이너 이름 |
| `container_port` | `number` | `8080` | 앱 HTTP 포트(1024 이상) |
| `health_check_path` | `string` | `/` | 대상 그룹 헬스 체크 경로. 도구는 healthy 뒤에 ready로 알린다 |
| `cpu` | `number` | `256` | Fargate CPU 단위 |
| `memory` | `number` | `512` | Fargate 메모리 MiB |
| `cpu_architecture` | `string` | `X86_64` | 이미지 아키텍처. `X86_64` 또는 `ARM64` |
| `bootstrap_image` | `string` | `public.ecr.aws/docker/library/busybox:stable` | 첫 개정판 자리표시 이미지. 태스크 0개라 받지 않음 |
| `allowed_image_prefixes` | `list(string)` | `[]` | 도구가 받을 이미지 저장소. 비우면 같은 계정·Region의 ECR |
| `log_retention_days` | `number` | `7` | 태스크 로그 보존 일수 |
| `tags` | `map(string)` | `{}` | 공통 태그 추가/재정의 |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| `region` | 스테이징 Region |
| `cluster_name` | ECS 클러스터 이름 |
| `service_name` | 도구가 갱신할 ECS 서비스 |
| `task_family` | 도구가 개정할 태스크 정의 family |
| `target_group_arn` | 도구가 healthy를 기다릴 대상 그룹 |
| `internal_url` | 스캐너만 닿는 내부 주소 |
| `alb_security_group_id` | 내부 ALB 보안 그룹 |
| `log_group_name` | 태스크 로그 그룹 |
| `tool_config` | 스테이징 도구 설정 파일에 그대로 넣는 객체 |

`tool_config`는 `cloud`, `region`, `account_id`, `cluster`, `service`, `task_family`, `container_name`, `target_group_arn`, `url`, `allowed_image_prefixes`를 담는다.

## 준비와 검증

실제 tfvars, backend, state와 Plan은 gitignore 대상이다. 예시 파일을 실제 파일 위에 복사하지 않는다. VPC와 Subnet은 기존 Workload 네트워크와 맞춰 정하며, 이 root는 VPC·Route·TGW를 만들지 않는다.

```bash
TERRAFORM_BIN=terraform terraform/staging-aws/scripts/validate.sh
```

검증 스크립트는 `fmt -check`, `validate`, mock Provider `terraform test`(전부 `command = plan`)만 실행한다. 실제 Plan/Apply와 backend 초기화는 하지 않는다.
