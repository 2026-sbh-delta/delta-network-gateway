# DAST 점검 에이전트 HTTP API

팀 파이프라인이 DAST 점검을 호출·조회하기 위한 계약 문서다. 기준 코드는 `dast_agent/server.py`, `dast_agent/contracts.py`이며, 2026-11-01 기준 실제 동작으로 확인했다.

## 1. 한눈에 보기

| 용도 | 메서드 · 경로 | 비고 |
|---|---|---|
| 설정·토큰 조회 | `GET /api/config` | 쓰기 요청에 필요한 토큰을 받는다 |
| 프로젝트 목록·생성 | `GET`·`POST /api/projects` | 프로젝트(시스템) 단위로 점검 이력을 묶는다 |
| 프로젝트 상세 | `GET /api/projects/{project_id}` | 취약점별 현재 상태·이력·수정/PR 상태 |
| 점검 시작 | `POST /api/runs` | SAST 결과를 넣으면 `run_id`를 돌려준다 |
| 점검 목록 | `GET /api/runs?project={id}` | 최신순 50개(메모리 + 디스크 기록), 프로젝트로 거르기 |
| 점검 상태 | `GET /api/runs/{run_id}` | 진행 중 점검의 요약 |
| 점검 전체 | `GET /api/runs/{run_id}/view` | 입력 + 결과 + 증거. 서버 재시작 전 기록도 조회 |
| 실시간 이벤트 | `GET /api/runs/{run_id}/events` | SSE. 처음부터 다시 받을 수 있다 |
| 재검증 | `POST /api/runs/{run_id}/findings/{finding_id}/reverify` | 같은 에이전트 맥락을 이어받는다 |
| 수정안 생성·조회 | `POST`·`GET /api/runs/{run_id}/findings/{finding_id}/fix` | SAST+DAST 맥락으로 패치 생성 |
| PR 생성 | `POST /api/runs/{run_id}/findings/{finding_id}/pr` | 사용자 승인(`approve: true`) 필요 |
| 보고서 상태 | `GET /api/runs/{run_id}/report` | 점검이 끝나면 자동 생성 |
| 보고서 재생성 | `POST /api/runs/{run_id}/report` | |
| 증거 파일 | `GET /api/artifacts/{run_id}/{file}` | `report.json`, `report.html`, `report.pdf`, 스크린샷 PNG |

파이프라인 연동 흐름:

```
SAST 결과 ─▶ POST /api/runs ─▶ run_id
              │
              ├─ (선택) GET /api/runs/{run_id}/events   실시간 화면·단계
              ▼
          GET /api/runs/{run_id}  status가 running이 아닐 때까지 확인
              ▼
          GET /api/runs/{run_id}/view   결과·증거
          GET /api/runs/{run_id}/report  → report.pdf
              ▼  (수정 배포 후)
          POST /api/runs/{run_id}/findings/{finding_id}/reverify ─▶ 새 run_id
```

## 2. 인증

- 쓰기 요청(`POST`)에는 `X-DAST-Token` 헤더가 필요하다. 값은 `GET /api/config`의 `csrf_token`이다(서버를 시작할 때마다 새로 발급).

## 3. 프로젝트

점검 이력과 취약점 상태는 프로젝트(점검 대상 시스템) 단위로 관리한다.

| 호출 | 설명 |
|---|---|
| `POST /api/projects` | `{"name", "repo_url", "prod_url", "target_id", "description"}` → 생성된 프로젝트. `repo_url`은 GitHub 주소이거나 빈 값, `prod_url`은 http(s) URL, `target_id`는 등록된 점검 대상(스테이징)이다 |
| `GET /api/projects` | 프로젝트 목록과 `run_count`·`last_run_at`·`open`(미조치)·`fixed`(조치 완료) |
| `GET /api/projects/{id}` | 프로젝트 정보와 `vulnerabilities[]`: 항목별 `state`(open·reopen·warn·check·fixed·ok), `state_label`(미조치·재발·주의·확인 필요·조치 완료·양호), `latest`, `checks`, `history[]`, `fix` |

