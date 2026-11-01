#!/usr/bin/env python3
"""Read-only project/Billing/API/inventory checks using local authorized-user ADC.

Tokens stay in memory. This script never enables APIs or changes Billing/IAM.
Credit balances and free-trial status require Cloud Billing console verification.
"""
import argparse
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--project-number", required=True)
    parser.add_argument("--region", default="asia-northeast3")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    ca = Path("/etc/ssl/cert.pem")
    ctx = ssl.create_default_context(cafile=str(ca) if ca.is_file() else None)
    adc = json.loads((Path.home() / ".config/gcloud/application_default_credentials.json").read_text())
    if adc.get("type") != "authorized_user" or adc.get("quota_project_id") != args.project_id:
        raise ValueError("Expected authorized-user ADC with the authorized quota project.")
    form = {k: adc[k] for k in ("client_id", "client_secret", "refresh_token")}
    form["grant_type"] = "refresh_token"
    request = urllib.request.Request("https://oauth2.googleapis.com/token", data=urllib.parse.urlencode(form).encode())
    try:
        with urllib.request.urlopen(request, context=ctx, timeout=30) as response:
            token = json.load(response)["access_token"]
    except urllib.error.HTTPError:
        raise ValueError("ADC refresh failed. Run gcloud auth application-default login again.") from None

    def get(url):
        try:
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
            with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            # Do not print credential-bearing request objects or response bodies.
            raise ValueError(f"Read-only API request failed: HTTP {error.code}, {urllib.parse.urlsplit(url).path}") from None

    project = get(f"https://cloudresourcemanager.googleapis.com/v1/projects/{args.project_id}")
    if project.get("projectId") != args.project_id or project.get("projectNumber") != args.project_number or project.get("lifecycleState") != "ACTIVE":
        raise ValueError("Project identity/state mismatch. No changes were made.")
    billing = get(f"https://cloudbilling.googleapis.com/v1/projects/{args.project_id}/billingInfo")
    account = get("https://cloudbilling.googleapis.com/v1/" + billing["billingAccountName"]) if billing.get("billingAccountName") else {}
    apis = {service: get(f"https://serviceusage.googleapis.com/v1/projects/{args.project_number}/services/{service}?fields=state")["state"]
            for service in ("compute.googleapis.com", "iap.googleapis.com", "oslogin.googleapis.com")}
    out = {"project_id": args.project_id, "project_number": args.project_number,
           "billing_enabled": billing.get("billingEnabled", False), "billing_account_open": account.get("open", False),
           "credit_balance_verified": False, "apis": apis, "inventory_verified": False}
    if out["billing_enabled"] and out["billing_account_open"] and apis["compute.googleapis.com"] == "ENABLED":
        base = f"https://compute.googleapis.com/compute/v1/projects/{args.project_id}"
        # Return entire inventories; never silently treat a partial page as complete.
        def pages(url, key):
            items = []
            while True:
                page = get(url)
                items.extend(page.get(key, []))
                if not page.get("nextPageToken"):
                    return items
                url = url.split("&pageToken=")[0] + "&pageToken=" + urllib.parse.quote(page["nextPageToken"])
        out["networks"] = pages(base + "/global/networks?fields=items(name,autoCreateSubnetworks),nextPageToken", "items")
        out["subnets"] = get(base + "/aggregated/subnetworks?fields=items,nextPageToken")
        if out["subnets"].get("nextPageToken"):
            raise ValueError("Subnet inventory is paginated; inspect all pages before choosing CIDRs.")
        out["region"] = get(base + f"/regions/{args.region}?fields=name,status,quotas")
        out["image"] = get("https://compute.googleapis.com/compute/v1/projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64?fields=name,status,diskSizeGb,family")
        out["inventory_verified"] = True
    if args.output:
        # Output is metadata only, but keep consistent private evidence permissions.
        import os
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(out, stream, indent=2)
    print(json.dumps({k: v for k, v in out.items() if k not in ("networks", "subnets", "region")}, indent=2))
    return 0 if out["inventory_verified"] and all(s == "ENABLED" for s in apis.values()) else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
