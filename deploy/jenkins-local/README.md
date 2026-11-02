# Jenkins 로컬 시험대

Jenkins 파이프라인이 판단 서비스(`services/delta-ai`)를 단계마다 호출하고, 그 결과로 카나리 승격·롤백·사람 확인을 정하는 흐름을 노트북에서 확인한다. 설계와 검증 기록은 [AI-DLC 문서](../../docs/ai-dlc/jenkins-local.md)에 있다.

**로컬 전용이다.** Jenkins 컨테이너가 root 로 돌고 `docker.sock` 을 마운트하므로 그대로 서버에 올리지 않는다. HAProxy 설정도 동규 님이 만드는 실제 설정이 아니라 대역이다.

## 실행

```bash
./up.sh
```

`.env` 가 없으면 임의의 비밀번호와 토큰으로 만든다. 값은 출력하지 않는다.

| 대상 | 주소 | 비고 |
|---|---|---|
| Jenkins | http://127.0.0.1:18080 | `admin`, 비밀번호는 `.env` |
| 서비스 | http://127.0.0.1:18081/api/hello | HAProxy, `X-App-Version` 헤더로 버전 확인 |
| delta-ai | 외부 포트 없음 | Compose 내부망에서만 호출 |

```bash
./down.sh            # 멈춘다. 빌드 기록은 남는다
./down.sh --volumes  # 빌드 기록까지 지운다
```

실제 Claude 로 판단 서비스만 다시 띄운다. 키는 파일에 쓰지 않고 이 명령에만 넘긴다.

```bash
ANTHROPIC_API_KEY="$(cat ~/.config/anthropic/delta-ai.key)" DELTA_LLM=claude docker compose up -d --wait delta-ai
```

가짜 LLM 으로 되돌린다.

```bash
docker compose up -d --wait delta-ai
```

## 시나리오

Jenkins 의 `SCENARIO` 파라미터로 고른다. Job 을 처음 올린 뒤에는 Jenkins 가 파라미터를 아직 모르므로 첫 빌드 한 번은 기본값(`good`)으로 돌아간다.

| 값 | 넣는 문제 | 기대 결과 |
|---|---|---|
| `good` | 없음 | 10→50→100% 승격, SUCCESS |
| `vulnerable-code` | 셸 명령 주입 코드 | 가짜 LLM: 사람 확인에서 멈춤 / Claude: 패치를 샌드박스에서 검증한 뒤 사람 승인으로 배포 |
| `bad-patch` | 패치와 충돌하는 테스트 | 수정 루프가 멈추고 배포하지 않음 |
| `failing-test` | 깨지는 테스트 | 배포 전 중단, 원인 분석 |
| `bad-canary` | 새 버전이 500 을 낸다 | 10% 에서 자동 롤백, 원인 분석 |
| `gate-down` | 판단 서비스 호출 불가 | 통과로 보지 않고 중단 |
| `low-traffic` | 표본이 20건 미만 | 사람 확인에서 멈춤 |

## 파이프라인

```text
테스트 → SAST(Semgrep) → 판단(수정 루프) → 재발 확인 → 이미지 빌드 → 스모크 → 대기 슬롯 배포
  → 10% → 판단 → 50% → 판단 → 100% → 판단 → 이전 슬롯 정리
```

| 판정 | `gate.py` 종료 코드 | 파이프라인 동작 |
|---|---|---|
| `pass` | 0 | 다음 단계 |
| `fix` | 10 | 수정 루프 실행. 검증되면 사람 승인 뒤 배포, 아니면 중단 |
| `needs_human` | 20 | SAST·표본 부족은 `input` 으로 대기. 트래픽 지표 초과는 자동 롤백 |
| 응답 없음 | 30 | 통과로 보지 않는다. 트래픽 단계면 롤백 |

- 트래픽 지표가 기준을 넘으면 `traffic.py` 가 종료 코드 3 을 내고, 판단 서비스 응답을 기다리지 않고 먼저 롤백한다. LLM 은 그 뒤 원인 설명만 한다.
- 판정 자체가 중단 사유일 때는 실패 단계 분석을 다시 부르지 않는다. 종료 코드 0 인 단계를 실패로 보고하게 되기 때문이다.

산출물은 빌드마다 보관한다.

| 파일 | 내용 |
|---|---|
| `events.jsonl` | 판단 호출 이벤트(run_id, step, state, verdict, timestamp) |
| `logs/<단계>.log`, `.exit` | 단계별 로그와 종료 코드 |
| `sast.sarif`, `sast-result.json` | Semgrep 결과와 판단 결과 |
| `metrics-<비율>.json`, `gate-<비율>.json` | 트래픽 단계 지표와 판단 결과 |
| `failure-analysis.json` | 실패 원인 분석 |

## 테스트

```bash
../../services/delta-ai/.venv/bin/python -m pytest -q tests
```

## 여기서 확인할 수 없는 것

- 사설망·ANF 경유, Jenkins 의 인터넷 경로, 실제 서버 메모리(t4g.nano·e2-micro)
- AWS→GCP 교차 배포, ARM/x86 이미지 빌드
- 동규 님의 실제 HAProxy 설정
- Dependency-Check, SonarQube, ZAP (아직 넣지 않았다)
