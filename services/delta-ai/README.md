# delta-ai

Delta 본선 AI 판단 서비스 MVP. 플랫폼이 호출하면 규칙 검사와 근거 문서로 판정·수정안을 돌려준다. 커밋·배포는 하지 않는다.

## 실행

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/python scripts/demo.py fake
```

```bash
DELTA_API_TOKEN=<임의의 값> DELTA_LLM=fake .venv/bin/uvicorn delta_ai.api:app --port 8090
```

`DELTA_LLM` 은 `none`(기본)·`fake`·`claude` 이다. `claude` 는 `ANTHROPIC_API_KEY` 가 필요하다. `fake` 는 분석을 하지 않는 고정 응답이라 연동 확인용이다.

## API

모든 요청에 `Authorization: Bearer <DELTA_API_TOKEN>` 이 필요하다(`/healthz` 제외).

| 메서드 | 경로 | 용도 |
|---|---|---|
| POST | `/v1/assessments` | 판단 요청 |
| GET | `/v1/rule-candidates` | 규칙 후보 목록 |
| POST | `/v1/rule-candidates/{id}/decision` | 후보 승인·거절 `{"decision":"approved","approver":"이름"}` |

스키마는 [schema/](schema/), 요청 예시는 [fixtures/](fixtures/) 에 있다.

```bash
curl -s -X POST localhost:8090/v1/assessments -H "Authorization: Bearer $DELTA_API_TOKEN" -H 'content-type: application/json' -d @fixtures/parity-waf-missing.json
```

| `kind` | 호출 시점 | 필요한 입력 |
|---|---|---|
| `migration_parity` | 이전 직전, 샌드박스 시험 후 | `source_env`, `target_env`, `evidence.attack_results` |
| `code_security` | CI | `evidence.sarif`, `evidence.code_files` |
| `feedback_ingest` | 긴 리뷰·모의해킹 완료 후 | `evidence.report` |

## 판정

- `pass`: 막는 항목이 없다.
- `fix`: 전부 수정안이 있다. `remediation` 을 샌드박스에 적용하고 다시 호출한다.
- `needs_human`: `reasons` 를 보여 주고 사람이 정한다.

| `reasons` | 뜻 |
|---|---|
| `EVIDENCE_UNKNOWN` | 값이나 시험 결과를 모른다 |
| `NO_EVIDENCE_DOC` | 판단 근거 문서가 없다 |
| `AUTOFIX_FORBIDDEN` | 권한·시크릿·경로처럼 사람이 정할 변경이다 |
| `PATCH_MISSING` / `PATCH_REJECTED` | 패치가 없거나 게이트에서 거절됐다 |
| `LLM_UNAVAILABLE` / `LLM_OUTPUT_INVALID` | LLM 을 못 썼거나 없는 근거를 인용했다 |
| `RULE_APPROVAL_REQUIRED` / `DETECTOR_MISSING` | 규칙 후보 승인이 필요하거나 탐지기를 못 만들었다 |

코드 패치는 서버가 `evidence.code_files` 원본 기준으로 다시 만든 diff 이므로 `git apply` 로 적용한다. 원본 파일을 보내지 않으면 패치를 돌려주지 않는다.

`checks` 는 대시보드 신호등용이다(`green`·`red`·`unknown`).

## 현재 한계

- 픽스처 중 Semgrep SARIF(`demo-app.sarif.json`)만 실제 실행 결과다. 인벤토리·공격 결과·보고서는 손으로 쓴 예시이고 팀 실제 형식이 아니다.
- WAF 수정안은 AWS → GCP 만 만든다. 다른 방향은 사람에게 넘긴다.
- 샌드박스 수정 에이전트는 아직 없다. 지금은 패치를 제안만 한다.
- 근거 검색은 규칙 ID 정확 매칭만 한다. 공식 문서 전체를 색인하지 않았다.
- 규칙 저장소는 프로세스 1개 전제의 JSON 파일이다.

`delta_ai/llm.py`·`knowledge.py`·`secrets_pattern.py`·`errors.py`·`resources.py` 는 예선 review-service 에서 가져왔다.
