# GCP 스테이징 기반

파이프라인 스테이징의 GCP 기반을 만든다. AWS 스테이징([staging-aws](../staging-aws/README.md))과 같은 역할이다. Cloud Run 서비스 1개와 외부 Application Load Balancer, 그 앞의 Cloud Armor를 둔다. 이미지는 [스테이징 도구](../../services/delta-staging/README.md)가 실행마다 새 개정판으로 바꾼다. 이 root는 기반만 소유하며 컨테이너의 `image`와 개정판 라벨은 `ignore_changes`로 넘긴다. 환경 변수(`SERVED_BY=gcp`, `APP_ENV=staging`, `DATABASE_URL` 비밀값 참조)는 이 root가 정한다.

| AWS 스테이징 | GCP 스테이징 |
| --- | --- |
| ECS Fargate 서비스(태스크 1개) | Cloud Run 서비스(개정판당 인스턴스 최대 1개, 최소 1개 유지) |
| 공개 ALB | 전역 외부 Application LB(HTTP 80) + 서버리스 NEG |
| ALB 보안 그룹의 스캐너 /32 | Cloud Armor 정책의 스캐너 /32(기본 규칙은 전부 거부) |
| ECR | Artifact Registry(태그 변경 불가, 최근 20개 보존) |
| 정책 없는 태스크 역할 | 역할 없는 런타임 서비스 계정 |
| AWS 전용 스테이징 PostgreSQL(같은 계약, GCP 뒤에 적용) | GCP 전용 스테이징 Cloud SQL PostgreSQL 16 |

Cloud Run은 LB를 거친 요청만 받는다(`INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`). 스캐너 브라우저가 AWS와 같은 평범한 요청을 보내도록 호출자 IAM 검사는 끈다(`invoker_iam_disabled`). 접근 통제는 Cloud Armor가 한다.

- 동적 모드(기본, `dynamic_scanner_access = true`): 스테이징 도구가 배포 때 DAST 실행기의 **현재 공인 IP /32 하나만** 우선순위 1000 규칙으로 넣고, 다음 실행에서 덮어쓰고, 중지할 때 지운다.
- 고정 대역: `scanner_public_cidrs`(/24 이하 공인 대역, 10개까지)를 우선순위 1001 규칙으로 둔다. `0.0.0.0/0`은 받지 않는다.

교체 배포는 새 개정판을 이전 개정판 옆에 띄운 뒤 준비되면 트래픽을 100% 옮긴다. 도구는 새 개정판이 트래픽 100%를 받을 때 `ready`로 알린다.

## 스테이징 DB

