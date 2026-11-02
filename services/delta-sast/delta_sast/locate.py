"""① 목록: Opengrep으로 소스·싱크·비밀값 위치를 표시한다."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .model import Loc, Mark, Secret

RULES = Path(__file__).parent / "rules" / "locate.yaml"
# Opengrep 기본 무시 목록(tests, test 등)과 맞춘다. AST 쪽도 같은 목록을 쓴다.
EXCLUDES = (".venv", "venv", "node_modules", ".git", "__pycache__", "site-packages", "tests", "test")


class LocateError(RuntimeError):
    pass


def opengrep_binary(name: str = "opengrep") -> str:
    found = shutil.which(name)
    if not found:
        raise LocateError(f"{name} 실행 파일을 찾지 못했다. PATH를 확인한다.")
    return found


def run_opengrep(root: Path, rules: Path = RULES, binary: str = "opengrep", timeout: int = 60) -> dict:
    cmd = [opengrep_binary(binary), "scan", "--config", str(rules), "--json", "--quiet",
           "--disable-version-check", "--no-git-ignore"]
    for pattern in EXCLUDES:
        cmd.append(f"--exclude={pattern}")
    cmd.append(str(root))
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise LocateError(f"opengrep이 {timeout}초 안에 끝나지 않았다") from exc
    if done.returncode not in (0, 1):
        raise LocateError(f"opengrep 실패(exit {done.returncode}): {done.stderr.strip()[:500]}")
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise LocateError("opengrep JSON 출력을 읽지 못했다") from exc


def _loc(path: str, start: dict, end: dict) -> Loc:
    return Loc(path, start["line"], start["col"], end["line"], end["col"])


def _string_value(text: str | None) -> str | None:
    if not text:
        return None
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return None


def redact(value: str) -> str:
    keep = value[:4] if len(value) > 8 else ""
    return f"{keep}{'*' * 8}"


def parse(report: dict, root: Path) -> tuple[list[Mark], list[Secret]]:
    root = root.resolve()
    marks: list[Mark] = []
    secrets: list[Secret] = []
    for item in report.get("results", []):
        path = Path(item["path"]).resolve()
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = item["path"]
        extra = item.get("extra", {})
        meta = (extra.get("metadata") or {}).get("delta") or {}
        metavars = extra.get("metavars") or {}
        rule_id = item["check_id"].rsplit(".", 1)[-1]
        loc = _loc(rel, item["start"], item["end"])
        role = meta.get("role")
        if role == "secret":
            value = (metavars.get("$VAL") or {}).get("abstract_content") or extra.get("lines", "")
            secrets.append(Secret(rule_id, meta.get("kind", ""), loc, redact(value.strip())))
            continue
        if role not in ("source", "sink"):
            continue
        arg = None
        if role == "sink" and meta.get("arg_metavar") in metavars:
            mv = metavars[meta["arg_metavar"]]
            arg = _loc(rel, mv["start"], mv["end"])
        kind = meta.get("kind", "")
        if meta.get("kind_metavar") in metavars:
            kind = metavars[meta["kind_metavar"]].get("abstract_content", kind)
        field_name = None
        if meta.get("field_metavar") in metavars:
            field_name = _string_value(metavars[meta["field_metavar"]].get("abstract_content"))
        marks.append(Mark(rule_id, role, loc, meta=meta, arg=arg, kind=kind or meta.get("context", ""),
                          field_name=field_name, message=extra.get("message", "")))
    marks.sort(key=lambda m: m.loc.key())
    secrets.sort(key=lambda s: s.loc.key())
    return marks, secrets


def locate(root: Path, rules: Path = RULES, binary: str = "opengrep") -> tuple[list[Mark], list[Secret]]:
    return parse(run_opengrep(root, rules, binary), root)
