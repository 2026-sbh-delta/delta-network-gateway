# 검증된 장애 사례와 진단 피드백

## 승인·범위

2026-11-06 사용자가 제안한 1차 범위(사례 저장, 조치 결과 검증, 검토 후 검색, 평가셋 후보 추출)를 승인하고 구현을 요청했다. Ideation/Inception/Construction 설계는 이 실행 요청 범위로 진행한다. 별도 `codex/verified-case-learning`, 기준 `3ddf601`. 팀 SAST/DAST/platform 변경, 공용 배포, Git 게시, Slack/Notion 쓰기는 범위 밖이다.

## 로컬 ADR 초안 — 팀 번호 미할당

- 문제: 보고서 확인/재시도 접수를 해결 성공으로 볼 수 없고, 과거 보고서가 다음 진단/평가에 연결되지 않는다.
- 기존 보고서는 불변으로 유지한다. 별도 버전 사례에 입력/보고서 해시, 환경/코드/이미지, 조치, 재검사 범위/근거와 success/failure/unknown/stopped를 연결한다. 성공은 해당 단계의 확인에 한정하며 원인 가설 확정과 구분한다.
- AI 서비스의 별도 SQLite 저장소에 버전을 보관한다. 그래프는 기존 인증으로 게시만 가능하고, 승격/거절은 서버 로컬 CLI와 OS 접근권한으로 분리한다. 검토는 정확한 버전 해시에 묶인다. 새 결과는 재검토가 필요하다.
- 진단은 같은 서비스/환경/단계의 승인된 사례만 제한 검색한다. 오류 코드/규칙을 추가 비교한다. 현재 실행과 동일 관측은 제외한다. 과거 사례를 이번 관측으로 취급하거나 자동 실행/승인하지 않는다.
- 기존 진단 API에 호환 가능한 context/사례 참조를 추가한다. 검색된 스냅샷을 보고서에 묶고 LLM 요청 해시에도 반영한다.
- 그래프 보고서 저장 후 unknown 사례를 자동 생성한다. 사람 조치 후 원래 단계의 재검사 결과를 연결한다. 로컬 outbox가 AI 장애 시 기록을 보존하며 재전송은 멱등이다. 이전 사건/성공한 재시도를 소급 추측하지 않는다.
- 평가셋은 사람이 별도로 작성한 기대 확인 항목/금지 단정으로 생성한다. LLM 가설을 정답으로 자동 승격하지 않는다. 평가에 포함한 모든 원본/동일 관측 사례를 검색에서 제외한다.
- 단일 AI 저장소의 현 서비스 범위이다. 다중 테넌트 권한체계/전용 UI/의미 검색/자동 규칙 활성화/파인튜닝은 후속이다.

## 작업 단위·검증 기준

1. 사례 스키마/SQLite 버전·승격·검색·CLI. 재시작/동시 쓰기/중복/역순 게시, 비밀값 마스킹, 서비스 경계 검사.
2. 진단 검색·출처 검증·실제 프롬프트 해시 및 보고서 표시. 과거 사례를 현재 증거로 인용하면 거부.
3. 그래프 보고서/재검사 연결·outbox. 확인/접수만으로 success 금지, 학습 저장 실패가 게이트를 바꾸지 않음.
4. 평가셋 추출/고정 검색 스냅샷/기준 대비 비교. 동일 사례 정답 누출 차단.

## 현재 상태

설계·로컬 구현 완료. 실제 AWS/Claude 검증, 배포, 커밋, 푸시, PR/main 반영 없음. 테스트 최종 결과는 아래에 기록한다.

## 제공되는 경로와 범위

```mermaid
flowchart LR
  A[보고서 저장] --> B[unknown 사례 · 로컬 outbox]
  B --> C[인증 게시 · AI SQLite]
  D[수정 커밋 또는 허용된 사람 조치] --> E[원래 검사 재실행]
  E --> F[success / failure / unknown / stopped · 새 버전]
  F --> C
  C --> G[로컬 검토 · 버전에 연결된 승인/거절]
  G --> H[다음 진단의 과거 사례 검색]
  G --> I[독립 기대값이 있는 평가셋 후보 추출]
```

