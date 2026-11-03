# Delta Platform

개발자용 플랫폼의 웹·API·Worker를 관리한다. 현재 운영 경로는 **고정 로그인 → 공개 GitHub 등록 → 소스 확인 → Jenkins 이미지 빌드 → ECS 실행 검사·SAST → 결과·로그 조회**다. [Jenkins AWS 운영 기록](../docs/ai-dlc/platform-jenkins-aws.md)에서 배포와 실제 기능 검증 상태를 확인한다. 빌드 성공은 실행 검사나 배포 성공을 뜻하지 않는다.

- [구현 범위·검증 기록](../docs/ai-dlc/platform-u1.md)
- [OTel 대시보드·앱/DB 이전 진행](../docs/ai-dlc/platform-continuity.md) · [관측 연결](observability/README.md) · [세션 유지 예제](examples/session-app/README.md) · [이전 설명 HTML](docs/migration.ko.html)
- [전체 방향과 후속 단위](../docs/ai-dlc/platform.md) · [한국어 구성 설명](docs/overview.ko.html)
- AWS 배포 완료: https://d3udcz0zch0on.cloudfront.net · [실제 검증](../docs/ai-dlc/platform-aws.md). GitHub 워크플로는 [PR #7](https://github.com/2026-sbh-delta/delta-network-gateway/pull/7)에서 CI 성공 후 병합됐다. [OIDC 역할 생성과 변수 입력](deploy/GITHUB.md)을 완료했으며, 새 워크플로도 PR #8에서 main에 반영했고 GitHub OIDC Plan·Release 및 배포 후 로그인·Worker 작업까지 검증했다.

고객 앱 빌드는 사설 Jenkins 컨트롤러와 전용 에이전트를 사용한다. 플랫폼 자체의 인프라·릴리스는 GitHub Actions/OIDC가 담당한다. [AWS 구성·운영 안내](deploy/jenkins/aws/README.md)를 따른다.

## 로컬 실행

Python 3.12, uv, Node.js 24, Docker Compose가 필요하다. 아래 명령은 저장소의 `platform/`에서 시작한다.

```bash
cd backend
uv sync --frozen
uv run platform-init
cd ..
docker compose up -d --wait
cd web
npm ci
npm run build
cd ../backend
uv run alembic upgrade head
uv run platform-api
```

`platform-init`에서 사용할 **고정 ID·비밀번호**를 입력한다. 실제 설정은 Git에서 제외된 `platform/.env`, `platform/backend/.env`에 저장한다. 로그인 비밀번호는 Argon2id 해시만 저장한다. 기존 설정이 있으면 덮어쓰지 않는다.

다른 터미널의 `platform/backend/`에서 Worker를 실행한다.

```bash
uv run platform-worker
```

**접속: <http://127.0.0.1:8088>**. 입력한 고정 계정으로 로그인한다. API가 빌드한 웹 화면도 함께 제공한다. API와 Worker는 별도 프로세스이며 같은 PostgreSQL을 사용한다. 기존 로컬 서비스의 8080 포트와 겹치지 않도록 8088을 사용한다.

중지는 API·Worker 터미널의 Ctrl+C와 `platform/`의 `docker compose stop`이다. `docker compose down -v`는 DB 데이터를 삭제하므로 일반 종료에 사용하지 않는다.

## 사용 흐름

1. 공개 GitHub 저장소 주소와 브랜치 또는 커밋을 등록한다.
2. 빌드 경로와 Dockerfile 경로는 **저장소 최상위 기준**이다. Dockerfile은 빌드 경로 안에 있어야 한다.
3. **소스 확인**을 누른다. 공개 여부, 커밋, 빌드 디렉터리, Dockerfile의 위치·유형을 검사한다.
4. 빌드 실행기를 연결한 환경에서는 성공한 소스 검사 아래 **이 커밋 빌드**를 누른다. 성공 시 이미지 digest를, 실패 시 원인과 빌드 로그를 확인한다.
5. U3 실행기를 연결한 환경에서는 성공한 빌드 아래 **이 이미지 실행 확인**을 누른다. ECS에서 실제 이미지·앱 응답을 확인하고 태스크를 정리한다. 진행 기록·실패 이유·중단 요청을 확인할 수 있다.
6. SAST 서비스를 연결한 환경에서는 **이 빌드의 코드 검사**로 같은 커밋의 발견 사항과 근거를 확인한다. 검사 완료는 보안 통과나 운영 배포 승인이 아니다. AI 수정·운영 배포는 후속 단계다. [검사 계약과 설정](contracts/security-checks.md)을 따른다.
7. 이번 브랜치에는 **이 빌드의 DAST 검사**를 추가했다. AWS ECS에서 같은 이미지를 실행해 팀 DAST로 XSS 후보를 확인한 후 앱을 종료한다. 운영 연결·릴리스는 아직 하지 않았다. [DAST 계약](contracts/dast-checks.md) · [운영 연결](deploy/dast/README.md). GCP·온프레미스 검사 결과와 구분한다.

GitHub 무인증 조회 한도에 도달하면 실패 사유를 표시한다. 검사는 소스 코드를 실행하지 않는다. 파일 존재 확인과 빌드·보안 검사를 구분한다. U3 샌드박스는 등록한 앱 포트·헬스 체크 경로를 사용한다. 소스 확인 단계는 앱을 실행하지 않는다.

## 구조

