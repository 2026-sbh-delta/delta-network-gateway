# delta-sast

Python(Flask) 소스를 점검해 SARIF 2.1.0을 낸다. DAST는 이 SARIF만 읽는다. 설계는 [docs/ai-dlc/sast.md](../../docs/ai-dlc/sast.md).

```text
① 목록   locate.py   Opengrep 규칙(rules/locate.yaml)으로 입력·싱크·비밀값 위치 표시
② 경로   graph.py    AST로 호출·데이터 흐름·저장소(dict·list·DB 테이블)를 이어 입력→싱크 경로와 출력 맥락
③ 분류   classify.py 필터 없음→후보 / 안전 목록→양호 / 캐시→재사용 / 그 밖→LLM 대기
④ 판단   judge.py    LLM 대기 경로만 Claude에 묻고 인용 줄·페이로드 틀을 검증
⑤ 출력   sarif.py    codeFlows + properties.dast + partialFingerprints
```

## 실행

opengrep 1.33.1이 PATH에 있어야 한다.

```bash
PYTHONPATH=services/delta-sast uv run --no-project --with pyyaml --with anthropic \
  python -m delta_sast scan <소스 경로> --out result.sarif --run-id run-42 --commit abc1234
```

| 옵션 | 기본값 | 뜻 |
|---|---|---|
| `--out` | `delta-sast.sarif` | SARIF 파일 |
| `--cache` | `.delta-sast-cache.json` | 규칙 캐시와 LLM 응답 캐시 |
| `--payload-mode` | `ai` | `preset`이면 페이로드 틀을 비우고 DAST 기본 틀을 쓴다 |
| `--no-llm` | 끔 | 규칙·캐시까지만. LLM 대기 경로는 `uncertain`으로 남는다 |

`ANTHROPIC_API_KEY`가 없으면 LLM 없이 진행한다. 모델은 `SAST_LLM_MODEL`(기본 `claude-sonnet-5-5`).

## HTTP 서버 (관리 에이전트용)

```bash
DELTA_SAST_TOKEN=<토큰> SAST_LOCAL_ROOTS=<허용 로컬 경로> \
  PYTHONPATH=services/delta-sast uv run --no-project --with pyyaml --with anthropic --with fastapi \
  --with uvicorn --with pydantic uvicorn delta_sast.api:app --port 8790
```

| 도구 | HTTP | 용도 |
|---|---|---|
| `sast_scan` | `POST /v1/sast/scans` | 점검 시작(202, running). 패치 회차는 `baseline_run_id`로 이전 회차와 비교 |
| `sast_status` | `GET /v1/sast/scans/{run_id}` | 상태·단계·요약(`summary.dast_targets`) |
| `sast_sarif` | `GET /v1/sast/scans/{run_id}/sarif` | SARIF 원문(done일 때만) |
| `sast_dast_feedback` | `POST /v1/sast/scans/{run_id}/dast-results` | DAST 재현 결과로 규칙 캐시 학습 |

`GET /v1/tools`가 LLM 도구 정의(`tools`)와 HTTP 경로(`routes`)를 함께 준다. `python -m delta_sast tools`로도 볼 수 있다.

| 환경 변수 | 기본값 | 뜻 |
|---|---|---|
| `DELTA_SAST_TOKEN` | (필수) | Bearer 토큰 |
| `DELTA_SAST_STATE` | `var/sast` | 상태·SARIF·캐시 폴더 |
| `SAST_REPO_PREFIXES` | `https://github.com/2026-sbh-delta/` | 허용할 원격 저장소 접두어(쉼표 구분) |
| `SAST_LOCAL_ROOTS` | (비움 = 로컬 거절) | 허용할 로컬 경로 루트 |
| `ANTHROPIC_API_KEY`, `SAST_LLM_MODEL` | | LLM 판단. 키가 없으면 규칙·캐시까지만 |

## DAST가 읽는 필드

- 대상: `kind`가 `fail` 또는 `review`이고 `properties.dast.verifiable`이 `true`인 결과
- `properties.dast`: `input`, `field`, `entry`, `render`, `context`, `stored`, `payload`
- `partialFingerprints["deltaFlow/v1"]`: 줄 번호와 무관한 경로 ID. 패치 후 회귀 대상 매칭에 쓴다
- `runs[0].properties.dast_targets`: DAST 대상 수. 0이면 파이프라인이 DAST를 건너뛸 수 있다
- `baselineState`, `runs[0].properties.baseline`: `baseline_run_id`를 줬을 때 이전 회차 대비 new·unchanged·updated와 새 탐지·해결 수

## 테스트

```bash
PYTHONPATH=services/delta-sast uv run --no-project --with pyyaml --with pytest --with anthropic \
  --with fastapi --with httpx --with pydantic python -m pytest services/delta-sast/tests -q
```