- 사례 생성: 진단 보고서 저장 시 시작한다. 확인/중단은 unknown/stopped다. 보고서 생성 전에 자동 복구로 끝난 장애와 과거 실행을 소급 추측해 라벨링하지 않는다.
- 결과 연결: 기존 GCP DAST 수정→새 커밋/빌드→DAST 재검사, 온프레미스 사람 조치→원래 실행 재검사에 연결했다. 온프레미스 pass는 runtime UID/이미지/HTTP/정리 검사 범위이며 DAST/전체 이전 통과와 구분한다. 범위·해시·실제 확인한 바인딩 상세를 사례에 보존한다.
- 게시: 그래프 보고서와 체크포인트를 보존하고 별도 `case-outbox`를 사용한다. AI 장애 시 pending, 다음 실행/재개 시작에 최대100개 재전송한다. 개별 사례 저장 시에도 게시를 시도한다. 새 모델 호출/클라우드 실행/승격은 재전송에 포함되지 않는다. 첫 미전송 건에서 서비스 오류가 나면 나머지는 보존된다.
- 검토: API 게시자는 승인할 수 없다. AI 서버 로컬 CLI/OS 접근 권한을 가진 운영자가 원본 보고서와 재검사 상세를 확인한다. 새 결과 버전은 승인 상태를 상속하지 않는다. 같은 버전의 승인을 거절로 바꾸면 검색에서 즉시 제외되며 이전 검토 이벤트는 남는다. 현재 실행 중 진단은 이미 선택한 사례 스냅샷을 사용한다.
- 검색: 동일 서비스/환경/노드 + 같은 오류 코드 또는 규칙 교집합. 최대200개 최신 승인 후보 중 최대3개를 결정적으로 선택한다. 텍스트 의미 검색이 아니며 문장만 비슷한 사례를 찾지는 않는다. 모델 입력에서 검토 기대값은 제외하고, 현재 관측과 과거 증거를 구분한다. 사례 ID/원본 실행/버전/결과 범위를 보고서 JSON/Markdown·기존 화면에 표시한다.
- 영속성: 기본 사례 DB는 기존 `DELTA_RULE_STORE`와 같은 디렉터리이므로 기존 AI 볼륨에 남는다. 다른 경로의 `DELTA_CASE_STORE`를 지정할 때는 해당 경로를 영속 볼륨으로 연결해야 한다. SQLite 파일0600, outbox0600, 사례 디렉터리0700. 기존 보고서/룰 후보 DB는 수정하지 않는다.

## 운영자 사용법

AI 서버에서 해당 서비스의 Python 환경으로 실행한다. 아래 경로는 예시이며 실제 `DELTA_RULE_STORE`/`DELTA_CASE_STORE`를 따른다. 생산자 토큰을 CLI 인수로 넘길 필요가 없다.

```bash
python -m delta_ai.case_cli --store /state/verified-cases.sqlite list
python -m delta_ai.case_cli --store /state/verified-cases.sqlite list --offset 50
```

목록의 최신 `case_id`/`revision`, `case.resolution.checks`의 상세와 원본 보고서를 확인한 뒤 검토 파일을 작성한다. 다음은 형식 예시이며 실제 결과를 확인한 운영자의 이름/근거/기대값으로 작성한다.

```json
{
  "decision": "approved",
  "reviewer": "operator",
  "reason": "동일 작업 ID와 이미지 바인딩, HTTP 재검사 근거를 확인했다. 원인 가설은 미확정이다.",
  "required_terms": ["HTTP", "작업"],
  "forbidden_terms": ["전체 이전 완료"]
}
```

```bash
python -m delta_ai.case_cli --store /state/verified-cases.sqlite review CASE_ID REVISION --file review.json
python -m delta_ai.case_cli --store /state/verified-cases.sqlite export CASE_ID --output candidates.json
python -m delta_ai.case_cli --store /state/verified-cases.sqlite evaluate candidates.json --llm fake --output fake-evaluation.json
```

`required_terms`는 모델 가설에서 자동 생성하지 않는다. 독립 기대 확인 항목이 없는 사례도 검색용 승인할 수 있지만 평가셋으로 추출할 수는 없다. `decision: rejected` 검토 파일로 같은 명령을 실행하면 승인 취소 이력을 추가한다. export/evaluate 출력은0600, 기존 파일 덮어쓰기 금지다.

