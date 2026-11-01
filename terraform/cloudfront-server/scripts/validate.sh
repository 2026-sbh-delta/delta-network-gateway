#!/usr/bin/env bash
# Isolated mock Plans only. Never initializes the real backend or applies AWS resources.
set -euo pipefail
module_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
terraform_bin="${TERRAFORM_BIN:-terraform}"
validation_dir="$(mktemp -d "${TMPDIR:-/tmp}/delta-cloudfront-validation.XXXXXX")"
cleanup() {
  if [[ "${DELTA_KEEP_VALIDATION:-0}" != "1" ]]; then rm -rf "$validation_dir"; fi
}
trap cleanup EXIT
"$terraform_bin" -chdir="$module_dir" fmt -check -recursive
cp "$module_dir/"*.tf "$module_dir/.terraform.lock.hcl" "$module_dir/terraform.tfvars.example" "$validation_dir/"
cp -R "$module_dir/tests" "$module_dir/web" "$validation_dir/"
init_args=(-backend=false -input=false -lockfile=readonly -no-color)
if [[ -d "$module_dir/../workload-server/.terraform/providers" ]]; then
  init_args+=("-plugin-dir=$module_dir/../workload-server/.terraform/providers")
fi
"$terraform_bin" -chdir="$validation_dir" init "${init_args[@]}"
"$terraform_bin" -chdir="$validation_dir" validate -no-color
test_exit=0
"$terraform_bin" -chdir="$validation_dir" test -var-file=terraform.tfvars.example -verbose -json | python3 -c '
import json,sys
for line in sys.stdin:
    e=json.loads(line)
    if e.get("type")=="test_plan": e["test_plan"].pop("provider_schemas",None)
    print(json.dumps(e))
' > "$validation_dir/test-events.jsonl" || test_exit=$?
python3 - "$validation_dir/test-events.jsonl" "$test_exit" <<'PY'
import collections,json,sys
checked=False
for line in open(sys.argv[1]):
    e=json.loads(line)
    if e.get("type")=="test_summary" or (e.get("type")=="test_run" and e.get("test_run",{}).get("progress")=="complete"):
        print(e.get("@message"))
    if e.get("type")=="diagnostic" and sys.argv[2]!="0":
        print(e["diagnostic"].get("summary"),e["diagnostic"].get("detail"),e["diagnostic"].get("range"))
    if e.get("type")=="test_plan" and e.get("@testrun")=="inspected_alb_demo":
        counts=collections.Counter(r["type"] for r in e["test_plan"].get("resource_changes",[]) if r.get("mode")=="managed")
        expected={"terraform_data":1,"aws_internet_gateway":1,"aws_subnet":2,"aws_route_table":2,
                  "aws_route_table_association":2,"aws_route":2,"aws_security_group":1,
                  "aws_vpc_security_group_egress_rule":2,"aws_vpc_security_group_ingress_rule":6,
                  "aws_lb":1,"aws_lb_target_group":2,"aws_lb_target_group_attachment":2,
                  "aws_lb_listener":1,"aws_lb_listener_rule":2,"aws_cloudfront_vpc_origin":1,
                  "aws_cloudfront_origin_request_policy":1,"aws_cloudfront_distribution":1,
                  "aws_s3_bucket":1,"aws_s3_bucket_public_access_block":1,"aws_s3_bucket_ownership_controls":1,
                  "aws_s3_bucket_server_side_encryption_configuration":1,"aws_s3_bucket_policy":1,
                  "aws_s3_object":2,"aws_cloudfront_origin_access_control":1}
        if dict(counts)!=expected: raise SystemExit("Unexpected resource inventory: "+str(counts))
        checked=True
if sys.argv[2]=="0" and not checked: raise SystemExit("Resource inventory was not checked.")
PY
if [[ "${DELTA_KEEP_VALIDATION:-0}" == "1" ]]; then printf 'Validation artifacts: %s\n' "$validation_dir"; fi
exit "$test_exit"
