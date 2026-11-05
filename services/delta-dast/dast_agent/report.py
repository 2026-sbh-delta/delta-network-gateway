"""웹 애플리케이션 취약점 점검 보고서(HTML·PDF).

사실(결과·위험도·위치·증거·기록)은 점검 기록에서 코드가 채운다. Claude는 구조화 출력
스키마로 개조식 서술 필드만 쓴다. 고정 템플릿으로만 렌더링하므로 양식은 항상 같다.
"""

import asyncio
import base64
from datetime import datetime
import html
import json
from pathlib import Path
import tempfile

from playwright.async_api import async_playwright

from .agent import model_name
from .runner import browser_executable


TITLE = "웹 애플리케이션 취약점 점검 보고서"
# 결과 표기: 국내 점검 보고서 관례(취약/양호)를 따른다.
RESULT = {"REPRODUCED": ("취약", "vuln"), "NOT_REPRODUCED": ("양호", "ok"),
          "BLOCKED": ("주의", "warn"), "INCONCLUSIVE": ("판정 불가", "na")}
RESULT_NOTE = {"REPRODUCED": "검사 문자열이 실제 브라우저에서 실행됨",
               "NOT_REPRODUCED": "검사 문자열이 저장·출력되었으나 실행되지 않음",
               "BLOCKED": "출력은 인코딩되지 않았으나 CSP가 실행을 차단함",
               "INCONCLUSIVE": "입력 지점 또는 출력 확인에 실패해 판단 근거가 부족함"}
LEVEL = {"CRITICAL": "상", "HIGH": "상", "MEDIUM": "중", "LOW": "하", "INFO": "하"}
CONTEXT = {"html_unescaped": "HTML 본문(인코딩 없음)", "html_escaped": "HTML 본문(인코딩됨)",
           "attribute": "HTML 속성값", "script": "스크립트 내부"}
ACTION = {"left_click": "클릭", "double_click": "더블클릭", "type": "일반 입력", "key": "키 입력",
          "scroll": "스크롤", "navigate": "이동", "new_session": "새 세션", "type_marker": "필터링 확인 표식 입력",
          "type_canary": "검사 문자열 입력", "type_impact": "영향 확인 입력", "finish": "종료"}

WRITER_PROMPT = (
    "당신은 웹 취약점 점검 보고서의 작성자다. 주어진 점검 사실(JSON)만 근거로 쓴다. "
    "문체는 국내 점검 보고서의 개조식이다. 각 항목은 명사형이나 '~함', '~됨', '~임'으로 끝낸다. "
    "수식어, 감탄, 비유, 강조 표현을 쓰지 않는다. 결과를 바꾸거나 사실에 없는 내용을 추측하지 않는다. "
    "페이로드 원문·코드·URL은 쓰지 않는다. '양호'는 해당 입력에서 실행되지 않았다는 뜻일 뿐 "
    "사이트 전체의 안전을 뜻하지 않는다."
)


def _bullets(max_items: int, max_len: int) -> dict:
    return {"type": "array", "minItems": 1, "maxItems": max_items,
            "items": {"type": "string", "maxLength": max_len}}


def narrative_schema(finding_ids: list[str]) -> dict:
    item = {
        "type": "object", "additionalProperties": False,
        "required": ["finding_id", "description", "result", "impact", "remediation"],
        "properties": {
            "finding_id": {"type": "string", "enum": finding_ids},
            "description": _bullets(3, 120),   # 점검 내용
            "result": _bullets(3, 120),        # 점검 결과
            "impact": _bullets(2, 120),        # 영향
            "remediation": _bullets(4, 140),   # 조치 방안
        },
    }
    return {
        "type": "object", "additionalProperties": False,
        "required": ["summary", "findings"],
        "properties": {
            "summary": _bullets(4, 140),
            "findings": {"type": "array", "minItems": len(finding_ids),
                         "maxItems": len(finding_ids), "items": item},
        },
    }


