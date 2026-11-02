# 파이프라인 고정 전이 그래프

- 기준일: 2026-11-02 (Asia/Seoul)
- 상태: 1단계 로컬 구현·검증 완료. **브랜치는 푸시했고(PR 전) 서버 배포는 안 함.** AWS·GCP 이전은 포함하지 않는다.
- 브랜치: `feat/pipeline-graph` (`feat/demo-integration` `ef323a0` 에서 분기)
- 승인: 사용자 지시 "네, 1단계 시작"을 아래 범위의 Ideation·Inception·Construction 승인으로 기록한다. 팀 합의 전이다.
- 결정 근거: [ADR-AI-40](../../services/delta-ai/docs/adr/ADR-AI-40-pipeline-graph.md) (제안)

## Ideation

- 문제: 흐름 구동 코드가 콘솔의 `flow.py` 에 직접 짜여 있어 재시작하면 실행을 잃고, 이후 도구와 수정 반복을 붙일 자리가 없다.
- 사용자: 시연자(김연재), 파이프라인 담당 팀원(정준우).
- 성공 기준: 시연 3개 시나리오가 전과 같게 돌고, 게이트를 통과하지 못하면 배포를 호출하지 않으며, 강제 종료 뒤 같은 배포를 두 번 시작하지 않고 이어 간다.
- Non-goals: AWS·GCP 배포, Jenkins 작업 분리, SAST 연결, `fix_review`, HTTP API, 취소, 대상별 잠금.

## Inception

- `services/delta-pipeline` 에 LangGraph 그래프를 두고 콘솔은 화면과 입구만 맡는다. 전이는 코드로 고정하고 LLM 은 전이를 고르지 않는다.
- 저장은 `durability="sync"` 로 한다. 시험에서 기본 저장은 강제 종료 시 끝난 노드를 다시 돌렸다(3회 중 3회).
- Jenkins 빌드에 `RUN_ID` 를 넣어 재개 때 같은 빌드를 찾는다. 롤백은 Jenkins 에 그대로 둔다.
- 수용 기준: 기존 흐름 테스트 통과, 프로세스 강제 종료 시험 통과, 실제 로컬 스택 3개 시나리오와 강제 종료 재개 통과.

## 검증 기록 (2026-11-02)

| 명령·동작 | 결과 |
|---|---|
| `services/delta-pipeline`: `.venv/bin/python -m pytest -q` | 32개 통과 (기존 흐름 + 게이트·재개·조회 실패) |
| `deploy/jenkins-local/tests` (delta-ai 가상환경) | 51개 통과 |
| 코드를 일부러 망가뜨려 테스트 확인: 빌드 찾기 제거, async 저장, DAST 사람 확인 건너뜀, 재검증 판정 무시, 배포 전 재확인 제거 | 각각 테스트 실패로 잡음. 사람 확인 건너뜀과 재확인 제거는 처음엔 못 잡아 테스트를 추가했다 |
| 1차 실제 실행 (빌드 #88~90) | **정상·취약 실패.** Jenkins 재시작 직후 조회 15초 초과 한 번으로 흐름이 멈췄고, #88 은 혼자 계속 돌아 #89·#90 과 겹쳤다. 장애·강제 종료 재개는 통과 |
| 수정: 조회 5회까지 재시도, 작업 설정에 동시 빌드 금지 추가 | 재시작 직후에도 동시 빌드 금지가 켜진 것을 확인 |
| 2차 실제 실행 (콘솔 API, 빌드 #91~95) | 정상 `deployed`(기준 버전 #91 후 #92), 취약 재검증 후 `deployed`(#93), 장애 `rolled_back`(#94), 카나리 중 콘솔 SIGKILL 후 재개 `deployed`(#95, 같은 `RUN_ID` 빌드 1개) |
| 화면에서 정상 시나리오 실행 (빌드 #96) | 사람 확인·완료 표시 정상, 게이트웨이 요청 421건 실패 0건 |

실제 실행은 로컬 Docker 대역, DAST `reviewed`(LLM 없음), delta-ai 실제 Claude 다. 온프레미스 판단은 저장된 결과를 재사용했다.

## 알려진 한계

- 조회가 5회 연속 실패하면 흐름은 멈추지만 Jenkins 빌드는 계속 돌 수 있다. 취소 API 가 없어 사람이 Jenkins 에서 멈춰야 한다.
- 사람 확인 대기 시간은 재개 때 처음부터 다시 잰다. DAST 서버가 재시작되면 진행 중 점검을 잃고 멈춘다(통과로 보지 않는다).
- 콘솔 밖 입구(`POST /v1/runs`)는 없다. 앞서 제안한 계약은 2단계 이후에 맞춘다.
- `feat/demo-integration` 작업 폴더의 미커밋 `migration_mapping` 변경은 이 브랜치에 없다.

## Operation

- 실행: [demo-console](../../deploy/demo-console/README.md) 의 `up.sh`. Jenkins 를 이 브랜치의 `casc.yaml`·`Jenkinsfile` 로 다시 띄워야 `RUN_ID` 가 적용된다.
- 2026-11-02 검증 뒤 로컬 Jenkins 는 `feat/demo-integration` 작업 폴더로 되돌렸다. 이 상태로는 새 콘솔의 재개 멱등이 동작하지 않는다.

## 다음 작업

AWS·GCP 프로비저닝·배포·전환 경로를 정하고 그래프에 도구 자리를 만든다(ADR-AI-40 "아직 정하지 않은 것").