- 운영 서버 주소(`prod_url`)는 기록용이다. 점검은 `target_id`의 스테이징에서만 실행한다.
- PR 대상 원격 저장소는 프로젝트의 `repo_url`이다(비어 있으면 로컬 커밋까지만).
- 상태 판정: 최근 결과가 취약이면 미조치, 취약 → 양호 → 취약이면 재발, 취약 이후 양호면 조치 완료.

## 4. 점검 시작 — `POST /api/runs`

요청 (`Content-Type: application/json`, 최대 128KB):

```json
{
  "scenario": "vulnerable",
  "planner": "agent",
  "concurrency": 2,
  "impact": true,
  "project_id": "a8e0b99bddf5",
  "report": { "source": "...", "source_label": "...", "findings": [ ... ] }
}
```

| 필드 | 값 | 설명 |
|---|---|---|
| `scenario` | `vulnerable` \| `fixed` \| `csp` | 대상 프로필. 지금은 로컬 실습 게시판의 비교군이고, 스테이징 대상이 추가되면 확장한다 |
| `planner` | `agent`(기본 권장) \| `computer_use` \| `claude` \| `reviewed` | `agent`: Agent SDK 에이전트가 화면을 보고 점검. `computer_use`: 공식 Computer Use 툴셋(API 키 필요). 나머지 둘은 등록 경로 방식(구버전) |
| `concurrency` | 1~5, 기본 2 | 취약점을 동시에 몇 개 점검할지. 취약점마다 브라우저·에이전트를 따로 쓴다 |
| `impact` | bool, 기본 `true` | 취약이 확인되면 영향 시연 화면(HACKED 등)을 실행할지 여부. 끄면 무해한 검사 문자열만 쓴다 |
| `project_id` | 프로젝트 ID(생략하면 기본 프로젝트) | 점검을 묶을 프로젝트 |
| `report` | 아래 SAST 형식 | 점검할 취약점 목록 |

응답 `200`: `{"run_id": "1d236fb7-b130-4ff2-ad63-87e58596be49"}`

### 3.1 SAST 입력 형식

두 형식을 받는다.

**(A) 내부 형식**

```json
{
  "source": "ai-sast",
  "source_label": "AI SAST 결과",
  "findings": [{
    "id": "SAST-XSS-101",
    "title": "댓글 저장형 XSS",
    "cwe": "CWE-79",
    "severity": "HIGH",
    "description": "댓글 입력이 저장된 뒤 목록에 인코딩 없이 출력됨",
    "source_file": "app/comments.py",
    "line": 31,
    "trigger": { "target_id": "local-fixture", "payload_id": "bsod_v1" }
  }]
}
```

| 필드 | 필수 | 제한 |
|---|---|---|
| `source` | ✓ | 영문·숫자·`_`·`-` 1~64자 |
| `source_label` | ✓ | 1~200자 |
| `findings[]` | ✓ | 1~10개, `id` 중복 불가 |
| `id` | ✓ | 영문·숫자·`_`·`-` 1~64자 |
| `title` · `cwe` · `severity` · `description` · `source_file` · `line` | ✓ | 길이 제한 있음(contracts.py) |
| `trigger` | | 힌트. `target_id`(점검 대상), `payload_id`(영향 시연 화면 종류: `visual_svg_v1` HACKED, `bsod_v1` 블루스크린) |
| `payload` | | 쓰지 않는 것을 권장. 넣으면 등록된 템플릿과 완전히 같아야 한다 |

**(B) AWS Security Agent 코드 리뷰 형식**

`findings[0]`에 `findingId`가 있으면 이 형식으로 보고 변환한다. `findingId`, `name`, `description`, `reasoning`, `riskType`, `riskLevel`, `codeLocations[]`를 사용한다. 예시는 `examples/aws-code-review-sample.json`에 있다.

