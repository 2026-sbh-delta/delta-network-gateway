# 진단·보고서·로그·온프레미스 연결 인수 안내

2026-11-06 사용자 승인: 로컬 후속 작업을 커밋·푸시하고 팀이 검토·통합할 PR로 정리한다. PR 병합과 AWS 배포는 이번 요청에 포함하지 않는다. 새 ADR 번호는 임의로 지정하지 않는다.

## 소스와 현재 상태

- 전달 브랜치: `codex/diagnostics-pipeline-handoff`.
- 최신 팀 main `ad48e2e`(#59)를 merge commit으로 통합했다. 도메인 전환/사람 승인 AWS 복귀, 운영 배포·서명·FE/BE 묶음, OIDC 인증, 모르는 옵션의 실행 재개 거부를 보존했다. 6파일의 충돌을 양쪽 기능을 유지해 해결했다.
- 사용자 담당 AI 진단/복구 제안, 보고서·다운로드·사람 확인, 중앙 로그 근거, 제한 복구, 독립 온프레미스 실행·정리 전 로그 보존을 포함한다. `platform/`, `services/delta-sast/`, `services/delta-dast/`는 최신 main 대비 변경하지 않았다.
- 이 브랜치는 최신 팀 코드와 합친 **소스 인수본**이다. 현재 AWS 배포본으로 간주하지 않는다. 아래 실제 환경 결과는 통합 전 각 검증 이미지에서 확인한 기록이다.

```bash
git fetch origin codex/diagnostics-pipeline-handoff
git switch -c review/diagnostics origin/codex/diagnostics-pipeline-handoff
```

PR 승인 후 병합할 경우 해시 인용 보존을 위해 merge commit 방식을 사용한다. 이 문서의 예제는 main 직접 push·PR 자동 병합·인프라 적용을 실행하지 않는다.

## 결합 지점

기존 빌드·SAST·스테이징·DAST·수정·운영 승인/배포 흐름을 유지한다. 실패·미실행·모호한 결과를 그래프의 예외 경로로 보낸다.

```text
단계 결과 + 같은 실행의 제한 로그
→ DiagnosticClient → POST /v1/diagnoses
→ ReportStore(JSON/Markdown, 영속 저장)
→ 콘솔의 원인 후보·근거·한계·다운로드·사람 확인
→ 같은 case_id와 허용 조치 검증 → 재조회/허용 재개 또는 중단
```

| 접점 | 코드/계약 | 적용 조건 |
| --- | --- | --- |
| 실행 생성 | `services/delta-orchestrator/delta_orchestrator/pipelines.py:platform_run`, `Run` | `diagnostic`, `evidence`, 선택적 `onprem` 의존성 주입. 팀의 release/domain 설정 보존 |
| 진단 | `delta_pipeline/diagnostic_client.py`, `delta_ai/api.py`, `diagnostic_contract.py` | `/v1/diagnoses`. AI는 설명·근거·가설을 반환하며 판정이나 배포 승인을 대신하지 않음 |
| 예외/보고서 | `exception_flow.py`, `reports.py`, graph의 `diagnose_exception`/`save_diagnostic_report` | 보고서 해시·실행·이미지·commit 바인딩. 진단 불능도 보고서/사람 확인으로 보냄 |
| 조회/선택 | `GET /api/pipeline/runs/{run_id}/reports/{case_id}/{format}`, `POST /api/pipeline/runs/{run_id}/answer` | 인증·실행 소유권. 선택 본문에 `value`, `case_id`. 과거 case의 선택 거부 |
| 중앙 로그 | `cloudwatch_evidence.py`, `evidence.py` | 최근 시간·고정 그룹·개수/페이지 제한, 마스킹·미해석/조회 공백 표시. 환경 참고와 실행 직접 근거 구분 |
| 복구 제안 | `POST /v1/recovery-proposals`, `assistance.py`, `recovery.py` | 허용 목록·예산·동일 실행·소유권·정리 재검증. 자연어 명령을 실행하지 않음 |
| 온프레미스 | `onprem_ssm.py`, `onprem_independent_store.py`, `onprem_independent_worker.py` | 등록된 성공 빌드 재사용, 고정 SSM 문서/해시, 독립 큐·증거 저장소. 팀 Worker/큐 유지 |
| 정리 전 로그 | `onprem_evidence.py`, `onprem_worker_bridge.py` | FE/BE의 UID·이미지·commit 확인 후 제한 로그 영속 저장. 수집 실패가 정리를 막지 않음 |

강화 진단이 켜지면 기존 종료 후 `deployment_gate` 설명 콜백은 중복 호출하지 않는다. 강화 진단이 꺼져 있으면 팀의 기존 설명 경로를 유지한다.

## 설치·회귀 재현

Python 3.12, Node.js, uv가 필요하다. 아래 설치는 외부 의존성만 고정하고 Delta 패키지는 현재 checkout에서 설치한다. `requirements-test.txt`에는 로컬 절대 경로나 자격증명이 없다.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -c deploy/orch-diagnostics/requirements-test.txt -e './services/delta-ai[dev]' -e './services/delta-pipeline[dev]' -e './services/delta-orchestrator[dev]' -e ./platform/backend -e ./services/delta-staging
bash deploy/orch-diagnostics/check.sh
```

`.github/workflows/diagnostics-check.yml`이 동일 서비스 회귀와 두 Terraform root의 validate/mock test를 수행한다. AWS 자격증명을 사용하지 않으며 배포하지 않는다. Terraform 테스트는 **각 파일에 `mock_provider "aws" {}`가 있는지 먼저 검사**한다. 실제 state에 연결한 `terraform test`를 실행하지 않는다.

## 런타임 설정과 활성화

| 설정 | 의미 |
| --- | --- |
| `DELTA_DIAGNOSTIC_URL`, `DELTA_DIAGNOSTIC_TOKEN`, 선택 `DELTA_DIAGNOSTIC_CA_BASE64` | 기존 비밀 저장소에서 진단 연결값 공급. 값은 Git/문서/출력에 기록하지 않음 |
| `DELTA_DIAGNOSTICS_REQUIRED=1` | 연결 누락이면 시작을 거부하는 강화 진단 모드 |
| `DELTA_LOG_EVIDENCE_ENABLED=1` | 고정 CloudWatch 근거 수집. Terraform `orchestrator-evidence-aws`의 3그룹 읽기 권한 필요 |
| `ORCH_STATE_DIR` | 체크포인트·보고서·runtime-evidence·알림 저장소. 기존 영속 볼륨을 유지 |
| `DELTA_ONPREM_ENABLED=1`, `DELTA_ONPREM_DOCUMENT`, `DELTA_ONPREM_DOCUMENT_HASH` | 독립 온프레미스 경로를 명시적으로 켜고 고정 문서/해시 공급 |
| 요청의 `execution_cloud=onprem`, `onprem_build_id`, `assistance=true` | 등록 빌드와 지원 범위로 제한. 운영 release/DNS 전환과 결합하지 않음 |

기존 운영 흐름은 위 옵션을 임의로 켜지 않고 유지할 수 있다. 모든 환경을 대상으로 하는 일반 장애 자동 복구는 제공하지 않는다. 독립 FE/BE 어댑터의 `retry_ready`/`resume_ready`가 거부하는 재배포·재개는 활성화하지 않는다. 영속 알림/웹훅 core 모듈은 포함하지만, 이전 로컬 시험본의 별도 알림함 API·UI·운영 웹훅 설정 전체를 이 인수본에 통합한 것은 아니다. 실제 Slack 자동 발송은 하지 않았다.

배포 helper들은 고정 인스턴스의 **호스트에서 실행하는 템플릿**이다. `IMAGE`, `EXPECTED_IMAGE`, `DOCUMENT`, `DOCUMENT_HASH` 등은 검토한 digest와 Terraform 출력으로 렌더링한 뒤 사용한다. 로컬에서 그대로 실행하는 일반 CLI가 아니다. 이미 실행 중인 작업이 있으면 교체하지 않고, 현재 이미지·설정·영속 상태를 확인하고 실패 시 이전 컨테이너로 되돌린다. `release_executor.py`는 독립 컨테이너만 변경하며 팀 Worker를 교체하지 않는다. `release_validation.py`는 공용 컨테이너를 보존하는 loopback 전용 시험 인스턴스용이다.

## 실제 환경 검증과 남은 인수 조건

- **GCP 검사/진단:** `b719e7707a4b418b93252a0ff2a68f68`: 새 SAST15파일 → FE/BE staging → XSS 재현1 → 실행 로그8/GCP40(미해석19) → 실제 Claude → 보고서/화면·인증 다운로드. 해당 시간대 AWS 로그0건은 안전 판정이 아니다.
- **온프레미스 실행/복구:** `0b52bda1bab844e4a7f6fb93b55e546c`: 호출자 응답 유실에도 같은 native 작업으로 복구, UID/digest/HTTP·정리·실제 Claude·보고서 확인. 단일 이미지 실행 검사이며 DAST는 not_run이다.
- **독립 FE/BE/로그:** `daa4646866be4178aff5385838521ec9`: 새 SAST → FE/BE 실행/통신 → DAST HTTP422 → 두 컨테이너 로그 영속 보존 → 정리 → 실제 Claude 실패 보고서. 두 Pod/pull Secret 부재 확인. DAST 성공을 확인한 것은 아니다.
- **남은 오류:** 실제 DAST 카탈로그에 onprem 등록의 기본 `platform-sandbox`가 없어 KeyError를 재현했다. 대상 설정을 팀과 정리한 뒤 새 브라우저 DAST 실행이 필요하다. 이 PR은 팀 DAST 소스를 수정하지 않는다.
- scanner 토큰은 기존 발급 기준 2026-11-07 16:37 KST경 만료 예정. 후속 실제 검사 전 만료·회전을 확인한다. 비밀값은 공유하지 않는다.
- 이 소스 인수본은 AWS에 추가 배포하지 않았다. PR 통합 뒤 새 실행으로 진단/로그/보고서·인증·재시작 보존·사람 선택을 확인해야 한다. 전체 차분 plan/apply·기존 DB 이관·도메인 이전 완료를 주장하지 않는다.

검증 시점별 상세는 [기존 진단 검증](orchestrator-diagnostics-ui.md), [중앙 로그 근거](ai-aws-readiness.md), [온프레미스 복구](onprem-runtime.md), [실제 DAST·로그 후속](onprem-dast-evidence.md)를 참고한다. 운영 이력의 미푸시/미통합 표기는 해당 시점의 사실이며 현재 게시 상태는 이 인수 PR과 Git 이력으로 확인한다.

사용자 검토용 Notion: https://app.notion.com/p/4108bee9ada48115bceee5ba380c21a9 . 비공개 초안이므로 외부 열람은 별도 공유 권한을 따른다.

## 인수본 검증 기록 (2026-11-06)

- `AWS_EC2_METADATA_DISABLED=true bash deploy/orch-diagnostics/check.sh`: AI323 / pipeline187 / orchestrator12, 합계522개 통과. 외부 모델·스캐너·클라우드 호출은 대체 응답이며 실제 환경 검증 기록과 구분한다. compileall·Node 구문·PR 기준 diff 공백 검사도 통과했다.
- 두 Terraform root에서 Docker Terraform1.16.4로 `init -backend=false -lockfile=readonly`, `validate`, `test` 실행: onprem2 / evidence1, 총 mock3개 통과. 실 AWS Plan/Apply·배포는 실행하지 않았다.
- 기존 main의 “모르는 옵션은 재개하지 않음” 테스트는 이제 지원하는 onprem 옵션 대신 미지원 미래 옵션으로 fixture를 바꿔 본래 계약을 유지했다. main의 도메인 복귀·운영 배포 회귀를 함께 통과했다.
- 기존 알림 core의 미선언 `delta_diff` 의존성을 제거하고 영속성·실행별 조회·횟수 한도·전송 목적지/형식 테스트9개를 인수본에 포함했다.
- GitHub Actions에서 같은 검증을 수행하도록 PR 전용 workflow를 추가했다. 원격 CI의 최신 상태는 PR Checks에서 확인한다.
