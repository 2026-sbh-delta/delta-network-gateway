# SAST 점검 서비스

## 단계와 승인 상태

- 기준일: 2026-11-02 (Asia/Seoul).
- Ideation 승인: 사용자(정준우)가 다음을 지시했다. 이 문서를 그 지시의 범위로 기록한다.
  - SAST 결과는 SARIF와 코드 흐름으로 내보낸다.
  - 규칙 → AI → DAST의 하이브리드로 가고, 규칙으로 굳히기는 꼭 넣는다(2026-11-01).
  - 빠른 모드와 전체 LLM 검증 모드를 둘 다 구현한다. 시연은 빠른 모드로 하되 완성도를 높인다(2026-11-02).
- Inception 승인: 사용자가 "정할 것"의 제안대로 시작하라고 지시했다(2026-11-02).
- 현재 단계: Construction. U1~U5, U7 구현과 로컬 테스트 완료. U6(전체 모드)은 미착수. 푸시 전.
- 범위: `services/delta-sast/`의 점검 도구. 빠른 모드(파이프라인)와 전체 모드(파이프라인 밖), SARIF 출력, 규칙 캐시, 관리 에이전트용 도구 정의.
- 범위 밖: SCA(의존성 취약점), IaC 점검, DAST 실행, 수정안 생성(DAST 서비스가 맡음).

## Ideation

- 문제: 시그니처 SAST는 필터가 있어도 취약으로 잡거나(오탐), 파일·DB를 넘는 흐름을 놓친다(미탐). 반대로 LLM이 소스 전체를 보면 느리고, 결과가 매번 바뀌고, 없는 흐름을 지어낸다.
- 사용자: 상위 관리 에이전트(호출자), DAST 서비스(결과 소비자), 개발자(보고서 독자).
- 성공 기준
  - 파이프라인 모드가 빌드와 병렬로 60초 안에 끝난다.
  - 같은 코드에는 같은 결과가 나온다(LLM 판정은 캐시로 고정).
  - 파일과 DB를 넘는 저장형 흐름을 SARIF `codeFlows`로 낸다.
  - DAST가 그대로 쓸 가설(진입 라우트, 입력 칸, 출력 라우트, 출력 맥락)을 붙인다.
- 원칙: 파이프라인 보안 점검의 목표는 모든 제로데이를 막는 것이 아니다. 배포마다 근거 있는 점검 결과를 남기는 것이다.

## Inception

### 두 모드

| | 빠른 모드 `fast` (기본, 파이프라인) | 전체 모드 `full` (파이프라인 밖) |
|---|---|---|
| 목적 | 배포마다 빠르고 재현되는 게이트 | 넓게 보고 놓친 것을 찾아 규칙을 늘림 |
| LLM이 보는 범위 | 규칙으로 판정이 안 나는 경로만 | 라우트에 닿는 모든 함수와 경로 + 목록에 없는 싱크 후보 |
| 시간 목표 | 60초 이내 | 분 단위. 야간 정기·수동 실행 |
| 결과 쓰임 | 게이트와 DAST 가설 | 같은 SARIF + 규칙 후보(사람 승인 후 빠른 모드 규칙으로 반영) |
| 재현성 | 결정적(LLM 판정은 캐시) | LLM 판단이 섞임. 그래프 근거가 없는 결과는 `불확실`로 표시 |

두 모드는 같은 SARIF 형식과 같은 규칙 캐시를 쓴다. 전체 모드가 찾고 사람이 승인한 것이 빠른 모드의 규칙이 된다.

### 빠른 모드 흐름

```text
① 목록   Opengrep 규칙 + AST → 소스·싱크·필터·저장(쓰기/읽기)·라우트 위치
② 경로   호출 그래프 + 데이터 흐름 + 저장 경계(테이블·컬럼 쓰기→읽기) → 소스→싱크 경로, 출력 맥락
③ 분류   결정적 규칙: 후보 확정 / 양호 확정 / 캐시 판정 / LLM 판단
④ 판단   LLM: 분류 ④ 경로만. "이 필터가 이 맥락에서 모든 분기에서 막는가"
⑤ 출력   SARIF 2.1.0 + codeFlows + properties.dast
⑥ 캐시   DAST 증명 결과로 (필터 코드 해시, 맥락) 판정 저장
```