> SAST 쪽이 URL·셀렉터·페이로드를 줄 필요는 없다. 에이전트가 설명·파일·줄을 근거로 화면에서 입력 지점을 찾는다.

## 5. 점검 결과

### 4.1 상태 — `GET /api/runs/{run_id}`

```json
{
  "run_id": "…", "status": "running",
  "scenario": "vulnerable", "planner": "agent", "concurrency": 2,
  "started_at": 1793520604.1, "duration_ms": 0,
  "finding_count": 2, "counts": {"REPRODUCED": 1},
  "results": [ … ],
  "reverify_of": {"run_id": "…", "finding_id": "…"}
}
```

- `status`: `running` → `done`(모든 항목 판정 완료) 또는 `error`(하나 이상 판정 불가).
- 점검 상태는 서버 메모리에 있다. 서버가 재시작된 뒤에는 `/view`로 조회한다.
- `reverify_of`는 재검증일 때만 있다.

### 4.2 전체 — `GET /api/runs/{run_id}/view`

4.1의 필드에 `report`(입력 SAST)와 `live`(메모리에 있으면 true)가 추가된다. 디스크 기록은 `runs/<run_id>/report.json`에서 읽는다.

### 4.3 항목 결과 `results[]`

```json
{
  "finding_id": "SAST-XSS-101",
  "title": "댓글 저장형 XSS",
  "status": "REPRODUCED",
  "message": "새 세션 재방문에서 저장된 검사 문자열의 실행을 확인했습니다(저장형).",
  "duration_ms": 64021,
  "evidence": {
    "agent_session_id": "746a440b-…",
    "resumed_from": null,
    "hypothesis": {"page_hint": "…", "input_hint": "…", "expected_context": "html_body", "plan": "…"},
    "reflection_contexts": ["html_unescaped"],
    "canaries": ["impact:bsod_v1"],
    "input_points": [{"method": "POST", "url": "http://…/posts/{id}/comments", "pattern": "…", "params": ["comment"], "kind": "probe"}],
    "executed": [{"full_url": "http://…/posts/0daad…", "phase": "impact", "session": 2}],
    "screenshots": ["/api/artifacts/<run_id>/SAST-XSS-101-agent-sdk-02-executed.png"],
    "actions": [{"action": "left_click", "detail": "(480, 612)", "reason": "댓글 입력란 선택"}],
    "cost_usd": 0.12
  }
}
```

| `status` | 보고서 표기 | 의미 |
|---|---|---|
| `REPRODUCED` | 취약 | 이번 점검 고유 표식이 실제 브라우저에서 실행됨 |
| `NOT_REPRODUCED` | 양호 | 검사 문자열이 저장·출력됐지만 실행되지 않음(대상 전체의 안전을 뜻하지 않음) |
| `BLOCKED` | 주의 | 출력은 인코딩되지 않았으나 CSP가 실행을 막음 |
| `INCONCLUSIVE` | 판정 불가 | 입력 지점·출력 확인 실패 등 근거 부족 |

판정은 AI의 주장이 아니라 브라우저에서 관측한 증거(콘솔 표식·실행 표식·CSP 위반)로 코드가 정한다. 입력 지점은 파라미터 **이름**만 기록하고 값은 남기지 않는다.

## 6. 실시간 이벤트 — `GET /api/runs/{run_id}/events` (SSE)

`data:` 한 줄이 JSON 이벤트 하나다. 연결하면 처음부터 다시 받는다(`Last-Event-ID`로 이어 받기 가능). 점검이 끝나면 스트림이 닫힌다. 병렬 점검에서는 항목별 이벤트에 `finding_id`가 붙는다.

