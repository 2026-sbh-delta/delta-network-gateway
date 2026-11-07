"""Export the public diff contract without bootstrapping AWS credentials or the API."""

import json
from pathlib import Path

from delta_diff.contract import DiffRequest, DiffResult

out = Path(__file__).resolve().parent.parent / "schema"
out.mkdir(exist_ok=True)
for name, model in {"diff-request": DiffRequest, "diff-result": DiffResult}.items():
    (out / f"{name}.schema.json").write_text(
        json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n"
    )
