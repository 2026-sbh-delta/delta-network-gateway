# SAST 기반 경량 DAST MVP

SoftBank Hackathon 2026 본선 데모용 프로토타입이다. 일반 SAST finding 또는 AWS Security Agent 형식 결과를 받고, 코드에 등록된 target·route·field·payload만 골라 실제 Chrome으로 저장형 XSS를 검증한다. 게시글 저장 뒤 **새 브라우저 컨텍스트에서 재방문**해 실행 여부를 판정한다.

## 실행

```bash
cd services/delta-dast
uv sync
uv run python -m dast_agent serve --port 8765 --target-port 8766
```

- 제어 화면: <http://127.0.0.1:8765>
- 의도적으로 취약한 게시판: <http://127.0.0.1:8766/vulnerable/board>
- 수정 비교군: <http://127.0.0.1:8766/fixed/board>
- CSP 비교군: <http://127.0.0.1:8766/csp/board>

시스템 Chrome/Chromium이 필요하다. 자동 탐색이 안 되면 `DAST_BROWSER_EXECUTABLE`에 실행 파일의 절대 경로를 지정한다.

## 공개 주소로 열기 (플랫폼)

플랫폼에서는 CloudFront 뒤에서 제어 화면을 연다([deploy/platform](../../deploy/platform/README.md)).

| 환경 변수 | 역할 |
|---|---|
| `DAST_BIND_HOST` | 제어 화면을 열 주소(기본 `127.0.0.1`). 실습 게시판은 항상 `127.0.0.1`이다 |
| `DAST_PUBLIC_ORIGIN` | 받을 공개 주소(`https://…`, 경로 없음). 이 Host와 Origin만 받는다 |
| `DAST_ADMIN_PASSWORD` | 공개 주소 로그인 비밀번호(16자 이상) |
| `DAST_SESSION_SECRET` | 세션 서명 키. 없으면 재시작할 때마다 다시 로그인한다 |
| `DAST_INTERNAL_HOSTS` | 플랫폼 내부망 주소(쉼표 구분, 예: `dast.cp.internal:8765`). 이 주소로는 읽기(GET)만 받는다. 메인 페이지 상태 조회용 |

로컬 주소(127.0.0.1, localhost, SSM 포트 포워딩 포함)는 지금처럼 로그인 없이 쓴다. `/healthz`는 ALB 헬스 체크용이다.

## 연동 API

팀 파이프라인 연동용 HTTP API 계약은 [docs/API.md](docs/API.md)에 정리했다.

## AI 에이전트 모드 (기본)

`검사 시작`은 SAST → DAST를 한 번에 돈다. SAST 서버(`DELTA_SAST_URL`·`DELTA_SAST_TOKEN`)로 소스를 점검하고, DAST 대상이 있으면 그 결과로 선택한 프로젝트의 스테이징을 바로 점검한 뒤 결과를 SAST로 되돌린다. SAST가 연결되지 않으면 시작하지 않는다. `SAST 실행`·`이 결과로 DAST 시작`은 단계를 나눠 볼 때 쓴다.

화면에서 `DAST 계획`을 `AI 에이전트 · 화면 보고 직접 조작`으로 두고 `검사 시작`을 누른다. Claude가 스크린샷을 보고 클릭·입력·새 세션 재방문을 스스로 결정한다. 셀렉터나 경로 등록이 필요 없다. 대상은 `dast_agent/targets/<id>.json`의 `origin`·`entry_path`·`allowed_path_prefixes`로만 제한된다. 상세는 [docs/API.md](docs/API.md)를 참고한다.

## 데모 흐름 (등록 경로 모드)

1. 내부 SAST 형식 또는 [`examples/aws-code-review-sample.json`](examples/aws-code-review-sample.json)을 읽는다.
2. AWS 형식은 내부 finding으로 변환하되 trigger·payload를 임의 생성하지 않는다.
3. `Claude Agent SDK`가 registry의 target·route·field·payload ID를 선택한다. 수동 모드는 입력에 완전한 등록 ID가 있어야 한다.
4. 서버가 선택 조합, 입력 힌트, 원문 payload 일치를 다시 검증한다.
5. 실행부가 정상 텍스트를 먼저 저장·재방문해 대상의 기본 동작을 확인한다.
6. 등록된 페이로드를 registry의 폼 필드에 입력해 저장한다.
7. 브라우저 컨텍스트를 폐기하고 새 컨텍스트에서 목록과 상세 페이지를 연다.
8. 실행 nonce와 브라우저 콘솔 로그가 모두 일치할 때만 `REPRODUCED`로 판정한다.
9. 화면 캡처 4장과 `report.json`을 `runs/<run-id>/`에 남긴다.