def facts(record: dict) -> dict:
    inputs = {f.get("id"): f for f in (record.get("input") or {}).get("findings", [])}
    items = []
    for result in record.get("results", []):
        ev = result.get("evidence") or {}
        source = inputs.get(result["finding_id"], {})
        paths = [e["url"] for e in ev.get("executed", []) + ev.get("rendered", []) if e.get("url")]
        points = ev.get("input_points") or []
        point = next((p for p in points if p.get("kind") == "probe"), points[0] if points else None)
        executed = next((e for e in ev.get("executed", []) if e.get("full_url")), None)
        rendered = next((e for e in ev.get("rendered", []) if e.get("full_url")), None)
        items.append({
            "finding_id": result["finding_id"],
            "title": result.get("title") or source.get("title") or result["finding_id"],
            "severity": str(source.get("severity") or "MEDIUM").upper(),
            "cwe": source.get("cwe") or "-",
            "source": f'{source.get("source_file", "-")}:{source.get("line", "-")}',
            "sast": source.get("description") or "",
            "status": result["status"], "message": result.get("message") or "",
            "path": paths[0] if paths else "-",
            "input_point": (f'{point["method"]} {point["pattern"]} · 파라미터 {", ".join(point["params"])}'
                            if point else "-"),
            "exec_point": (f'GET {executed["full_url"]}' if executed
                           else f'GET {rendered["full_url"]} (출력만 확인, 실행 안 됨)' if rendered else "-"),
            "agent_session": ev.get("agent_session_id") or "-",
            "resumed_from": ev.get("resumed_from"),
            "reflection": [CONTEXT.get(c, c) for c in ev.get("reflection_contexts") or []],
            "probe": [p.replace("impact:", "영향 확인 ") for p in ev.get("canaries") or []],
            "fresh_session": any(e.get("session", 1) > (ev.get("probe_session") or 1)
                                 for e in ev.get("executed", [])),
            "duration_s": round((result.get("duration_ms") or 0) / 1000),
            "actions": [(a.get("action"), a.get("detail"), a.get("reason")) for a in ev.get("actions", [])],
            "impact_demo": bool(ev.get("impact")),
        })
    return {"session": record.get("run_id") or "-", "scenario": record.get("scenario") or "-",
            "reverify_of": record.get("reverify_of"),
            "target_id": next((((r.get("evidence") or {}).get("target_id")) for r in record.get("results", [])), "-"),
            "source": record.get("source_label") or "-", "planner": record.get("planner") or "-",
            "started_at": record.get("started_at"), "duration_s": round((record.get("duration_ms") or 0) / 1000),
            "findings": items}


def fallback(data: dict) -> dict:
    vulns = [f for f in data["findings"] if f["status"] == "REPRODUCED"]
    return {
        "summary": [f"점검 항목 {len(data['findings'])}건 중 취약 {len(vulns)}건 확인됨",
                    "상세 결과와 조치 방안은 3장과 4장을 참조함"],
        "findings": [{
            "finding_id": f["finding_id"],
            "description": ["SAST 결과의 입력 지점에 검사 문자열을 입력하고 출력 화면에서 실행 여부를 확인함"],
            "result": [RESULT_NOTE[f["status"]]],
            "impact": ["저장된 입력을 조회하는 사용자 브라우저에서 스크립트가 실행될 수 있음"]
                      if f["status"] == "REPRODUCED" else ["해당 입력에서는 영향이 확인되지 않음"],
            "remediation": ["출력 위치에 맞는 HTML 인코딩 적용", "CSP 적용 검토"],
        } for f in data["findings"]],
    }