- Opengrep은 "취약 여부"가 아니라 위치 표시만 맡는다. 파일 안 오염 분석 결과는 빠른 참고로만 받는다. 공식 README 기준으로 오염 분석은 파일 안(`--taint-intrafile`)까지다.
- 그래프(호출 그래프, 데이터 흐름, 저장 경계)는 자체 Python AST로 만든다. CFG는 만들지 않고, 분기별 필터 적용은 ④에서 LLM이 함수 코드를 보고 판단한다.
- 출력 맥락(`html_text`, `html_attr`, `js_string`, `url`, `sql`, `shell`)은 싱크 주변 AST로 정한다.

### 분류 규칙(③, 결정적)

LLM이 아니라 코드가 정한다. 경로마다 위에서부터 보고, 처음 걸리는 줄에서 끝난다.

| 순서 | 조건 | 결과 | LLM |
|---|---|---|---|
| 1 | 소스와 싱크 사이에 필터가 없고, 싱크가 그 맥락에서 위험 | 후보 확정 → DAST | 안 씀 |
| 2 | 필터가 안전 목록에 있고 맥락이 맞음 | 양호 확정 | 안 씀 |
| 3 | 규칙 캐시에 `(필터 코드 해시, 맥락)` 판정이 있음 | 캐시 판정 | 안 씀 |
| 4 | 그 밖(직접 만든 필터, 맥락이 다른 안전 필터, 일부 분기 필터, 형 변환, 끊긴 경로) | LLM 판단 | 씀 |

안전 목록은 맥락별 YAML 규칙 파일로 관리한다. 예: `html_text`에는 `markupsafe.escape`, `html.escape`, Jinja2 자동 escape를 둔다. "후보 확정"은 SAST 기준이고, DAST로 검증 가능한 유형의 최종 판정은 DAST 재현 결과다.

### 전체 모드 흐름

```text
① ② 빠른 모드와 같은 목록·경로
③ 라우트에서 닿는 모든 함수를 LLM이 검토(함수 단위 병렬, 그래프 문맥 포함)
   - 그래프 경로가 있는 지적 → 빠른 모드와 같은 판정 형식
   - 그래프 경로가 없는 지적 → "불확실", 근거 줄이 실제 코드에 있는지만 검증
④ 목록에 없는 싱크·소스·필터 후보 제안 → 규칙 후보(사람 승인 전까지 비활성)
⑤ SARIF + 규칙 후보 목록
```

- 전체 모드 결과는 파이프라인 게이트를 막지 않는다. 사람이 보거나 DAST 에이전트 모드로 확인한다.
- 승인된 규칙 후보는 Opengrep 규칙이나 안전 목록에 추가되어 다음 빠른 모드부터 적용된다.

### 검증할 수 없는 유형

하드코딩된 비밀값, 약한 암호화처럼 웹 요청으로 재현할 수 없는 결과에는 `dast.verifiable = false`를 붙인다. 이런 결과는 SAST 판정이 최종 게이트다. LLM은 비밀값이 테스트용 가짜인지 실제 형식인지만 판단한다.

### 예시: 저장형 XSS

```python
# board.py:13  body = request.form["body"]   → 14  save_post(title, clean(body))
# sanitize.py:8 clean(): s.replace("<script>", "")
# store.py:21   INSERT INTO posts(title, body) → 26 SELECT title, body FROM posts
# views.py:53   f"<p>{r['body']}</p>" → 54 Markup(html)
```

1. 목록: 소스 `board.py:13`, 필터 호출 `clean`, 저장 쓰기 `posts.body`(21), 읽기(26), 싱크 `Markup`(54).
2. 경로: `13 → 14 → 21 ⇢ DB posts.body ⇢ 26 → 50 → 53 → 54`, 진입 `POST /board/new`, 출력 `GET /board`, 맥락 `html_text`. 필터 `clean@sha256:ab12…`.
3. 분류: 필터가 있고 안전 목록에 없고 캐시에 없음 → ④.
4. LLM 판단: `vulnerable`. `<script>`만 지워서 `html_text`에서 다른 태그가 남는다는 근거 줄(`sanitize.py:8`, `views.py:53`)과 페이로드 틀 `img_onerror`를 돌려준다.
5. SARIF: `codeFlows`에 위 경로, `properties.dast`에 진입·입력 칸·출력·맥락·페이로드 틀.
6. DAST 재현 → 캐시 `(clean@ab12…, html_text) = 효과 없음`. 같은 필터를 쓰는 다른 입력은 다음부터 LLM 없이 판정한다.

같은 경로의 `title`은 필터가 없으므로 ③-1에서 LLM 없이 후보 확정된다.

### 데이터 계약

**호출**