## 상태 의미

| 상태 | UI | 의미 |
|---|---|---|
| `REPRODUCED` | 노란색 | 저장 후 새 컨텍스트 재방문에서 JS 실행 표식과 로그가 모두 일치 |
| `NOT_REPRODUCED` | 초록색 | 정상 대조 검사는 통과했지만 해당 페이로드 실행은 관측되지 않음 |
| `BLOCKED` | 회색 | 저장된 인라인 이벤트와 CSP 차단 로그를 함께 확인 |
| `INCONCLUSIVE` | 빨간색 | 입력, 브라우저, 계획 또는 증거가 불충분해 판정 불가 |

`NOT_REPRODUCED`는 사이트 전체가 안전하다는 뜻이 아니다. 이번 입력과 재현 절차에서 실행되지 않았다는 뜻이다.

## Claude Agent SDK의 역할

Claude는 브라우저를 직접 조종하지 않고 SAST finding에 대해 등록된 `(target_id, route_id, field, payload_id)`를 고르고 `verify` 또는 `insufficient`로 분류한다. 선택지는 실행 시 생성한 JSON Schema enum으로 제한하고 서버가 조합을 재검증한다. 도구, 파일, 웹, 셸 접근은 비활성화했다. 브라우저 조작과 판정은 결정론적 Playwright 실행부가 담당한다.

모델은 `DAST_CLAUDE_MODEL`로 고정하며 기본값은 `claude-sonnet-5-5`다. `ANTHROPIC_API_KEY`가 프로세스 환경에 있으면 SDK가 기본 인증 경로로 사용한다. 이 프로그램은 자격증명 파일을 읽거나 키를 출력하지 않는다. 로컬 Claude Code 로그인에 의존하는 개발 환경과 ECS 환경의 인증은 별도로 준비해야 한다.

Claude 연결 없이 시연하려면 화면에서 `수동 검토 계획 · 모델 호출 없음`을 선택한다. 이 모드는 자동 대체가 아니라 명시적인 데모 모드다.

## 검증

서버를 실행한 상태에서 다음 명령으로 세 비교군을 실제 UI로 검사한다.

```bash
uv run python scripts/e2e_smoke.py
# 비용이 발생하는 AWS 형식 + Claude 실호출까지 포함
uv run python scripts/e2e_smoke.py --include-claude
```

기대 결과는 `vulnerable=REPRODUCED`, `fixed=NOT_REPRODUCED`, `csp=BLOCKED`다. UI 캡처는 `demo-shots/`에 생성된다.

## 범위와 다음 연결점

스테이징 대상(`staging-aws`, `staging-gcp`)은 target JSON에 주소를 적지 않고 `"staging": "aws|gcp"`만 둔다. 점검할 때마다 스테이징 도구의 `GET /v1/staging/targets`에서 현재 주소를 받는다(`DELTA_STAGING_URL`, 기본 `http://127.0.0.1:8091`, `DELTA_STAGING_TOKEN`). 스테이징 도구에 닿지 않거나 주소를 못 찾으면 사전 확인에서 원인을 보여 주고 바로 멈춘다.

대상은 [`dast_agent/targets/`](dast_agent/targets/), 페이로드는 [`dast_agent/payloads/`](dast_agent/payloads/)에 등록한다. 현재는 loopback `local-fixture`와 `visual_svg_v1`만 있다. 스테이징은 소유한 host를 target JSON의 명시적 origin으로 추가한 경우에만 실행된다. SAST나 Claude가 URL·셀렉터·payload 원문을 새로 만들어 실행할 수 없다.

AWS Security Agent 코드 리뷰의 시간, API 출력, DAST 입력 변환 주의점은 [`docs/AWS_CODE_REVIEW.md`](docs/AWS_CODE_REVIEW.md)에 정리했다. AWS finding에 브라우저용 페이로드와 폼 경로가 항상 포함된다고 가정하면 안 된다.
