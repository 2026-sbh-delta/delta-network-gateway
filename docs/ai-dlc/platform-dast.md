# 플랫폼 DAST 연결

- 기준일: 2026-11-04 (Asia/Seoul).
- 승인: 사용자의 `Proceed the DAST module connection, the next task.` 지시로 설계·구현·검증을 진행한다. 기존 SAST 운영 기록 변경은 보존한다.
- 후속 승인(2026-11-04): 합의한 TODO 구현과 커밋·PR 병합을 사용자에게 위임받았다. 필요한 검증과 CI가 통과한 플랫폼 작업을 순서대로 병합한다.
- 브랜치: `feat/platform-dast`, 기준 main `d4200f7`.
- 현재 상태: API·Worker·화면·운영 연결 코드 구현 및 로컬 검증 완료. AWS 실제 Plan/Apply·도구 이미지 배포·플랫폼 운영 릴리스·커밋·푸시는 하지 않았다.

## 목적과 범위

개발자가 성공한 빌드와 그 커밋의 SAST 결과를 선택하면, 같은 이미지의 앱을 ECS에서 실행하고 팀의 `services/delta-dast`로 XSS 재현을 확인한다. 결과와 근거를 저장하고 검사 앱을 종료한다. 검사 완료는 전체 안전 판정이나 운영 배포 승인이 아니다.

이번 범위는 DAST 요청·결과·취소·복구·정리와 화면이다. 사용자의 추가 지시로 GCP·온프레미스로 이전할 때 대상 환경에서 별도 검사한다는 원칙을 계약과 화면에 반영했다. 현재 실행기는 AWS ECS이며 다른 환경의 실제 실행기를 구현했다고 표시하지 않는다. AI 수정안·PR·사람 승인 후 코드 반영, DB 이전, 운영 트래픽 전환은 후속 단계다. 기존 DAST의 AI/브라우저 검사 로직을 재사용한다.

## 설계

1. 성공한 빌드와 같은 서비스·커밋·digest의 완료된 SAST만 받는다. 원본 SARIF를 다시 받아 저장한 해시와 대조한다. 외부 URL은 사용자 입력으로 받지 않는다.
2. 기존 플랫폼 전용 ECS 풀을 사용하고 실행 확인과 DAST가 동시에 점유하지 못하게 DB와 Worker 잠금을 공유한다. 팀의 별도 스테이징은 변경하지 않는다.
3. 실제 ECS 이미지·개정판·헬스를 확인한 뒤, 등록된 `platform-sandbox` 대상에만 DAST를 요청한다. DAST가 지원하는 XSS 후보가 없으면 미실행으로 표시한다.
4. 팀 DAST에 플랫폼 전용 API를 추가한다. 별도 서버 토큰, 고정된 대상 등록, 요청 UUID와 입력 해시를 디스크에 보존한다. 응답 유실 후 같은 ID를 조회해 중복 검사를 막는다. 서버 재시작으로 실행이 끊기면 재현 성공으로 표시하지 않는다.
5. SAST 해시·빌드·커밋·이미지·대상·DAST ID를 묶는다. 화면에는 제한한 결과 필드와 근거 해시만 제공한다. 토큰·원본 코드·페이로드는 반환하지 않는다.
6. 성공·오류·취소 모두 검사 앱을 종료한다. DAST 취소 확인이 안 되면 앱부터 종료하되 같은 풀은 재사용하지 않고 정리를 재시도한다. 늦게 도착한 요청은 취소 표식으로 거부한다.
7. 사설 HTTPS의 플랫폼 전용 경로만 노출한다. 도구 VM에서 기존 플랫폼 내부 ALB의 80 포트만 허용한다. 일반 사용자의 직접 접근은 열지 않는다.

## 구현 단위와 완료 기준

