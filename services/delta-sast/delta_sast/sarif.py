"""⑤ 출력: SARIF 2.1.0 + codeFlows + properties.dast.

DAST가 읽는 규칙
- kind가 fail(후보·취약) 또는 review(불확실)이고 properties.dast.verifiable이 true인 결과만 공격 대상이다.
- kind가 pass인 결과는 "필터 확인됨" 기록이다. 공격하지 않는다.
- partialFingerprints["deltaFlow/v1"]는 줄 번호와 무관하게 같은 경로를 가리킨다. 패치 후 회귀 대상 매칭에 쓴다.
"""

from __future__ import annotations

import hashlib

from .graph import Project
from .model import Flow, Judgment, Loc, Secret

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
TOOL_VERSION = "0.1.0"

CONTEXT_TEXT = {
    "html_text": "HTML 본문", "html_attr": "HTML 속성", "url": "URL 속성", "js_string": "JavaScript 문자열",
    "sql": "SQL 쿼리", "shell": "셸 명령", "template": "템플릿",
}
RULES = {
    "delta.xss.stored": ("저장형 XSS", "입력이 저장소를 거쳐 다른 응답의 HTML에 escape 없이 출력된다."),
    "delta.xss.reflected": ("반사형 XSS", "입력이 같은 응답의 HTML에 escape 없이 출력된다."),
    "delta.sqli": ("SQL 인젝션", "입력이 조립된 SQL 문자열로 실행된다."),
    "delta.cmdi": ("명령 인젝션", "입력이 셸 명령으로 실행된다."),
    "delta.ssti": ("템플릿 인젝션", "입력으로 템플릿을 조립해 렌더링한다."),
    "delta.secret": ("하드코딩 비밀값", "비밀값으로 보이는 문자열이 코드에 있다."),
}
KIND_BY_VERDICT = {"candidate": "fail", "vulnerable": "fail", "uncertain": "review", "safe": "pass"}
LEVEL_BY_KIND = {"fail": "error", "review": "warning", "pass": "none"}


def rule_id(flow: Flow) -> str:
    if flow.vuln == "xss":
        return "delta.xss.stored" if flow.stored else "delta.xss.reflected"
    return f"delta.{flow.vuln}"


class Columns:
    """UTF-8 바이트 열 → 유니코드 코드 포인트 열(run.columnKind = unicodeCodePoints)."""

    def __init__(self, project: Project):
        self.lines = {m.path: m.source.splitlines() for m in project.modules.values()}

    def char(self, path: str, line: int, byte_col: int) -> int:
        lines = self.lines.get(path)
        if not lines or not 1 <= line <= len(lines):
            return byte_col
        raw = lines[line - 1].encode("utf-8")
        return len(raw[: max(byte_col - 1, 0)].decode("utf-8", errors="ignore")) + 1


def physical(loc: Loc, cols: Columns) -> dict:
    region = {"startLine": loc.line, "startColumn": cols.char(loc.path, loc.line, loc.col)}
    if loc.end_line >= loc.line and loc.end_col > 1:
        region |= {"endLine": loc.end_line, "endColumn": cols.char(loc.path, loc.end_line, loc.end_col)}
    return {"artifactLocation": {"uri": loc.path, "uriBaseId": "SRCROOT"}, "region": region}


def sink_ordinals(flows: list[Flow], project: Project) -> dict[tuple, tuple[str, int]]:
    """싱크 위치 → (싱크가 있는 함수, 그 함수 안에서 몇 번째 싱크인지). 줄이 밀려도 순서는 같다."""
    by_func: dict[str, set[tuple]] = {}
    for flow in flows:
        func = project.enclosing(flow.sink.path, flow.sink.line)
        name = func.qname if func else flow.sink.path
        by_func.setdefault(name, set()).add(flow.sink.key())
    return {key: (name, i) for name, keys in by_func.items() for i, key in enumerate(sorted(keys))}


def fingerprint(flow: Flow, ordinals: dict[tuple, tuple[str, int]]) -> str:
    sink_func, ordinal = ordinals[flow.sink.key()]
    parts = [flow.vuln, flow.context, flow.source.func, f"{flow.source.kind}.{flow.source.field_name}",
             sink_func, str(ordinal), ",".join(r.text() for r in flow.render)]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def route_dict(routes) -> dict | None:
    return {"method": routes[0].method, "path": routes[0].path} if routes else None


def message(flow: Flow) -> str:
    entry = flow.entry[0].text() if flow.entry else flow.source.func
    render = flow.render[0].text() if flow.render else "응답"
    field = f"{flow.source.kind}.{flow.source.field_name or '*'}"
    via = f" 저장소 {', '.join(flow.stores)}를 거쳐" if flow.stores else ""
    filt = f" 필터({', '.join(s.name for s in flow.sans)})를 거쳐" if flow.sans else " 필터 없이"
    where = CONTEXT_TEXT.get(flow.context, flow.context)
    if flow.vuln == "xss":
        return f"{entry}의 {field} 값이{via}{filt} {render} 응답의 {where}에 출력된다."
    return f"{entry}의 {field} 값이{filt} {where}에 들어간다."