| 위치 | 역할 |
| --- | --- |
| `web/` | React·TypeScript 화면, API 연결, 브라우저 테스트 |
| `backend/src/delta_platform/api/` | 고정 계정 로그인, 요청 검증, 서비스·작업 조회 |
| `backend/src/delta_platform/core/` | 인증, PostgreSQL 저장, 중복 요청·작업 소유권·상태 전이 |
| `backend/src/delta_platform/adapters/` | GitHub 소스 확인·Jenkins 빌드·ECS 실행 확인·SAST/DAST 결과 연결. 과거 CodeBuild 호환 유지 |
| `backend/src/delta_platform/worker/` | 작업 선점, 검사, 재시도·중단 후 복구 |
| `backend/migrations/` | Alembic DB 변경 이력 |
| `contracts/` | [HTTP 규격](contracts/u1-http.md), [데이터 구조](contracts/u1-data.md) |
| `automation/` | 후속 고객 VM 배포 자동화 영역 |
| `compose.yaml` | 개발용 PostgreSQL, 55432 포트, 영속 볼륨 |
| `compose.test.yaml` | 시험 전용 PostgreSQL, 55433 포트 |

기존 `terraform/`, `services/`와 분리한다. Python 패키지명은 표준 라이브러리와 충돌하지 않도록 `delta_platform`이다. 공통 작업 공간이며 개인별 권한·사용자 관리 기능은 없다. 문서는 한국어, 코드 주석은 필요하면 영어로 작성한다.

## 검증

시험용 DB는 실제 PostgreSQL이며, 아래 시험은 `platform_test`의 데이터를 비운다. 개발용 DB와 포트·계정을 분리했다. 백엔드 시험과 브라우저 시험은 같은 시험 DB를 쓰므로 동시에 실행하지 않는다.

```bash
docker compose -f compose.test.yaml up -d --wait
cd backend
uv run pytest -q -m 'not live'
uv run ruff check .
uv run ruff format --check .
cd ../web
npm run build
npx playwright install chromium
npm run test:e2e
npm run format:check
```

로컬 브라우저 시험은 실제 API·별도 Worker 프로세스·PostgreSQL을 사용하고 **GitHub·AWS·SAST 응답은 fixture로 대체**한다. 시험 계정은 시험 서버에만 적용되며 운영 코드의 기본 계정이 아니다. `test-results/`의 화면 캡처도 검증용 데이터다. 운영 HTTPS에서는 실제 AWS 빌드·로그와 데스크톱·모바일 화면을 별도로 검증했다. Jenkins·SAST의 실제 로컬 검증은 각 AI-DLC 기록을 따른다.

실제 GitHub 조회는 `platform/backend/`에서 별도로 실행한다.

```bash
uv run pytest -q -m live
```

공개 `docker/welcome-to-docker`의 고정 커밋으로 검사한다. 네트워크·GitHub 요청 한도가 필요하다. 검증 결과와 아직 수행하지 않은 작업은 [U1 기록](../docs/ai-dlc/platform-u1.md)을 따른다.

## 운영과 후속 범위

운영에서는 HTTPS origin과 Secure 쿠키를 사용한다. `PLATFORM_LOCAL_MODE=true`는 loopback 개발 환경 전용이다. API CLI는 loopback에만 열리며 프록시 헤더를 신뢰하지 않는다. 역방향 프록시를 연결할 경우 로그인 시도 제한은 프록시 주소 기준으로 집계된다. 실제 클라이언트 IP 신뢰 설정은 배포 경로에 맞춰 별도 검증해야 한다.

DB migration은 배포 전에 한 번 실행한다. `/health/ready`는 DB·스키마 준비 상태만 확인하며 Worker 생존을 보장하지 않는다. Worker가 종료되면 최대 60초 뒤 다른 Worker가 작업을 회수한다. 진행 중 검사와 결과는 DB에 남는다.

기능 코드 롤백 전 DB 백업과 migration 호환성을 확인한다. 최초 migration의 downgrade는 테이블을 삭제하므로 실제 데이터가 있는 환경에서 일반 롤백으로 사용하지 않는다.

U2의 새 기본 실행기는 Jenkins이며 별도 에이전트에서 고객 Dockerfile을 빌드하고 결과 digest를 저장한다. 기존 CodeBuild 설정·이미지·이력은 전환 기간 동안 유지한다. [Jenkins 구성](deploy/jenkins/README.md)과 [API 계약](contracts/u2-builds.md)을 따른다. U3의 실제 배포 상태는 [U3 기록](../docs/ai-dlc/platform-u3.md), SAST의 로컬 구현·검증 상태는 [SAST 기록](../docs/ai-dlc/platform-security.md)을 따른다. Jenkins 운영 전환과 공개 웹의 실제 빌드·ECS 실행/종료 검증은 완료했다. SAST 사설 연결·운영 활성화·공개 웹 검증을 완료했다. [SAST 운영 안내](deploy/sast/README.md)를 따른다. 이후 DAST, Terraform·VM 배포, 관측 조회, 같은 엔진의 DB 이전으로 이어간다. **커밋 전에 변경 내용·검증 결과·Conventional Commit 메시지를 알리고, 푸시는 별도 지시에 따른다.**

현재 관측·계획 화면은 PR #22/#23으로 AWS에 배포됐다. [workflow/AI 식별 계약](docs/workflows.ko.md), [PostgreSQL 이전 실행기와 로컬 전체 앱 검증](migration/README.md), [실제 운영 기록](../docs/ai-dlc/platform-continuity.md)을 따른다. 승인한 DB 이전 CLI의 진행 상태를 대시보드에 연결하는 후속 구현이 추가됐다. 실제 클라우드 이전과 웹 자동 실행기는 아직 연결하지 않았다.
