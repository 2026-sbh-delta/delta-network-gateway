"""② 경로: Python AST로 호출 그래프·데이터 흐름·저장 경계를 만들고 소스→싱크 경로를 낸다.

- 함수 안은 문장 순서대로 보고(분기는 합침), 함수 사이는 요약(summary)을 호출 위치에서 풀어 쓴다.
- 전역 dict·list와 DB 테이블은 저장소로 보고, 쓰기와 읽기를 필드 단위로 잇는다.
- CFG는 만들지 않는다. 분기마다 필터가 걸리는지는 LLM 판단 몫이다.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import re
import string
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from .locate import EXCLUDES
from .model import KEY_FIELD, Flow, Hit, Label, Loc, Mark, Par, Ref, Route, San, Src, Write
from .policy import Policy

LabelSet = dict[tuple, Label]

MAX_STEPS = 60
MAX_STORE_DEPTH = 3
BUILTIN_NAMES = frozenset(dir(builtins))
MUTATORS = frozenset({"append", "extend", "add", "insert", "update", "setdefault", "appendleft", "push"})
PASSTHROUGH_WRAPPERS = frozenset({"reversed", "sorted", "list", "tuple", "iter", "set"})
STORE_FACTORIES = frozenset({"dict", "list", "set", "defaultdict", "OrderedDict", "deque", "Counter"})
URL_ATTRS = frozenset({"href", "src", "action", "formaction", "data", "poster", "srcset", "background"})
ROUTE_METHODS = {"get": "GET", "post": "POST", "put": "PUT", "delete": "DELETE", "patch": "PATCH"}
HTML_RE = re.compile(r"<[a-zA-Z!/]")
INSERT_RE = re.compile(r"insert\s+(?:or\s+\w+\s+)?into\s+[\"`\[]?(\w+)[\"`\]]?\s*\(([^)]*)\)\s*values\s*\(([^)]*)\)", re.I | re.S)
SELECT_RE = re.compile(r"select\s+.+?\s+from\s+[\"`\[]?(\w+)", re.I | re.S)
UPDATE_RE = re.compile(r"update\s+[\"`\[]?(\w+)[\"`\]]?\s+set\s+(.+?)(?:\s+where\s|$)", re.I | re.S)
PATH_PARAM_RE = re.compile(r"<(?:(\w+):)?(\w+)>")
NON_STRING_CONVERTERS = frozenset({"int", "float", "uuid"})
VULN_BY_CONTEXT = {"sql": "sqli", "shell": "cmdi", "template": "ssti"}
AST_HTML_RULE = "delta-ast-html-build"


# ---------- 라벨 집합 ----------


def lset(*labels: Label) -> LabelSet:
    out: LabelSet = {}
    for label in labels:
        _put(out, label)
    return out


def _put(out: LabelSet, label: Label) -> None:
    key = label.ident()
    old = out.get(key)
    if old is None or len(label.steps) < len(old.steps):
        out[key] = label


def union(*sets: LabelSet) -> LabelSet:
    out: LabelSet = {}
    for s in sets:
        for label in s.values():
            _put(out, label)
    return out


def _append(steps: tuple[Loc, ...], loc: Loc) -> tuple[Loc, ...]:
    if steps and steps[-1].key() == loc.key():
        return steps
    if len(steps) >= MAX_STEPS:
        return steps
    return (*steps, loc)


def _join(a: tuple[Loc, ...], b: tuple[Loc, ...]) -> tuple[Loc, ...]:
    out = a
    for loc in b:
        out = _append(out, loc)
    return out


def with_step(s: LabelSet, loc: Loc) -> LabelSet:
    return lset(*(Label(l.origin, _append(l.steps, loc), l.sans) for l in s.values()))


def with_san(s: LabelSet, san: San) -> LabelSet:
    return lset(*(Label(l.origin, _append(l.steps, san.loc), (*l.sans, san)) for l in s.values()))


def narrow(s: LabelSet, field_name: str | None) -> LabelSet:
    if field_name is None:
        return s
    out = []
    for l in s.values():
        if isinstance(l.origin, Ref) and l.origin.field_name is None:
            out.append(Label(Ref(l.origin.store, field_name), l.steps, l.sans))
        else:
            out.append(l)
    return lset(*out)


def key_refs(s: LabelSet) -> LabelSet:
    return narrow(s, KEY_FIELD)


def code_hash(text: str) -> str:
    norm = "\n".join(line.rstrip() for line in text.strip().splitlines())
    return "sha256:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def html_context(prefix: str) -> str:
    """싱크 앞쪽 문자열로 출력 맥락을 정한다."""
    low = prefix.lower()
    if low.rfind("<script") > low.rfind("</script"):
        return "js_string"
    lt, gt = prefix.rfind("<"), prefix.rfind(">")
    if lt > gt:
        m = re.search(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*([\"']?)([^\"']*)$", prefix[lt:])
        if m:
            attr = m.group(1).lower()
            if attr in URL_ATTRS:
                return "url"
            if attr.startswith("on"):
                return "js_string"
        return "html_attr"
    return "html_text"


# ---------- 프로젝트 색인 ----------


@dataclass
class FuncInfo:
    qname: str
    module: "ModuleInfo"
    node: ast.FunctionDef | ast.AsyncFunctionDef
    params: list[str]
    routes: list[Route] = field(default_factory=list)

    @property
    def source(self) -> str:
        return ast.get_source_segment(self.module.source, self.node) or ""


@dataclass
class ModuleInfo:
    name: str
    path: str
    source: str
    tree: ast.Module
    is_pkg: bool
    imports: dict[str, str] = field(default_factory=dict)
    funcs: dict[str, FuncInfo] = field(default_factory=dict)
    stores: set[str] = field(default_factory=set)
    consts: dict[str, str] = field(default_factory=dict)
    prefixes: dict[str, str] = field(default_factory=dict)  # Blueprint 변수 → url_prefix

    def loc(self, node: ast.AST, note: str = "") -> Loc:
        return Loc(self.path, node.lineno, node.col_offset + 1, node.end_lineno or node.lineno,
                   (node.end_col_offset or node.col_offset) + 1, note)


@dataclass
class Summary:
    ret: LabelSet = field(default_factory=dict)
    hits: dict[tuple, Hit] = field(default_factory=dict)
    writes: dict[tuple, Write] = field(default_factory=dict)
    callees: set[str] = field(default_factory=set)


def _module_name(rel: Path) -> tuple[str, bool]:
    parts = list(rel.with_suffix("").parts)
    is_pkg = parts[-1] == "__init__"
    if is_pkg:
        parts = parts[:-1]
    return ".".join(parts), is_pkg


class Project:
    def __init__(self, root: Path, marks: list[Mark], policy: Policy):
        self.root = root.resolve()
        self.policy = policy
        self.modules: dict[str, ModuleInfo] = {}
        self.funcs: dict[str, FuncInfo] = {}
        self.stores: set[str] = set()
        self.consts: dict[str, str] = {}
        self.marks = {m.loc.key(): m for m in marks if m.role in ("source", "sink")}
        self.parse_errors: list[str] = []
        self._summaries: dict[str, Summary] = {}
        self._active: set[str] = set()
        self._load()

    # ----- 적재 -----

    def _load(self) -> None:
        for path in sorted(self.root.rglob("*.py")):
            rel = path.relative_to(self.root)
            if any(part in EXCLUDES for part in rel.parts):
                continue
            source = path.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(source, filename=str(rel))
            except SyntaxError as exc:
                self.parse_errors.append(f"{rel.as_posix()}: {exc.msg}")
                continue
            name, is_pkg = _module_name(rel)
            self.modules[name] = ModuleInfo(name, rel.as_posix(), source, tree, is_pkg)
        for mod in self.modules.values():
            self._index(mod)

    def _package(self, mod: ModuleInfo, level: int) -> str:
        parts = mod.name.split(".") if mod.name else []
        if not mod.is_pkg:
            parts = parts[:-1]
        if level > 1:
            parts = parts[: len(parts) - (level - 1)]
        return ".".join(parts)

    def _index(self, mod: ModuleInfo) -> None:
        for node in mod.tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod.imports[alias.asname or alias.name.split(".")[0]] = (
                        alias.name if alias.asname else alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    pkg = self._package(mod, node.level)
                    base = f"{pkg}.{base}".strip(".") if base else pkg
                for alias in node.names:
                    mod.imports[alias.asname or alias.name] = f"{base}.{alias.name}".strip(".")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add_func(mod, node, node.name)
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self._add_func(mod, item, f"{node.name}.{item.name}")
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                for target in targets:
                    if not isinstance(target, ast.Name) or value is None:
                        continue
                    q = f"{mod.name}.{target.id}".strip(".")
                    if self._is_store_value(value):
                        mod.stores.add(target.id)
                        self.stores.add(q)
                    elif (text := const_str(value)) is not None:
                        mod.consts[target.id] = text
                        self.consts[q] = text
                    elif isinstance(value, ast.Call) and self._call_name(value) == "Blueprint":
                        prefix = next((const_str(k.value) for k in value.keywords if k.arg == "url_prefix"), None)
                        mod.prefixes[target.id] = prefix or ""
        for info in list(mod.funcs.values()):
            info.routes = self._routes(mod, info)

    @staticmethod
    def _call_name(node: ast.Call) -> str:
        f = node.func
        return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""

    def _is_store_value(self, value: ast.AST) -> bool:
        if isinstance(value, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)):
            return True
        return isinstance(value, ast.Call) and self._call_name(value) in STORE_FACTORIES

    def _add_func(self, mod: ModuleInfo, node: ast.FunctionDef | ast.AsyncFunctionDef, local: str) -> None:
        a = node.args
        params = [p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
        q = f"{mod.name}.{local}".strip(".")
        info = FuncInfo(q, mod, node, params)
        mod.funcs[local] = info
        self.funcs[q] = info

    def _routes(self, mod: ModuleInfo, info: FuncInfo) -> list[Route]:
        out = []
        for dec in info.node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            attr = dec.func.attr
            path = const_str(dec.args[0]) if dec.args else None
            if path is None or attr not in (*ROUTE_METHODS, "route"):
                continue
            owner = dec.func.value.id if isinstance(dec.func.value, ast.Name) else ""
            full = (mod.prefixes.get(owner, "") + path) or "/"
            if attr == "route":
                methods = ["GET"]
                for kw in dec.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        methods = [m.upper() for e in kw.value.elts if (m := const_str(e))] or methods
            else:
                methods = [ROUTE_METHODS[attr]]
            out.extend(Route(m, full, info.qname) for m in methods)
        return out

    def enclosing(self, path: str, line: int) -> FuncInfo | None:
        """그 줄을 품은 가장 안쪽 함수."""
        best = None
        for info in self.funcs.values():
            node = info.node
            if info.module.path == path and node.lineno <= line <= (node.end_lineno or node.lineno):
                if best is None or node.lineno >= best.node.lineno:
                    best = info
        return best

    # ----- 이름 풀기 -----

    def resolve_name(self, mod: ModuleInfo, name: str) -> str | None:
        if name in mod.funcs or name in mod.stores or name in mod.consts:
            return f"{mod.name}.{name}".strip(".")
        if name in mod.imports:
            return mod.imports[name]
        if name in BUILTIN_NAMES:
            return name
        return None

    def resolve_expr(self, mod: ModuleInfo, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.resolve_name(mod, node.id)
        if isinstance(node, ast.Attribute):
            base = self.resolve_expr(mod, node.value)
            return f"{base}.{node.attr}" if base else None
        return None

    # ----- 요약 -----

    def summary(self, qname: str) -> Summary:
        if qname in self._summaries:
            return self._summaries[qname]
        if qname in self._active:
            return Summary()  # 재귀 호출은 빈 요약으로 끊는다
        self._active.add(qname)
        try:
            result = FunctionAnalyzer(self, self.funcs[qname]).run()
        finally:
            self._active.discard(qname)
        self._summaries[qname] = result
        return result

    def analyze_all(self) -> None:
        for q in sorted(self.funcs):
            self.summary(q)

    # ----- 경로 조립 -----

    def _callers(self) -> dict[str, set[str]]:
        callers: dict[str, set[str]] = defaultdict(set)
        for q, s in self._summaries.items():
            for callee in s.callees:
                callers[callee].add(q)
        return callers

    def routes_reaching(self, qname: str, callers: dict[str, set[str]] | None = None) -> tuple[Route, ...]:
        callers = callers if callers is not None else self._callers()
        seen, queue, routes = {qname}, deque([qname]), []
        while queue:
            cur = queue.popleft()
            info = self.funcs.get(cur)
            if info and info.routes:
                routes.extend(info.routes)
                continue
            for c in sorted(callers.get(cur, ())):
                if c not in seen:
                    seen.add(c)
                    queue.append(c)
        return tuple(sorted(set(routes), key=lambda r: (r.path, r.method)))

    def _writes_by_store(self) -> dict[str, list[Write]]:
        out: dict[str, list[Write]] = defaultdict(list)
        for s in self._summaries.values():
            for w in s.writes.values():
                if not isinstance(w.label.origin, Par):
                    out[w.store].append(w)
        return out

    def _resolve(self, label: Label, writes: dict[str, list[Write]], depth: int = 0):
        """라벨을 실제 요청 입력(Src)까지 거슬러 올라간다. (Src, steps, sans, stores)를 낸다."""
        origin = label.origin
        if isinstance(origin, Src):
            yield origin, label.steps, label.sans, ()
            return
        if not isinstance(origin, Ref) or depth >= MAX_STORE_DEPTH:
            return
        for w in writes.get(origin.store, ()):
            if origin.field_name == KEY_FIELD:
                ok = w.field_name == KEY_FIELD
            elif origin.field_name is None:
                ok = w.field_name != KEY_FIELD
            else:
                ok = w.field_name in (origin.field_name, None)
            if not ok:
                continue
            for src, steps, sans, stores in self._resolve(w.label, writes, depth + 1):
                yield src, _join(steps, label.steps), (*sans, *label.sans), (*stores, origin.store)

    def flows(self) -> list[Flow]:
        self.analyze_all()
        writes = self._writes_by_store()
        callers = self._callers()
        best: dict[tuple, Flow] = {}
        for s in self._summaries.values():
            for hit in s.hits.values():
                if isinstance(hit.label.origin, Par):
                    continue
                render = self.routes_reaching(hit.func, callers)
                for src, steps, sans, stores in self._resolve(hit.label, writes):
                    vuln = VULN_BY_CONTEXT.get(hit.context, "xss")
                    flow = Flow(vuln, hit.context, src, hit.sink, hit.sink_rule, steps, tuple(sans),
                                self.routes_reaching(src.func, callers), render, tuple(dict.fromkeys(stores)))
                    key = (src, hit.context, render, tuple(x.code_hash for x in flow.sans))
                    old = best.get(key)
                    if old is None or len(flow.steps) < len(old.steps):
                        best[key] = flow
        # 앞쪽 싱크에서 이미 보고된 값이 바깥 HTML에 다시 들어간 경로는 뺀다.
        sinks_by_src: dict[Src, set[tuple]] = defaultdict(set)
        for flow in best.values():
            sinks_by_src[flow.source].add(flow.sink.key())
        kept = [f for f in best.values()
                if not any(s.key() in sinks_by_src[f.source] and s.key() != f.sink.key() for s in f.steps)]
        return sorted(kept, key=lambda f: (f.sink.key(), f.source.loc.key(), f.context))


# ---------- 함수 하나 분석 ----------


class FunctionAnalyzer:
    def __init__(self, project: Project, info: FuncInfo):
        self.p = project
        self.info = info
        self.mod = info.module
        self.out = Summary()

    def loc(self, node: ast.AST, note: str = "") -> Loc:
        return self.mod.loc(node, note)

    def run(self) -> Summary:
        node = self.info.node
        env: dict[str, LabelSet] = {}
        path_params = {name: conv for r in self.info.routes for conv, name in PATH_PARAM_RE.findall(r.path)}
        a = node.args
        for i, arg in enumerate((*a.posonlyargs, *a.args, *a.kwonlyargs)):
            if arg.arg in path_params:
                if path_params[arg.arg] in NON_STRING_CONVERTERS:
                    env[arg.arg] = {}
                    continue
                src = Src("path", arg.arg, self.loc(arg), self.info.qname)
                env[arg.arg] = lset(Label(src, (self.loc(arg, f"입력 path.{arg.arg}"),)))
                continue
            env[arg.arg] = lset(Label(Par(i, arg.arg), (self.loc(arg, f"매개변수 {arg.arg}"),)))
        for _ in range(2):  # 반복문에서 앞 회차 값이 뒤로 흐르는 경우까지 본다
            self.block(node.body, dict(env))
        return self.out

    # ----- 기록 -----

    def hit(self, context: str, sink: Loc, rule: str, labels: LabelSet) -> None:
        for label in labels.values():
            label = Label(label.origin, _append(label.steps, sink), label.sans)
            key = (context, sink.key(), label.ident())
            old = self.out.hits.get(key)
            if old is None or len(label.steps) < len(old.label.steps):
                self.out.hits[key] = Hit(context, sink, rule, label, self.info.qname)

    def write(self, store: str, field_name: str | None, labels: LabelSet, loc: Loc) -> None:
        loc = Loc(loc.path, loc.line, loc.col, loc.end_line, loc.end_col,
                  f"저장 {store}.{field_name or '*'}")
        for label in labels.values():
            label = Label(label.origin, _append(label.steps, loc), label.sans)
            key = (store, field_name, label.ident())
            old = self.out.writes.get(key)
            if old is None or len(label.steps) < len(old.label.steps):
                self.out.writes[key] = Write(store, field_name, label, loc, self.info.qname)

    # ----- 문장 -----

    def block(self, stmts: list[ast.stmt], env: dict[str, LabelSet]) -> dict[str, LabelSet]:
        for st in stmts:
            env = self.stmt(st, env)
        return env

    @staticmethod
    def merge(*envs: dict[str, LabelSet]) -> dict[str, LabelSet]:
        out: dict[str, LabelSet] = {}
        for env in envs:
            for k, v in env.items():
                out[k] = union(out.get(k, {}), v)
        return out

    def stmt(self, st: ast.stmt, env: dict[str, LabelSet]) -> dict[str, LabelSet]:
        if isinstance(st, ast.Assign):
            for target in st.targets:
                env = self.assign(target, st.value, env, st)
        elif isinstance(st, ast.AnnAssign) and st.value is not None:
            env = self.assign(st.target, st.value, env, st)
        elif isinstance(st, ast.AugAssign):
            cur = self.ev(st.target, env)
            value = union(cur, self.ev(st.value, env))
            env = self.bind(st.target, value, env, st)
        elif isinstance(st, ast.Expr):
            self.ev(st.value, env)
        elif isinstance(st, ast.Return) and st.value is not None:
            ret = with_step(self.ev(st.value, env), self.loc(st, "반환"))
            self.out.ret = union(self.out.ret, ret)
        elif isinstance(st, ast.If):
            self.ev(st.test, env)
            env = self.merge(self.block(st.body, dict(env)), self.block(st.orelse, dict(env)))
        elif isinstance(st, (ast.For, ast.AsyncFor)):
            it = self.ev(st.iter, env)
            loop = env
            for _ in range(2):
                inner = self.bind_iter(st.target, st.iter, it, dict(loop))
                loop = self.merge(loop, self.block(st.body, inner))
            env = self.block(st.orelse, loop)
        elif isinstance(st, ast.While):
            loop = env
            for _ in range(2):
                self.ev(st.test, loop)
                loop = self.merge(loop, self.block(st.body, dict(loop)))
            env = self.block(st.orelse, loop)
        elif isinstance(st, (ast.With, ast.AsyncWith)):
            for item in st.items:
                value = self.ev(item.context_expr, env)
                if item.optional_vars is not None:
                    env = self.bind(item.optional_vars, value, env, st)
            env = self.block(st.body, env)
        elif isinstance(st, ast.Try) or (hasattr(ast, "TryStar") and isinstance(st, ast.TryStar)):
            body = self.block(st.body, dict(env))
            branches = [self.block(h.body, self.merge(env, body)) for h in st.handlers]
            env = self.merge(body, *branches, self.block(st.orelse, dict(body)))
            env = self.block(st.finalbody, env)
        elif isinstance(st, ast.Match):
            self.ev(st.subject, env)
            env = self.merge(env, *(self.block(c.body, dict(env)) for c in st.cases))
        return env

    def assign(self, target: ast.AST, value_node: ast.AST, env: dict[str, LabelSet], st: ast.stmt):
        if (isinstance(target, (ast.Tuple, ast.List)) and isinstance(value_node, (ast.Tuple, ast.List))
                and len(target.elts) == len(value_node.elts)):
            for t, v in zip(target.elts, value_node.elts):
                env = self.assign(t, v, env, st)
            return env
        value = self.ev(value_node, env)
        fields = None
        if isinstance(value_node, ast.Dict):
            fields = {k: self.ev(v, env) for key, v in zip(value_node.keys, value_node.values)
                      if (k := const_str(key)) is not None}
        return self.bind(target, value, env, st, fields)

    def bind(self, target: ast.AST, value: LabelSet, env: dict[str, LabelSet], st: ast.stmt,
             fields: dict[str, LabelSet] | None = None) -> dict[str, LabelSet]:
        if isinstance(target, ast.Name):
            env = dict(env)
            env[target.id] = with_step(value, self.loc(st, f"대입 {target.id}"))
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                env = self.bind(elt, value, env, st)
        elif isinstance(target, ast.Starred):
            env = self.bind(target.value, value, env, st)
        elif isinstance(target, ast.Subscript):
            key = const_str(target.slice)
            key_labels = self.ev(target.slice, env)
            env = self.store_into(target.value, key, value, key_labels, env, st, fields)
        elif isinstance(target, ast.Attribute):
            env = self.store_into(target.value, target.attr, value, {}, env, st, None)
        return env

    def global_store(self, node: ast.AST, env: dict[str, LabelSet]) -> str | None:
        if isinstance(node, ast.Name) and node.id in env:
            return None
        q = self.p.resolve_expr(self.mod, node)
        return q if q in self.p.stores else None

    def store_into(self, base: ast.AST, key: str | None, value: LabelSet, key_labels: LabelSet,
                   env: dict[str, LabelSet], st: ast.stmt, fields: dict[str, LabelSet] | None):
        loc = self.loc(st)
        store = self.global_store(base, env)
        if store:
            if fields is not None:
                for name, labels in fields.items():
                    self.write(store, name, labels, loc)
            else:
                self.write(store, key, value, loc)
            if key_labels:
                self.write(store, KEY_FIELD, key_labels, loc)
            return env
        base_labels = self.ev(base, env)
        for label in base_labels.values():
            if isinstance(label.origin, Ref):
                field_name = label.origin.field_name or key
                self.write(label.origin.store, field_name, value, loc)
        if isinstance(base, ast.Name):
            env = dict(env)
            env[base.id] = union(env.get(base.id, {}), with_step(value, loc))
        return env

    def bind_iter(self, target: ast.AST, iter_node: ast.AST, labels: LabelSet,
                  env: dict[str, LabelSet]) -> dict[str, LabelSet]:
        loc = self.loc(target, "반복 변수")
        parts = self.element_parts(iter_node, labels)
        if isinstance(target, (ast.Tuple, ast.List)) and parts and len(parts) == len(target.elts):
            for t, part in zip(target.elts, parts):
                env = self.bind_loop_var(t, with_step(part, loc), env)
            return env
        return self.bind_loop_var(target, with_step(labels, loc), env)

    def bind_loop_var(self, target: ast.AST, value: LabelSet, env: dict[str, LabelSet]):
        if isinstance(target, ast.Name):
            env = dict(env)
            env[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                env = self.bind_loop_var(elt, value, env)
        return env

    def element_parts(self, node: ast.AST, labels: LabelSet) -> list[LabelSet] | None:
        while (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id in PASSTHROUGH_WRAPPERS and len(node.args) == 1):
            node = node.args[0]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "items":
            return [key_refs(labels), labels]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "enumerate":
            return [{}, labels]
        return None

    # ----- 식 -----

    def ev(self, node: ast.AST | None, env: dict[str, LabelSet]) -> LabelSet:
        if node is None:
            return {}
        mark = self.p.marks.get(self.loc(node).key())
        if mark is not None and mark.role == "source":
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    self.ev(child, env)
            src = Src(mark.kind, mark.field_name, self.loc(node), self.info.qname)
            return lset(Label(src, (self.loc(node, f"입력 {mark.kind}.{mark.field_name or '*'}"),)))
        if isinstance(node, ast.Constant):
            return {}
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            store = self.global_store(node, env)
            if store:
                return lset(Label(Ref(store, None), (self.loc(node, f"읽기 {store}"),)))
            return {}
        if isinstance(node, ast.Attribute):
            store = self.global_store(node, env)
            if store:
                return lset(Label(Ref(store, None), (self.loc(node, f"읽기 {store}"),)))
            return narrow(self.ev(node.value, env), None)
        if isinstance(node, ast.Subscript):
            base = self.ev(node.value, env)
            self.ev(node.slice, env)
            return narrow(base, const_str(node.slice))
        if isinstance(node, ast.Call):
            return self.call(node, env)
        if isinstance(node, ast.JoinedStr):
            return self.fstring(node, env)
        if isinstance(node, ast.BinOp):
            return self.binop(node, env)
        if isinstance(node, ast.Compare):
            for child in (node.left, *node.comparators):
                self.ev(child, env)
            return {}
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            return self.comprehension(node, env)
        if isinstance(node, ast.Dict):
            for k in node.keys:
                self.ev(k, env)
            return union(*(self.ev(v, env) for v in node.values))
        if isinstance(node, ast.NamedExpr):
            value = self.ev(node.value, env)
            env[node.target.id] = value
            return value
        if isinstance(node, ast.Lambda):
            return {}
        return union(*(self.ev(child, env) for child in ast.iter_child_nodes(node) if isinstance(child, ast.expr)))

    def comprehension(self, node: ast.AST, env: dict[str, LabelSet]) -> LabelSet:
        inner = dict(env)
        for gen in node.generators:
            it = self.ev(gen.iter, inner)
            inner = self.bind_iter(gen.target, gen.iter, it, inner)
            for cond in gen.ifs:
                self.ev(cond, inner)
        if isinstance(node, ast.DictComp):
            return union(self.ev(node.key, inner), self.ev(node.value, inner))
        return self.ev(node.elt, inner)

    # ----- HTML 조립 싱크 -----

    def html_parts(self, parts: list[tuple[str, ast.AST | None]], env: dict[str, LabelSet],
                   whole: ast.AST) -> LabelSet:
        """(문자열 조각, 값 노드) 목록을 보고 HTML이면 값마다 출력 맥락을 붙여 싱크로 기록한다."""
        literal = "".join(text for text, _ in parts)
        is_html = bool(HTML_RE.search(literal))
        prefix = ""
        result: LabelSet = {}
        for text, value_node in parts:
            prefix += text
            if value_node is None:
                continue
            labels = self.ev(value_node, env)
            if labels and is_html:
                sink = self.loc(value_node, "출력")
                self.hit(html_context(prefix), sink, AST_HTML_RULE, labels)
                labels = with_step(labels, sink)  # 바깥 HTML에 다시 들어가도 같은 경로로 묶는다
            result = union(result, labels)
            prefix += "X"
        return result

    def fstring(self, node: ast.JoinedStr, env: dict[str, LabelSet]) -> LabelSet:
        parts: list[tuple[str, ast.AST | None]] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append((str(value.value), None))
            elif isinstance(value, ast.FormattedValue):
                parts.append(("", value.value))
        return self.html_parts(parts, env, node)

    def binop(self, node: ast.BinOp, env: dict[str, LabelSet]) -> LabelSet:
        if isinstance(node.op, ast.Add):
            flat: list[ast.AST] = []

            def walk(n: ast.AST) -> None:
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
                    walk(n.left)
                    walk(n.right)
                else:
                    flat.append(n)

            walk(node)
            if any(const_str(n) is not None for n in flat):
                parts = [(const_str(n), None) if const_str(n) is not None else ("", n) for n in flat]
                return self.html_parts(parts, env, node)
        if isinstance(node.op, ast.Mod) and (template := self.template_text(node.left)) is not None:
            values = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
            chunks = re.split(r"%(?:\([^)]*\))?[-#0 +]*\d*(?:\.\d+)?[sdifr]", template)
            parts: list[tuple[str, ast.AST | None]] = []
            for i, chunk in enumerate(chunks):
                parts.append((chunk, None))
                if i < len(chunks) - 1:
                    parts.append(("", values[min(i, len(values) - 1)]))
            return self.html_parts(parts, env, node)
        return union(self.ev(node.left, env), self.ev(node.right, env))

    def template_text(self, node: ast.AST) -> str | None:
        if (text := const_str(node)) is not None:
            return text
        if isinstance(node, (ast.Name, ast.Attribute)):
            q = self.p.resolve_expr(self.mod, node)
            if q in self.p.consts:
                return self.p.consts[q]
        return None

    def format_call(self, node: ast.Call, template: str, env: dict[str, LabelSet]) -> LabelSet:
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        auto = 0
        parts: list[tuple[str, ast.AST | None]] = []
        try:
            pieces = list(string.Formatter().parse(template))
        except ValueError:
            return union(*(self.ev(a, env) for a in node.args), *(self.ev(v, env) for v in kw.values()))
        for literal, field_name, _spec, _conv in pieces:
            parts.append((literal, None))
            if field_name is None:
                continue
            head = re.split(r"[.\[]", field_name, maxsplit=1)[0]
            if head == "":
                value = node.args[auto] if auto < len(node.args) else None
                auto += 1
            elif head.isdigit():
                value = node.args[int(head)] if int(head) < len(node.args) else None
            else:
                value = kw.get(head)
            parts.append(("", value))
        return self.html_parts(parts, env, node)

    # ----- 호출 -----

    def call(self, node: ast.Call, env: dict[str, LabelSet]) -> LabelSet:
        func = node.func
        mark = self.p.marks.get(self.loc(node).key())
        arg_labels = [self.ev(a, env) for a in node.args]
        kw_labels = {k.arg: self.ev(k.value, env) for k in node.keywords}
        all_args = union(*arg_labels, *kw_labels.values())
        if mark is not None and mark.role == "sink":
            danger = self.sink_arg(node, mark, arg_labels, env)
            if danger:
                self.hit(mark.kind or mark.meta.get("context", ""), self.loc(node, "싱크"), mark.rule_id, danger)

        in_env = isinstance(func, ast.Name) and func.id in env
        q = None if in_env else self.p.resolve_expr(self.mod, func)
        policy = self.p.policy

        if q is not None and q in policy.kills:
            return {}
        if q is not None and (q in policy.sanitizers or (q in self.p.funcs and policy.is_filter_name(q))):
            internal = q in self.p.funcs
            digest = code_hash(self.p.funcs[q].source) if internal else f"name:{q}"
            target = arg_labels[0] if arg_labels else all_args
            return with_san(target, San(q, digest, self.loc(node, f"필터 {q}"), internal))
        if q is not None and q in self.p.funcs:
            return self.instantiate(q, node, arg_labels, kw_labels)
        if q == "re.sub" and len(arg_labels) >= 3:
            segment = ast.get_source_segment(self.mod.source, node) or "re.sub"
            return with_san(arg_labels[2], San("re.sub", code_hash(segment), self.loc(node, "필터 re.sub")))
        if isinstance(func, ast.Attribute) and not self._is_module_attr(func, env):
            return self.method(node, func, arg_labels, all_args, env)
        if isinstance(func, ast.Name) and func.id in PASSTHROUGH_WRAPPERS | {"str", "repr", "format"}:
            return all_args
        return all_args

    def _is_module_attr(self, func: ast.Attribute, env: dict[str, LabelSet]) -> bool:
        """os.system처럼 모듈 함수 호출인지(값의 메서드 호출이 아닌지)."""
        base = func.value
        while isinstance(base, ast.Attribute):
            base = base.value
        if not isinstance(base, ast.Name) or base.id in env:
            return False
        return base.id in self.mod.imports and self.global_store(func.value, env) is None and (
            self.p.resolve_expr(self.mod, func.value) not in self.p.consts)

    def sink_arg(self, node: ast.Call, mark: Mark, arg_labels: list[LabelSet],
                 env: dict[str, LabelSet]) -> LabelSet:
        if mark.arg is not None:
            for i, a in enumerate(node.args):
                if self.loc(a).key() == mark.arg.key():
                    return arg_labels[i]
            for child in ast.walk(node):
                if isinstance(child, ast.expr) and self.loc(child).key() == mark.arg.key():
                    return self.ev(child, env)
        return arg_labels[0] if arg_labels else {}

    def method(self, node: ast.Call, func: ast.Attribute, arg_labels: list[LabelSet],
               all_args: LabelSet, env: dict[str, LabelSet]) -> LabelSet:
        name = func.attr
        if name == "format" and (template := self.template_text(func.value)) is not None:
            return self.format_call(node, template, env)
        if name in ("execute", "executemany") and node.args and (sql := self.template_text(node.args[0])):
            return self.sql(node, sql, env)
        base = self.ev(func.value, env)
        if name in MUTATORS:
            loc = self.loc(node)
            for label in base.values():
                if isinstance(label.origin, Ref):
                    self.write(label.origin.store, label.origin.field_name, all_args, loc)
            store = self.global_store(func.value, env)
            if store:
                self.write(store, None, all_args, loc)
            if isinstance(func.value, ast.Name) and func.value.id in env:
                env[func.value.id] = union(env[func.value.id], with_step(all_args, loc))
            return {}
        if name in ("get", "pop"):
            default = arg_labels[1] if len(arg_labels) > 1 else {}
            return union(narrow(base, const_str(node.args[0]) if node.args else None), default)
        if name == "keys":
            return key_refs(base)
        if name in ("items", "values", "copy"):
            return base
        if name == "replace":
            segment = ast.get_source_segment(self.mod.source, node) or "replace"
            replaced = with_san(base, San("str.replace", code_hash(segment), self.loc(node, "필터 str.replace")))
            return union(replaced, arg_labels[1] if len(arg_labels) > 1 else {})
        if name == "join":
            return all_args
        return union(base, all_args)

    def sql(self, node: ast.Call, sql: str, env: dict[str, LabelSet]) -> LabelSet:
        loc = self.loc(node)
        params = node.args[1] if len(node.args) > 1 else None
        values = params.elts if isinstance(params, (ast.Tuple, ast.List)) else []
        named = ({const_str(k): v for k, v in zip(params.keys, params.values)}
                 if isinstance(params, ast.Dict) else {})

        def param_labels(placeholder: str, index: int) -> LabelSet:
            placeholder = placeholder.strip()
            if placeholder.startswith(":") or placeholder.startswith("%("):
                key = placeholder.lstrip(":%(").rstrip(")s")
                return self.ev(named.get(key), env)
            return self.ev(values[index], env) if index < len(values) else {}

        if m := INSERT_RE.search(sql):
            table, cols, holders = m.group(1), m.group(2), m.group(3)
            cols = [c.strip().strip("\"`[]") for c in cols.split(",")]
            holders = holders.split(",")
            pos = 0
            for col, holder in zip(cols, holders):
                labels = param_labels(holder, pos)
                if holder.strip() in ("?", "%s"):
                    pos += 1
                if labels:
                    self.write(f"db.{table}", col, labels, loc)
            return {}
        if m := UPDATE_RE.search(sql):
            table, assigns = m.group(1), m.group(2)
            pos = 0
            for part in assigns.split(","):
                if "=" not in part:
                    continue
                col, holder = (x.strip() for x in part.split("=", 1))
                labels = param_labels(holder, pos)
                if holder in ("?", "%s"):
                    pos += 1
                if labels:
                    self.write(f"db.{table}", col.strip("\"`[]"), labels, loc)
            return {}
        if m := SELECT_RE.search(sql):
            store = f"db.{m.group(1)}"
            return lset(Label(Ref(store, None), (self.loc(node, f"읽기 {store}"),)))
        return {}

    def instantiate(self, q: str, node: ast.Call, arg_labels: list[LabelSet],
                    kw_labels: dict[str | None, LabelSet]) -> LabelSet:
        self.out.callees.add(q)
        summary = self.p.summary(q)
        params = self.p.funcs[q].params
        mapping: dict[int, LabelSet] = {i: labels for i, labels in enumerate(arg_labels)}
        for name, labels in kw_labels.items():
            if name in params:
                mapping[params.index(name)] = labels

        def subst(label: Label) -> list[Label]:
            if not isinstance(label.origin, Par):
                return [label]
            return [Label(a.origin, _join(a.steps, label.steps), (*a.sans, *label.sans))
                    for a in mapping.get(label.origin.index, {}).values()]

        ret: LabelSet = {}
        for label in summary.ret.values():
            for new in subst(label):
                _put(ret, new)
        for h in summary.hits.values():
            if isinstance(h.label.origin, Par):
                for new in subst(h.label):
                    key = (h.context, h.sink.key(), new.ident())
                    old = self.out.hits.get(key)
                    if old is None or len(new.steps) < len(old.label.steps):
                        self.out.hits[key] = Hit(h.context, h.sink, h.sink_rule, new, self.info.qname)
        for w in summary.writes.values():
            if isinstance(w.label.origin, Par):
                for new in subst(w.label):
                    key = (w.store, w.field_name, new.ident())
                    old = self.out.writes.get(key)
                    if old is None or len(new.steps) < len(old.label.steps):
                        self.out.writes[key] = Write(w.store, w.field_name, new, w.loc, self.info.qname)
        return with_step(ret, self.loc(node, f"호출 {q.rsplit('.', 1)[-1]}"))
