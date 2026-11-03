# 플랫폼 SAST 결과 연결

- 기준일: 2026-11-03. Jenkins 전환에 이어 사용자의 다음 작업 구현 지시를 수행한다.
- 목적: 성공한 이미지 빌드의 고정 커밋을 기존 SAST 서비스에 검사시키고 근거·발견 사항을 플랫폼에서 확인한다.
- 범위: API/DB 작업 이력·Worker·SAST HTTP 어댑터·화면·계약/회귀 검증. DAST/AI 수정/운영 배포 승인은 포함하지 않는다.
- 상태: 구현·로컬 API/DB/브라우저·실제 SAST HTTP 연결 검증 완료. 새 스키마는 `0004`. 운영 DB·SSM·공개 플랫폼에는 적용하지 않았다. Jenkins 변경과 함께 `337b0ec`으로 커밋·푸시했고 [PR #18](https://github.com/2026-sbh-delta/delta-network-gateway/pull/18)에서 검토한다. 최신 CI는 PR Checks를 따른다.

## 설계와 기준

1. 같은 서비스의 성공한 빌드만 참조한다. build ID·image digest·공개 저장소·40자리 commit을 snapshot에 고정한다.
2. `security_check` 실행 ID를 SAST의 `run_id`로 사용한다. 응답 유실/Worker 복구 시 같은 ID·입력으로 조회/재접수한다. SAST API의 중복 방지 계약을 따른다.
3. API는 지정한 검사 서버만 호출하며 인증 토큰·private snapshot을 사용자에게 반환하지 않는다. POST 결과·상태의 저장소/커밋/ID를 대조한다.
4. 완료 상태만으로 안전 판정을 내리지 않는다. SARIF 2.1.0을 검증하고 fail/review/기타 결과 수와 최대 100개 규칙·위치를 저장한다. 원문 비밀값이나 코드 조각은 화면에 보내지 않는다. 전체 SARIF의 해시를 근거로 기록한다.
5. 상태 `succeeded`는 검사 실행 완료다. 조치·검토 필요 여부는 별도이며 `deployment_approved=false`, `dast_check=not_run`이다. 빈 결과도 전체 안전 보장이 아니다.
6. SAST 미설정·서비스 오류·커밋 불일치·근거 누락은 실패/미연결로 표시한다. 다른 서비스의 빌드를 빌려 검사하거나 활성 작업과 충돌할 수 없다.

## 다음 연결 경계

현재 ECS 실행 확인은 검사 후 태스크를 종료한다. DAST에는 점검 동안 앱을 유지하는 실행 소유권, 검사 서비스만 접근하는 네트워크, 고정 digest/대상 ID와 종료 계약이 필요하다. 기존 로컬 게시판용 DAST에 고객 앱 주소만 보내 동일한 대상으로 검사했다고 표시하지 않는다.

## 운영과 복구

`PLATFORM_SAST_URL`, `PLATFORM_SAST_TOKEN`을 서버에만 설정한다. 운영은 HTTPS, 로컬 검증은 loopback HTTP를 허용한다. SAST 서버의 저장소 허용 목록도 별도로 맞춰야 한다. 미설정 상태에서도 기존 플랫폼 기능은 유지되고 새 검사 요청만 503으로 거부된다.

DB migration `0004`는 실행 종류를 추가한다. 보안 검사 기록이 있으면 자동 downgrade를 거부한다. 이전 앱으로 복구할 때는 배포 전 DB 백업 또는 스키마 호환 릴리스를 사용한다.

## 검증 기록

2026-11-03 검증했다.

- `cd platform/backend && uv run pytest -q`: 전체 122개 통과, live GitHub 1개 선택 제외. 고정 커밋·digest 연결, 응답 유실 후 같은 ID 회수, 다른 커밋/무효 SARIF 거부, 0건의 한계, 인증/CSRF, 이력 격리를 포함한다.
- `cd platform/web && npm run build && npm run test:e2e`: 빌드 성공, 전체 9개 통과. 실제 API/별도 Worker/PostgreSQL을 사용하고 외부 SAST 응답은 fixture다. 조치 대상·확인 필요·근거 표시와 커밋 불일치 실패를 확인했다. 데스크톱/390px 모바일 스크린샷을 검토했고 가로 넘침이 없다.
- Ruff·Prettier 검사 통과. `0004`는 실제 테스트 DB에 적용됐다. 운영 DB migration은 하지 않았다.
- 이후 migration 테스트 2개를 추가하고 `uv run pytest -q tests/test_security.py`로 SAST 관련 14개가 통과했다. `0003 → 0004`에서 기존 빌드 이력 보존과, 보안 검사 기록이 있을 때 `0003` downgrade 거부·현재 스키마 보존을 실제 PostgreSQL에서 확인했다.

### 실제 SAST 연결

기존 `services/delta-sast/Dockerfile`로 이미지를 만들고 loopback `127.0.0.1:18790`에 임시 컨테이너를 실행했다. 별도 임시 토큰, `SAST_LLM_BACKEND=off`, 공개 Docker 저장소 허용 범위를 사용했다. AWS 인증이나 호스트 디렉터리는 전달하지 않았다.

실제 `SecurityRunner` HTTP 어댑터가 `docker/welcome-to-docker`의 Jenkins 검증 커밋 `68c1b9f87c41fb3fef2667e27149865c2f42d1eb`을 요청했다. SAST 실행 ID `12cb4c9e-4505-4946-90ea-07279b8cccd7`의 fetch/scan/done과 SARIF 수신을 확인했다. 결과를 실제 빌드 `c11607b2-32ef-4246-985b-b3ed9ec5739e`의 digest와 연결했다.

이 검증은 제출 기록 저장소만 메모리 대역을 사용한 **실제 HTTP 어댑터↔SAST/공개 GitHub 연결 시험**이다. DB/Worker/UI 연결은 위 통합·브라우저 시험으로 검증했으며 공개 운영 플랫폼에서 실행한 것은 아니다. 이 저장소는 주로 JavaScript이고 현재 검사기의 지원 규칙 범위에서 발견 사항이 0건이었다. 앱 전체 보안 통과나 JavaScript 전체 분석 성공으로 해석하지 않는다. LLM 호출도 0건이다.

근거 해시는 `bc861b59b1e1165574f694c8961d7ffe0f9fb07c5d76552d9adde6710baa6e15`, 결과는 ignored `platform/.runtime/sast-live-result.json`에 보존했다. 검증 후 임시 컨테이너와 토큰 파일을 제거했다. 운영 SAST 서버/팀 도구의 설정은 수정하지 않았다.

API·결과 필드·설정·롤백은 [검사 계약](../../platform/contracts/security-checks.md)을 따른다.
