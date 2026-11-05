# GCP 스테이징 기반

파이프라인 스테이징의 GCP 기반을 만든다. AWS 스테이징([staging-aws](../staging-aws/README.md))과 같은 역할이다. Cloud Run 서비스 1개와 외부 Application Load Balancer, 그 앞의 Cloud Armor를 둔다. 이미지는 [스테이징 도구](../../services/delta-staging/README.md)가 실행마다 새 개정판으로 바꾼다. 이 root는 기반만 소유하며 컨테이너의 `image`와 개정판 라벨은 `ignore_changes`로 넘긴다. 환경 변수(`SERVED_BY=gcp`, `APP_ENV=staging`, `DATABASE_URL` 비밀값 참조)는 이 root가 정한다.

| AWS 스테이징 | GCP 스테이징 |
| --- | --- |
| ECS Fargate 서비스(태스크 1개) | Cloud Run 서비스(개정판당 인스턴스 최대 1개, 최소 1개 유지) |
| 공개 ALB | 전역 외부 Application LB(HTTP 80) + 서버리스 NEG |
| ALB 보안 그룹의 스캐너 /32 | Cloud Armor 정책의 스캐너 /32(기본 규칙은 전부 거부) |
| ECR | Artifact Registry(태그 변경 불가, 최근 20개 보존) |
| 앱/reset execution role별 secret 접근 | 앱/reset 서비스 계정별 secret·SQL Client 접근 |
| dual일 때 별도 RDS PostgreSQL18 | 별도 Cloud SQL PostgreSQL18 |

Cloud Run은 LB를 거친 요청만 받는다(`INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`). 스캐너 브라우저가 AWS와 같은 평범한 요청을 보내도록 호출자 IAM 검사는 끈다(`invoker_iam_disabled`). 접근 통제는 Cloud Armor가 한다.

- 동적 모드(기본, `dynamic_scanner_access = true`): 스테이징 도구가 배포 때 DAST 실행기의 **현재 공인 IP /32 하나만** 우선순위 1000 규칙으로 넣고, 다음 실행에서 덮어쓰고, 중지할 때 지운다.
- 고정 대역: `scanner_public_cidrs`(/24 이하 공인 대역, 10개까지)를 우선순위 1001 규칙으로 둔다. `0.0.0.0/0`은 받지 않는다.

교체 배포는 새 개정판을 이전 개정판 옆에 띄운 뒤 준비되면 트래픽을 100% 옮긴다. 도구는 새 개정판이 트래픽 100%를 받을 때 `ready`로 알린다.

## 스테이징 DB와 dual 구성

스테이징 전용 PostgreSQL18(`POSTGRES_18`), `board_staging`을 사용한다. Enterprise/db-f1-micro, single zone, HDD 10GiB, 백업·삭제 보호 없음이다. AWS·운영 DB와 공유하거나 동기화하지 않는다.

`dual_container=false`가 기본이며 기존 `app:8080` 하나와 legacy Flask `DATABASE_URL` secret 참조·`python -m app.reset` 진입점을 유지한다. `dual_container=true`면 FE ingress 3000, BE sidecar 8080, `cloud-sql-proxy` sidecar를 같은 revision에 둔다. 이름은 `frontend`/`backend`로 고정한다. FE→BE는 `127.0.0.1:8080`, BE→Proxy는 `PGHOST=127.0.0.1`, `PGPORT=5432`다. Proxy startup→BE `/healthz`→FE startup 순서이며 두 앱 컨테이너에 `CLOUD_PROVIDER=gcp`, `APP_ENV=staging`을 전달한다. BE/Proxy에는 ingress `ports`를 선언하지 않는다. dual에서 DB 비활성은 거부한다.

Node는 `STORAGE_BACKEND=postgres`, `PGDATABASE=board_staging`, `PGUSER=board_app`과 secret 참조인 `PGPASSWORD`를 받는다. loopback 구간만 `PGSSLMODE=disable`이며 Proxy→Cloud SQL TLS/IAM은 유지한다. legacy 모드만 내장 `/cloudsql` 소켓을 마운트한다. SQL authorized networks는 비어 있다. Node가 Unix socket을 직접 지원한다고 가정하지 않는다.

