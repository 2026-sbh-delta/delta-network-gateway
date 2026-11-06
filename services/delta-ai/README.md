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

`DELTA_LLM_TIMEOUT_SECONDS`(기본 60)로 LLM 호출 시간 제한을 정한다. 재시도는 1회다.

컨테이너 실행은 [Dockerfile](Dockerfile) 로 하며 8090 포트, 비루트 사용자로 돈다. Jenkins 연동 예시는 [deploy/jenkins-local](../../deploy/jenkins-local/README.md) 에 있다.

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
| `migration_mapping` | 이전 전, 대상 자원을 만들기 전 | `evidence.mapping` (서브넷·서버·보안 도구). `scripts/extract_terraform.py` 로 만들 수 있다. 결과의 `mapping.cost` 는 서버 월 예상 비용 참고 값이다 |
| `migration_parity` | 이전 직전, 샌드박스 시험 후 | `source_env`, `target_env`, `evidence.attack_results` |
| `feedback_ingest` | 긴 리뷰·모의해킹 완료 후 | `evidence.report` |
| `deployment_gate` | 배포 단계·트래픽 단계 후 | `evidence.deployment` |
| `dast_verification` | DAST 점검이 끝난 뒤, 트래픽을 옮기기 전 | `evidence.dast` (점검 서비스가 관측한 항목별 상태) |
| `runtime_security` | 배포 후 주기적으로 | `evidence.image_vulnerabilities`, `cluster_checks`, `log_signals`, `network_posture` 중 하나 이상 |

`migration_parity` 는 `evidence.reachability`(실제 연결 시험)와 `target_env.network.firewall_rules` 도 받는다. 보내면 `PAR-NET-002`, `PAR-NET-003` 을 점검한다. 응답의 `verified_controls` 는 막혔다고 실측된 통제다.

`network_posture` 를 보내면 응답의 `posture` 가 네트워크·노드·클러스터 계층을 실측으로 나눠 `isolated`, `partial`, `open`, `unknown` 으로 판정하고, `upstream_firewall` 에 따라 할 수 있는 조치만 제안한다. 상위 방화벽을 관리할 수 없는 환경에는 VLAN 변경을 제안하지 않는다. 제안은 자동으로 적용하지 않는다.

온프레미스 노드에서 증거를 모아 보내려면 [수집기](scripts/collect_onprem.py)를 쓴다. 설정 형식은 [예시](scripts/collect_onprem.example.json)를 보고, 실제 주소가 든 설정은 저장소 밖에 둔다. 방화벽 접근이 없으면 `firewall_ssh` 를 빼고 `upstream_firewall` 을 `unmanaged` 로 둔다. 연결 시험은 설정한 대상과 `probe_default_gateway` 로 켠 기본 게이트웨이에만 보내며 주변 기기를 찾아 스캔하지 않는다. 노드를 격리로 인정받으려면 `vantage_ssh` 와 `inbound_probes` 로 노드 밖 지점에서 시험한다. 읽기 전용이지만 시험 공격 요청과 스캔용 파드는 만든다.

```bash
python scripts/collect_onprem.py --config <설정.json> --out <결과 폴더> --dry-run
```

## 판정

- `pass`: 막는 항목이 없다.
- `fix`: 전부 수정안이 있다. `remediation` 을 샌드박스에 적용하고 다시 호출한다.
- `needs_human`: `reasons` 를 보여 주고 사람이 정한다.

| `reasons` | 뜻 |
|---|---|
| `EVIDENCE_UNKNOWN` | 값이나 시험 결과를 모른다 |
| `NO_EVIDENCE_DOC` | 판단 근거 문서가 없다 |
| `AUTOFIX_FORBIDDEN` | 권한·시크릿·경로처럼 사람이 정할 변경이다 |
| `PATCH_MISSING` | 수정안이 없다 |
| `LLM_OUTPUT_INVALID` | LLM 이 없는 근거를 인용했거나 형식이 틀렸다 |
| `RULE_APPROVAL_REQUIRED` / `DETECTOR_MISSING` | 규칙 후보 승인이 필요하거나 탐지기를 못 만들었다 |

`checks` 는 대시보드 신호등용이다(`green`·`red`·`unknown`).

## 현재 한계

- 코드 보안 판단(`code_security`, SARIF 입력, 코드 패치)은 2026-11-02에 뺐다. SAST는 별도 서비스에서 다시 설계한다.
- 승인된 규칙 후보(`/v1/rule-candidates`)를 적용할 코드 검사가 지금은 없다. 적용 지점은 SAST 쪽에서 정한다.
- 인벤토리·공격 결과·보고서 픽스처는 손으로 쓴 예시이고 팀 실제 형식이 아니다.
- WAF 수정안은 AWS → GCP(Cloud Armor)와 GCP → AWS(AWS WAF 관리형 그룹)만 만든다. 다른 방향은 사람에게 넘긴다.
- 샌드박스 수정 에이전트는 아직 없다. 지금은 수정안을 제안만 한다.
- 근거 검색은 규칙 ID 정확 매칭만 한다. 공식 문서 전체를 색인하지 않았다.
- 규칙 저장소는 프로세스 1개 전제의 JSON 파일이다.

`delta_ai/llm.py`·`knowledge.py`·`secrets_pattern.py`·`errors.py`·`resources.py` 는 예선 review-service 에서 가져왔다.

## 검증된 장애 사례 피드백 (2026-11-06)

진단 보고서는 기존 계약을 유지하고 검토된 과거 사례를 별도 참고 자료로 사용한다. 이는 `feedback_ingest` 탐지 규칙 후보와 별개이며, 승인된 탐지 규칙을 SAST로 활성화하는 기능은 추가하지 않는다.

- 그래프가 인증된 `POST /v1/incident-cases`로 게시한다. 게시자 토큰에는 사례 승인 API가 없다.
- 사례는 `DELTA_CASE_STORE` 또는 `DELTA_RULE_STORE`와 같은 디렉터리의 `verified-cases.sqlite`에 영속 저장한다.
- 서버 로컬 `python -m delta_ai.case_cli`로 목록 확인·정확한 버전 검토·승인 취소·평가셋 후보 추출을 수행한다.
- 진단 요청의 `context`가 없으면 사례 검색을 하지 않아 기존 호출과 호환된다.
- 조치 결과와 원인 가설의 정확성은 별개다. 동일 서비스/환경/실패 단계에서 검증·승격된 사례만 참고하며 현재 실행/동일 관측은 제외한다.
- 실제 운영 배포·사례 데이터 이관은 이번 로컬 구현에 포함되지 않는다. [상세 절차](../../docs/ai-dlc/verified-case-learning.md).
