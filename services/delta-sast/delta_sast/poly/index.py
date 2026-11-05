"""두 엔진(Python 해석기, 다언어 엔진)을 SARIF·LLM 판단에 같은 모양으로 보이게 하는 색인.

SARIF와 판단 단계가 쓰는 것은 세 가지뿐이다: 파일 원문(modules), 줄을 감싸는 함수(enclosing), 내부 필터 함수(funcs).
다언어 엔진의 함수는 Opengrep이 찾은 이름 있는 함수 구간이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .engine import Facts


@dataclass(frozen=True)
class Node:
    lineno: int
    end_lineno: int
    decorator_list: tuple = ()


@dataclass(frozen=True)
class Module:
    path: str
    source: str = field(compare=False)


@dataclass(frozen=True)
class Func:
    qname: str
    module: Module
    node: Node

    @property
    def source(self) -> str:
        lines = self.module.source.splitlines()
        return "\n".join(lines[self.node.lineno - 1:self.node.end_lineno])


class Workspace:
    """Python Project(있으면)와 다언어 사실을 합친 색인. Project와 같은 이름의 속성·메서드를 둔다."""

    def __init__(self, project, facts: Facts | None):
        self.project = project
        self.facts = facts
        self.root = project.root
        self.funcs = project.funcs
        self.parse_errors = list(project.parse_errors) + list(facts.errors if facts else [])
        self.modules = dict(project.modules)
        self._poly: dict[str, Module] = {}
        if facts:
            for path, source in facts.files.items():
                module = Module(path, source)
                self._poly[path] = module
                self.modules.setdefault(f"poly:{path}", module)

    def enclosing(self, path: str, line: int):
        if path not in self._poly:
            return self.project.enclosing(path, line)
        best = None
        for fn in self.facts.funcs:
            if fn.path == path and fn.line <= line <= fn.end_line and (best is None or fn.end - fn.start < best.end - best.start):
                best = fn
        if best is None:
            return None
        return Func(f"{path.rsplit('/', 1)[-1]}:{best.name}", self._poly[path], Node(best.line, best.end_line))

    def __getattr__(self, name):
        return getattr(self.project, name)