| `type` | 주요 필드 | 용도 |
|---|---|---|
| `stage` | `stage`(analyze·explore·reflect·detect·judge·impact), `status`(running·done·failed·skipped), `detail` | 단계 진행 |
| `finding` | `status`, `message`, `mapping` | 항목 상태 변경 |
| `result` | `result`(4.3 형식) | 항목 판정 완료 |
| `agent` | `action`, `detail`, `message` | 에이전트 행동·판단 근거 |
| `probe` / `execution` | `payload_id` / `message` | 검사 문자열 입력 / 실행 감지 |
| `frame` | `image`(JPEG data URL), `width`, `height` | 실시간 화면(항목별 최신 1장) |
| `cursor` / `click` | `x`, `y` | 커서 이동·클릭 위치 |
| `state` / `log` | `message` | 진행 메시지 |
| `done` | 4.1과 같은 요약 | 점검 종료 |

파이프라인이 화면을 직접 보여줄 필요가 없으면 SSE 없이 4.1을 몇 초 간격으로 확인해도 된다.

## 7. 재검증 — `POST /api/runs/{run_id}/findings/{finding_id}/reverify`

수정 배포 후 같은 항목을 다시 점검한다. 원래 점검한 에이전트의 세션을 분기(fork)해 이어받으므로, 경로를 다시 찾지 않고 바로 들어간다.

요청(생략 가능): `{"scenario": "fixed", "impact": true}` → 응답 `{"run_id": "<새 점검>"}`

- 새 점검에는 `reverify_of`가 기록된다. 결과·보고서 형식은 일반 점검과 같다.
- 조건: 원 점검이 끝났고, 해당 항목에 `agent_session_id`가 있어야 한다(`planner: agent`로 점검한 항목).
- 세션 기록은 서버 머신의 디스크(`~/.claude/projects/…`)에 있다. 인증 수단(로그인·API 키)과 무관하게 이어받을 수 있지만, 같은 서버(같은 디스크)에서만 가능하다.

## 8. 코드 수정안과 PR

| 호출 | 설명 |
|---|---|
| `POST …/findings/{finding_id}/fix` | 취약·주의 항목의 수정안 생성을 시작한다 → `{"status": "generating"}` |
| `GET …/findings/{finding_id}/fix` | `{"status": "none"\|"generating"\|"ready"\|"failed", "summary", "changes": [{"file","reason"}], "verification", "files", "patch", "trace": [{"role":"source|propagation|sink","file","line","code","note"}], "pr"}` |
| `GET …/findings/{finding_id}/source?file=&line=&context=12` | 수정 전 원본 코드에서 `line` 위아래 `context`줄(3–40)을 돌려준다 → `{"file","line","start","total","lines":[{"n","text"}]}`. 경로는 저장소 기준 상대 경로만 받는다(`..`·절대 경로는 400) |
| `POST …/findings/{finding_id}/pr` | 본문 `{"approve": true}`가 있어야 한다. 브랜치 `dast/fix-<항목>-<세션8자리>`에 커밋하고, 원격이 설정돼 있으면 GitHub PR을 만든다 |

- 수정 에이전트는 SAST(파일·줄·설명)와 DAST(입력 지점·실행 지점·출력 위치·CSP) 근거를 받는다. 대상 저장소의 작업 사본에서 Read·Grep·Glob·Edit만 쓰고, 결과는 `git diff` 패치로 남긴다.
- 수정 대상 저장소는 대상 설정의 `repo`로 지정한다: `{"path": "<저장소 경로>", "remote": "", "base_branch": "main"}`.
  - `remote`가 비어 있으면 PR 응답은 `{"status": "local_only", "branch", "commit"}`이다.
  - `remote`가 있으면 `GITHUB_TOKEN`으로 브랜치를 올리고 PR을 만들어 `{"status": "created", "url", "number"}`를 돌려준다.
- PR 본문은 변경 내용·근거(SAST·DAST)·확인한 것 순서이고, 머지 전 재검증을 안내한다.

### 개발자 대화

