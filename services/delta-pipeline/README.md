# delta-pipeline

빌드 → SAST → 스테이징 → DAST를 잇는 고정 전이 그래프다. 오케스트레이터(`services/delta-orchestrator`)가 이 그래프를 띄우고, 실행은 각 도구가 맡는다. 여기서는 순서, 게이트 판정, 사람 확인, 재개만 다룬다.

연재님의 `feat/pipeline-graph`(dab7b38, [ADR-AI-40](../delta-ai/docs/adr/ADR-AI-40-pipeline-graph.md))를 바탕으로 했다. 재개 규칙, 시작·대기 노드 분리, 사람 확인(`interrupt`), Jenkins 호출은 그대로 쓴다. 바꾼 부분은 아래와 같다.

- **흐름:** 웹 갈래(명세 v1.1 6-2) 최소 연결로 바꿨다. 온프렘 증거, 카나리, 이전(대역) 노드는 이 흐름에 없어서 가져오지 않았다. 원본 브랜치에 그대로 있다.
- **게이트:** delta-ai 호출이 아니라 이 코드가 판정한다(명세 v1.1 7장, 11/4 결정). delta-ai는 장애 원인 파악에 남긴다(`clients.DeltaAi`).
- **DAST:** 화면용 CSRF 토큰과 내장 샘플 입력 대신 기계용 토큰과 SAST가 만든 SARIF를 쓴다.
- **Jenkins:** 작업 두 개(`delta-build` 빌드 에이전트, `delta-deploy` 배포 전용 실행기)를 쓴다. 진행 상태는 플러그인 없이 기본 `api/json`으로 읽는다.

```text
prepare → (build_start → build_wait ∥ sast_start → sast_wait) → join ─┬→ ask_sast ─┐
                                                                     └────────────┴→ stage_start → stage_wait
  → dast_start ─┬→ dast_wait ─┬→ ask_dast ─┐
                │             └────────────┴→ done
                └→ done (점검 대상 0건)
  (어느 노드든 멈추면 → halted)
```

## 구조

| 파일 | 역할 |
|---|---|
| `graph.py` | 전이 표. 다음 단계는 코드로만 정한다. LLM 은 전이를 고르지 않는다 |
| `steps.py` | 노드 본문과 게이트 판정(최소판), 단계 직전 게이트 재확인 |
| `run.py` | 실행 구동, 사람 답 대기, 강제 종료 뒤 재개 |
| `model.py` | 실행 입력(`Options`), 화면용 노드 상태(`Board`) |
| `clients.py` | SAST·DAST·Jenkins·delta-ai HTTP 호출 |

## 게이트(최소판, 1-A에서 명세 7장 전체로 채운다)

- **SAST:** 점검 실패나 점검한 커밋이 다르면 멈춘다. 비밀값이 있으면 사람 확인을 받는다. 그 밖은 통과이고, DAST 대상이 0건이면 DAST를 건너뛴다.
- **스테이징:** Jenkins 실패, 준비 안 됨, digest 불일치, 데이터 초기화 기록 없음이나 남은 행이 0이 아니면 멈춘다.
- **DAST:** 모든 항목이 `NOT_REPRODUCED`일 때만 자동 통과다. 그 밖(재현, 판정 불가 등)은 사람 확인이다(D3, 자동 통과 금지).

## 재개 규칙

- 체크포인트는 `durability="sync"` 로 저장한다. 기본값으로 두면 강제 종료 때 끝난 노드가 다시 돌아 배포가 두 번 시작될 수 있다.
- 재개하면 끝나지 못한 노드만 처음부터 다시 돈다. 사람 확인 노드는 `interrupt` 앞에 부수 효과를 두지 않는다.
- Jenkins 빌드에는 `RUN_ID` 를 넣고, 시작 전에 같은 값의 빌드가 큐·이력에 있는지 찾는다. 이력은 최근 50개까지만 본다.
- SAST는 같은 `run_id`로 다시 시작하면 기존 점검을 돌려준다. DAST 시작과 결과 저장 사이에 죽으면 점검이 한 번 더 돈다. 배포에는 영향이 없다.
- 표시 상태는 `<상태 디렉터리>/runs/<run_id>.json`, 체크포인트는 `<상태 디렉터리>/checkpoints.sqlite` 에 있다.

## 테스트

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

LangSmith 추적은 켜지 않는다. `LANGSMITH_TRACING` 을 설정하면 실행 내용이 외부로 나갈 수 있다.
