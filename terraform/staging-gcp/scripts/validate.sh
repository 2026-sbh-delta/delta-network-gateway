#!/usr/bin/env bash
# Never invokes a real plan/apply/destroy. All test run blocks use command=plan
# and mock_provider google. The GCS backend is not initialized or migrated.
set -euo pipefail

module_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
terraform_bin="${TERRAFORM_BIN:-terraform}"
validation_dir="$(mktemp -d "${TMPDIR:-/tmp}/delta-staging-gcp-validation.XXXXXX")"
cleanup() {
  if [[ "${DELTA_KEEP_VALIDATION:-0}" != "1" ]]; then
    rm -rf "$validation_dir"
  fi
}
trap cleanup EXIT

"$terraform_bin" -chdir="$module_dir" fmt -check -recursive
cp "$module_dir/"*.tf "$validation_dir/"
cp "$module_dir/.terraform.lock.hcl" "$validation_dir/"
cp -R "$module_dir/tests" "$validation_dir/"

init_args=(-backend=false -input=false -lockfile=readonly -no-color)
if [[ -d "$module_dir/.terraform/providers" ]]; then
  init_args+=("-plugin-dir=$module_dir/.terraform/providers")
fi
"$terraform_bin" -chdir="$validation_dir" init "${init_args[@]}"
"$terraform_bin" -chdir="$validation_dir" validate -no-color
"$terraform_bin" -chdir="$validation_dir" test -no-color
