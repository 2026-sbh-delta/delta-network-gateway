"""Install a bounded hook while preserving the current runtime's other code."""

import ast
import hashlib
import sys
from importlib.util import find_spec
from pathlib import Path

mode, expected = sys.argv[1:]
module = "delta_platform.api.app" if mode == "platform" else "delta_orchestrator.app"
path = Path(find_spec(module).origin)
source = path.read_text()
assert hashlib.sha256(source.encode()).hexdigest() == expected, (
    "runtime API base changed"
)
tree = ast.parse(source)
create = next(
    n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "create_app"
)
if mode == "platform":
    anchor = [
        n
        for n in create.body
        if isinstance(n, ast.Expr)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Attribute)
        and n.value.func.attr == "include_router"
        and any(isinstance(a, ast.Name) and a.id == "protected" for a in n.value.args)
    ]
    hook = "    from delta_platform.adapters.workflow_diff import install as install_workload_diff\n    install_workload_diff(protected, workflows, settings)\n"
else:
    anchor = [
        n
        for n in create.body
        if isinstance(n, ast.Return)
        and isinstance(n.value, ast.Name)
        and n.value.id == "app"
    ]
    hook = "    from .platform_diff import router as platform_diff_router\n    app.include_router(platform_diff_router())\n"
assert len(anchor) == 1, "single reviewed install point required"
lines = source.splitlines(keepends=True)
lines.insert(anchor[0].lineno - 1, hook)
updated = "".join(lines)
compile(updated, str(path), "exec")
path.write_text(updated)
print("API hook installed; existing source retained")