실제 모델 비교는 승인된 비용 범위에서 `--llm claude`로 실행한다. 키는 기존 비밀 파일에서 해당 명령에만 제공하고 출력하지 않는다. baseline/with_cases 각1회이므로 사례 수의2배 모델 호출이다. fake/none 결과를 실제 모델 성능으로 설명하지 않는다.

## 평가 해석

추출한 평가 후보는 request/report/검증 결과와 정확한 버전, 독립 기대 문자열·금지 단정을 포함한다. 평가기는 먼저 승인 검색 후보를 고정하고 모든 평가 원본 ID/동일 관측 signature를 제외한다. 실행 ID/커밋 해시가 달라졌어도 같은 관측 본문은 동일 signature로 정규화한다. 기대값과 저장된 원래 AI 보고서는 모델에게 주지 않는다.

출력 지표는 baseline/with_cases의 통과 수, 누락 확인 항목, 금지 단정, 모델 분석 상태, 검색한 사례/스냅샷이다. 문자열 검사는 의미적 원인 정확도 전체를 측정하지 않는다. 판단 품질 개선은 실제 모델·독립 평가셋으로 후속 실측해야 한다.

## 통합·복구 주의

이 소스는 onprem 검증 브랜치 기준이며 공용 런타임 최신본으로 자동 배포하지 않았다. 기존 공유 오케스트레이터 변경을 보존해 통합해야 한다. AI/그래프 계약을 함께 갱신하며 기존 인증/TLS/볼륨을 보존한다. 사례 쓰기/검색 장애는 원래 실행 게이트를 바꾸지 않는다. 기능을 되돌릴 때 기존 보고서/체크포인트/사례 DB/outbox를 삭제하지 않는다. 이전 버전 AI는 새 context/사례 게시 API를 지원하지 않으므로 두 서비스 동시 호환 검증이 필요하다.

## 검증 기록

2026-11-06, 준비된 Python3.12 venv와 이 worktree의 소스 경로를 명시해 실행했다. 서로 같은 테스트 모듈 이름이 있어 AI와 pipeline/orchestrator는 분리 실행한다.

```bash
PYTHONPATH=services/delta-ai ../delta-network-gateway-diagnostics-handoff/.venv/bin/python -m pytest -q services/delta-ai/tests --tb=short --junitxml=../notes/verified-case-learning-20261106/ai-tests.xml
PYTHONPATH=services/delta-ai:services/delta-pipeline:services/delta-orchestrator:platform/backend ../delta-network-gateway-diagnostics-handoff/.venv/bin/python -m pytest -q services/delta-pipeline/tests services/delta-orchestrator/tests --tb=short --junitxml=../notes/verified-case-learning-20261106/pipeline-tests.xml
node --check services/delta-orchestrator/delta_orchestrator/static/app.js
git diff --check
PYTHONPATH=services/delta-ai ../delta-network-gateway-diagnostics-handoff/.venv/bin/python -m delta_ai.case_cli --help
```

- AI **333 passed / 1.93秒**, pipeline/orchestrator **187 passed / 24.27秒**, 합계 **520 passed**. 새 사례 전용 시험은 AI14개 + pipeline11개로 기존 회귀를 포함한다. pytest-asyncio의 기존 fixture-loop 기본값 경고는 남아 있으며 실패는 없다.
- 실제 그래프를 로컬 가짜 실행 도구로 구동하여 수정→새 커밋/빌드→DAST 재검사 성공/다시 재현된 실패/사람 승인으로 미해결 유지/중단을 확인했다.
- 실제 DiagnosticClient 직렬화·Bearer·크기 제한을 FastAPI TestClient에 연결하여 그래프→인증 게시→검토→다음 진단의 과거 사례→평가셋 추출까지 검증했다. 외부 HTTP 서버나 실제 클라우드 스캐너 호출 시험은 아니다.
- 별도 프로세스를 사람 확인 대기/재배포 접수 뒤/조회 뒤에 강제 종료하고 재개했다. 각 시험은 기존3회 실패 후 사람 재배포1회, 총 native작업4개를 유지하며 완료 사례1개를 기록했다. 실제 k3s 장애 시험이 아니라 영속 fake provider fixture다.
- DB 재시작/동시 게시8건/중복·역순 재전송, 새 버전 재검토, 승인 취소 감사, 현재 실행/같은 관측/다른 서비스·환경·오류 제외, 비밀값 마스킹, 미인증401·검증오류422 비밀값 비반사·과대입력413을 확인했다.
- 기대값 독립 작성, 평가 원본·중복 제외, 모델 호출 중 승인 추가에도 평가 검색 스냅샷 유지, 현재 근거/과거 사례 인용 검증 및 조회 실패 한계를 확인했다. 품질 평가 시험은 가짜 모델이며 실제 진단 품질 향상 수치가 아니다.
- JS 구문/CLI 도움말/공백 검사 통과. 보안 검토는 인증·생산자/검토 분리·서비스 범위·SQL 매개변수·영속 파일 권한·비밀값·출력 HTML escaping을 확인했다. 변경 범위에서 미해결 critical/high 발견 없음. 실제 클라우드·실제 Claude·브라우저 시각 검증·공용 최신본 재통합은 실행하지 않았다.

