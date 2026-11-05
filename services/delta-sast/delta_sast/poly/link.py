"""흐름 조각 잇기(언어 중립). engine.py가 모은 한 파일 안 조각을 다섯 가지 이음새로 이어 Flow를 만든다.

이음새
1. 함수 인자 → 그 함수 매개변수(호출 이름으로 찾는다. 같은 이름이 여럿이면 모두 잇는다)
2. 함수 반환 → 그 함수를 부른 곳의 반환값
3. DB 쓰기(테이블·열) → 같은 테이블 읽기(저장형)
4. 서버 응답(경로) → 같은 경로를 부르는 브라우저 호출(계층 간). 주소가 호출하는 쪽 인자에 달려 있으면 부른 곳에서 정한다
5. 위험 출력 → Flow
출발점은 요청 입력이다. 이음새를 못 찾으면 그 갈래는 끝난다(통과로 보지 않고 흐름이 없을 뿐이다).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from ..model import Flow, Loc, Route, Src
from .engine import Facts, Fn, Seg, Span, split_args

VULN = {"html_text": "xss", "sql": "sqli", "shell": "cmdi", "template": "ssti"}
MAX_DEPTH = 14
SQL_WRITE = re.compile(r"(?is)^\s*(?:insert|replace)\s+into\s+([\w.\"`]+)\s*\(([^)]*)\)")
SQL_UPDATE = re.compile(r"(?is)^\s*update\s+([\w.\"`]+)\s+set\s+(.+?)(?:\swhere\s|$)")
SQL_READ = re.compile(r"(?is)\bfrom\s+([\w.\"`]+)")
SQL_SELECT = re.compile(r"(?is)^\s*select\s+(.*?)\s+from\s")


def table_name(raw: str) -> str:
    """스키마 접두사·따옴표를 뺀 테이블 이름(delta_notes.notes → notes)."""
    return raw.strip("\"`").split(".")[-1].strip("\"`").lower()


def written(sql: str) -> tuple[str, list[str]] | None:
    m = SQL_WRITE.match(sql)
    if m:
        return table_name(m.group(1)), [c.strip().strip("\"`").lower() for c in m.group(2).split(",")]
    m = SQL_UPDATE.match(sql)
    if m:
        cols = [part.split("=", 1)[0].strip().strip("\"`").lower() for part in m.group(2).split(",")]
        return table_name(m.group(1)), cols
    return None


def read_table(sql: str) -> str | None:
    m = SQL_READ.search(sql)
    return table_name(m.group(1)) if m else None


def read_columns(sql: str) -> set[str] | None:
    """SELECT 열 이름(별칭 앞 원래 이름). '*'이거나 읽지 못하면 None(모든 열)."""
    m = SQL_SELECT.match(sql)
    if not m or "*" in m.group(1):
        return None
    cols = set()
    for a, b in split_args(m.group(1)):
        part = m.group(1)[a:b].strip()
        part = re.split(r"(?i)\s+as\s+", part)[0].strip()
        cols.add(part.split(".")[-1].strip("\"`").lower())
    return cols


def norm_url(text: str, prefix: bool = False) -> str:
    """경로 정규화: 따옴표 제거, 앞쪽 ${…} 접두사 제거, 나머지 ${…}·:이름 → :param, 질의 문자열 제거."""
    t = text.strip()
    if len(t) >= 2 and t[0] in "'\"`" and t[-1] == t[0]:
        t = t[1:-1]
    while t.startswith("${"):
        end = t.find("}")
        if end < 0:
            break
        t = t[end + 1:]
    t = re.sub(r"\$\{[^}]*\}", ":param", t)
    t = re.sub(r"/:[A-Za-z_]\w*", "/:param", t)
    t = re.sub(r"\{[A-Za-z_]\w*\}", ":param", t)
    t = t.split("?", 1)[0]
    if prefix:
        t = t.rstrip("/") + "/:param"
    t = re.sub(r"/+", "/", "/" + t.lstrip("/"))
    return t.rstrip("/") or "/"


@dataclass(frozen=True)
class Chain:
    src: Src
    steps: tuple[Loc, ...]
    entry: tuple[Route, ...]
    stores: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    site: Route | None = None  # 가장 최근에 부른 곳의 경로(응답 위치가 경로 밖 도우미 함수일 때 쓴다)
    render: tuple[Route, ...] = ()
    pending: tuple | None = None  # (주소 틀, 함수 이름, 매개변수, 목표 경로): 부른 곳에서 주소를 정해야 하는 브라우저 호출
    depth: int = 0
    seen: frozenset = frozenset()


class Linker:
    def __init__(self, facts: Facts):
        self.facts = facts
        self.flows: dict[tuple, Flow] = {}
        self.by_param: dict[tuple, list[Seg]] = {}
        self.by_read: dict[str, list[Seg]] = {}
        self.by_ret: dict[str, list[Seg]] = {}
        self.fetch: list[Seg] = []
        self.funcs_by_name: dict[str, list[Fn]] = {}
        for fn in facts.funcs:
            self.funcs_by_name.setdefault(fn.name, []).append(fn)
        for seg in facts.segs:
            if seg.origin == "param":
                fn = self.enclosing(seg.path, seg.src_start)
                if fn and seg.src_text in fn.params:
                    self.by_param.setdefault((fn.key, fn.params.index(seg.src_text)), []).append(seg)
            elif seg.origin == "read":
                table = read_table(self.span_value(self.facts.sqls, seg.path, seg.src_start) or "")
                if table:
                    self.by_read.setdefault(table, []).append(seg)
            elif seg.origin == "ret":
                callee = callee_name(seg.src_text)
                if callee:
                    self.by_ret.setdefault(callee, []).append(seg)
            elif seg.origin == "fetch":
                self.fetch.append(seg)

    # ----- 위치 사실 조회 -----
    def enclosing(self, path: str, offset: int) -> Fn | None:
        best = None
        for fn in self.facts.funcs:
            if fn.path == path and fn.start <= offset < fn.end and (best is None or fn.end - fn.start < best.end - best.start):
                best = fn
        return best

    @staticmethod
    def innermost(spans: list[Span], path: str, offset: int) -> Span | None:
        best = None
        for s in spans:
            if s.path == path and s.start <= offset < s.end and (best is None or s.end - s.start < best.end - best.start):
                best = s
        return best

    def span_value(self, spans: list[Span], path: str, offset: int) -> str | None:
        s = self.innermost(spans, path, offset)
        return s.value if s else None

    def route_at(self, path: str, offset: int) -> Route | None:
        span = self.innermost([r for r in self.facts.routes if not r.handler], path, offset)
        if span is None:
            fn = self.enclosing(path, offset)
            span = next((r for r in self.facts.routes if fn and r.handler == fn.name), None)
            if span is None:
                return None
            return Route(span.method or "GET", norm_url(span.value, span.prefix), fn.name)
        method = self.innermost(self.facts.methods, path, offset)
        if method and span.start <= method.start:
            verb = method.value
        elif span.method:
            verb = span.method
        else:
            # 같은 경로 안의 GET 분기 밖이면 본문을 받는 쪽(POST)이다
            gets = [m for m in self.facts.methods if m.path == path and span.start <= m.start < span.end and m.value == "GET"]
            verb = "POST" if gets else "GET"
        fn = self.enclosing(path, offset)
        return Route(verb, norm_url(span.value, span.prefix), fn.name if fn else path)

    # ----- 잇기 -----
    def run(self) -> list[Flow]:
        for seg in self.facts.segs:
            if seg.origin != "req":
                continue
            route = self.route_at(seg.path, seg.src_start)
            fn = self.enclosing(seg.path, seg.src_start)
            kind = "body" if route and route.method in ("POST", "PUT", "PATCH") else "query"
            src = Src(kind, None, seg.src, fn.name if fn else seg.path)
            self.follow(seg, Chain(src, (), (route,) if route else (), site=route))
        return list(self.flows.values())

    def follow(self, seg: Seg, chain: Chain) -> None:
        ident = (seg.path, seg.origin, seg.sink, seg.src_start, seg.sink_start)
        if chain.depth > MAX_DEPTH or ident in chain.seen:
            return
        steps = chain.steps + tuple(s for s in seg.steps if not chain.steps or s != chain.steps[-1])
        chain = replace(chain, steps=steps, depth=chain.depth + 1, seen=chain.seen | {ident})
        here = self.route_at(seg.path, seg.sink_start)
        if seg.sink == "danger":
            self.emit(seg, chain, here)
        elif seg.sink == "write":
            self.write(seg, chain)
        elif seg.sink == "respond":
            route = here or chain.site
            if route is not None:
                self.respond(replace(chain, render=(Route("GET", route.path, route.func),)), route)
        elif seg.sink == "arg":
            self.call(seg, replace(chain, site=here or chain.site))
        elif seg.sink == "return":
            fn = self.enclosing(seg.path, seg.sink_start)
            if fn:
                for nxt in self.by_ret.get(fn.name, []):
                    resolved = self.resolve_pending(chain, fn, nxt)
                    if resolved is not None:
                        self.follow(nxt, resolved)

    def emit(self, seg: Seg, chain: Chain, here: Route | None) -> None:
        if chain.pending is not None:
            return
        sink_text = seg.meta.get("$X", {}).get("text", "")
        field = next((c for c in chain.columns if re.search(rf"\b{re.escape(c)}\b", sink_text)), None)
        field = field or (chain.columns[0] if chain.columns else None)
        render = chain.render or ((Route("GET", here.path, here.func),) if here else ())
        flow = Flow(VULN.get(seg.context, "xss"), seg.context, replace(chain.src, field_name=field), seg.sink_loc,
                    f"poly.{seg.pack}.{seg.context}", chain.steps, (), chain.entry, render, chain.stores)
        key = (chain.src.loc.key(), seg.sink_loc.key(), seg.context)
        known = self.flows.get(key)
        # 같은 입력·출력이면 저장소를 거친(더 구체적인) 흐름, 그다음 짧은 경로를 남긴다
        if known is None or (bool(flow.stores), -len(flow.steps)) > (bool(known.stores), -len(known.steps)):
            self.flows[key] = flow

    def write(self, seg: Seg, chain: Chain) -> None:
        target = written(seg.meta.get("$Q", {}).get("text", "").strip("'\"`"))
        if not target:
            return
        table, cols = target
        value = seg.meta.get("$X", {}).get("text", "")
        cols_hit = cols
        if value.strip().startswith("["):  # 배열로 넘긴 열 값: 입력에서 온 이름이 들어 있는 자리만
            inner = value.strip()[1:-1]
            names = {s.note.removeprefix("전달: ") for s in chain.steps if s.note.startswith("전달: ")}
            hit = [i for i, (a, b) in enumerate(split_args(inner))
                   if any(re.search(rf"\b{re.escape(n)}\b", inner[a:b]) for n in names if n)]
            cols_hit = [cols[i] for i in hit if i < len(cols)] or cols
        else:  # 가변 인자로 넘긴 열 값: SQL 문자열 다음 몇 번째 인자인지가 열 위치다
            index = value_index(self.facts.files.get(seg.path, ""), seg)
            if index is not None and index < len(cols):
                cols_hit = [cols[index]]
        nxt = replace(chain, stores=tuple(dict.fromkeys(chain.stores + (table,))),
                      columns=tuple(dict.fromkeys(chain.columns + tuple(c for c in cols_hit if c != "id"))))
        for read in self.by_read.get(table, []):
            selected = read_columns(self.span_value(self.facts.sqls, read.path, read.src_start) or "")
            columns = nxt.columns if selected is None else tuple(c for c in nxt.columns if c in selected)
            if selected is not None and not columns:
                continue  # 이 조회는 입력이 들어간 열을 읽지 않는다
            self.follow(read, replace(nxt, columns=columns))

    def respond(self, chain: Chain, route: Route) -> None:
        target = route.path
        for seg in self.fetch:
            template = self.span_value(self.facts.fetches, seg.path, seg.src_start) or seg.src_text
            fn = self.enclosing(seg.path, seg.src_start)
            params = [p for p in (fn.params if fn else ()) if p and re.search(rf"\b{re.escape(p)}\b", template)]
            if params and fn:
                self.follow(seg, replace(chain, pending=(template, fn.name, fn.params, target)))
            elif norm_url(template) == target:
                self.follow(seg, chain)

    def resolve_pending(self, chain: Chain, fn: Fn, ret_seg: Seg) -> Chain | None:
        if chain.pending is None:
            return chain
        template, name, params, target = chain.pending
        if name != fn.name:
            return chain
        args_text = call_args(ret_seg.src_text)
        if args_text is None:
            return None
        args = [args_text[a:b].strip() for a, b in split_args(args_text)]
        url = template
        for i, p in enumerate(params):
            if i < len(args) and p:
                url = re.sub(rf"\$\{{\s*{re.escape(p)}\s*\}}", strip_quotes(args[i]), url)
        return replace(chain, pending=None) if norm_url(url) == target else None

    def call(self, seg: Seg, chain: Chain) -> None:
        callee = seg.meta.get("$F", {}).get("text", "")
        index = arg_index(self.facts.files.get(seg.path, ""), seg)
        if not callee or index is None:
            return
        for fn in self.funcs_by_name.get(callee, []):
            for nxt in self.by_param.get((fn.key, index), []):
                self.follow(nxt, chain)


def strip_quotes(text: str) -> str:
    t = text.strip()
    if len(t) >= 2 and t[0] in "'\"`" and t[-1] == t[0]:
        return t[1:-1]
    return t


def callee_name(text: str) -> str | None:
    """호출식 원문에서 부른 함수 이름(마지막 식별자)."""
    head = text.split("(", 1)[0].replace("await", " ").strip()
    m = re.search(r"([A-Za-z_$][\w$]*)\s*$", head)
    return m.group(1) if m else None


def call_args(text: str) -> str | None:
    start = text.find("(")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
    return None


def arg_index(source: str, seg: Seg) -> int | None:
    """인자 조각에서 위험한 값($X)이 몇 번째 인자인지. 호출 이름($F) 뒤 괄호부터 센다."""
    fmeta, xmeta = seg.meta.get("$F"), seg.meta.get("$X")
    if not fmeta or not xmeta:
        return None
    data = source.encode("utf-8")
    open_at = data.find(b"(", fmeta["end"])
    if open_at < 0:
        return None
    depth, close_at = 0, None
    for i in range(open_at, len(data)):
        if data[i:i + 1] == b"(":
            depth += 1
        elif data[i:i + 1] == b")":
            depth -= 1
            if depth == 0:
                close_at = i
                break
    if close_at is None:
        return None
    inner = data[open_at + 1:close_at].decode("utf-8", errors="replace")
    base = open_at + 1
    for i, (a, b) in enumerate(split_args(inner)):
        lo = base + len(inner[:a].encode("utf-8"))
        hi = base + len(inner[:b].encode("utf-8"))
        if lo <= xmeta["start"] < hi:
            return i
    return None


def value_index(source: str, seg: Seg) -> int | None:
    """SQL 문자열($Q) 뒤 인자 가운데 위험한 값($X)의 순서(0부터)."""
    qmeta, xmeta = seg.meta.get("$Q"), seg.meta.get("$X")
    if not qmeta or not xmeta:
        return None
    data = source.encode("utf-8")
    tail_start = qmeta["end"]
    depth, close_at = 0, None
    for i in range(tail_start, len(data)):
        ch = data[i:i + 1]
        if ch in (b"(", b"[", b"{"):
            depth += 1
        elif ch in (b")", b"]", b"}"):
            if depth == 0:
                close_at = i
                break
            depth -= 1
    if close_at is None:
        return None
    tail = data[tail_start:close_at].decode("utf-8", errors="replace")
    comma = tail.find(",")
    if comma < 0:
        return None
    base = tail_start + len(tail[:comma + 1].encode("utf-8"))
    rest = tail[comma + 1:]
    for i, (a, b) in enumerate(split_args(rest)):
        lo = base + len(rest[:a].encode("utf-8"))
        hi = base + len(rest[:b].encode("utf-8"))
        if lo <= xmeta["start"] < hi:
            return i
    return None


def link(facts: Facts) -> list[Flow]:
    return Linker(facts).run()
