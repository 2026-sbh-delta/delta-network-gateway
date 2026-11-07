#!/usr/bin/env bash
# Never invokes a real plan/apply/destroy. All test run blocks use command=plan
# and mock_provider aws. The S3 backend is not initialized or migrated.
set -euo pipefail

module_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
terraform_bin="${TERRAFORM_BIN:-terraform}"
validation_dir="$(mktemp -d "${TMPDIR:-/tmp}/delta-workload-validation.XXXXXX")"
cleanup() {
  if [[ "${DELTA_KEEP_VALIDATION:-0}" != "1" ]]; then
    rm -rf "$validation_dir"
  fi
}
trap cleanup EXIT

python3 -m unittest discover -s "$module_dir/tests" -p 'test_*.py'
python3 -m unittest discover -s "$module_dir/scripts" -p 'test_*.py'
"$terraform_bin" -chdir="$module_dir" fmt -check -recursive
cp "$module_dir/"*.tf "$validation_dir/"
cp "$module_dir/.terraform.lock.hcl" "$validation_dir/"
cp "$module_dir/terraform.tfvars.example" "$validation_dir/"
cp -R "$module_dir/tests" "$validation_dir/"
cp -R "$module_dir/modules" "$validation_dir/"

init_args=(-backend=false -input=false -lockfile=readonly -no-color)
if [[ -d "$module_dir/.terraform/providers" ]]; then
  init_args+=("-plugin-dir=$module_dir/.terraform/providers")
elif [[ -d "$module_dir/../network-security/.terraform/providers" ]]; then
  init_args+=("-plugin-dir=$module_dir/../network-security/.terraform/providers")
fi
"$terraform_bin" -chdir="$validation_dir" init "${init_args[@]}"
"$terraform_bin" -chdir="$validation_dir" validate -no-color

test_exit=0
test_args=(-var-file=terraform.tfvars.example -json)
if [[ "${DELTA_VERBOSE_PLAN:-0}" == "1" ]]; then
  test_args+=(-verbose)
fi
"$terraform_bin" -chdir="$validation_dir" test "${test_args[@]}" | python3 -c '
import json, sys
for line in sys.stdin:
    event = json.loads(line)
    if event.get("type") == "test_plan":
        event["test_plan"].pop("provider_schemas", None)
    print(json.dumps(event, ensure_ascii=False))
' > "$validation_dir/test-events.jsonl" || test_exit=$?
python3 - "$validation_dir/test-events.jsonl" "$test_exit" <<'PY'
import json, sys
events = [json.loads(line) for line in open(sys.argv[1])]
passed = {event.get("@testrun") for event in events if event.get("type") == "test_run" and event.get("test_run", {}).get("status") == "pass"}
for event in events:
    if event.get("type") == "diagnostic" and event.get("@testrun") in passed:
        continue
    if event.get("type") == "test_summary" or (
        event.get("type") == "test_run" and event.get("test_run", {}).get("progress") == "complete"
    ) or (sys.argv[2] != "0" and event.get("type") == "diagnostic"):
        print(event.get("@message", event["type"]))
        if event.get("diagnostic", {}).get("severity") == "error":
            print(event["diagnostic"].get("detail", ""))
PY
if [[ "${DELTA_KEEP_VALIDATION:-0}" == "1" ]]; then
  printf 'Validation artifacts: %s\n' "$validation_dir"
fi
if [[ "$test_exit" != "0" ]]; then
  exit "$test_exit"
fi
child_dir="$validation_dir/modules/sample-app"
cp "$module_dir/.terraform.lock.hcl" "$child_dir/"
"$terraform_bin" -chdir="$child_dir" init "${init_args[@]}"
"$terraform_bin" -chdir="$child_dir" test -no-color