증거는 작업 루트 `notes/verified-case-learning-20261106/{ai-tests.xml,pipeline-tests.xml,summary.json}`에 있다. Git 소스는 미커밋/미푸시, PR/main 반영·원격 CI·AWS 변경·팀 Slack/Notion 쓰기 없음.


## 2026-11-06 최신 공용·실행 중 소스 통합 및 AWS 배포 승인

사용자가 최신 공용 코드와 충돌 없이 통합하고 실제 AWS 배포/Claude 피드백 검증을 요청했다. main `ad48e2e`와 공유 PR #60 `03a29a3`를 merge commit으로 보존했다. 기존 AWS 실행 이미지77ea8010의 미반영6개 파일은 사용자 명시 승인으로 코드만 읽어3방향 통합했다. 요청 멱등성/승인ID/정리 API/운영 자원 표시/온프레미스 ECR 선택을 보존하고, diagnostics 옵션 보강으로 멱등 요청 비교가 깨지는 경우를 회귀로 고정했다.

배포는 기존 AI/오케스트레이터2개만 변경하며 기존43실행/80영속파일·인증/설정/로그/볼륨을 보존한다. 기대 이전 이미지/동시변경·idle 재확인, 후보 인증 검사, 중지 후 state 백업 확인, 실패 시 기존 컨테이너 복구를 사용한다. Terraform/SG/IAM/DNS/팀 Worker/스캐너 변경은 하지 않는다. 검증은 독립 service_id의 로컬HTTP 장애를 AWS호스트 안에 만들고 실제Claude/API/사례게시/승인/다음진단/평가를 검사한다. 합성장애임을 기록하며 고객 앱 장애·전체 이전으로 설명하지 않는다.

통합 회귀: AI337통과, pipeline/orchestrator210통과(이후 요청멱등성 회귀1건 추가). 최종 실행·배포 결과는 완료 후 갱신한다.

## 2026-11-06 실제 AWS 배포·Claude 검증 완료