async def write_narrative(data: dict) -> tuple[dict, str]:
    ids = [f["finding_id"] for f in data["findings"]]
    if not ids:
        return fallback(data), "기본 문구"
    brief = {**data, "findings": [{k: v for k, v in f.items() if k != "actions"} for f in data["findings"]]}
    try:
        from claude_agent_sdk import ClaudeAgentOptions, PermissionResultDeny, ResultMessage, query

        async def deny(*_args):
            return PermissionResultDeny(message="보고서 작성에는 도구를 쓰지 않는다.")

        with tempfile.TemporaryDirectory(prefix="dast-report-") as directory:
            options = ClaudeAgentOptions(
                model=model_name(), tools=[], allowed_tools=[], setting_sources=[], skills=[],
                plugins=[], mcp_servers={}, strict_mcp_config=True, can_use_tool=deny, cwd=directory,
                max_turns=2, max_budget_usd=0.3, extra_args={"no-session-persistence": None},
                stderr=lambda _line: None, system_prompt=WRITER_PROMPT, effort="low",
                output_format={"type": "json_schema", "schema": narrative_schema(ids)},
            )
            result = None
            async with asyncio.timeout(150):
                async for message in query(prompt="점검 사실:\n" + json.dumps(brief, ensure_ascii=False),
                                           options=options):
                    if isinstance(message, ResultMessage):
                        result = message
        value = result.structured_output if result and not result.is_error else None
        value = json.loads(value) if isinstance(value, str) else value
        if not value or sorted(f["finding_id"] for f in value["findings"]) != sorted(ids):
            raise ValueError("스키마와 맞지 않는 서술")
        return value, model_name()
    except Exception:
        return fallback(data), "기본 문구"


def overall(data: dict) -> str:
    """종합 위험도는 코드가 정한다: 취약 항목 중 가장 높은 위험도."""
    levels = [LEVEL.get(f["severity"], "중") for f in data["findings"] if f["status"] == "REPRODUCED"]
    for level in ("상", "중", "하"):
        if level in levels:
            return level
    return "해당 없음"


def _image(directory: Path, finding_id: str) -> str | None:
    shots = (sorted(directory.glob(f"{finding_id}-*-executed.png"))
             or sorted(directory.glob(f"{finding_id}-*-final.png")))
    if not shots:
        return None
    return "data:image/png;base64," + base64.b64encode(shots[0].read_bytes()).decode()


