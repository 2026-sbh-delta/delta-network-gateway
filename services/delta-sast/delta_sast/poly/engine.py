"""언어 중립 엔진의 사실·흐름 조각 수집.

언어별 규칙 묶음(packs/*.yaml)을 Opengrep 규칙으로 바꿔 두 번 실행한다.
1차(위치 사실): 이름 있는 함수, 경로 선언, 메서드 분기, SQL 호출, 클라이언트 호출 주소.
2차(흐름 조각, taint): 출발점(요청·매개변수·DB 조회·함수 반환·클라이언트 응답) → 도착점(위험 출력·DB 쓰기·응답·
함수 인자·반환). 언어 의미(대입·전달·문자열 조립)는 Opengrep이 따라간다. 조각을 잇는 일은 link.py가 한다.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..locate import EXCLUDES, LocateError, opengrep_binary
from ..model import Loc

PACKS = Path(__file__).parent / "packs"
EXTENSIONS = {"javascript": (".js", ".mjs", ".cjs", ".jsx"), "typescript": (".ts", ".tsx"),
              "java": (".java",), "go": (".go",)}
ORIGINS = ("req", "param", "read", "ret", "fetch")
SINKS = ("danger", "write", "respond", "arg", "return")


@dataclass(frozen=True)
class Fn:
    """이름 있는 함수(요약 단위)."""

    pack: str
    path: str
    name: str
    line: int
    end_line: int
    start: int  # 바이트 위치
    end: int
    params: tuple[str, ...]

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.path, self.name, self.line)


@dataclass(frozen=True)
class Span:
    """경로·메서드·SQL·호출 주소처럼 구간과 값 하나를 가진 사실."""

    path: str
    start: int
    end: int
    line: int
    value: str
    prefix: bool = False
    method: str | None = None
    handler: str = ""  # 경로가 이름으로 가리키는 처리 함수(Go http.HandleFunc 등). 비어 있으면 구간이 처리 범위


@dataclass
class Seg:
    """흐름 조각 하나: 출발점 → 도착점(한 파일 안)."""

    pack: str
    origin: str  # ORIGINS
    sink: str  # SINKS
    context: str  # danger 맥락(html_text·sql·shell·template)
    path: str
    src: Loc
    src_text: str
    src_start: int
    sink_loc: Loc
    sink_start: int
    steps: tuple[Loc, ...]
    meta: dict = field(default_factory=dict)  # 도착점 메타변수 값($F·$Q 등)과 위치


@dataclass
class Facts:
    files: dict[str, str] = field(default_factory=dict)  # 경로 → 원문
    funcs: list[Fn] = field(default_factory=list)
    routes: list[Span] = field(default_factory=list)
    methods: list[Span] = field(default_factory=list)
    sqls: list[Span] = field(default_factory=list)
    fetches: list[Span] = field(default_factory=list)
    segs: list[Seg] = field(default_factory=list)
    accessors: set[str] = field(default_factory=set)  # 저장소·원격 호출을 감싼 함수 이름
    errors: list[str] = field(default_factory=list)


def load_packs() -> list[dict]:
    return [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(PACKS.glob("*.yaml"))]


def _present(root: Path) -> set[str]:
    langs = set()
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if any(part in EXCLUDES for part in path.relative_to(root).parts):
            continue
        for lang, exts in EXTENSIONS.items():
            if path.suffix.lower() in exts:
                langs.add(lang)
    return langs


def _rule(rid: str, langs: list[str], body: dict) -> dict:
    return {"id": rid, "languages": langs, "severity": "INFO", "message": rid, **body}


def _either(patterns: list[str]) -> dict:
    return {"pattern-either": [{"pattern": p} for p in patterns]} if len(patterns) > 1 else {"pattern": patterns[0]}


def _with_param(fn_pattern: str) -> str:
    """함수 모양의 매개변수 자리를 '…, $P, …'로 바꿔 매개변수 하나를 묶는다."""
    return fn_pattern.replace("$...PARAMS", "..., $P, ...")


def fact_rules(pack: dict) -> list[dict]:
    langs, pid = pack["languages"], pack["id"]
    rules = [_rule(f"fact.{pid}.func.{i}", langs, {"pattern": p}) for i, p in enumerate(pack.get("functions", []))]
    for i, route in enumerate(pack.get("routes", [])):
        body = {"pattern": route["pattern"]}
        if route.get("method_regex"):
            body = {"patterns": [{"pattern": route["pattern"]},
                                 {"metavariable-regex": {"metavariable": route["method_metavar"], "regex": route["method_regex"]}}]}
        rules.append(_rule(f"fact.{pid}.route.{i}", langs, body))
    for i, p in enumerate(pack.get("methods", [])):
        rules.append(_rule(f"fact.{pid}.method.{i}", langs, {"pattern": p}))
    for i, p in enumerate(pack.get("sql_reads", []) + pack.get("sql_writes", [])):
        rules.append(_rule(f"fact.{pid}.sql.{i}", langs, {"pattern": p}))
    for i, p in enumerate(pack.get("fetches", [])):
        rules.append(_rule(f"fact.{pid}.fetch.{i}", langs, {"pattern": p}))
    return rules


def _as_source(pattern: str) -> str:
    """출발점 패턴의 메타변수 이름을 바꾼다. taint 규칙에서 출발점·도착점이 같은 이름을 쓰면 값이 하나로 묶인다."""
    for name in ("$...PARAMS", "$DB", "$Q", "$X", "$F", "$O", "$M", "$URL"):
        pattern = pattern.replace(name, name + "SRC")
    return pattern


def _sources(pack: dict, origin: str, names: set[str]) -> list[dict] | None:
    if origin == "req":
        out = [{"patterns": [{"pattern-inside": p}, {"pattern": "$REQ"}]} for p in pack.get("requests", [])]
        for item in pack.get("inputs", []):  # 요청 객체 없이 바로 입력이 되는 식·매개변수
            if isinstance(item, dict):
                out.append({"patterns": [{"pattern-inside": _as_source(item["inside"])}, {"pattern": _as_source(item["pattern"])}]})
            else:
                out.append({"pattern": _as_source(item)})
        return out or None
    if origin == "param":
        return [{"patterns": [{"pattern-inside": _as_source(_with_param(p))}, {"pattern": "$P"}]}
                for p in pack.get("functions", [])] or None
    if origin == "read":
        return [{"patterns": [{"pattern": _as_source(p)},
                              {"metavariable-regex": {"metavariable": "$QSRC", "regex": r"(?is)^\s*select\b"}}]}
                for p in pack.get("sql_reads", [])] or None
    if origin == "ret":
        if not names:
            return None
        regex = "^(" + "|".join(sorted(names)) + ")$"
        return [{"patterns": [{"pattern-either": [{"pattern": "$FSRC(...)"}, {"pattern": "$OSRC.$FSRC(...)"}]},
                              {"metavariable-regex": {"metavariable": "$FSRC", "regex": regex}}]}]
    if origin == "fetch":
        return [{"pattern": _as_source(p)} for p in pack.get("fetches", [])] or None
    return None


def _sinks(pack: dict, sink: str, names: set[str]) -> list[tuple[str, list[dict]]]:
    """(맥락, 싱크 목록). danger는 맥락별로 나눈다."""
    focus = {"focus-metavariable": "$X"}
    if sink == "danger":
        def one(item) -> dict:
            if isinstance(item, dict):  # {pattern, not: [...]}: 순수 문자열처럼 위험하지 않은 모양은 뺀다
                return {"patterns": [{"pattern": item["pattern"]}, *({"pattern-not": n} for n in item.get("not", [])), focus]}
            return {"patterns": [{"pattern": item}, focus]}
        return [(ctx, [one(p) for p in pats]) for ctx, pats in pack.get("sinks", {}).items()]
    if sink == "write":
        pats = pack.get("sql_writes", [])
        return [("", [{"patterns": [{"pattern": p}, focus,
                                    {"metavariable-regex": {"metavariable": "$Q", "regex": r"(?is)^\s*(insert|update|replace)\b"}}]}
                      for p in pats])] if pats else []
    if sink == "respond":
        pats = pack.get("responses", [])
        return [("", [{"patterns": [{"pattern": p}, focus]} for p in pats])] if pats else []
    if sink == "arg":
        if not names:
            return []
        regex = "^(" + "|".join(sorted(names)) + ")$"
        return [("", [{"patterns": [{"pattern-either": [{"pattern": "$F(..., $X, ...)"}, {"pattern": "$O.$F(..., $X, ...)"}]},
                                    focus, {"metavariable-regex": {"metavariable": "$F", "regex": regex}}]}])]
    if sink == "return":
        pats = pack.get("returns", [])
        return [("", [{"patterns": [{"pattern": p}, focus]} for p in pats])] if pats else []
    return []


def _opaque(pack: dict, origin: str, accessors: set[str]) -> list[dict]:
    """결과가 인자에서 오지 않는 호출: 클라이언트 호출(주소 인자)과 DB 조회(조회 키). 그 결과는 각각 'fetch'·'read'
    출발점으로만 다루고, 다른 출발점의 흐름은 이 호출을 지나 결과로 번지지 않게 한다(조회 키 ≠ 조회한 데이터)."""
    out = []
    if origin != "fetch":
        out += [{"pattern": _as_source(p).replace("SRC", "SAN")} for p in pack.get("fetches", [])]
    if origin != "read":
        out += [{"patterns": [{"pattern": _as_source(p).replace("SRC", "SAN")},
                              {"metavariable-regex": {"metavariable": "$QSAN", "regex": r"(?is)^\s*select\b"}}]}
                for p in pack.get("sql_reads", [])]
    if origin != "ret" and accessors:
        regex = "^(" + "|".join(sorted(accessors)) + ")$"
        # 저장소·원격 호출을 감싼 프로젝트 함수(조회·fetch 결과를 반환)도 같다. 결과는 'ret' 출발점과 요약으로만 잇는다
        out.append({"patterns": [{"pattern-either": [{"pattern": "$FSAN(...)"}, {"pattern": "$OSAN.$FSAN(...)"}]},
                                 {"metavariable-regex": {"metavariable": "$FSAN", "regex": regex}}]})
    return out


def taint_rules(pack: dict, names: set[str], accessors: set[str] = frozenset()) -> list[dict]:
    langs, pid = pack["languages"], pack["id"]
    rules = []
    for origin in ORIGINS:
        sources = _sources(pack, origin, names)
        if not sources:
            continue
        sanitizers = [{"pattern": p} for p in pack.get("sanitizers", [])] + _opaque(pack, origin, accessors)
        for sink in SINKS:
            for ctx, sinks in _sinks(pack, sink, names):
                if not sinks:
                    continue
                body = {"mode": "taint", "pattern-sources": sources, "pattern-sinks": sinks}
                if sanitizers:
                    body["pattern-sanitizers"] = sanitizers
                rules.append(_rule(f"seg.{pid}.{origin}.{sink}.{ctx or '-'}", langs, body))
    return rules


def run(root: Path, rules: list[dict], binary: str = "opengrep", timeout: int = 120, traces: bool = False) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
        yaml.safe_dump({"rules": rules}, fh, allow_unicode=True, sort_keys=False)
        config = fh.name
    cmd = [opengrep_binary(binary), "scan", "--config", config, "--json", "--quiet", "--disable-version-check",
           "--no-git-ignore"]
    if traces:
        cmd.append("--dataflow-traces")
    cmd += [f"--exclude={p}" for p in EXCLUDES] + [str(root)]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise LocateError(f"opengrep(다언어)이 {timeout}초 안에 끝나지 않았다") from exc
    finally:
        Path(config).unlink(missing_ok=True)
    if done.returncode not in (0, 1):
        raise LocateError(f"opengrep(다언어) 실패(exit {done.returncode}): {done.stderr.strip()[:500]}")
    return json.loads(done.stdout)


def _rel(root: Path, path: str) -> str:
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except ValueError:
        return path


def _loc(rel: str, start: dict, end: dict, note: str = "") -> Loc:
    return Loc(rel, start["line"], start["col"], end["line"], end["col"], note)


def _text(mv: dict | None, source: bytes | None = None) -> str:
    """메타변수 값. 원문이 있으면 위치로 잘라 그대로 쓴다(Opengrep 요약 문자열은 쉼표·${ 등을 버린다)."""
    if not mv:
        return ""
    if source is not None and "start" in mv:
        return source[mv["start"]["offset"]:mv["end"]["offset"]].decode("utf-8", errors="replace")
    return mv.get("abstract_content", "")


def is_path_literal(text: str) -> bool:
    """경로 문자열 리터럴인지(따옴표·백틱으로 감싸고, 앞의 ${…} 접두사를 빼면 /로 시작)."""
    t = text.strip()
    if len(t) < 2 or t[0] not in "'\"`" or t[-1] != t[0]:
        return False
    body = t[1:-1]
    while body.startswith("${"):
        end = body.find("}")
        if end < 0:
            return False
        body = body[end + 1:]
    return body.startswith("/")


def split_args(text: str) -> list[tuple[int, int]]:
    """괄호·대괄호·중괄호 깊이를 보며 최상위 쉼표로 인자 구간(상대 위치)을 나눈다. 문자열 안 쉼표는 무시한다."""
    parts, depth, start, quote, i = [], 0, 0, "", 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = ""
        elif c in "'\"`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append((start, i))
            start = i + 1
        i += 1
    parts.append((start, len(text)))
    return [(a, b) for a, b in parts if text[a:b].strip()]


def params_of(text: str, style: str = "trailing") -> tuple[str, ...]:
    """매개변수 목록 문자열 → 이름. style: trailing(JS·TS 'name: T = d', Java 'T name'은 마지막 토큰),
    leading(Go 'name T'는 첫 토큰). 구조 분해 매개변수는 원문 그대로 자리만 둔다."""
    names = []
    for a, b in split_args(text):
        piece = text[a:b].strip().split("=", 1)[0].strip().lstrip(".").strip()
        if piece.startswith(("{", "[")):
            names.append(piece)
            continue
        if ":" in piece:
            piece = piece.split(":", 1)[0].strip()
        tokens = [t for t in piece.replace("@", " ").split() if t]
        if tokens:
            piece = tokens[0] if style == "leading" else tokens[-1]
        names.append(piece.rstrip("?"))
    return tuple(names)


def collect(root: Path, binary: str = "opengrep") -> Facts:
    root = root.resolve()
    facts = Facts()
    langs = _present(root)
    packs = [p for p in load_packs() if set(p["languages"]) & langs]
    if not packs:
        return facts
    report = run(root, [r for p in packs for r in fact_rules(p)], binary)
    facts.errors += [str(e.get("message", ""))[:200] for e in report.get("errors", []) if e.get("level") == "error"]
    by_id = {p["id"]: p for p in packs}
    raw: dict[str, bytes] = {}

    def source_of(rel: str) -> bytes:
        if rel not in raw:
            try:
                raw[rel] = (root / rel).read_bytes()
            except OSError:
                raw[rel] = b""
        return raw[rel]

    funcs: dict[tuple, Fn] = {}
    for item in report.get("results", []):
        _, pid, kind, idx = item["check_id"].rsplit(".", 4)[-4:]
        rel = _rel(root, item["path"])
        src = source_of(rel)
        mv = item["extra"].get("metavars") or {}
        start, end = item["start"]["offset"], item["end"]["offset"]
        if kind == "func":
            name = _text(mv.get("$NAME"), src)
            if name:
                params = params_of(_text(mv.get("$...PARAMS"), src), by_id[pid].get("param_style", "trailing"))
                funcs.setdefault((rel, name, start), Fn(pid, rel, name, item["start"]["line"], item["end"]["line"],
                                                         start, end, params))
        elif kind == "route":
            spec = by_id[pid]["routes"][int(idx)]
            url = _text(mv.get("$URL"), src)
            if not is_path_literal(url):
                continue
            method = (_text(mv.get(spec["method_metavar"]), src).upper() if spec.get("method_metavar")
                      else spec.get("method"))
            handler = _text(mv.get(spec["handler_metavar"]), src) if spec.get("handler_metavar") else ""
            facts.routes.append(Span(rel, start, end, item["start"]["line"], url, bool(spec.get("prefix")), method, handler))
        elif kind == "method":
            facts.methods.append(Span(rel, start, end, item["start"]["line"],
                                      _text(mv.get("$M"), src).strip("'\"`").upper()))
        elif kind == "sql":
            facts.sqls.append(Span(rel, start, end, item["start"]["line"], _text(mv.get("$Q"), src).strip("'\"`")))
        elif kind == "fetch":
            facts.fetches.append(Span(rel, start, end, item["start"]["line"], _text(mv.get("$URL"), src)))
    facts.funcs = sorted(funcs.values(), key=lambda f: (f.path, f.start, -f.end))
    for rel, data in raw.items():
        facts.files[rel] = data.decode("utf-8", errors="replace")
    names = {f.name for f in facts.funcs}
    # 1차: 저장소·원격 호출을 감싼 함수(조회·fetch 결과가 반환에 닿는 함수)를 찾는다
    first = run(root, [r for p in packs for r in taint_rules(p, names)
                       if r["id"].split(".")[2] in ("read", "fetch") and r["id"].split(".")[3] == "return"], binary)
    accessors = set()
    for item in first.get("results", []):
        rel = _rel(root, item["path"])
        offset = item["start"]["offset"]
        inner = [f for f in facts.funcs if f.path == rel and f.start <= offset < f.end]
        if inner:
            accessors.add(min(inner, key=lambda f: f.end - f.start).name)
    facts.accessors = accessors
    report = run(root, [r for p in packs for r in taint_rules(p, names, accessors)], binary, traces=True)
    facts.errors += [str(e.get("message", ""))[:200] for e in report.get("errors", []) if e.get("level") == "error"]
    seen = set()
    for item in report.get("results", []):
        _, pid, origin, sink, ctx = item["check_id"].rsplit(".", 5)[-5:]
        rel = _rel(root, item["path"])
        extra = item["extra"]
        trace = extra.get("dataflow_trace") or {}
        source = trace.get("taint_source")
        if not source or source[0] != "CliLoc":
            continue
        sloc, stext = source[1]
        src = _loc(rel, sloc["start"], sloc["end"], "입력")
        steps = [src]
        for var in trace.get("intermediate_vars", []):
            loc = var["location"]
            steps.append(_loc(rel, loc["start"], loc["end"], f"전달: {var.get('content', '')}"))
        sink_loc = _loc(rel, item["start"], item["end"], "출력")
        steps.append(sink_loc)
        key = (pid, origin, sink, ctx, rel, sloc["start"]["offset"], item["start"]["offset"])
        if key in seen:
            continue
        seen.add(key)
        src_bytes = source_of(rel)
        meta = {k: {"text": _text(v, src_bytes), "start": v["start"]["offset"], "end": v["end"]["offset"]}
                for k, v in (extra.get("metavars") or {}).items() if "start" in v}
        facts.segs.append(Seg(pid, origin, sink, "" if ctx == "-" else ctx, rel, src, stext,
                              sloc["start"]["offset"], sink_loc, item["start"]["offset"], tuple(steps), meta))
    return facts
