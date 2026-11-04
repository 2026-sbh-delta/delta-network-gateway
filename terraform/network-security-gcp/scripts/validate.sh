#!/usr/bin/env bash
set -euo pipefail
module_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validation_dir="$(mktemp -d "${TMPDIR:-/tmp}/delta-gcp-validation.XXXXXX")"
cleanup() {
  if [[ "${DELTA_KEEP_VALIDATION:-0}" != "1" ]]; then rm -rf "$validation_dir"; fi
}
trap cleanup EXIT
terraform -chdir="$module_dir" fmt -check -recursive
cp "$module_dir/"*.tf "$validation_dir/"
cp "$module_dir/.terraform.lock.hcl" "$module_dir/terraform.tfvars.example" "$validation_dir/"
cp -R "$module_dir/tests" "$module_dir/app" "$module_dir/templates" "$validation_dir/"
terraform -chdir="$validation_dir" init -backend=false -input=false -lockfile=readonly -plugin-dir="$module_dir/.terraform/providers" -no-color
terraform -chdir="$validation_dir" validate -no-color
test_exit=0
terraform -chdir="$validation_dir" test -var-file=terraform.tfvars.example -json -verbose | python3 -c '
import json, sys
for line in sys.stdin:
    event = json.loads(line)
    if event.get("type") == "test_plan":
        event["test_plan"].pop("provider_schemas", None)
    print(json.dumps(event))
' > "$validation_dir/test-events.jsonl" || test_exit=$?
python3 - "$validation_dir/test-events.jsonl" "$test_exit" <<'PY'
import json, sys
events = [json.loads(line) for line in open(sys.argv[1])]
passed = {e.get('@testrun') for e in events if e.get('type') == 'test_run' and e.get('test_run', {}).get('status') == 'pass'}
for event in events:
    if event.get('type') == 'test_plan':
        assert 'synthetic-test-key' not in json.dumps(event['test_plan']), 'Ephemeral PSK appeared in a mock Plan'
    if event.get('type') == 'test_summary' or (event.get('type') == 'diagnostic' and event.get('@testrun') not in passed):
        print(event.get('@message', event['type']))
        if event.get('diagnostic', {}).get('severity') == 'error':
            print(event['diagnostic'].get('detail', ''))
if sys.argv[2] != '0':
    raise SystemExit(int(sys.argv[2]))
print('Mock Plan output: no ephemeral PSK values')
PY
python3 -B -m unittest discover -s "$module_dir/tests" -p 'test_*.py' -v
if [[ "${DELTA_KEEP_VALIDATION:-0}" == "1" ]]; then printf 'Validation artifacts: %s\n' "$validation_dir"; fi