def render_html(record: dict, story: dict, writer: str, directory: Path) -> str:
    e = lambda value: html.escape(str(value if value not in (None, "") else "-"))
    ul = lambda items: "<ul>" + "".join(f"<li>{e(i)}</li>" for i in items) + "</ul>"
    data = facts(record)
    by_id = {f["finding_id"]: f for f in story["findings"]}
    findings = data["findings"]
    when = datetime.fromtimestamp(data["started_at"]) if data["started_at"] else datetime.now()
    doc_no = f'DAST-{when:%Y%m%d}-{data["session"][:8]}'
    counts = {key: sum(1 for f in findings if f["status"] == key) for key in RESULT}
    ids = {f["finding_id"]: f"V-{i:02d}" for i, f in enumerate(findings, 1)}
    # SAST·DAST 결과로 LLM이 정한 조치 순서(있을 때만)
    ranked = (record.get("priority") or {}).get("items") or []
    level = {"urgent": "긴급", "high": "높음", "medium": "보통", "low": "낮음"}
    priority_html = "" if not ranked else (
        "<h3>조치 우선순위</h3><p>" + e(record["priority"].get("summary")) + "</p>"
        '<table class="list"><thead><tr><th>순위</th><th>번호</th><th>수준</th><th>근거</th></tr></thead><tbody>'
        + "".join(f'<tr><td>{e(i["rank"])}</td><td>{e(ids.get(i["finding_id"], i["finding_id"]))}</td>'
                  f'<td>{e(level.get(i["level"], i["level"]))}</td><td class="l">{e(i["rationale"])}</td></tr>'
                  for i in ranked) + "</tbody></table>")

    toc = [("1", "점검 개요", [("1.1", "점검 목적"), ("1.2", "점검 대상 및 범위"), ("1.3", "점검 방법"), ("1.4", "판정 기준")]),
           ("2", "점검 결과 요약", [("2.1", "종합 결과"), ("2.2", "취약점 목록")]),
           ("3", "상세 점검 결과", [(ids[f["finding_id"]], f["title"]) for f in findings]),
           ("4", "조치 권고 사항", []),
           ("부록", "점검 수행 기록", [])]
    toc_html = "".join(
        f'<li><span class="no">{e(no)}</span><span class="t">{e(title)}</span></li>'
        + "".join(f'<li class="sub"><span class="no">{e(sno)}</span><span class="t">{e(stitle)}</span></li>'
                  for sno, stitle in subs)
        for no, title, subs in toc)

    list_rows = "".join(
        f'<tr><td>{ids[f["finding_id"]]}</td><td class="l">{e(f["title"])}</td><td>{e(f["cwe"])}</td>'
        f'<td>{LEVEL.get(f["severity"], "중")}</td><td class="{RESULT[f["status"]][1]}">{RESULT[f["status"]][0]}</td></tr>'
        for f in findings)

    details = []
    figure_no = 0
    for f in findings:
        n = by_id[f["finding_id"]]
        label, tone = RESULT[f["status"]]
        image = _image(directory, f["finding_id"])
        fix_file = directory / "fix" / f["finding_id"] / "fix.json"
        pr_file = directory / "fix" / f["finding_id"] / "pr.json"
        fix_row = ""
        if fix_file.is_file():
            fix = json.loads(fix_file.read_text())
            pr = json.loads(pr_file.read_text()) if pr_file.is_file() else None
            pr_text = (f'PR #{pr.get("number")} {pr.get("url")}' if pr and pr.get("url")
                       else f'로컬 브랜치 {pr.get("branch")} · 커밋 {pr.get("commit")}' if pr else "미생성(승인 대기)")
            fix_row = (f'<tr><th>수정안</th><td colspan="3">{e(fix.get("summary"))}'
                       f'<br><span class="mono">{e(", ".join(fix.get("files", [])))}</span></td></tr>'
                       f'<tr><th>PR</th><td colspan="3" class="mono">{e(pr_text)}</td></tr>')
        figure = ""
        if image:
            figure_no += 1
            figure = (f'<figure><img src="{image}" alt="점검 화면"><figcaption>[그림 {figure_no}] '
                      f'{e(ids[f["finding_id"]])} 점검 시 확인 화면'
                      f'{" (영향 확인용 화면 변조 포함, 점검 환경 한정)" if f["impact_demo"] else ""}</figcaption></figure>')
        details.append(f'''
<section class="detail">
  <h3>{ids[f["finding_id"]]}. {e(f["title"])}</h3>
  <table class="kv">
    <tr><th>취약점 분류</th><td>{e(f["cwe"])} (크로스사이트 스크립팅)</td><th>위험도</th><td>{LEVEL.get(f["severity"], "중")}</td></tr>
    <tr><th>점검 결과</th><td class="{tone}">{label}</td><th>소요 시간</th><td>{f["duration_s"]}초</td></tr>
    <tr><th>관련 소스</th><td colspan="3" class="mono">{e(f["source"])}</td></tr>
    <tr><th>입력 지점</th><td colspan="3" class="mono">{e(f["input_point"])}</td></tr>
    <tr><th>실행 지점</th><td colspan="3" class="mono">{e(f["exec_point"])}</td></tr>
    <tr><th>에이전트 세션</th><td colspan="3" class="mono">{e(f["agent_session"])}{(" (이어받음: " + e(f["resumed_from"]) + ")") if f["resumed_from"] else ""}</td></tr>
    <tr><th>출력 위치</th><td colspan="3">{e(", ".join(f["reflection"]) or "확인 안 됨")}</td></tr>
    <tr><th>사용 검사</th><td colspan="3">{e(", ".join(f["probe"]) or "-")}{" · 새 세션 재방문에서 실행 확인" if f["fresh_session"] else ""}</td></tr>
    <tr><th>점검 내용</th><td colspan="3">{ul(n["description"])}</td></tr>
    <tr><th>점검 결과</th><td colspan="3">{ul(n["result"])}</td></tr>
    <tr><th>영향</th><td colspan="3">{ul(n["impact"])}</td></tr>
    <tr><th>조치 방안</th><td colspan="3">{ul(n["remediation"])}</td></tr>
    {fix_row}
  </table>
  {figure}
</section>''')

    actions = "".join(
        f'<tr><td>{ids[f["finding_id"]]}</td><td>{LEVEL.get(f["severity"], "중")}</td><td class="l">{e(f["title"])}</td>'
        f'<td class="l">{ul(by_id[f["finding_id"]]["remediation"])}</td></tr>'
        for f in findings if f["status"] in ("REPRODUCED", "BLOCKED"))
    actions = actions or '<tr><td colspan="4">조치가 필요한 항목 없음</td></tr>'

    logs = "".join(
        f'<h4>{ids[f["finding_id"]]}. {e(f["title"])}</h4><table class="log"><thead><tr><th>순번</th><th>동작</th><th>대상</th><th>판단 근거</th></tr></thead><tbody>'
        + "".join(f'<tr><td>{i}</td><td>{e(ACTION.get(a, a))}</td><td class="mono">{e(d)}</td><td class="l">{e(r)}</td></tr>'
                  for i, (a, d, r) in enumerate(f["actions"], 1))
        + "</tbody></table>" for f in findings)

    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{TITLE} {e(doc_no)}</title>
