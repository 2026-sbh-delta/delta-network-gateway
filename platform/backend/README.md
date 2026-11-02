# 플랫폼 API와 Worker

Python 3.12, FastAPI, PostgreSQL, SQLAlchemy, Alembic을 사용한다. 패키지와 버전은 `pyproject.toml`, `uv.lock`에 기록한다. [실행 안내](../README.md)를 따른다.

| 모듈 | 책임 |
| --- | --- |
| `api/` | 고정 로그인·세션, 입력 검증, 작업 접수·조회, 빌드한 웹 제공 |
| `core/` | 서비스 등록, 세션·작업 상태, DB 트랜잭션, 중복 실행 방지 |
| `adapters/` | 고정 GitHub API 호스트로 공개 소스 정보 조회 |
| `worker/` | 작업 선점, heartbeat, 오류 처리·재시도·중단 후 복구 |

API는 장시간 검사를 실행하지 않는다. Worker는 HTTP 요청 중 DB 잠금을 유지하지 않고, 결과를 저장할 때 자신의 lease token이 유효한지 확인한다. 작업 상태와 이벤트를 같은 트랜잭션에 저장한다.

## 설정

| 환경변수 | 역할 |
| --- | --- |
| `PLATFORM_DATABASE_URL` | `postgresql+psycopg://` 연결 주소, 필수 |
| `PLATFORM_LOGIN_ID` | 고정 로그인 ID, 필수 |
| `PLATFORM_PASSWORD_HASH` | Argon2id 비밀번호 해시, 필수 |
| `PLATFORM_PUBLIC_ORIGIN` | 경로·끝 슬래시 없는 웹 origin, 필수 |
| `PLATFORM_LOCAL_MODE` | loopback HTTP 개발 모드, 기본 false |
| `PLATFORM_WEB_DIST` | 빌드한 웹 파일 경로, 미지정 시 저장소의 `web/dist`, 운영 이미지는 `/app/web/dist` |
| `PLATFORM_SESSION_HOURS` | 세션 절대 만료 시간, 기본 8시간, 1~24시간 |

`.env`는 명령을 실행하는 현재 디렉터리에서 읽는다. 운영 ID·비밀번호·DB 연결 값은 저장소나 로그에 넣지 않는다. 고정 자격 증명이 변경되면 기존 세션을 거부한다. 모든 API·Worker에는 동일한 설정을 적용한다.

## 시험과 제한

`tests/`는 실제 PostgreSQL의 인증·상태 보존·동시성·재시도·Worker 종료 복구를 검증한다. 일반 시험은 GitHub HTTP fixture를 사용하며, 실제 공개 저장소 시험은 `-m live`로 구분한다. `tests/e2e_server.py`는 시험 DB를 비우는 브라우저 시험 전용 서버로 운영에 사용하지 않는다.

U1은 파일의 위치·유형을 검사한다. 빌드·DAST·배포 성공을 생성하지 않는다. 외부 변경이 없는 소스 읽기 작업의 재시도 방식이며 Terraform Apply에 그대로 적용해서는 안 된다.

[HTTP 계약](../contracts/u1-http.md) · [DB 구조](../contracts/u1-data.md) · [구현·검증 기록](../../docs/ai-dlc/platform-u1.md)
