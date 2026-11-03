"""안전 목록·경로를 끊는 호출·필터 이름 패턴."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

SAFE_LIST = Path(__file__).parent / "rules" / "safe_sanitizers.yaml"


@dataclass(frozen=True)
class Policy:
    contexts: dict[str, frozenset[str]]
    kills: frozenset[str]
    filter_pattern: re.Pattern[str]

    @property
    def sanitizers(self) -> frozenset[str]:
        names: set[str] = set()
        for items in self.contexts.values():
            names |= items
        return frozenset(names)

    def is_filter_name(self, qualified: str) -> bool:
        return qualified in self.sanitizers or bool(self.filter_pattern.search(qualified.rsplit(".", 1)[-1]))

    def safe_for(self, name: str, context: str) -> bool:
        return name in self.contexts.get(context, frozenset())


def load_policy(path: Path = SAFE_LIST) -> Policy:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    contexts = {k: frozenset(v or []) for k, v in (data.get("contexts") or {}).items()}
    return Policy(contexts=contexts, kills=frozenset(data.get("kills") or []),
                  filter_pattern=re.compile(data.get("filter_name_pattern") or r"(?!x)x"))
