"""점검 단계 사이에 주고받는 값."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Role = Literal["source", "sink", "secret"]


@dataclass(frozen=True)
class Loc:
    """코드 위치. line은 1부터, col은 1부터 세는 UTF-8 바이트 위치(Opengrep과 같다)."""

    path: str
    line: int
    col: int
    end_line: int
    end_col: int
    note: str = ""

    def key(self) -> tuple[str, int, int, int, int]:
        return (self.path, self.line, self.col, self.end_line, self.end_col)


@dataclass(frozen=True)
class Mark:
    """Opengrep이 표시한 위치 하나."""

    rule_id: str
    role: Role
    loc: Loc
    meta: dict = field(default_factory=dict, hash=False, compare=False)
    arg: Loc | None = None  # 싱크의 위험한 인자 위치
    kind: str = ""
    field_name: str | None = None
    message: str = ""


# ---------- 데이터 흐름 라벨 ----------


@dataclass(frozen=True)
class Src:
    """요청 입력이 처음 들어온 곳."""

    kind: str
    field_name: str | None
    loc: Loc
    func: str


@dataclass(frozen=True)
class Par:
    """함수 매개변수. 호출하는 쪽에서 실제 값으로 바뀐다."""

    index: int
    name: str


@dataclass(frozen=True)
class Ref:
    """전역 저장소(dict·list·DB 테이블)에서 읽은 값. field가 None이면 레코드 전체."""

    store: str
    field_name: str | None


KEY_FIELD = "__key__"

Origin = Src | Par | Ref


@dataclass(frozen=True)
class San:
    """경로 위에서 값을 거친 필터."""

    name: str
    code_hash: str
    loc: Loc
    internal: bool = False


@dataclass(frozen=True)
class Label:
    origin: Origin
    steps: tuple[Loc, ...] = ()
    sans: tuple[San, ...] = ()

    def ident(self) -> tuple:
        return (self.origin, self.sans)


@dataclass(frozen=True)
class Hit:
    """싱크에 닿은 값 하나."""

    context: str
    sink: Loc
    sink_rule: str
    label: Label
    func: str  # 이 hit가 확정된(구체 출처가 된) 함수


@dataclass(frozen=True)
class Write:
    """전역 저장소 쓰기."""

    store: str
    field_name: str | None
    label: Label
    loc: Loc
    func: str


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    func: str

    def text(self) -> str:
        return f"{self.method} {self.path}"


@dataclass
class Flow:
    """소스에서 싱크까지 이어진 경로 하나. 분류 전 상태."""

    vuln: str  # xss | sqli | cmdi | ssti
    context: str
    source: Src
    sink: Loc
    sink_rule: str
    steps: tuple[Loc, ...]
    sans: tuple[San, ...]
    entry: tuple[Route, ...]
    render: tuple[Route, ...]
    stores: tuple[str, ...] = ()

    @property
    def stored(self) -> bool:
        return bool(self.stores)

    def ident(self) -> tuple:
        return (self.source, self.context, self.sink.key(), tuple(s.code_hash for s in self.sans))


@dataclass
class Secret:
    rule_id: str
    kind: str
    loc: Loc
    preview: str  # 앞부분만 남기고 가린 값


@dataclass
class Judgment:
    by: Literal["rule", "cache", "llm", "pending"]
    verdict: Literal["candidate", "vulnerable", "safe", "uncertain"]
    reason: str
    cache_key: str | None = None
    payload: str | None = None
    cited: tuple[Loc, ...] = ()
