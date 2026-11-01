#!/usr/bin/env bash
# Isolated local validation only. Never runs a real Plan, Apply or Destroy.
set -euo pipefail
module_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
terraform_bin="${TERRAFORM_BIN:-terraform}"
validation_dir="$(mktemp -d "${TMPDIR:-/tmp}/delta-gcp-validation.XXXXXX")"
cleanup() {
  if [[ "${DELTA_KEEP_VALIDATION:-0}" != "1" ]]; then rm -rf "$validation_dir"; fi
}
trap cleanup EXIT
python3 -m unittest discover -s "$module_dir/tests" -p 'test_*.py'
"$terraform_bin" -chdir="$module_dir" fmt -check -recursive
cp "$module_dir/"*.tf "$validation_dir/"
cp "$module_dir/.terraform.lock.hcl" "$validation_dir/"
cp "$module_dir/terraform.tfvars.example" "$validation_dir/"
cp -R "$module_dir/tests" "$module_dir/templates" "$module_dir/app" "$validation_dir/"
init_args=(-backend=false -input=false -lockfile=readonly -no-color)
if [[ -d "$module_dir/.terraform/providers" ]]; then init_args+=("-plugin-dir=$module_dir/.terraform/providers"); fi
"$terraform_bin" -chdir="$validation_dir" init "${init_args[@]}"
"$terraform_bin" -chdir="$validation_dir" validate -no-color
test_exit=0
"$terraform_bin" -chdir="$validation_dir" test -var-file=terraform.tfvars.example -verbose -json | python3 -c '
import json,sys
for line in sys.stdin:
    event=json.loads(line)
    if event.get("type")=="test_plan": event["test_plan"].pop("provider_schemas",None)
    print(json.dumps(event))
' > "$validation_dir/test-events.jsonl" || test_exit=$?
python3 - "$validation_dir/test-events.jsonl" "$test_exit" <<'PY'
import collections,json,sys
raw=open(sys.argv[1]).read()
if "MockEphemeralSecretNotReal" in raw:
    raise SystemExit("Ephemeral mock PSK unexpectedly appeared in test plan output.")
expected={"terraform_data":1,"google_project_service":3,"google_compute_network":1,
          "google_compute_subnetwork":1,"google_compute_address":1,"google_compute_firewall":3,
          "google_compute_instance":1,"google_compute_ha_vpn_gateway":1,"google_compute_router":1}
foundation_checked=False
for line in raw.splitlines():
    e=json.loads(line)
    if e.get("type")=="test_summary" or (e.get("type")=="test_run" and e.get("test_run",{}).get("progress")=="complete"):
        print(e.get("@message"))
    if e.get("type")=="diagnostic" and sys.argv[2]!="0":
        print(e["diagnostic"].get("summary"),e["diagnostic"].get("detail"))
    if e.get("type")=="test_plan" and e.get("@testrun")=="private_foundation":
        changes=e["test_plan"].get("resource_changes",[])
        counts=collections.Counter(r["type"] for r in changes if r.get("mode")=="managed")
        if dict(counts)!=expected: raise SystemExit("Unexpected foundation resource inventory: "+str(counts))
        foundation_checked=True
if sys.argv[2]=="0" and not foundation_checked: raise SystemExit("Foundation plan inventory was not checked.")
PY
if [[ "${DELTA_KEEP_VALIDATION:-0}" == "1" ]]; then printf 'Validation artifacts: %s\n' "$validation_dir"; fi
exit "$test_exit"