- 최종 서비스 소스 `523ae5a0f596f3ecc9e0a6c02949f4d688e19a48`. main `ad48e2e`·공유 PR #60 `03a29a3`를 merge commit `9e75ae3`으로 보존했고 실행 중 공용6개 파일의 미반영 변경을 추가 통합했다. 최종 main 재조회에서도 ad48e2e가 유지됐다. main 직접 push/PR 병합은 하지 않았다.
- AI 기존 `10.90.10.90`: `delta-cp-ai/api@sha256:7f387e27cc11de0171a133e57aa030ef5461b00e41d585b6dd39270cb4f7c746`. 오케스트레이터 기존 `10.90.10.75`: `delta-cp-orchestrator@sha256:db5d8bff7395debc2fe7fdfb65609093d542559fc4b44556e987af9c5a2b7431`.
- AI 배포 SSM `497d718f-5fd7-4c10-9ce6-63523413b148`, 오케스트레이터 `a00cf015-4392-4639-bc94-cb9706b622a7`. 후보검사0모델호출, 기존 인증/TLS/환경/로그/볼륨 보존, 중지 후 state 백업 검증. 복구용 이전 컨테이너는 stopped로 보존했다. 사설NGINX는 기존 catch-all404를 유지하고 `/v1/incident-cases`만 추가했다.
- 실제 import 모듈 해시 AI38개/오케스트레이터70개가 커밋 소스와 일치했다. AI 지식134청크를 확인했다. 오케스트레이터 복사소스76파일도 일치한다.
- 기존43개 실행과80영속파일은 최종조회까지 모두 같은 해시다. 미완료 실행0개다. 검증용 기록은 원래 runs 밖의 `/state/case-learning-verification/1aa722776440495bb635c6106af1301d/`에 분리했다. 고객 빌드/스캐너/배포/DNS 작업 제출0건, SG/IAM/Terraform/팀Worker 변경0건이다.
- 검증 그룹 `1aa722776440495bb635c6106af1301d`. first: 실제AWS컨테이너 안의 독립 loopbackHTTP서버503 → 실제Claude Sonnet5.5 진단9.82초 → 검증용 handler 복구 → 실제HTTP200 → success 사례3버전 게시. 사용자 승인 검증프로그램이 로컬CLI로 합성사례만 승인했다. 사람의 실제 운영승인이라고 표기하지 않는다.
- next: 새 독립HTTP503 → 실제Claude Sonnet5.5 진단9.35초. 승인된 first를 `related_cases`로 검색했고 모델의 `historical_case_ids`에도 실제first ID가 들어갔다. 복구 없이 반복한 실제503을 failure로 기록했다. 과거 성공을 현재 해결/원인 확정으로 해석하지 않았다.
- next를 독립기대값 `503`으로CLI평가셋에 추출했다. 평가기는 next원본/동일관측을 제외하고 실제Claude baseline/with_cases 각1회 실행(총21.2초). baseline1/1·with_cases1/1, 각각 검색0건/first1건. 총 실제모델4호출이다. 합성1건의 연결검증이므로 정확도 향상을 주장하지 않는다.
- 검증완료 후 first/next의 검색승인을 취소했다. 원본2사례/6버전·감사4이벤트·평가파일은 보존했다. AI 정상재시작 뒤 영속5파일 해시와 해당사례/감사·승인취소를 재확인했다.
- 공개 `https://d2rjxujgsi1fjg.cloudfront.net/login`200, 익명runs/capabilities API401. 사설진단/복구/사례게시 각각 익명401·빈인증요청422. 브라우저 시각검증은 수행하지 않았다.
- 최종 로컬회귀 AI337 + pipeline/orchestrator211 = **548 passed**. XML `ai-tests.xml`, `integration-tests-final.xml`. `node --check`, `compileall`, `git diff --check` 통과. 실제배포는 위증거를 따르며 GitHub CI/머지와 구분한다.

증거 정본은 작업 루트 `notes/case-learning-aws-20261106/summary.json` 및 `ai-release.json`, `orch-release.json`, `verified-{first,next}.json`, `reviewed-{first,next}.json`, `evaluated.json`, `ai-restart-verified.json`, `orch-after.json`, `*-modules-verified.json`, `public-auth.json`이다. 실제제어기·모델/CLI검증 코드는 `deploy/case-learning/`에 보존했다. 기존 운영인증이나 원문자격증명은 기록·반출하지 않았다.


## 2026-11-06 콘솔 검토 후속

사례 검토·승인·취소와 독립 기대값 입력, 선택한 최신 승인 사례의 평가셋 JSON 추출을 기존 콘솔에 로컬 구현했다. 생산자와 분리된 운영자 API와 기존 관리자 세션을 사용한다. CLI도 유지한다. 전체 회귀561개와 실제 브라우저6시나리오 통과이며, 이 후속의 AWS 배포/검토 토큰·프록시 연결은 미실행이다. [후속 설계·검증 상태](case-review-console.md)와 [운영 연결](../../deploy/case-review-console/README.md)을 따른다.


2026-11-06 콘솔 후속도 AWS 배포를 완료했다. 최종 서비스76324d8, 최종 회귀562개·실제 관리자 화면의 사례승인/기대값입력/평가셋다운로드/취소 및 재시작영속 검증 완료. 기존45실행/118파일 보존, 원본4사례10버전 유지·감사2건만 추가. 비합성사례는 변경하지 않았다. [최신 운영 결과](case-review-console.md#2026-11-06-실제-aws-배포콘솔-검증-완료)가 이전 로컬 구현 상태를 대체한다.
