#!/usr/bin/env python3
"""Read-only project/billing/API/network checks using ADC; never prints tokens."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

APIS = (
    "compute.googleapis.com", "networksecurity.googleapis.com",
    "networkconnectivity.googleapis.com", "pubsub.googleapis.com",
    "logging.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com",
    "sts.googleapis.com", "iap.googleapis.com", "oslogin.googleapis.com", "privateca.googleapis.com",
    "cloudbilling.googleapis.com",
)
PERMISSIONS = (
    "serviceusage.services.enable", "compute.networks.create", "compute.subnetworks.create",
    "compute.firewallPolicies.create", "compute.vpnGateways.create", "compute.routers.create",
    "compute.instances.create", "networksecurity.firewallEndpoints.create",
    "networksecurity.firewallEndpointAssociations.create", "networksecurity.securityProfiles.create",
    "networksecurity.securityProfileGroups.create", "networkconnectivity.hubs.create",
    "networkconnectivity.spokes.create", "logging.sinks.create", "pubsub.topics.create",
    "pubsub.subscriptions.create", "iam.workloadIdentityPools.create",
    "iam.workloadIdentityPoolProviders.create",
)


def inspect(project_id, region, session):
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "project_id": project_id}

    def get(url):
        response = session.get(url, timeout=20)
        if response.status_code != 200:
            error = response.json().get("error", {})
            result = {"http_status": response.status_code}
            for detail in error.get("details", []):
                if detail.get("reason"):
                    result["error_reason"] = detail["reason"]
            return result
        return response.json()

    project = get(f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}")
    result["project"] = {key: project[key] for key in ("projectId", "projectNumber", "lifecycleState", "parent", "http_status") if key in project}
    if "projectNumber" not in project:
        result["ready"] = False
        return result
    permission_response = session.post(
        f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}:testIamPermissions",
        json={"permissions": list(PERMISSIONS)}, timeout=20,
    )
    granted = permission_response.json().get("permissions", []) if permission_response.status_code == 200 else []
    checked = permission_response.status_code == 200
    result["permissions"] = {"http_status": permission_response.status_code, "checked": checked, "missing": sorted(set(PERMISSIONS) - set(granted)) if checked else None}
    billing = get(f"https://cloudbilling.googleapis.com/v1/projects/{project_id}/billingInfo")
    result["billing"] = {key: billing[key] for key in ("billingEnabled", "http_status", "error_reason") if key in billing}
    if billing.get("billingAccountName"):
        account = get("https://cloudbilling.googleapis.com/v1/" + billing["billingAccountName"])
        result["billing"]["account_open"] = account.get("open")
    result["apis"] = {}
    for api in APIS:
        service = get(f"https://serviceusage.googleapis.com/v1/projects/{project['projectNumber']}/services/{api}")
        result["apis"][api] = service.get("state", service)
    networks = get(f"https://compute.googleapis.com/compute/v1/projects/{project_id}/global/networks")
    subnets = get(f"https://compute.googleapis.com/compute/v1/projects/{project_id}/aggregated/subnetworks")
    result["networks"] = [{"name": item["name"], "autoCreateSubnetworks": item.get("autoCreateSubnetworks")} for item in networks.get("items", [])]
    result["subnets"] = [
        {key: subnet[key] for key in ("name", "ipCidrRange", "secondaryIpRanges", "region") if key in subnet}
        for scope in subnets.get("items", {}).values() for subnet in scope.get("subnetworks", [])
    ]
    result["inventory_verified"] = "http_status" not in networks and "http_status" not in subnets and not networks.get("nextPageToken") and not subnets.get("nextPageToken")
    regional = get(f"https://compute.googleapis.com/compute/v1/projects/{project_id}/regions/{region}")
    result["region"] = {key: regional[key] for key in ("name", "status", "quotas", "http_status") if key in regional}
    result["ready"] = bool(billing.get("billingEnabled") and result["inventory_verified"] and regional.get("status") == "UP" and checked and not result["permissions"]["missing"] and all(value == "ENABLED" for value in result["apis"].values()))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", default="term2-delta", choices=["term2-delta"])
    parser.add_argument("--region", default="asia-northeast3")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.is_symlink():
        parser.error("Refusing a symlink output destination")
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        with AuthorizedSession(credentials) as session:
            result = inspect(args.project_id, args.region, session)
    except Exception as error:
        result = {"project_id": args.project_id, "ready": False, "error_type": type(error).__name__}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.touch(mode=0o600, exist_ok=True)
        args.output.chmod(0o600)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