기존 Cloud Run 관리형 egress를 사용하며 VPC/VM/connector/route를 만들지 않는다. Proxy는 SQL Admin API HTTPS443와 DB TLS3307 경로가 필요하다. 기존 환경이 VPC `all-traffic` egress로 제한된 경우 API용 PGA/PSC route 및 DB3307 경로를 네트워크 담당이 먼저 확인해야 한다. 이 root는 해당 사설 egress 경로를 자동 구성하지 않는다. [공식 Proxy 연결 조건](https://docs.cloud.google.com/sql/docs/postgres/sql-proxy).

reset Job은 별도 서비스 계정·별도 DB secret을 사용한다. dual에서는 `/app`의 `node scripts/reset-db.mjs`와 BE digest를 사용하며 task 1개·병렬도 1·재시도 0이다. `APP_RESET_DATABASE=PGDATABASE=board_staging`이다. Job은 Proxy를 먼저 기동하고 Node 종료 시 `NODE_OPTIONS` hook으로 loopback `/quitquitquit`를 POST한다. 실패한 reset의 종료 코드는 보존하고 Proxy 종료 실패도 실패 처리한다. [Proxy 종료 API](https://github.com/GoogleCloudPlatform/cloud-sql-proxy/blob/main/docs/cmd/cloud-sql-proxy.md).

**DB는 사람이 준비하는 단계가 없다(11/6 단순화).** Terraform이 DB 사용자 `board_app` 하나와 비밀번호를 만들고, 접속 비밀값(`-database-url`, `-runtime-password`)을 직접 채운다. BE와 초기화 Job이 같은 사용자·서비스 계정을 쓴다. 초기화 Job(`node scripts/reset-db.mjs`)이 스키마 생성(`001-notes.sql`)과 비우기를 한 번에 한다. 배포마다 비우는 시험 데이터만 담는 DB라 역할 분리는 두지 않는다.

기존 `POSTGRES_16`/`board`에서 이번 구성으로의 변경은 파괴적이다. 인스턴스 이름도 `<prefix>-db-pg18`로 바뀌므로 기존 Cloud SQL 교체와 DB·사용자·연결/secret 갱신이 필요하다. `create_before_destroy`만으로 데이터가 복사되지 않으며, 백업·복원/폐기 결정과 사용자 Apply 승인이 먼저다. 실제 plan/apply·Job 실행은 수행하지 않았다.

## 입력 속성

| 속성명 | 타입 | 기본값 | 역할 |
| --- | --- | --- | --- |
| `name` | `string` | `delta` | 리소스 이름 접두사(10자 이하) |
| `environment` | `string` | `lab` | 이름과 환경 라벨(8자 이하) |
| `project_id` | `string` | 필수 | 스테이징 프로젝트 |
| `region` | `string` | `asia-northeast3` | Cloud Run·NEG·Artifact Registry 리전 |
| `enable_services` | `bool` | `true` | Cloud Run·Artifact Registry·Compute·IAM·SQL Admin·Secret Manager·Logging API 켜기. destroy 때 끄지 않음 |
| `dynamic_scanner_access` | `bool` | `true` | 도구가 배포 때 실행기 /32를 Cloud Armor에 허용 |
| `scanner_public_cidrs` | `set(string)` | `[]` | 미리 열 실행기 고정 공인 대역 |
| `container_name` | `string` | `app` | 단일 컨테이너 이름. dual 모드에서는 `frontend`로 고정 |
| `container_port` | `number` | `8080` | 단일 ingress 포트. dual 모드에서는 FE 3000, BE 8080 고정 |
| `health_check_path` | `string` | `/` | 시작 검사 경로이자 도구가 LB로 응답을 확인하는 경로 |
| `cpu` | `string` | `1` | CPU 한도 |
| `memory` | `string` | `512Mi` | 메모리 한도 |
| `min_instances` | `number` | `1` | 상시 유지 인스턴스. 1이면 교체 때 콜드 스타트가 없음 |
| `bootstrap_image` | `string` | `us-docker.pkg.dev/cloudrun/container/hello` | 첫 개정판 자리표시 이미지 |
| `create_repository` | `bool` | `true` | 스테이징 이미지 Artifact Registry 저장소 생성 |
| `allowed_image_prefixes` | `list(string)` | `[]` | 도구가 받을 이미지 저장소. 비우면 이 프로젝트의 스테이징 저장소 |
| `labels` | `map(string)` | `{}` | 공통 라벨 추가/재정의 |
| `database_enabled` | `bool` | `true` | 스테이징 Cloud SQL·DB 비밀값·초기화 Job 생성 |

| `dual_container` | `bool` | `false` | 단일 `app:8080` 호환. `true`면 `frontend:3000`·`backend:8080` 및 Node reset 사용 |
| `bootstrap_backend_image` | `string` | `us-docker.pkg.dev/cloudrun/container/hello` | 초기 BE 이미지. dual 배포 전 `/app/scripts`·migration이 포함된 실제 digest로 교체 필수 |
| `schema_prepared` | `bool` | `false` | provider의 최초 reset 순차 실행. 실제 스키마·역할 준비 검증 후 `true`로 병렬 실행 허용 |
| `cloud_sql_proxy_image` | `string` | `gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.26.2` | 공식 Auth Proxy v2. 배포 시 승인 digest 고정 권장 |

## 출력 속성

| 속성명 | 역할 |
| --- | --- |
| `region` | 스테이징 리전 |
| `service_name` | 도구가 갱신할 Cloud Run 서비스 |
| `url` | 스캐너 출처만 닿는 스테이징 주소 |
| `repository_url` | 스테이징 이미지 저장소 접두사. 만들지 않으면 `null` |
| `security_policy` | 스테이징 앞의 Cloud Armor 정책 |
| `tool_config` | 스테이징 도구 설정 파일에 그대로 넣는 객체. 주소 대신 전역 IP 이름(`address`)만 담아 다시 apply해도 바뀌지 않는다. `environment = "staging"`과 초기화 Job 이름(`reset_job`)을 포함한다 |
| `operator_handoff` | 비밀번호 없이 DB·master/runtime/reset secret 참조·실행 주체·로그/IAM 준비 정보를 인계하는 별도 객체 |

`tool_config`는 기존 cloud/project/region/service/container, Cloud Armor·address·health path·허용 registry 설정에 아래 필드를 추가한다.

| 내부 필드 | 값·의미 |
| --- | --- |
| `environment` | 항상 `staging` (`environment` 입력은 이름·라벨용) |
| `backend_container_name` | dual일 때 `backend`, 단일 모드 `null` |
| `reset_container_name` | `reset` |
| `reset_job` | DB가 켜지면 `<prefix>-db-reset`, 비활성 시 `null` |
| `expected_database_major` | `18`, 실제 버전 관측을 대신하지 않음 |
| `database_name` | `board_staging` |
| `schema_prepared` | 기본 `false`: 첫 reset 성공 후 앱 배포. 준비 확인 뒤 `true`가 reset/배포 병렬 실행 opt-in |

`operator_handoff`는 DB가 켜질 때만 객체다. connection/name, runtime URL·password secret ID, runtime SA, reset Job resource 및 고정 log filter를 제공한다. 사람이 쓸 준비 단계는 없고 참고용이다. `tool_config`에 병합하지 않는다. 실행별 필터에는 provider가 정확한 execution name을 추가한다.

## 준비와 검증

초기 빈 secret 때문에 새 환경은 DB·계정·secret 기반 준비 → 별도 SQL provision·secret 버전 입력 → 서비스/Job 구성 순서가 필요하다. 사용자가 검토·승인한 기반 전용 단계별 적용 후 완성 root를 적용한다. 빈 secret 상태에서 전체 root를 한 번 적용해 서비스가 정상 기동한다고 주장하지 않는다. 이후 파이프라인 실행 중에는 이미지만 바꾼다. 실제 tfvars, backend, state와 Plan은 gitignore 대상이다. state는 GCS 버킷에 둔다([backend.hcl.example](backend.hcl.example)).

```bash
TERRAFORM_BIN=terraform terraform/staging-gcp/scripts/validate.sh
```

검증 스크립트는 `.tf`·lock·기존 tests만 임시 디렉터리에 복사한 뒤 그 안에서 `fmt -check`, `validate`, mock Provider `terraform test`(전부 `command = plan`)만 실행한다. 실제 tfvars·plan·state는 복사/로드하지 않는다. 실제 Plan/Apply와 backend 초기화는 하지 않는다.

같은 이미지를 AWS와 GCP에 올리면 digest가 같다. 빌드 뒤 ECR과 Artifact Registry 양쪽에 같은 매니페스트를 푸시한다(아키텍처별 단일 매니페스트, AWS README 참고).
