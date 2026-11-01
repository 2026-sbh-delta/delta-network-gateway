"""Missing-feature adapter only; never replace an existing implementation."""

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "src", ROOT):
    sys.path.insert(0, str(candidate))


def required(module_name, attribute):
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        # Missing dependencies INSIDE production modules must remain import errors.
        if exc.name not in {"dast_agent", module_name}:
            raise
        module = None
    if module is not None and hasattr(module, attribute):
        return getattr(module, attribute)

    def missing(*args, **kwargs):
        raise NotImplementedError(f"MISSING_FEATURE: {module_name}.{attribute}")

    return missing


normalize_findings = required("dast_agent.ingestion", "normalize_findings")
validate_origin = required("dast_agent.scope", "validate_origin")
validate_agent_plan = required("dast_agent.planner", "validate_agent_plan")
run_check = required("dast_agent.runner", "run_check")
