# 플랫폼 U1 — 고정 로그인과 소스 사전 검사

## 상태와 범위

- 작성일: 2026-11-02 (Asia/Seoul), 브랜치 `feat/platform`.
- 사용자의 `Proceed.`에 따라 U1의 API·DB·로그인·검증 계획을 구체화했다. 사용자의 `You can start implementation.`으로 U1 구현을 승인받았다. API·Worker·웹 구현과 로컬 검증을 수행했고, 후속 AWS 배포 지시로 업무 계정 616723161368의 서울 리전에 배포했다. HTTPS·재시작·백업 복원·브라우저 결과와 CI 준비 상태는 [AWS 기록](platform-aws.md)을 따른다. 이번 변경의 커밋·푸시는 미실행이다.

## 설계와 구현 범위

U1은 고정 계정 로그인 → 공개 GitHub 서비스 등록 → 소스 사전 검사 → 결과·이력 조회까지다. 이미지 빌드·코드 실행·ECS 검사·고객 VM 배포는 후속 단위이며, 소스 확인 성공을 배포 성공으로 표시하지 않는다.

| 영역 | 승인된 설계 |
| --- | --- |
| 로그인 | 고정 ID·비밀번호 한 쌍, 공통 작업 공간. Argon2id 해시·서버 세션·운영 Secure 쿠키·Origin/CSRF 검사 |
| 서비스 등록 | 공개 GitHub URL, ref, 빌드 경로, Dockerfile, 앱 포트·헬스 경로를 검증하고 저장 |
| 소스 확인 | GitHub API의 공개 저장소·확정 커밋·경로·일반 Dockerfile 존재 확인. redirect·임의 호스트·symlink/submodule 거부 |
| 작업 처리 | API 접수와 별도 Worker, PostgreSQL 상태·이벤트 저장. 중복 접수 방지와 같은 서비스의 활성 작업 제한 |
| 장애 복구 | 60초 lease, 15초 heartbeat, 최대 3회 시도. DB 시계와 lease token으로 종료된 Worker의 늦은 결과 차단 |
| 화면 | 실제 저장 결과·실패 이유·진행 이력, 실행 중에만 polling. 미구현 기능의 성공 상태를 만들지 않음 |

API·Worker는 공통 저장 모듈을 사용한다. 작업 선점은 짧은 트랜잭션의 `FOR UPDATE SKIP LOCKED`로 처리하고 외부 HTTP 요청 중 DB 잠금을 유지하지 않는다. 커밋 SHA를 고정한 뒤 재시도에서도 같은 소스를 확인한다. 상태와 이벤트는 한 트랜잭션으로 저장한다.

계약은 [HTTP 계약](../../platform/contracts/u1-http.md)과 [데이터 구조](../../platform/contracts/u1-data.md)를 따른다. 구현 순서는 DB·migration → 인증 → 등록·작업 API → GitHub 어댑터·Worker → 웹 → 통합 검증이었다. 실제 DB·인증 보호·중복/동시성·Worker 복구·실제 GitHub·브라우저 검증을 완료 기준으로 삼았다.

## 구현·검증 기록

2026-11-03 (Asia/Seoul)에 확인했다.

| 명령·검증 | 결과 |
| --- | --- |
| `uv sync --python 3.12`, Alembic 초기 migration 생성·`upgrade head` | Python 3.12.12, PostgreSQL 17에 테이블·제약·인덱스 생성 |
| `uv run pytest -q -m 'not live'` | 56개 통과. 실제 PostgreSQL, HTTP fixture. 중복 접수·동시 선점·실제 Worker 프로세스 강제 종료와 복구·만료된 소유자 차단 포함 |
| `uv run pytest -q -m live` | 실제 GitHub 검사 1개 통과. `docker/welcome-to-docker` 커밋 `68c1b9f87c41fb3fef2667e27149865c2f42d1eb` 검사·결과 저장 |
| `npm run build` | TypeScript 검사·Vite 빌드 성공. Node.js 24 사용 |
| `npm run test:e2e` | 브라우저 3개 통과. 실제 API·별도 Worker·DB, GitHub 응답은 fixture. 성공·실패·새로고침·로그아웃·모바일 검사 |
| 화면 캡처 확인 | 데스크톱 검사 결과·모바일 목록에 잘린 내용이나 가로 넘침 없음. 캡처는 시험용 데이터 |
| `uv run ruff check .`, `uv run ruff format --check .`, `git diff --check` | 통과 |

초기 재시도 시험은 호스트 시각과 DB 시각 차이로 실패했다. 대기 시각을 DB 시계로 설정하도록 시험을 수정한 뒤 전체 통과했다. 시간 소유권 판단은 실제 구현에서도 DB 시계를 사용한다. FastAPI/Starlette TestClient의 httpx 사용 중단 예정 경고 1개가 남아 있으며 기능 실패는 아니다.

### 구현 검토

- API·Worker가 같은 저장 모듈을 사용하며 상태·이벤트는 원자적으로 저장한다. 외부 요청 중 DB 행 잠금을 유지하지 않는다.
- 로그인 설정 누락은 기동 오류다. 운영 쿠키 Secure, Origin·CSRF 검사, 비밀번호 해시·세션 토큰 해시, 공유 DB의 시도 제한을 적용했다.
- GitHub 호스트 고정, redirect 거부, 경로 검증, 응답·횟수·시간 제한과 커밋 고정을 적용했다.
- 고객 계정 생성·고객 클라우드 관리 권한·고객 코드 실행은 추가하지 않았다. 플랫폼 자체의 AWS 배포는 별도 운영 작업으로 구분한다.
