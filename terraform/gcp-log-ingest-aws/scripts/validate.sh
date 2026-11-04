#!/usr/bin/env bash
set -euo pipefail
module_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
artifact="$module_dir/../../services/gcp-log-collector/.build/collector.zip"
validation_dir="$(mktemp -d "${TMPDIR:-/tmp}/delta-gcp-ingest-validation.XXXXXX")"
trap 'rm -rf "$validation_dir"' EXIT
terraform -chdir="$module_dir" fmt -check -recursive
cp "$module_dir/"*.tf "$module_dir/.terraform.lock.hcl" "$module_dir/terraform.tfvars.example" "$validation_dir/"
cp -R "$module_dir/tests" "$validation_dir/"
terraform -chdir="$validation_dir" init -backend=false -input=false -lockfile=readonly -plugin-dir="$module_dir/../network-security/.terraform/providers" -no-color
terraform -chdir="$validation_dir" validate -no-color
terraform -chdir="$validation_dir" test -var-file=terraform.tfvars.example -var="collector_zip=$artifact" -no-color