<style>{REPORT_CSS}</style></head><body>

<section class="cover">
  <p class="class">대외비</p>
  <div class="cover-main">
    <p class="cover-sub">{e(data["target_id"])} 동적 점검(DAST){" · 재검증" if data.get("reverify_of") else ""}</p>
    <h1>{TITLE}</h1>
  </div>
  <table class="cover-info">
    <tr><th>문서 번호</th><td>{e(doc_no)}</td></tr>
    <tr><th>점검 대상</th><td>{e(data["target_id"])} ({e(data["scenario"])})</td></tr>
    <tr><th>점검 일자</th><td>{when:%Y년 %m월 %d일}</td></tr>
    <tr><th>점검 세션</th><td class="mono">{e(data["session"])}</td></tr>
    <tr><th>작성</th><td>DAST 점검 플랫폼</td></tr>
  </table>
</section>

<section class="page toc"><h2 class="plain">목차</h2><ol>{toc_html}</ol></section>

<section class="page">
<h2>1. 점검 개요</h2>
<h3>1.1 점검 목적</h3>
<ul><li>정적 분석(SAST)에서 식별된 취약점 후보가 실제 운영 환경과 같은 조건에서 악용 가능한지 확인함</li>
<li>확인된 취약점의 영향과 조치 방안을 제시함</li></ul>
<h3>1.2 점검 대상 및 범위</h3>
<table class="kv"><tr><th>대상</th><td>{e(data["target_id"])} ({e(data["scenario"])})</td><th>점검 항목</th><td>{len(findings)}건</td></tr>
<tr><th>입력 출처</th><td colspan="3">{e(data["source"])}</td></tr>
<tr><th>점검 범위</th><td colspan="3">등록된 대상 출처와 경로에 한정함. 외부 요청·웹소켓·서비스 워커는 차단함</td></tr>
<tr><th>점검 일시</th><td>{when:%Y-%m-%d %H:%M}</td><th>총 소요</th><td>{data["duration_s"]}초</td></tr>
{(f'<tr><th>점검 구분</th><td colspan="3">재검증 (원 점검 세션 {e(data["reverify_of"]["run_id"])} · 항목 {e(data["reverify_of"]["finding_id"])})</td></tr>') if data.get("reverify_of") else ""}</table>
<h3>1.3 점검 방법</h3>
<table class="steps"><thead><tr><th>단계</th><th>내용</th></tr></thead><tbody>
<tr><td>분석</td><td class="l">SAST 결과로 입력 화면·입력 지점·출력 위치에 대한 가설 수립</td></tr>
<tr><td>탐색</td><td class="l">실제 브라우저 화면을 기준으로 입력 지점 탐색</td></tr>
<tr><td>필터링 확인</td><td class="l">실행되지 않는 표식 문자열로 입력값 필터링·인코딩 여부와 출력 위치 확인</td></tr>
<tr><td>탐지</td><td class="l">출력 위치에 맞는 검사 문자열 입력 후 저장·재방문으로 실행 여부 확인</td></tr>
<tr><td>판정</td><td class="l">점검별 고유 표식의 실행 증거(브라우저 콘솔·실행 표식)로만 판정</td></tr></tbody></table>
<h3>1.4 판정 기준</h3>
<table class="steps"><thead><tr><th>결과</th><th>기준</th></tr></thead><tbody>
{"".join(f'<tr><td class="{t}">{l}</td><td class="l">{RESULT_NOTE[k]}</td></tr>' for k, (l, t) in RESULT.items())}
</tbody></table>
<p class="note">※ ‘양호’는 해당 입력과 절차에서 실행되지 않았음을 뜻하며, 대상 전체의 안전을 보증하지 않음</p>
</section>