```json
{"run_id": "run-42", "repo": "<git URL 또는 경로>", "commit": "abc1234", "mode": "fast", "payload_mode": "ai"}
```

- `mode`: `fast` | `full`
- `payload_mode`: `ai` | `preset`. `preset`이면 `dast.payload`를 비우고 DAST가 맥락별 기본 틀을 쓴다.

**SARIF 결과의 `properties`**

| 필드 | 뜻 |
|---|---|
| `dast.verifiable` | DAST로 재현 가능한 유형인가 |
| `dast.entry`, `dast.field` | 입력 라우트와 입력 칸 |
| `dast.render` | 출력 라우트(저장형이면 진입과 다름) |
| `dast.context` | 출력 맥락 |
| `dast.payload` | 페이로드 틀 ID(허용 목록 안). 실제 문자열과 nonce는 DAST가 만든다 |
| `judgment.by` | `rule` / `cache` / `llm` |
| `judgment.verdict` | `candidate` / `vulnerable` / `safe` / `uncertain` |
| `judgment.cache_key` | `필터 코드 해시|맥락` |
| `judgment.mode` | `fast` / `full` |

**LLM 입출력(스키마 고정)**

- 보냄: 경로 1개, 경로 위 함수 코드(비밀값 마스킹, 비신뢰 경계로 감쌈), 출력 맥락, 질문.
- 받음: `verdict`, `reason`, `cited`(줄 목록), `payload.template`.
- 받은 뒤 검증: `cited`가 경로 안의 줄이 아니면 폐기. 경로를 새로 만들거나 늘리지 못한다. 페이로드 틀은 허용 목록만 받는다.

**규칙 캐시 항목**

```json
{"sanitizer": "clean", "code_hash": "sha256:ab12…", "context": "html_text",
 "verdict": "ineffective", "evidence": {"llm": "run-42", "dast": "run-42:REPRODUCED"}}
```

LLM 판정과 DAST 결과가 어긋나면 캐시하지 않고 `불확실`로 남긴다. 함수 코드가 바뀌면 해시가 달라져 그 함수만 다시 판단한다.

### 도구 인터페이스

스테이징 도구와 같은 형식으로 둔다. `GET /v1/tools`가 LLM 도구 정의와 HTTP 경로를 함께 준다.

| 도구 | HTTP | 용도 |
|---|---|---|
| `sast_scan` | `POST /v1/sast/scans` | 점검 시작. 바로 `running`을 돌려준다 |
| `sast_status` | `GET /v1/sast/scans/{run_id}` | 상태, 단계, 요약(유형별 개수, LLM 호출 수, 캐시 적중 수) |
| `sast_sarif` | `GET /v1/sast/scans/{run_id}/sarif` | SARIF 원문 |
| `sast_dast_feedback` | `POST /v1/sast/scans/{run_id}/dast-results` | DAST 재현 결과를 받아 LLM 판정과 맞으면 규칙 캐시로 굳힌다 |
| `sast_rule_candidates` | `GET /v1/sast/rule-candidates`, `POST .../{id}/decision` | 전체 모드가 낸 규칙 후보 조회·승인. U6에서 추가 |

- 인증: `Authorization: Bearer <DELTA_SAST_TOKEN>`. `/healthz`만 인증 없이 연다.
- 같은 `run_id`로 같은 요청을 다시 보내면 기존 상태를 돌려준다. 저장소·커밋이 다르면 409.
- 소스 가져오기: 원격은 `SAST_REPO_PREFIXES`(기본 `https://github.com/2026-sbh-delta/`)로 시작하는 https 주소와 commit이 필요하다. 그 커밋 하나만 얕게 받는다. 로컬은 `SAST_LOCAL_ROOTS` 아래 경로만 받는다. 주소에 자격 정보를 넣으면 거절한다.
- `mode: full`은 U6 전까지 501.
- 상태·SARIF·캐시는 `DELTA_SAST_STATE`(기본 `var/sast`)에 둔다. 서비스가 재시작되면 진행 중이던 점검은 failed로 바뀐다.

### 요구사항

1. 빠른 모드는 빌드와 병렬로 60초 안에 끝난다. LLM 호출은 분류 ④만, 경로 단위로 병렬 처리한다.
2. 같은 커밋과 같은 규칙·캐시면 같은 SARIF가 나온다.
3. 파일 간 호출과 DB 저장 경계를 넘는 경로를 `codeFlows`로 낸다.
4. LLM 출력은 스키마와 인용 줄을 검증하고, 실패하면 그 경로를 `uncertain`으로 둔다.
5. LLM에 보내는 코드는 비밀값을 마스킹하고 비신뢰 데이터로 감싼다.
6. 전체 모드는 게이트를 막지 않고, 규칙 후보는 사람 승인 전까지 쓰이지 않는다.
7. 결과는 SARIF 2.1.0만 낸다. DAST는 SARIF만 읽는다.

