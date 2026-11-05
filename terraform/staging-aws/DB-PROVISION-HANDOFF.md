> **폐기(11/6).** 이 인계안(수동 SQL·역할 3종·빈 비밀값)은 쓰지 않는다. 스테이징 DB는 Terraform이 단일 사용자와 비밀값까지 만든다. 각 root의 README를 따른다.

# DB 초기 준비와 delegated role 인계

2026-11-06 · `tracecompose-delivery-20261106` · IMPLEMENTATION_ONLY / pre_gate REVISED.
이 문서는 실행 전 인계다. SQL·비밀값 발급·클라우드 적용·초기화는 실행하지 않았다. `onboarding-aws`는 수정하지 않는다.

## 최초 준비 담당과 실행 위치

DB 운영자가 별도 관리자 자격으로 1회 준비한다. 앱/reset 역할에 마스터 자격을 주지 않는다. 새 VM을 만들지 않고 기존 승인된 tooling 호스트에서 동일한 BE 이미지를 실행한다. 해당 호스트에 컨테이너 실행과 승인된 secret 주입 수단이 있어야 한다.

- AWS: 기존 VPC에서 RDS에 도달하는 호스트 SG를 `database_provisioner_security_group_ids`에 명시한다. root는 그 SG↔RDS5432만 허용한다. `operator_handoff.master_secret_arn`의 managed master 자격은 DB 운영자만 읽는다. trusted RDS CA bundle과 hostname 검증을 사용한다. host의 secret API443·이미지 pull 경로도 기존 경로에서 확인한다. 네트워크를 확인하지 않고 로컬 PC에서 사설 RDS에 접속할 수 있다고 가정하지 않는다.
- GCP: 기존 tooling 호스트에서 Cloud SQL Auth Proxy를 loopback5432로 실행한다. 운영자에게 해당 프로젝트의 Cloud SQL Client와 `operator_handoff.bootstrap_password_secret` 읽기만 별도로 준다. Node 컨테이너가 호스트의 proxy에 도달하도록 해당 호스트의 host network를 사용한다. `PGHOST=127.0.0.1`, `PGSSLMODE=disable`은 그 로컬 연결에만 사용하며 Proxy의 외부 TLS는 유지한다. SQL authorized networks를 열지 않는다.

각 클라우드에서 관리자 `PGUSER=staging_admin`, 실제 staging host/port 및 `PGDATABASE=board_staging`을 확인한다. 관리자·앱·reset 비밀번호는 서로 다르게 발급한다. 비밀은 argv·Terraform 변수·일반 로그에 넣지 않고 승인된 secret 주입과 stdin만 사용한다.

BE의 기존 `/app/scripts/provision-db.mjs`는 stdin의 `{appUser, appPassword}`를 받아 `board_app` 로그인 역할, `migrations/001-notes.sql` 스키마와 테이블, 앱 DML·sequence USAGE GRANT를 만든다. 동일한 BE 이미지를 `/app` 작업 디렉터리에서 `node scripts/provision-db.mjs`로 실행한다. `appPassword`는 최소 32자이며 이 JSON을 일반 로그에 남기지 않는다. `PG*` 연결 환경은 관리자 전용 실행에만 주입한다. 앱 서버 startup은 migration을 실행하지 않는다.

**현재 BE provision 스크립트는 reset 역할 생성·소유권 이전·기존 광범위 권한 회수까지 구현하지 않는다.** 아래 SQL 단계는 DB 운영자가 별도 관리자 세션에서 실행하거나, 명세 범위의 BE provision 담당이 기존 provision 역할에 통합·검증해야 한다. Terraform 리소스나 `schema_prepared` 설정만으로 이 단계가 완료된 것은 아니다.

## 실행해야 하는 SQL 권한 단계

앱 데이터/스키마를 초기 생성한 직후, 같은 `board_staging` 관리자 세션에서 수행할 작업이다. 아래에는 비밀번호를 넣지 않는다. `board_reset` 로그인 password 설정은 안전한 관리자 클라이언트의 비밀번호 입력 기능으로 별도 수행하고 reset 전용 secret에만 저장한다.

```sql
-- 신규 staging의 역할 생성. 이미 있으면 소유권·membership을 먼저 조사한다.
CREATE ROLE board_reset LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
REVOKE ALL ON DATABASE board_staging FROM PUBLIC;
GRANT CONNECT ON DATABASE board_staging TO board_app, board_reset;
GRANT CREATE ON DATABASE board_staging TO board_reset;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA delta_notes OWNER TO board_reset;
ALTER TABLE delta_notes.notes OWNER TO board_reset;
-- 테이블 소유권 이전과 함께 identity sequence 소유권도 확인한다.
REVOKE ALL ON SCHEMA delta_notes FROM PUBLIC;
REVOKE ALL ON TABLE delta_notes.notes FROM PUBLIC, board_app;
REVOKE ALL ON SEQUENCE delta_notes.notes_position_seq FROM PUBLIC, board_app;
GRANT USAGE ON SCHEMA delta_notes TO board_app;
GRANT SELECT, INSERT, DELETE ON TABLE delta_notes.notes TO board_app;
GRANT USAGE ON SEQUENCE delta_notes.notes_position_seq TO board_app;
```