| 호출 | 설명 |
|---|---|
| `GET …/findings/{finding_id}/chat` | 항목별 대화 기록 → `{"messages": [{"role": "user"\|"assistant", "text", "at"}], "busy"}` |
| `POST …/findings/{finding_id}/chat` | 본문 `{"message": "질문"}`(1~2000자). 답을 NDJSON으로 흘려보낸다: `{"type":"text","text"}` · `{"type":"tool","name","target"}` · `{"type":"done","session_id"}` · `{"type":"error","message"}` |

- 점검이 끝난 모든 항목(취약·안전·주의·판정 불가)에서 쓸 수 있다.
- 첫 질문에 점검 기록을 넣는다: SAST 위치, DAST 행동·증거·판정 근거, AI 수정안과 코드 흐름. 페이로드 원문과 화면 이미지는 넣지 않는다.
- 이후 질문은 같은 대화 세션을 이어 받아 앞의 맥락을 기억한다.
- 대상 저장소 원본은 Read·Grep·Glob으로 읽기만 한다. 코드 수정, 명령 실행, 브라우저 조작, 판정 변경은 하지 않는다.
- 항목마다 한 번에 한 질문만 처리한다. 답하는 중에 다시 보내면 409를 돌려준다.
- 기록은 `runs/<run>/chat/<finding>/chat.json`에 남는다.

## 9. 보고서

| 호출 | 응답 |
|---|---|
| `GET /api/runs/{run_id}/report` | `{"status": "generating"}` 또는 `{"status": "ready", "html": "/api/artifacts/<id>/report.html", "pdf": "/api/artifacts/<id>/report.pdf"}` 또는 `{"status": "none"\|"failed"}` |
| `POST /api/runs/{run_id}/report` | `{"status": "generating"}` (다시 만들기) |

- 에이전트 모드 점검이 끝나면 약 10초 안에 자동으로 생성된다.
- 양식은 표지·목차·점검 개요·결과 요약·상세 결과·조치 권고·부록의 고정 템플릿이다.
- 결과·위험도·증거는 코드가 채우고, 서술 문장만 AI가 스키마에 맞춰 쓴다.

## 10. 오류 코드

| 코드 | 상황 |
|---|---|
| 403 | 허용되지 않은 Host·Origin, 또는 `X-DAST-Token` 불일치 |
| 400 | PR 요청에 승인(`approve: true`)이 없음 |
| 404 | 없는 점검·항목, 이어받을 세션이 없는 항목 |
| 502 | PR 생성 중 GitHub·git 단계 실패 |
| 409 | 진행 중인 점검 요청, 수정안 없이 PR 요청, 취약·주의가 아닌 항목의 수정 요청, 대상에 repo 미설정 |
| 413 / 415 / 422 | 입력 128KB 초과 / JSON이 아님 / 형식 오류 |
| 429 | 동시 점검 3개 초과(`MAX_PARALLEL`). 끝난 뒤 다시 요청 |

## 11. 호출 예

```bash
BASE=http://127.0.0.1:8765
TOKEN=$(curl -s $BASE/api/config | python3 -c 'import json,sys;print(json.load(sys.stdin)["csrf_token"])')
RUN=$(curl -s -X POST $BASE/api/runs -H 'Content-Type: application/json' -H "X-DAST-Token: $TOKEN" \
  -d @request.json | python3 -c 'import json,sys;print(json.load(sys.stdin)["run_id"])')
curl -s $BASE/api/runs/$RUN | python3 -m json.tool
curl -s $BASE/api/runs/$RUN/report
```

`request.json`은 3절 요청 본문이다.

## 12. 범위와 한계

- 지금 점검 유형은 저장형·반사형 XSS(CWE-79)다. 대상은 `dast_agent/targets/*.json`에 등록된 출처·경로로 제한된다.
- 스테이징을 연결하려면 대상 파일에 `origin`, `entry_path`, `allowed_path_prefixes`를 추가한다. 코드는 바꾸지 않는다.
- 점검 1건은 약 55~75초가 걸리고, Claude 사용 비용은 약 $0.11~0.13이다(재검증은 이어받은 맥락 때문에 약 $0.27).
