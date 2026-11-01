"""계약 JSON Schema 를 schema/ 에 쓴다. 다른 언어로 붙는 팀원이 바로 쓸 수 있게 한다."""

from __future__ import annotations

import json
from pathlib import Path

from delta_ai.contract import AssessmentRequest, AssessmentResult

OUT = Path(__file__).resolve().parent.parent / "schema"

for name, model in {"assessment-request": AssessmentRequest, "assessment-result": AssessmentResult}.items():
    (OUT / f"{name}.schema.json").write_text(
        json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
