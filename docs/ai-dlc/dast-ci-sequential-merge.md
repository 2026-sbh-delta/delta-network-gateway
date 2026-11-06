# DAST CI 실패 수정과 PR 순차 병합

## 승인과 결정

2026-11-06 사용자가 남아 있는 DAST CI 실패 해결 및 PR #60→#61 순서 main 병합을 요청했다. 별도 `codex/dast-ci-merge`, 기준 #60 `03a29a3`에서 진행한다. 두 PR은 merge commit으로 병합하며 CI를 우회하지 않는다. 이 요청은 CI 테스트의 플랫폼/DAST 경계 수정까지 승인한 것으로 적용한다.

로컬 ADR 초안(팀 번호 미할당): 실제 플랫폼DB·Worker·DAST API/변환/사전점검/판정/정리를 사용하고 외부 인증/브라우저/대상 HTTP/모델/SAST 회신만 시험 응답으로 바꾼다. 운영 사전점검을 끄거나 재현0건을 통과로 바꾸지 않는다. 수정안 생성은 별도 계약 시험이 있으므로 이 경계 시험의 대상 저장소는 None으로 두어 checkout/수정 모델을 실행하지 않는다.

## 원인과 변경

- 기존 `tests/integration_dast.py`는 `orchestrate`만 대체했다. agent 경로에 추가된 실제 인증·브라우저·대상 사전점검에서 종료되어 검사 호출이0건이었다. 격리 PostgreSQL에서 동일 assert실패를 재현했다.
- 실제 플랫폼 API는 impact=True·auto_fix=True를 명시하지만 오래된 시험은 impact=False를 기대했다. 서버 계약을 따라 기대값을 수정했다.
- 외부 경계만 명시적으로 대체하고 실제 preflight/routing을 유지한다. 성공 시 모든 eligible finding이 검사됐는지, 실패 시 검사0/INCONCLUSIVE 전건·requires_review·deployment_approved=False인지 검증한다. 두 경로 모두 앱 정리와 DAST 취소 기록을 확인한다.

## 검증 — 2026-11-06

독립 `.validation/dast-ci-venv`, 임시 PostgreSQL17 컨테이너 `delta-dast-ci-pg`(127.0.0.1:55433, 메모리 볼륨, platform_test DB)를 사용했다. 운영 DB/클라우드/모델 호출은 없다.

backend 디렉터리에서:

```bash
PYTHONPATH=../../services/delta-dast:../../services/delta-staging:. AWS_EC2_METADATA_DISABLED=true ../../.validation/dast-ci-venv/bin/python -m pytest -q tests/integration_dast.py --tb=short
PYTHONPATH=../../services/delta-dast:../../services/delta-staging:. AWS_EC2_METADATA_DISABLED=true ../../.validation/dast-ci-venv/bin/python -m pytest -q tests --tb=short
../../.validation/dast-ci-venv/bin/ruff check tests/integration_dast.py
../../.validation/dast-ci-venv/bin/ruff format --check tests/integration_dast.py
```

- 수정 전: 동일 `integration_dast.py:79` 재현0건 실패.
- 수정 후 통합2개 통과/1.18초. 플랫폼전체248개 통과·실클라우드 시험1개 deselected/29.44초.
- CI에서 사용하는 DAST 계약5파일45개 통과/1.65초. 인증·취소·근거 바인딩·온프레미스/Cloud Run 변환 포함.
- Ruff 검사/포맷·git diff --check 통과. 증거는 작업 루트 `notes/dast-ci-merge-20261106/`.

## 병합·운영 상태

로컬 수정/검증 완료. PR #60 head에 수정분 반영 후 CI 통과→#60 merge→#61 main 재지정/수정 보존→CI 통과→#61 merge 순서로 진행한다. 실제 병합 SHA와 최종 CI는 완료 후 작업 기록에 남긴다.

인프라/플랫폼/Worker 배포 workflow들은 workflow_dispatch 전용임을 확인했다. main push는 검사만 실행하며 이번 요청에서 수동 배포 workflow를 새로 실행하지 않는다. 기존 AWS 피드백 런타임(소스523ae5a, AI7f387e27/orchestrator db5d8bff)과 데이터를 보존한다.