### Unit of Work

| Unit | 내용 | 상태 |
| --- | --- | --- |
| U1 | Opengrep 설치·검증, 소스·싱크·필터·저장 규칙(YAML), 목록 추출 | 구현·테스트 |
| U2 | Python AST 호출 그래프, 데이터 흐름, 저장 경계, 출력 맥락 | 구현·테스트 |
| U3 | 분류 규칙, 안전 목록, 규칙 캐시 | 구현·테스트 |
| U4 | 빠른 모드 LLM 판단과 출력 검증 | 구현·가짜 LLM 테스트. 실제 API 호출 미검증 |
| U5 | SARIF 출력(`codeFlows`, `properties.dast`)과 DAST 입력 연결 | SARIF 구현·테스트. DAST 연결 미착수 |
| U6 | 전체 모드(함수 단위 검토, 규칙 후보) | 미착수 |
| U7 | 도구 API·CLI·도구 정의, 문서 | 구현·테스트 |

### 정할 것

| 항목 | 제안 | 상태 |
| --- | --- | --- |
| 대상 언어·프레임워크 | Python(Flask)부터. DAST 샘플과 같다 | 결정 |
| 취약점 범위(11/7까지) | 저장형·반사형 XSS, SQLi, 명령 주입, 하드코딩 비밀값 | 결정 |
| 엔진 | Opengrep(LGPL 2.1) + 자체 AST | 결정. Opengrep 1.33.1로 확인 |
| LLM | delta-ai와 같은 공급자·모델. 기본 `claude-sonnet-5-5`, `SAST_LLM_MODEL`로 바꾼다 | 결정 |
| SAST 후보가 없을 때 DAST | 기본은 실행. 시간이 부족하면 건너뛰는 옵션을 둔다 | 결정(2026-11-02) |
| 규칙 캐시 저장소 | JSON 파일(프로세스 1개). 다중 실행은 이후 | 제안 |

### 검증 계획

- U1~U5: 위 예시와 같은 샘플 앱(취약·수정·CSP)으로 SARIF 골든 테스트. 같은 입력을 두 번 돌려 결과가 같은지, 두 번째 실행의 LLM 호출이 0인지 확인한다.
- 빠른 모드 시간: 샘플 앱 기준 60초 이내를 측정해 기록한다.
- DAST 연동: SAST SARIF를 그대로 DAST에 넣어 재현까지 확인한다.
- 실행하지 않은 테스트는 통과로 기록하지 않는다.

### 패치 반복과의 관계 (2026-11-02 결정)

- 패치 커밋도 처음과 같은 웹 갈래를 다시 탄다: (SAST ∥ 빌드) → 스테이징 → DAST.
- 패치 단계 SAST의 일은 패치가 새로 만든 경로와 비밀값을 찾는 것이다. 기존 취약점이 고쳐졌는지는 DAST가 증명한다.
- 1회차에서 재현된 취약점은 SAST에서 사라져도 DAST 회귀 대상으로 넣는다. 매칭에는 `partialFingerprints["deltaFlow/v1"]`를 쓴다. 이 값은 줄 번호가 아니라 함수·입력 칸·출력 라우트로 만든다.
- 파이프라인 옵션: SAST의 DAST 대상(`runs[0].properties.dast_targets`)이 0이면 DAST를 건너뛸 수 있다. 기본은 실행이다. 회귀 대상이 있으면 이 옵션과 무관하게 실행한다.
- 회차마다 `run_id`를 다르게 준다(예: `run-42-1`, `run-42-2`). 같은 `run_id`는 멱등이라 다시 점검하지 않는다(2026-11-03 김연재 제안 수용).
- 패치 회차는 `baseline_run_id`에 이전 회차를 준다. 결과마다 SARIF `baselineState`(`new`·`unchanged`·`updated`)를 달고, 요약 `summary.baseline`에 `new_findings`·`new_review`·`still_open`·`resolved`와 경로 ID 목록을 준다. `fix_review`는 `new_findings > 0`이면 통과시키지 않는다. `new_review`(판정 불확실한 새 결과)는 `needs_human`으로 보내는 것을 제안했다.
- 알려진 한계: 경로 ID에 함수 안 싱크 순서가 들어가므로, 패치가 같은 함수 앞쪽에 새 출력을 넣으면 뒤쪽 기존 경로가 `new`로 보일 수 있다.

