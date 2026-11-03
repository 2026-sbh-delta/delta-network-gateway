# delta-pipeline

온프레미스 증거 → DAST → 판단 → Jenkins 배포를 잇는 고정 전이 그래프다. 판정은 delta-ai, 실행은 각 서비스가 맡고, 여기서는 순서와 사람 확인, 재개만 다룬다. 결정 근거는 [ADR-AI-40](../delta-ai/docs/adr/ADR-AI-40-pipeline-graph.md), 진행 기록은 [AI-DLC 문서](../../docs/ai-dlc/pipeline-graph.md)에 있다.

## 현재 범위

**배포 대상은 로컬 Docker 대역이고, 이전 단계도 대역이다. 실제 AWS·GCP 를 건드리지 않는다.** Jenkins 는 작업 하나이며, 실제 패치 재검증과 HTTP API(`POST /v1/runs`)는 아직 없다. 지금 입구는 시연 콘솔이다.

`migration` 시나리오는 배포가 끝난 뒤 다음 순서로 간다.

```text
대상 준비 → 대상 배포 → 복제 시작(기다리지 않음) → 10·50·100% (단계마다 deployment_gate)
  → 복제 확인(지연 5초 이하) → 사람 승인 → 전환 직전 재확인 → 쓰기 전환
```

- 쓰기 전환 전에 판정이 통과가 아니거나, 판단 서비스가 안 되거나, 복제가 안 맞거나, 사람이 멈추면 비율을 0 으로 돌린다. 지연을 모르면 맞춰진 것으로 보지 않는다.
- 쓰기 전환을 시작한 뒤에는 자동으로 되돌리지 않는다. 대상에 새로 쓴 값이 출발지로 돌아오지 않기 때문이다. 실패하면 멈추고 사람이 확인한다.
- 실제 도구는 `MigrationTools` 계약에 맞춰 붙인다. 모든 호출은 같은 `run_id` 로 다시 불러도 안전해야 한다.

## 구조

| 파일 | 역할 |
|---|---|
| `graph.py` | 전이 표. 다음 단계는 코드로만 정한다. LLM 은 전이를 고르지 않는다 |
| `steps.py` | 노드 본문과 배포 전 게이트 재확인 |
| `migration_steps.py` | 이전 단계 노드 본문 |
| `migration.py` | 이전 도구 계약(`MigrationTools`)과 대역(`StandInMigration`) |
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
