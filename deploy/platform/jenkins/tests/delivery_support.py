import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_controller(name, *, optional=False):
    path = CONTROLLER / (name + ".py")
    if optional and not path.exists():
        pytest.skip(f"신규 구현 대기: {path.name}; 기존 staging/JCasC RED와 구분")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(CONTROLLER))
    try:
        # production 경로에 __pycache__도 작성하지 않는다.
        exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    finally:
        sys.path.pop(0)
    return module