| 단위 | 구현 | 검증 기준 |
| --- | --- | --- |
| DAST API | 토큰 인증, 고정 대상, 멱등 접수·조회·취소 | 다른 대상/변경 입력 거부, 중복 실행 방지, 재시작·취소 이후 늦은 요청 거부 |
| 플랫폼 실행 | DB `0005`, API/Worker, 기존 ECS 수명 재사용 | 다른 빌드/SAST 거부, 풀 독점, 실제 digest 대조, 실패·취소·응답 유실 후 정리 |
| 화면 | 코드 검사 아래 DAST 결과·진행·중단 | 미실행·취약 재현·미재현·판정 불가를 구분, 새로고침·모바일 |
| 운영 연결 | 사설 경로·설정·배포 안내 | 계획과 적용 구분, 기존 SAST/DAST 자료 보존, main 반영 후 운영 검증 |

## 복구

설정이 바뀌거나 대상 소유권이 다르면 다른 실행을 정리하지 않는다. 실행 중에는 이미지와 대상 설정을 고정한다. Worker 복구 시 원격 검사 ID와 ECS 개정판을 재사용한다. DB `0005`에 DAST 기록이 있으면 자동 downgrade를 거부한다. 운영 배포는 사용자 main 병합 후 GitHub Release로 진행한다.

## 검증 기록

2026-11-04, `feat/platform-dast`에서 실행했다.

- `platform/backend`: `uv run ruff check .`, `uv run pytest -q` → 144 passed, 실제 GitHub 별도 검사 1 deselected. 실제 PostgreSQL `0005` 적용·기존 이력·다운그레이드 방어·취소·재시작·정리 실패 시 잠금 검증. ECS SDK와 SAST/DAST HTTP 경계는 fixture다.
- `services/delta-dast`: 플랫폼 API·SARIF·런타임 테스트. 취소 시 대기 항목이 먼저 끝나 브라우저 종료를 조기 보고하던 문제를 발견해 모든 자식 작업의 정리를 기다리도록 수정했다. 이 테스트는 실제 API와 비동기 작업을 사용하고 모델/브라우저 엔진은 대체한다. `uv run --project services/delta-dast --python 3.12 --frozen --with pytest --with pytest-asyncio --with httpx python -m pytest -q services/delta-dast/tests/test_platform_api.py services/delta-dast/tests/test_sarif_adapter.py services/delta-dast/tests/test_llm_runtime.py` → 22 passed.
- `platform/backend`: `uv run --with-editable ../../services/delta-dast pytest -q tests/integration_dast.py` → 1 passed. 실제 팀 DAST 서버 API·SARIF 변환·실행/결과 저장·취소 표식과 플랫폼/DB를 함께 검증했다. AWS SDK와 검사 엔진만 대체했으며, 실제 모델·브라우저 재현으로 표시하지 않는다.
- 후속 풀 경쟁 테스트 추가 후 `uv run pytest -q tests/test_dast.py` → 14 passed. 서로 다른 서비스의 일반 실행 검사와 DAST 동시 접수 중 하나만 허용함을 확인했다. Ruff 검사·포맷 통과.
- `platform/web`: `npm run build`, `npm run format:check`, `npm run test:e2e` → 12 passed. 실제 API·Worker·PostgreSQL을 사용해 재현·미실행·중단·새로고침·모바일을 확인했다. 처음에는 오래된 dist로 실행돼 3건 실패했고, 갱신 빌드 후 전부 통과했다. 로컬 Node 20.18 버전 경고가 있으나 빌드는 성공했고 CI는 Node 24를 사용한다.
- `python3 -m unittest discover -s platform/deploy/tests -v` → 32 passed. 기존 런타임 보존, 별도 토큰 유지, 활성 검사 시 대상 변경 거부, 사설 대상 제한을 확인했다.
- 기존 SAST 네트워크 root의 `terraform validate`, `terraform test` → valid, 6 passed. backend/state와 실제 tfvars를 복사하지 않은 임시 디렉터리의 mock 검증이다. AWS 실제 Plan/Apply가 아니다.
- 실제 AWS DAST, 실제 AI 모델·Chromium XSS 재현, GCP·온프레미스 실행, AI 수정 PR·사람 승인 후 재배포는 이번 검증에 포함하지 않았다.

## 운영 인계

[API 계약](../../platform/contracts/dast-checks.md)과 [배포 순서·롤백](../../platform/deploy/dast/README.md)을 따른다. main 병합 전 운영 배포를 하지 않았다. 현재 브랜치에는 직전 SAST 운영 검증 문서 변경도 보존했다.