<section class="page">
<h2>2. 점검 결과 요약</h2>
<h3>2.1 종합 결과</h3>
<table class="summary-t"><thead><tr><th>점검 항목</th><th>취약</th><th>주의</th><th>양호</th><th>판정 불가</th><th>종합 위험도</th></tr></thead>
<tbody><tr><td>{len(findings)}</td><td class="vuln">{counts["REPRODUCED"]}</td><td>{counts["BLOCKED"]}</td><td>{counts["NOT_REPRODUCED"]}</td>
<td>{counts["INCONCLUSIVE"]}</td><td class="{"vuln" if counts["REPRODUCED"] else ""}">{overall(data)}</td></tr></tbody></table>
{ul(story["summary"])}
<h3>2.2 취약점 목록</h3>
<table class="list"><thead><tr><th>번호</th><th>항목</th><th>분류</th><th>위험도</th><th>결과</th></tr></thead><tbody>{list_rows}</tbody></table>
</section>

<section class="page"><h2>3. 상세 점검 결과</h2>{"".join(details)}</section>

<section class="page">
<h2>4. 조치 권고 사항</h2>
{priority_html}
<table class="list"><thead><tr><th>번호</th><th>위험도</th><th>항목</th><th>조치 방안</th></tr></thead><tbody>{actions}</tbody></table>
<ul class="note-list"><li>조치 후 동일 항목으로 재점검하여 결과가 ‘양호’로 바뀌는지 확인할 것을 권고함</li></ul>
</section>