### 구현 메모

- Opengrep은 `tests/`, `test/` 아래를 기본으로 건너뛴다. AST 쪽도 같은 목록을 건너뛴다(`locate.EXCLUDES`).
- Opengrep JSON의 `col`은 1부터 세는 UTF-8 바이트 위치다. Python AST `col_offset + 1`과 같아서 그대로 맞춘다. SARIF는 `columnKind: unicodeCodePoints`로 바꿔 낸다.
- HTML 조립 싱크(f-string, `str.format`, `%`, `+`)와 출력 맥락은 AST가 찾는다. Opengrep 규칙은 요청 입력, SQL·셸·Markup·템플릿 싱크, 비밀값을 표시한다.
- Flask 경로 변수(`<post_id>`)도 입력으로 본다. `int`·`float`·`uuid` 변환기는 제외한다.
- 앞쪽 HTML 싱크에서 이미 보고된 값이 바깥 HTML에 다시 들어간 경로는 따로 내지 않는다.
- LLM 응답은 입력 해시로 캐시해 같은 코드의 재점검 때 다시 묻지 않는다. 규칙 캐시(`entries`)는 LLM 판정과 DAST 결과가 맞을 때만 쌓인다(`RuleCache.learn`).

### 검증 기록

- 2026-11-02, `PYTHONPATH=services/delta-sast uv run --no-project --with pyyaml --with pytest --with anthropic python -m pytest services/delta-sast/tests -q` → 25 passed.
  - 게시판 예제(DAST 샘플과 같은 코드): `form.body`, `form.comment` 저장형 XSS를 파일 3개와 저장소 `app.store.posts`를 넘는 경로로 낸다. 진입 `POST /posts`·`POST /posts/<post_id>/comments`, 출력 `GET /posts/<post_id>`. 제목은 `html.escape`로 양호.
  - 기타 예제: SQLi, 명령 주입, DB 테이블을 거치는 저장형 XSS, URL 맥락 XSS, 직접 만든 필터(LLM 대기), 비밀값. 바인딩 쿼리·`subprocess` 목록 인자·`int()` 변환은 경로로 내지 않는다.
  - LLM 판단은 가짜 클라이언트로 확인했다: 인용 줄 검증, 맥락 밖 페이로드 제거, 실패 시 uncertain, 두 번째 실행 LLM 호출 0, 같은 SARIF.
- 2026-11-02, CLI `python -m delta_sast scan <게시판 예제> --no-llm` → 후보 3, 양호 2, 위치 표시 1.9초, 경로 분석 0.002초.
- 2026-11-03, U7 포함 전체 테스트(`--with fastapi --with httpx --with pydantic` 추가) → 37 passed.
  - 패치 비교: 게시판 예제(A)와 body에 `escape`를 넣은 예제(B)를 `baseline_run_id`로 비교하면 `new_findings 0`, `resolved 1`, `still_open 2`.
- 2026-11-03, `uvicorn delta_sast.api:app`으로 실제 서버를 띄워 healthz → tools → 점검(running → done, 3.6초) → SARIF 조회까지 확인.
  - API: 인증, 도구 정의 형식, 점검 수명(running → done, 단계 기록), 같은 run_id 재요청·충돌, 허용 밖 저장소 실패, full 모드 501.
  - DAST 결과 학습: LLM이 판정한 결과에 REPRODUCED를 보내면 `ineffective`로 굳고, 다음 점검에서 같은 경로가 `cache`로 판정되며 LLM을 다시 부르지 않는다. 규칙으로 판정된 결과는 `skipped`.
  - 경로 ID: 같은 함수에서 같은 입력이 싱크 두 곳에 닿아도 겹치지 않는다(함수 안 싱크 순서 포함). 패치로 `escape`를 넣으면 같은 ID가 fail → pass로 바뀐다.
  - 로컬 커밋 지정 시 git archive로 그 커밋을 풀어 점검한다.
- 실제 Claude API 호출은 아직 검증하지 않았다(실행 환경에 API 키 없음).
- 게시판 예제의 `path.post_id` → 댓글 폼 `action` URL 후보는 게시글이 없으면 404로 끝나 실제로는 재현되지 않는 경로다. CFG가 없어 SAST는 후보로 내고, DAST 재현 실패로 걸러지는 것을 기대한다.

