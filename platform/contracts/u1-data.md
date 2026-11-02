# U1 데이터 구조

PostgreSQL에 저장하는 플랫폼 전용 상태다. 고객 DB의 데이터를 저장하거나 이전하지 않는다. 고정 로그인·공통 작업 공간이므로 `users`, 프로젝트 멤버십 테이블은 U1에 없다.

## 테이블

| 테이블 | 핵심 필드 | 제약·수명 |
| --- | --- | --- |
| `sessions` | token_hash, csrf_token, credential_version, created_at, expires_at | token_hash PK, 원본 세션 토큰 미저장, 로그아웃/만료/자격 증명 변경 시 무효 |
| `auth_rate_limits` | bucket_key, window_start, failures, blocked_until | API 프로세스 간 공유, 만료 bucket 정리 |
| `services` | id, name, repository_url, repo_owner, repo_name, source_ref, build_context, dockerfile_path, container_port, healthcheck_path, request_key, request_hash, created_at | UUID PK, 이름/등록 키 UNIQUE, 입력 제한은 API·DB에서 필요한 범위로 보장 |
| `runs` | id, service_id, kind, input_snapshot, request_key, request_hash, status, current_step, resolved_commit_sha, attempt, available_at, lease_token, lease_expires_at, result, error, created_at, started_at, finished_at | service FK, `(service_id, kind, request_key)` UNIQUE, 현재 상태 조건에 맞는 CHECK·index |
| `run_events` | run_id, sequence, event_type, step, message, details, created_at | run FK, `(run_id, sequence)` PK, 실행 결과와 같은 트랜잭션에 추가 |

`request_hash`는 정규화한 입력으로 계산한다. source-check 접수에서는 서비스 ID·kind·설정 snapshot 버전을 포함한다. 서비스 등록 정보는 U1에서 수정하지 않는다.

`runs.input_snapshot`은 서비스 설정의 복사본이다. 향후 서비스 수정 기능이 생겨도 과거 실행 의미가 바뀌지 않아야 한다. token·비밀값·HTTP 요청 원문은 snapshot·이벤트에 넣지 않는다.

## 작업 상태 불변 조건

- `kind=source_preflight`. 향후 실제 배포 작업과 명확히 구분한다.
- 같은 서비스의 `queued`/`running` 작업은 부분 UNIQUE index로 하나만 허용한다.
- queued: lease 없음, available_at 이후 실행 가능.
- running: lease token·만료 시각 필수. 선점 때마다 attempt 증가.
- succeeded: 커밋 SHA·검사 결과·finished_at 필수, error 없음.
- failed: error·finished_at 필수. 성공 결과 없음.
- terminal 상태에서는 lease를 비우고 결과를 변경하지 않는다. 재검사는 새 run이다.

DB 시간으로 lease를 판단한다. 전이는 행 잠금·token 비교로 수행하며, 현재 lease가 만료됐거나 token이 다르면 소유자의 갱신을 거부한다. 이 원칙을 API/Worker 양쪽에서 다르게 구현하지 않는다.

## 선점·재개

1. 실행 가능한 queued 작업 또는 lease가 만료된 running 작업을 `FOR UPDATE SKIP LOCKED`로 선점한다.
2. 같은 트랜잭션에서 새 token·만료 시각·attempt와 이벤트를 기록한다. 시도 상한이면 failed로 종료한다.
3. 트랜잭션을 닫은 후 GitHub 읽기 요청을 수행한다. 확보한 커밋 SHA는 token 확인 후 영속화한다.
4. heartbeat·중간 결과·최종 결과마다 token과 유효 lease를 확인한다.
5. 상태와 이벤트를 원자적으로 반영한다. Worker를 강제 종료하면 lease 만료 후 다른 Worker가 회수한다.

외부 HTTP 읽기가 두 번 발생할 수는 있다. U1은 외부 side effect가 없는 사전 검사이며, 이 방식을 Terraform Apply나 실제 배포에 그대로 적용해 안전한 재실행을 보장한다고 주장하지 않는다. 후속 작업은 외부 실행 ID와 실제 상태를 확인하는 별도 계약이 필요하다.

## 세션·운영

- 세션 식별자는 암호학적 난수로 발급하고 SHA-256 해시만 DB에 저장한다. 비밀번호는 이 방식으로 저장하지 않고 Argon2id를 사용한다.
- csrf_token은 세션에 묶인 별도 난수다. API에서 인증된 세션 확인 응답으로만 노출하고 요청 헤더와 비교한다.
- 고정 자격 증명 변경 시 이전 credential_version의 세션을 거부한다. 모든 API 인스턴스에는 같은 설정 버전을 적용한다.
- 작업 이력은 개인별 작업자를 식별하지 못한다. actor가 필요한 로그는 `shared_operator`로 표시한다.
- Alembic migration은 API/Worker 시작과 분리해 한 번 실행한다. 준비되지 않은 DB에서는 readiness가 실패한다.
- 로컬 Compose의 DB 데이터는 named volume에 보존한다. 일반 종료·재시작으로 volume을 삭제하지 않는다.

초기 Alembic revision `0001`로 구현했다. 실제 PostgreSQL의 제약·동시성·Worker 복구 검증 결과는 [U1 기록](../../docs/ai-dlc/platform-u1.md)을 따른다.