- **버전:** PostgreSQL 16(`POSTGRES_16`). 개발(로컬 Compose)·스테이징 공통 기준이다. 운영 DB 버전은 아직 정하지 않았다.
- **구성:** Cloud SQL 가장 작은 구성(db-f1-micro, 단일 영역, HDD 10GiB, 백업·삭제 보호 없음). 시연 뒤 사용자가 지시하면 삭제한다. 허용 네트워크 없이 Cloud Run 내장 연결(`/cloudsql` 소켓)로만 접속하고, 접속 주소는 Secret Manager의 `DATABASE_URL`로 넘긴다.
- **분리:** 이 DB는 GCP 스테이징 앱만 쓴다. AWS 스테이징 DB·운영 DB와 동기화하지 않는다. 같은 이미지·마이그레이션으로 각각 준비한다.
- **초기화:** 점검 회차마다 스테이징 도구가 배포 직전에 `<prefix>-db-reset` Job을 **배포할 이미지로** 실행한다. Job은 앱 저장소의 `python -m app.reset`(스키마 적용 → 게시글·댓글 비우기 → 남은 행 0 확인)을 `APP_ENV=staging`으로 돌린다. 실패하면 새 개정판을 띄우지 않는다.
- **운영 보호:** 운영에는 이 Job을 만들지 않는다. 스테이징 도구는 `environment = "staging"` 대상에만 초기화 Job을 허용하고, `app.reset`은 `APP_ENV=staging`이 아니면 거부한다.
- **점검 보호:** 스테이징 도구가 배포 때 실행 ID로 점유를 잡는다. 점유 중에는 다른 실행의 배포(=초기화)를 409로 거부한다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `name` | `string` | `delta` | 리소스 이름 접두사(10자 이하) |
| `environment` | `string` | `lab` | 이름과 환경 라벨(8자 이하) |
| `project_id` | `string` | 필수 | 스테이징 프로젝트 |
| `region` | `string` | `asia-northeast3` | Cloud Run·NEG·Artifact Registry 리전 |
| `enable_services` | `bool` | `true` | Cloud Run, Artifact Registry, Compute, IAM API 켜기. destroy 때 끄지 않음 |
| `dynamic_scanner_access` | `bool` | `true` | 도구가 배포 때 실행기 /32를 Cloud Armor에 허용 |
| `scanner_public_cidrs` | `set(string)` | `[]` | 미리 열 실행기 고정 공인 대역 |
| `container_name` | `string` | `app` | 도구가 이미지를 바꿀 컨테이너 이름 |
| `container_port` | `number` | `8080` | 앱 HTTP 포트. Cloud Run이 `PORT`로도 넘김 |
| `health_check_path` | `string` | `/` | 시작 검사 경로이자 도구가 LB로 응답을 확인하는 경로 |
| `cpu` | `string` | `1` | CPU 한도 |
| `memory` | `string` | `512Mi` | 메모리 한도 |
| `min_instances` | `number` | `1` | 상시 유지 인스턴스. 1이면 교체 때 콜드 스타트가 없음 |
| `bootstrap_image` | `string` | `us-docker.pkg.dev/cloudrun/container/hello` | 첫 개정판 자리표시 이미지 |
| `create_repository` | `bool` | `true` | 스테이징 이미지 Artifact Registry 저장소 생성 |
| `allowed_image_prefixes` | `list(string)` | `[]` | 도구가 받을 이미지 저장소. 비우면 이 프로젝트의 스테이징 저장소 |
| `labels` | `map(string)` | `{}` | 공통 라벨 추가/재정의 |
| `database_enabled` | `bool` | `true` | 스테이징 Cloud SQL·DB 비밀값·초기화 Job 생성 |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| `region` | 스테이징 리전 |
| `service_name` | 도구가 갱신할 Cloud Run 서비스 |
| `url` | 스캐너 출처만 닿는 스테이징 주소 |
| `repository_url` | 스테이징 이미지 저장소 접두사. 만들지 않으면 `null` |
| `security_policy` | 스테이징 앞의 Cloud Armor 정책 |
| `tool_config` | 스테이징 도구 설정 파일에 그대로 넣는 객체. 주소 대신 전역 IP 이름(`address`)만 담아 다시 apply해도 바뀌지 않는다. `environment = "staging"`과 초기화 Job 이름(`reset_job`)을 포함한다 |

## 준비와 검증

환경은 시연 전에 한 번 apply해 두고, 파이프라인 실행 중에는 이미지만 바꾼다. 실제 tfvars, backend, state와 Plan은 gitignore 대상이다. state는 GCS 버킷에 둔다([backend.hcl.example](backend.hcl.example)).

```bash
TERRAFORM_BIN=terraform terraform/staging-gcp/scripts/validate.sh
```

검증 스크립트는 `fmt -check`, `validate`, mock Provider `terraform test`(전부 `command = plan`)만 실행한다. 실제 Plan/Apply와 backend 초기화는 하지 않는다.

같은 이미지를 AWS와 GCP에 올리면 digest가 같다. 빌드 뒤 ECR과 Artifact Registry 양쪽에 같은 매니페스트를 푸시한다(아키텍처별 단일 매니페스트, AWS README 참고).