실제 DB18, schema/table/identity sequence의 reset 소유권과 app DML 권한을 각각 조회한다. 기존 역할을 재사용한다면 superuser/CREATEROLE/다른 역할 membership이나 DB/schema CREATE 잔여 권한도 회수해야 한다. 신규 역할 생성 명령을 기존 역할에 무조건 재실행하지 않는다. `board_app`으로 SELECT/INSERT/DELETE는 성공하고 CREATE·TRUNCATE는 거부되는지 확인한다. `board_reset`은 migration·TRUNCATE·identity reset을 할 수 있지만 다른 DB/운영에 권한이 없어야 한다. 생성된 BE 이미지의 `/app/scripts/{provision-db,reset-db}.mjs`와 `/app/migrations/001-notes.sql` 포함 여부도 확인한다.

## Secret 게시와 첫 reset

| 대상 | 저장할 값 | 허용 주체 |
| --- | --- | --- |
| AWS runtime envelope | JSON `DATABASE_URL`, `PGHOST`, `PGPASSWORD`, `PGSSLROOTCERT_PEM`; `board_app` 자격·해당 RDS CA | runtime execution role만 읽기 |
| AWS reset envelope | 같은 JSON 키, `board_reset`의 다른 password/URL | reset execution role만 읽기 |
| GCP runtime URL/password | `-database-url`, `-runtime-password`에 `board_app` URL/password를 각각 게시 | runtime SA만 읽기 |
| GCP reset URL/password | `-reset-url`, `-reset-password`에 `board_reset` URL/password를 각각 게시 | reset SA만 읽기 |
| master/bootstrap | 관리자 전용 연결 자격 | 별도 DB 운영자만 읽기 |

Node용 GCP URL은 `postgresql://board_app:<encoded-password>@127.0.0.1:5432/board_staging` 형태이며 reset은 사용자도 `board_reset`으로 다르다. legacy Flask 모드의 두 URL은 각각 `/cloudsql/<connection-name>` Unix socket을 가리키도록 작성한다. Node는 URL을 직접 해석하지 않고 명시한 `PG*`를 사용한다. Node의 `PGPASSWORD`는 별도 password secret 참조다. AWS CA는 승인된 RDS trust store에서 받은 해당 리전의 공인 CA bundle(Secrets Manager JSON 크기 한도 안에서 사용)이며, 앱이 제공한 임의 인증서를 신뢰시키지 않는다.

GCP 새 환경은 서비스가 empty secret을 읽고 실패하지 않게 기반(DB·관리자·secret envelope·SA/IAM)만 먼저 준비하고 SQL/secret 버전을 게시한 후 나머지 서비스/Job을 구성한다. 실제 단계별 plan과 apply 범위는 사용자 검토·승인이 필요하다. AWS는 service desired_count=0으로 기반을 준비할 수 있다.

최초 target은 `schema_prepared=false`다. provider는 실제 reset 종료·BE digest·stdout의 DB18/남은 행0 관측에 성공한 다음 앱을 시작해야 한다. Terraform이 schema 준비를 관측하지 않는다. 준비 확인 후 `schema_prepared=true`는 reset과 앱 배포의 병렬 실행을 허용하는 명시적인 opt-in이다. SQL/secret 준비 완료 전 단순히 true로 바꾸지 않는다.

## AWS delegated role에 추가할 권한

아래 `${region}`, `${account_id}`, `${prefix}`는 이 root 입력으로 치환한다. 난수 suffix가 붙는 역할/secret과 실제 revision ARN은 **비밀이 아닌** `operator_handoff` 참조로 확정한다. 계정 전체 secret/role 접근은 주지 않는다. 이 목록은 기존 앱 배포·스캐너 권한에 추가되는 reset/provision 인계이며 기존 정책 전체를 대체하지 않는다.