<section class="page appendix">
<h2>부록. 점검 수행 기록</h2>
<p class="note">점검 에이전트가 실제 브라우저에서 수행한 동작 기록임. 판단 근거는 에이전트가 남긴 기록을 그대로 옮김</p>
{logs}
<p class="note">서술 작성: {e(writer)} · 결과·위험도·증거는 점검 기록에서 자동 반영함</p>
</section>
</body></html>'''


REPORT_CSS = """
@page{size:A4;margin:18mm 16mm 20mm}*{box-sizing:border-box}
body{margin:0;font:11pt/1.65 "Apple SD Gothic Neo","Noto Sans KR","Malgun Gothic",sans-serif;color:#1a1a1a}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:9.5pt}
.page{break-before:page}
h2{font-size:16pt;margin:0 0 14px;padding-bottom:6px;border-bottom:2px solid #1f3a5f;color:#1f3a5f}
h2.plain{border:0}
h3{font-size:12pt;margin:18px 0 8px;color:#1f3a5f}
h4{font-size:10.5pt;margin:14px 0 6px}
ul{margin:4px 0;padding-left:18px}li{margin:2px 0}
table{width:100%;border-collapse:collapse;margin:6px 0 10px;font-size:10pt}
th,td{border:1px solid #b9c2cc;padding:6px 8px;vertical-align:top}
thead th,.kv th{background:#eef1f5;font-weight:600;text-align:center;white-space:nowrap}
.kv th{width:16%}.kv td ul{margin:0}
td{text-align:center}td.l,.kv td{text-align:left}
.vuln{color:#b42318;font-weight:700}.warn{color:#b54708;font-weight:700}.ok{color:#1f6f3f;font-weight:700}.na{color:#667085;font-weight:700}
.note{font-size:9.5pt;color:#555;margin:6px 0}.note-list{font-size:10pt}
/* 표지 */
.cover{height:257mm;display:flex;flex-direction:column;justify-content:space-between;padding:6mm 2mm}
.cover .class{align-self:flex-end;border:1.5px solid #b42318;color:#b42318;padding:3px 14px;font-weight:700;margin:0}
.cover-main{border-top:4px solid #1f3a5f;border-bottom:1px solid #1f3a5f;padding:26mm 0 22mm}
.cover-sub{margin:0 0 10px;color:#555;font-size:12pt}
.cover h1{margin:0;font-size:28pt;letter-spacing:-.5px;color:#1f3a5f}
.cover-info{width:70%;align-self:flex-end;font-size:10.5pt}.cover-info th{width:28%;background:#eef1f5;text-align:center}.cover-info td{text-align:left}
/* 목차 */
.toc ol{list-style:none;padding:0;margin:10px 0}
.toc li{display:flex;gap:14px;padding:7px 0;border-bottom:1px dotted #b9c2cc;font-weight:600}
.toc li.sub{padding-left:26px;font-weight:400;color:#333}
.toc .no{min-width:48px}
/* 상세 */
.detail{margin-bottom:14px}
.detail + .detail{break-before:page}
.detail table{break-inside:auto}.detail tr{break-inside:avoid}
figure{margin:8px 0 0;break-inside:avoid;text-align:center}
figure img{max-width:100%;max-height:70mm;object-fit:contain;border:1px solid #b9c2cc}
.detail .kv{font-size:9.5pt}.detail .kv th,.detail .kv td{padding:4px 7px}.detail .kv li{margin:0}
figcaption{font-size:9pt;color:#555;text-align:center;margin-top:4px}
.log{font-size:9pt;table-layout:fixed}.log td{padding:4px 6px;overflow-wrap:anywhere}
.log th:nth-child(1){width:7%}.log th:nth-child(2){width:15%}.log th:nth-child(3){width:20%}
"""

FOOTER = (f'<div style="width:100%;font-size:8px;color:#888;padding:0 16mm;display:flex;justify-content:space-between;'
          f'font-family:sans-serif"><span>{TITLE}</span><span>대외비</span>'
          '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')


async def to_pdf(source: Path, target: Path):
    async with async_playwright() as playwright:
        executable = browser_executable(playwright)
        if not executable:
            raise RuntimeError("PDF 변환에 쓸 Chrome이 없습니다.")
        browser = await playwright.chromium.launch(executable_path=executable, headless=True)
        try:
            page = await browser.new_page()
            # 보고서는 외부 자원이 없는 단일 HTML이라 네트워크를 쓰지 않는다.
            await page.route("**/*", lambda route: route.abort())
            await page.set_content(source.read_text(), wait_until="load")
            await page.pdf(path=str(target), format="A4", print_background=True,
                           display_header_footer=True, header_template="<div></div>",
                           footer_template=FOOTER,
                           margin={"top": "18mm", "bottom": "20mm", "left": "16mm", "right": "16mm"})
        finally:
            await browser.close()


async def build(record: dict, directory: Path) -> dict:
    data = facts(record)
    story, writer = await write_narrative(data)
    page = render_html(record, story, writer, directory)
    (directory / "report.html").write_text(page)
    (directory / "report-story.json").write_text(json.dumps(story, ensure_ascii=False, indent=2))
    await to_pdf(directory / "report.html", directory / "report.pdf")
    return {"writer": writer, "overall_risk": overall(data)}
