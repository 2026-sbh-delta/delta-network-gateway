# Jenkins 배포와 AI 판단 로컬 검증

- 기준일: 2026-11-02 (Asia/Seoul)
- 상태: 로컬 구현·검증 완료. **커밋·푸시·AWS 배포 안 함.** 사용자 지시 "우선 로컬에서 진행"에 따른 시험이며 팀 합의 전이다.
- 실행 방법: [deploy/jenkins-local](../../deploy/jenkins-local/README.md)
- 도구 선택 근거: [ADR-AI-25](../../services/delta-ai/docs/adr/ADR-AI-25-jenkins-gate.md) (제안)

## Ideation

- 문제: 배포 중 나오는 로그·지표·오류를 AI 판단과 연결할 수 있는지 AWS 자원 없이 먼저 확인한다.
- 사용자: 배포 담당(김연재), 시연을 보는 심사위원.
- 성공 기준: 나쁜 배포는 사람 없이 멈추고, 판단 서비스가 죽어도 통과로 처리되지 않으며, LLM 지시문 주입으로 판정이 바뀌지 않는다.
- Scope: Jenkins 파이프라인, `deployment_gate` 판단 종류, HAProxy 대역 위의 카나리.
- Non-goals: 실제 네트워크·서버 배포, 동규 님의 HAProxy 구현, 수정안 자동 적용, Dependency-Check·SonarQube·ZAP.

## Inception

- 판정은 코드가 정한다. LLM 은 설명과 패치만 만든다.
- 근거가 없으면 통과가 아니다. 지표 값·기준·표본이 없거나 표본이 20건 미만이면 `needs_human`(DEP-METRIC-002).
- 롤백은 판단 서비스를 기다리지 않는다. 지표 초과는 파이프라인이 먼저 롤백하고 LLM 은 그 뒤 설명한다.
- 실행과 판단을 분리한다(ADR-AI-05). 판단 서비스는 배포 명령을 실행하지 않는다.

## Construction

| 단위 | 위치 | 상태 |
|---|---|---|
| `deployment_gate` 계약·규칙 3개·문서 | `services/delta-ai/delta_ai/deployment.py`, `knowledge/rules/deployment/` | 구현·테스트 |
| 판단 서비스 이미지, LLM 시간 제한 | `services/delta-ai/Dockerfile`, `delta_ai/api.py` | 구현·실행 확인 |
| Jenkins·HAProxy·데모 앱 스택 | `deploy/jenkins-local/` | 구현·실행 확인 |
| 파이프라인, 호출·트래픽 스크립트 | `deploy/jenkins-local/pipeline/` | 구현·테스트 |

Jenkins 2.580.2-lts-jdk21, 플러그인 7개는 `jenkins/plugins.txt` 에 버전을 고정했다. 고정 버전으로 이미지를 다시 빌드해 아래 빌드 #15 이후를 돌렸다.

## 검증 기록 (2026-11-02)

단위 테스트:

| 명령 | 결과 |
|---|---|
| `services/delta-ai`: `.venv/bin/pytest -q` | 117 통과 |
| `deploy/jenkins-local`: `python -m pytest -q tests` | 24 통과 |

실제 파이프라인 실행(최종 코드, 가짜 LLM):

| 빌드 | 시나리오 | 결과 | 확인한 것 |
|---|---|---|---|
| #16~18, #26 | `good` | SUCCESS | 10→50→100%, 오류율 0, p95 약 2~25ms |
| #19 | `low-traffic` 진행 | SUCCESS | 10% 사람 승인, 이후 통과 |
| #21 | `gate-down` | FAILURE | 판단 불가를 통과로 보지 않음, 배포 전 중단 |
| #22 | `bad-canary` | FAILURE | 10% 오류율 0.575 로 자동 롤백, 기존 버전 19 계속 제공 |
| #23 | `failing-test` | FAILURE | 배포 전 중단, DEP-STAGE-001 분석 |
| #24 | `vulnerable-code` 중단 | ABORTED | 사람 확인 대기 후 중단, 실패 분석 호출 없음 |
| #25 | `low-traffic` 중단 | ABORTED | DEP-METRIC-002, 기존 버전 유지 |

실제 Claude(`claude-sonnet-5-5`):

| 빌드 | 시나리오 | 결과 |
|---|---|---|
| #20 | `vulnerable-code` | `fix` 와 패치로 FAILURE, 잘못된 단계 분석 없음. 앱 버전 19 유지 |
| 이전 빌드 | `vulnerable-code` | 패치는 인자 목록과 `--` 사용. 13.1초, 토큰 2085/1519 |
| 이전 빌드 | 실패 분석 | 4~5초. 로그에 없는 내용은 없다고 답함 |

통제 비교: 같은 서버 코드에 16 동시 요청 100건을 6회 보냈다.

| listen backlog | p95 | 900ms 초과 요청 |
|---|---|---|
| 5 (Python 기본값) | 1008~1068ms | 회당 9~10건 |
| 128 | 6~10ms | 0건 |

## 검증 중 발견한 것

1. **카나리 비율만큼은 사용자가 오류를 받는다.** 초기 `bad-canary` 빌드 #4 에서 외부 클라이언트가 롤백 전에 HTTP 500 을 4건 받았다. 게이트는 피해를 작게 만들 뿐 없애지 않는다. 시연에서 "오류 0"이라고 말하지 않는다.
2. **지연 게이트가 앱 설정 문제를 잡았다.** 빌드 #10 이 100% 에서 p95 1025ms 로 롤백됐다. 데모 앱의 listen backlog 가 5 였던 것이 원인이고(위 비교), 128 로 올린 뒤 재발하지 않았다. 다만 #10 당시 값을 직접 재현한 것은 아니고 같은 조건의 비교로 확인했다. 실제 서비스 앱에도 같은 점검이 필요하다.
3. **판정 중단을 단계 실패로 보고하던 결함을 고쳤다.** `fix` 판정 뒤 분석이 종료 코드 0 인 `sast` 를 실패로 설명했다. 판정이 중단 사유면 분석을 건너뛴다(#20, #24 로 확인).
4. **Semgrep 이미지 문제.** 최신 `setuptools` 에 `pkg_resources` 가 없어 `setuptools<81` 로 고정했다.
5. **post 실행 순서.** Declarative 는 `always` → `failure` → `cleanup` 순서다. 그래서 롤백은 `always`, 분석은 `failure`, 산출물 보관은 `cleanup` 에 둔다.

## 아직 안 된 것

- `fix` 패치 자동 적용과 재실행. ADR-AI-05 의 샌드박스 적용 주체가 없다. 지금은 패치를 남기고 멈춘다.
- 이벤트는 판단 호출 때만 남긴다. ADR-AI-24 진행 그래프에 쓰려면 단계 시작·종료도 내보내야 한다.
- Dependency-Check, SonarQube, ZAP.
- 사설망·ANF, Jenkins 외부 경로, 실제 서버 메모리, AWS→GCP 교차 배포, ARM/x86, 실제 HAProxy.
- `gate-down` 은 실패 분석도 판단 서비스가 필요해 원인 설명 없이 끝난다.
- Jenkins 자체의 보안 설정. 로컬 전용 구성이라 서버용으로 다시 만들어야 한다.

## Operation

- 시작·중지: [README](../../deploy/jenkins-local/README.md). 로컬 스택 외 배포 없음.
- 롤백: 파이프라인이 HAProxy 가중치를 안정 슬롯 100 으로 되돌리고 카나리 컨테이너를 지운다.
- 관측: 빌드 산출물(`events.jsonl`, 단계 로그, 지표·판단 JSON).