| 작업 | Action | Resource와 제한 |
| --- | --- | --- |
| reset 실행 | `ecs:RunTask` | `arn:aws:ecs:${region}:${account_id}:task-definition/${prefix}-db-reset:*`; `ecs:cluster`는 `operator_handoff.cluster_arn` |
| reset 정의 조회 | `ecs:DescribeTaskDefinition` | 이 조회 API는 resource-level 제한을 지원하지 않아 `*`가 필요. IAM으로 family만 읽도록 제한된다고 주장하지 않음 |
| reset/app 정의 등록 | `ecs:RegisterTaskDefinition` | `arn:aws:ecs:${region}:${account_id}:task-definition/${prefix}:*`와 `${prefix}-db-reset:*` 두 family만. `aws:RequestTag/DeltaRunId` 존재 조건 |
| 등록 시 태그 | `ecs:TagResource` | 같은 두 task-definition family revision ARN, `DeltaRunId` request tag로 제한 |
| reset 상태 조회 | `ecs:DescribeTasks` | `arn:aws:ecs:${region}:${account_id}:task/${prefix}/*`; cluster 조건 |
| 서비스 네트워크 조회 | `ecs:DescribeServices` | `operator_handoff.service_arn` |
| 등록/실행 역할 전달 | `iam:PassRole` | `operator_handoff`의 runtime/reset execution/task role ARN **4개만**; `iam:PassedToService=ecs-tasks.amazonaws.com` |
| stdout 읽기 | `logs:GetLogEvents` | `arn:aws:logs:${region}:${account_id}:log-group:/delta/${prefix}:log-stream:reset/reset/*`만. 실행할 때 task ID로 stream을 확정하므로 `DescribeLogStreams`·전체 로그 읽기 불필요 |

최신 [ECS action/resource 표](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ecs.html)를 기준으로 등록·실행은 family ARN으로 제한하고, resource type이 없는 DescribeTaskDefinition 예외는 분리한다. 실제 delegated 정책 반영과 API 성공은 미검증이다.

execution role은 root가 자기 secret의 정확한 ARN 하나에 `secretsmanager:GetSecretValue`를 부여한다. 런타임은 reset secret·master secret 접근 권한이 없고 반대도 같다. AWS managed Secrets Manager key를 사용하므로 별도 CMK decrypt 정책은 만들지 않는다. 이후 CMK로 바꾸면 해당 키와 Secret ARN encryption context로 제한한 권한을 별도 검토해야 한다.

DB 운영자에게만 master secret ARN 읽기, runtime/reset envelope ARN에 `secretsmanager:PutSecretValue` 권한을 준다. 이 권한을 앱 배포 delegated role과 자동으로 합치지 않는다. `onboarding-aws` 정책 반영은 해당 담당자 소관이다.

## GCP delegated role에 추가할 권한

Job 리소스는 `projects/${project_id}/locations/${region}/jobs/${prefix}-db-reset`이다. 해당 Job의 get/update/run 및 실행/operation 상태 조회와 reset SA의 `iam.serviceAccounts.actAs`가 필요하다. 서비스 배포에는 별도 runtime SA의 actAs가 필요하다. API 권한은 `run.jobs.get`, `run.jobs.update`, `run.jobs.run`, `run.executions.get`, `run.operations.get`을 실제 호출 resource 범위에 맞춘다. secret 값 읽기를 배포 delegated role에 부여하지 않는다.

stdout 수집에 `logging.logEntries.list`가 필요하다. provider는 다음 AND 조건에 **이번 실행의 짧은 execution name**을 추가해 조회한다.

```text
resource.type="cloud_run_job"
resource.labels.job_name="${prefix}-db-reset"
resource.labels.location="${region}"
logName="projects/${project_id}/logs/run.googleapis.com%2Fstdout"
labels."run.googleapis.com/execution_name"="<이번 실행 name>"
```

**이 조회 filter는 IAM 권한 경계가 아니다.** 현재 provider는 `resourceNames=[projects/<project>]`로 호출하므로 `logging.logEntries.list` 부여 시 프로젝트 로그 열람 범위가 생긴다. AWS의 stream ARN과 같은 격리를 원하면 운영 담당이 전용 log view/접근 역할을 준비하고 provider 담당이 그 view를 조회하게 바꿔야 한다. 이번 범위에서는 그 외부 정책/조회 변경을 구현하지 않았다. [공식 Logging 접근 제어](https://docs.cloud.google.com/logging/docs/access-control).

## 적용 전 남은 확인

실제 provider 구현과 `config.py`가 `database_name`, 두 컨테이너 이름, reset 이름, DB18 및 `schema_prepared`를 받아야 한다. 이미지 placeholder를 실제 digest로 교체하고 위 역할·secret·네트워크·SQL 검증을 수행해야 한다. GCP16 교체의 데이터 보존/폐기와 서비스 중단, 두 클라우드 비용 변경은 사용자 승인 대상으로 남긴다. mock GREEN은 실제 PG18·SQL GRANT·네트워크 도달성·reset/서비스 실행 성공을 증명하지 않는다.
