# delta-pipeline

온프레미스 증거 → DAST → 판단 → Jenkins 배포를 잇는 고정 전이 그래프다. 판정은 delta-ai, 실행은 각 서비스가 맡고, 여기서는 순서와 사람 확인, 재개만 다룬다. 결정 근거는 [ADR-AI-40](../delta-ai/docs/adr/ADR-AI-40-pipeline-graph.md), 진행 기록은 [AI-DLC 문서](../../docs/ai-dlc/pipeline-graph.md)에 있다.

## 현재 범위

`deploy/demo-console/flow.py` 를 동작 그대로 옮긴 1단계다. **배포 대상은 로컬 Docker 대역이고 AWS·GCP 로 이전하지 않는다.** Jenkins 는 작업 하나이며, 실제 패치 재검증과 HTTP API(`POST /v1/runs`)는 아직 없다. 지금 입구는 시연 콘솔이다.

## 구조

| 파일 | 역할 |
|---|---|
| `graph.py` | 전이 표. 다음 단계는 코드로만 정한다. LLM 은 전이를 고르지 않는다 |
| `steps.py` | 노드 본문과 배포 전 게이트 재확인 |
| `run.py` | 실행 구동, 사람 답 대기, 강제 종료 뒤 재개 |
| `model.py` | 시나리오, 화면용 노드 상태(`Board`), 판단 요청 |
| `clients.py` | delta-ai·DAST·Jenkins HTTP 호출 |

## 재개 규칙

- 체크포인트는 `durability="sync"` 로 저장한다. 기본값으로 두면 강제 종료 때 끝난 노드가 다시 돌아 배포가 두 번 시작될 수 있다.
- 재개하면 끝나지 못한 노드만 처음부터 다시 돈다. 외부 호출을 시작하는 노드와 기다리는 노드를 나누고, 사람 확인 노드는 `interrupt` 앞에 부수 효과를 두지 않는다.
- Jenkins 빌드에는 `RUN_ID` 를 넣고, 시작 전에 같은 값의 빌드가 큐·이력에 있는지 찾는다. 이력은 최근 50개까지만 본다.
- 판단 호출은 재개 때 다시 나갈 수 있다. DAST 시작과 결과 저장 사이에 죽으면 점검이 한 번 더 돈다. 배포에는 영향이 없다.
- 표시 상태는 `var/pipeline/runs/<run_id>.json`, 체크포인트는 `var/pipeline/checkpoints.sqlite` 에 있다. `var/` 는 커밋하지 않는다.

## 실행과 테스트

콘솔이 함께 띄운다([demo-console](../../deploy/demo-console/README.md)).

```bash
uv sync --all-extras
.venv/bin/python -m pytest -q
```

LangSmith 추적은 켜지 않는다. `LANGSMITH_TRACING` 을 설정하면 실행 내용이 외부로 나갈 수 있다.
