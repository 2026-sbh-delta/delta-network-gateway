# FE·BE 이미지 묶음과 운영 Compose 배포

2026-11-06 KST. 사용자 승인: 명세 제시 후 `개발해`.

## 목표와 범위

AWS 원본을 유지하고 같은 FE·BE digest 묶음을 GCP로 이관한다. AWS ECS·GCP Cloud Run 스테이징은 유지한다. 운영만 VM 한 대의 Compose다. 기존 Jenkins·Registry·수정루프를 확장한다.

승인 명세: `본선/자료/배포도구_명세_v1.md` (프로젝트 상위 자료 디렉터리). 구현은 Jenkins, delta-staging, delta-pipeline, 앱 운영 구성·BE 초기화·헬스, 스테이징 Terraform 변경안이다. SAST·DAST·onboarding-aws·앱 취약점 파일은 다른 담당의 변경을 보존한다.

## 계획과 승인

Ideation/Inception·구현 계획과 컨테이너 배치안은 명세와 사용자 `개발해`로 승인됐다. 독립 사전 검토의 누락 네 항목(운영 서명 발급 경로, 지연 POST, unknown 배포 충돌, 실제 PostgreSQL18)을 명세에 보완했다.

테스트를 먼저 추가해 기존 공개 경계의 실패를 확인한 뒤 구현한다. FE·BE 계약→스테이징→파이프라인/DAST 근거→운영 승인/실행·복귀를 연결한다. 기존 외부 시스템 재개·변경 없이 로컬부터 검증한다.

## 작업 상태

- 구현: 진행 중. 앱은 `../delta-sample-app-compose` detached worktree에서 작업하며 기존 `../delta-sample-app`의 수정 브랜치를 보존한다.
- 테스트: 기존 delta-staging 66개 통과. 새 동작의 테스트·결과는 아래에 이어 기록한다.
- Terraform: 코드 변경안 작성 예정. Plan/Apply 미실행.
- 운영 배포·DB 변경·Git commit/push/PR: 미실행. 사용자가 수행하는 절차로 분리한다.

## 검증 기록

| 일자 | 명령 | 결과 |
|---|---|---|
|2026-11-06|`services/delta-staging/.venv/bin/python -m pytest -q -p no:cacheprovider tests` (서비스 디렉터리에서 실행)|기준선 66 passed, 1 deprecation warning|

실제 클라우드의 digest 일치·DB18·운영 복귀·로그 수신·시간 실측은 로컬 검사와 별도다. 로컬 성공을 배포 완료로 보고하지 않는다.
