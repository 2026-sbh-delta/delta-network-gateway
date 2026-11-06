"""실패 원인을 단계(stage)·원인 코드(code)로만 남긴다. 공급자 응답·명령 출력·비밀은 담지 않는다."""

import re
from contextlib import contextmanager

CODE_RE = re.compile(r"[a-z0-9_]{1,64}")
current = {"stage": "request"}


class DeployError(RuntimeError):
    def __init__(self, stage, code):
        if not CODE_RE.fullmatch(stage) or not CODE_RE.fullmatch(code):
            stage, code = "unknown", "unexpected"
        super().__init__(f"{stage}/{code}")
        self.stage, self.code = stage, code


class CommandError(RuntimeError):
    """신뢰된 argv의 도구·하위 명령 이름만 원인 코드로 쓴다(stdout/stderr는 버린다)."""

    def __init__(self, args):
        tool = re.sub(r"[^a-z0-9]", "", str(args[0]).rsplit("/", 1)[-1].lower())[:16] or "command"
        sub = re.sub(r"[^a-z0-9]", "", str(args[1]).lower())[:16] if len(args) > 1 else ""
        self.code = f"{tool}_{sub}_failed" if sub and not str(args[1]).startswith("-") else f"{tool}_failed"
        super().__init__("Trusted deployment command failed: " + tool)


@contextmanager
def step(stage, code):
    """블록 안의 예외를 stage/code로 바꾼다. 하위 단계에서 이미 정한 DeployError는 그대로 올린다."""
    previous = current["stage"]
    current["stage"] = stage
    try:
        yield
    except DeployError:
        raise
    except Exception as exc:
        specific = getattr(exc, "code", None)
        raise DeployError(stage, specific if isinstance(specific, str) and CODE_RE.fullmatch(specific) else code) from exc
    current["stage"] = previous


def fail(stage, code):
    raise DeployError(stage, code)
