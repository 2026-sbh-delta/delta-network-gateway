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
| delta-ai | http://127.0.0.1:18090 | 시연 콘솔·수집기 호출용, 토큰 필요 |

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

Jenkins 의 `SCENARIO` 파라미터로 고른다.

| 값 | 넣는 문제 | 기대 결과 |
|---|---|---|
| `good` | 없음 | 10→50→100% 승격, SUCCESS |
| `vulnerable-code` | 셸 명령 주입 코드 | Semgrep 탐지 후 사람 확인에서 멈춤 (AI 판단 없음) |
| `failing-test` | 깨지는 테스트 | 배포 전 중단, 원인 분석 |
| `bad-canary` | 새 버전이 500 을 낸다 | 10% 에서 자동 롤백, 원인 분석 |
| `gate-down` | 판단 서비스 호출 불가 | 사전 확인에서 트래픽 전에 중단 |
| `low-traffic` | 표본이 20건 미만 | 사람 확인에서 멈춤 |

## 파이프라인

```text
판단 서비스 확인 → 테스트 → SAST(Semgrep) → 탐지 시 사람 확인 → 재발 확인 → 이미지 빌드 → 스모크 → 대기 슬롯 배포
  → 10% → 판단 → 50% → 판단 → 100% → 판단 → 이전 슬롯 정리
```

| 판정 | `gate.py` 종료 코드 | 파이프라인 동작 |
|---|---|---|
| `pass` | 0 | 다음 단계 |
| `fix` | 10 | 중단. 패치를 `sast-result.json` 에 남긴다. 자동 적용은 아직 없다 |
| `needs_human` | 20 | 표본 부족은 `input` 으로 대기. 트래픽 지표 초과는 자동 롤백 |
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

## 클라우드 이전

Jenkins 잡 `delta-cloud-migration` 이 판단 → 갭 메우기 → 대상 배포 → 동등성 확인 → 서비스별 단계 전환을 한 번에 돈다. 설계는 [클라우드 이전 파이프라인](../../docs/ai-dlc/cloud-migration-pipeline.md). **클라우드 자원은 만들지 않는다.** AWS·GCP 는 로컬 컨테이너와 HAProxy 대역이다.

| 대상 | 주소 | 비고 |
|---|---|---|
| 서비스별 비율 화면 (traffic-control) | http://127.0.0.1:18082 | 수동 모드에서 서비스마다 대상 비율을 바꾼다 |
| 이전 게이트웨이 | http://127.0.0.1:18083/ , /api/hello | `X-App-Version` 이 `<클라우드>-…` 로 어느 쪽이 응답했는지 보인다 |

| 파라미터 | 값 |
|---|---|
| `DIRECTION` | `aws-to-gcp`, `gcp-to-aws` |
| `SCENARIO` | `good`, `scc-standard`(GCP 대상 Standard), `iam-gap`(대응 없는 권한), `security-gap`(대응 없는 보안 기능), `bad-target`(api 대상 50% 실패), `gate-down` |
| `SCC_TIER` | `ask` 면 판단 뒤 묻는다. AWS 대상에는 쓰지 않는다 |
| `CUTOVER_MODE` | `auto`(`CUTOVER_STEPS` 순서) 또는 `manual`(화면) |

사람이 정할 것(SCC 등급, 잃어도 되는 능력, 자동으로 옮기지 않는 차이)이 있으면 입력 단계에서 멈춘다. 승인하지 않으면 진행하지 않는다. 빌드마다 `target-plan.json`(대상 타입·구조·WAF·보안 기능), `terraform-gaps.json`(대상 Terraform root 에 아직 없는 변수), `cutover-state.json` 을 남긴다.

## 테스트

```bash
../../services/delta-ai/.venv/bin/python -m pytest -q tests
```

## 여기서 확인할 수 없는 것

- 사설망·ANF 경유, Jenkins 의 인터넷 경로, 실제 서버 메모리(t4g.nano·e2-micro)
- 실제 AWS·GCP 로의 교차 배포(이전은 로컬 대역으로만 확인한다), 실제 Cloud Armor·AWS WAF·GuardDuty·SCC
- 동규 님의 실제 HAProxy 설정
- Dependency-Check, SonarQube, ZAP (아직 넣지 않았다)