def flow_result(flow: Flow, judgment: Judgment, ordinals: dict, cols: Columns, mode: str) -> dict:
    kind = KIND_BY_VERDICT[judgment.verdict]
    locations = []
    for i, step in enumerate(flow.steps):
        item = {"location": {"physicalLocation": physical(step, cols), "message": {"text": step.note or "경로"}}}
        if i == 0:
            item["kinds"] = ["source"]
        elif i == len(flow.steps) - 1:
            item["kinds"] = ["sink"]
        locations.append(item)
    return {
        "ruleId": rule_id(flow),
        "kind": kind,
        "level": LEVEL_BY_KIND[kind],
        "message": {"text": message(flow)},
        "locations": [{"physicalLocation": physical(flow.sink, cols)}],
        "codeFlows": [{"threadFlows": [{"locations": locations}]}],
        "partialFingerprints": {"deltaFlow/v1": fingerprint(flow, ordinals)},
        "properties": {
            "dast": {
                "verifiable": True,
                "input": flow.source.kind,
                "field": flow.source.field_name,
                "entry": route_dict(flow.entry),
                "render": route_dict(flow.render),
                "entries": [{"method": r.method, "path": r.path} for r in flow.entry],
                "renders": [{"method": r.method, "path": r.path} for r in flow.render],
                "context": flow.context,
                "stored": flow.stored,
                "payload": judgment.payload,
            },
            "judgment": {
                "by": judgment.by,
                "verdict": judgment.verdict,
                "reason": judgment.reason,
                "cache_key": judgment.cache_key,
                "mode": mode,
                "cited": [f"{c.path}:{c.line}" for c in judgment.cited],
            },
            "sanitizers": [{"name": s.name, "code_hash": s.code_hash, "at": f"{s.loc.path}:{s.loc.line}"}
                           for s in flow.sans],
            "stores": list(flow.stores),
        },
    }


def secret_result(secret: Secret, cols: Columns, mode: str) -> dict:
    return {
        "ruleId": "delta.secret",
        "kind": "fail",
        "level": "error",
        "message": {"text": f"비밀값으로 보이는 문자열이 코드에 있다(값 {secret.preview})."},
        "locations": [{"physicalLocation": physical(secret.loc, cols)}],
        "partialFingerprints": {"deltaFlow/v1": hashlib.sha256(
            f"secret|{secret.loc.path}|{secret.rule_id}|{secret.preview}".encode()).hexdigest()[:32]},
        "properties": {
            "dast": {"verifiable": False},
            "judgment": {"by": "rule", "verdict": "candidate", "reason": "웹 요청으로 재현할 수 없어 SAST 판정이 최종이다",
                         "cache_key": None, "mode": mode, "cited": []},
        },
    }


OPEN = ("fail", "review")


def annotate_baseline(sarif: dict, baseline: dict) -> dict:
    """기준 SARIF와 경로 ID를 비교해 결과마다 baselineState(new·unchanged·updated)를 달고 요약을 돌려준다."""
    fp = "deltaFlow/v1"
    base = {r["partialFingerprints"][fp]: r["kind"] for r in baseline["runs"][0]["results"]}
    current = {}
    new = []
    for r in sarif["runs"][0]["results"]:
        key = r["partialFingerprints"][fp]
        current[key] = r["kind"]
        if key not in base:
            r["baselineState"] = "new"
            if r["kind"] in OPEN:
                new.append(r)
        else:
            r["baselineState"] = "unchanged" if base[key] == r["kind"] else "updated"
    opened = [k for k, kind in base.items() if kind in OPEN]
    resolved = [k for k in opened if current.get(k, "pass") == "pass"]
    return {
        "run_id": baseline["runs"][0]["properties"]["run_id"],
        "new_findings": len(new),
        "new_review": sum(1 for r in new if r["kind"] == "review"),
        "still_open": sum(1 for k in opened if current.get(k) in OPEN),
        "resolved": len(resolved),
        "resolved_fingerprints": sorted(resolved),
        "new_fingerprints": sorted(r["partialFingerprints"][fp] for r in new),
    }


def build(findings: list[tuple[Flow, Judgment]], secrets: list[Secret], project: Project, *,
          run_id: str, commit: str | None, mode: str, payload_mode: str, stats: dict,
          repo: str | None = None) -> dict:
    cols = Columns(project)
    ordinals = sink_ordinals([f for f, _ in findings], project)
    results = [flow_result(f, j, ordinals, cols, mode) for f, j in findings]
    results += [secret_result(s, cols, mode) for s in secrets]
    dast_targets = sum(1 for r in results if r["kind"] in ("fail", "review") and r["properties"]["dast"]["verifiable"])
    return {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "delta-sast",
                "version": TOOL_VERSION,
                "informationUri": "https://github.com/2026-sbh-delta/delta-network-gateway",
                "rules": [{"id": rid, "name": name, "shortDescription": {"text": name},
                           "fullDescription": {"text": desc}} for rid, (name, desc) in RULES.items()],
            }},
            "columnKind": "unicodeCodePoints",
            "invocations": [{"executionSuccessful": True}],
            "versionControlProvenance": ([{"repositoryUri": repo, "revisionId": commit}]
                                         if repo and commit else []),
            "results": results,
            "properties": {
                "run_id": run_id,
                "commit": commit,
                "mode": mode,
                "payload_mode": payload_mode,
                "dast_targets": dast_targets,
                **stats,
            },
        }],
    }
