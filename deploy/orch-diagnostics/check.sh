#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
python_bin="${DIAGNOSTICS_PYTHON:-.venv/bin/python}"
"$python_bin" -m pytest -q -c services/delta-ai/pyproject.toml services/delta-ai/tests
"$python_bin" -m pytest -q -c services/delta-pipeline/pyproject.toml services/delta-pipeline/tests
"$python_bin" -m pytest -q services/delta-orchestrator/tests
"$python_bin" -m compileall -q services/delta-ai/delta_ai services/delta-pipeline/delta_pipeline services/delta-orchestrator/delta_orchestrator deploy/onprem-runtime deploy/orch-diagnostics deploy/ai-readiness
node --check services/delta-orchestrator/delta_orchestrator/static/app.js
node --check services/delta-orchestrator/delta_orchestrator/static/mapping.js
node --test services/delta-orchestrator/tests/mapping-ui.test.cjs
git diff --check
