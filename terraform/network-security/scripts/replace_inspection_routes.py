#!/usr/bin/env python3
"""Replace only existing Spoke route next hops from a reviewed Terraform JSON plan.

Dry run by default. After --execute, discard the old plan and create a fresh plan
before applying. This avoids Provider 6.71.0's delete/create for next-hop changes.
"""

import argparse
import json
import re
import subprocess
import sys
import time


def route_changes(plan):
    mode = plan["variables"]["inspection_mode"]["value"]
    if mode not in ("enabled", "bypass"):
        raise ValueError("Only enabled/bypass transition plans are supported.")
    routes = []
    for resource in plan.get("resource_changes", []):
        if not resource["address"].startswith('aws_ec2_transit_gateway_route.before_inspection['):
            continue
        change = resource["change"]
        if change["actions"] == ["no-op"]:
            continue
        if change["actions"] != ["delete", "create"]:
            raise ValueError("Expected an existing route replacement, not a new route or deletion.")
        before, after = change["before"], change["after"]
        for key in ("destination_cidr_block", "transit_gateway_route_table_id", "blackhole"):
            if before.get(key) != after.get(key):
                raise ValueError(f"Route replacement may not change {key}.")
        if after.get("blackhole"):
            raise ValueError("Blackhole routes are not supported.")
        for value in (before.get("transit_gateway_attachment_id"), after.get("transit_gateway_attachment_id")):
            if not isinstance(value, str) or not re.fullmatch(r"tgw-attach-[0-9a-f]{17}", value):
                raise ValueError("Both attachment IDs must be known before switching routes.")
        routes.append({
            "cidr": after["destination_cidr_block"],
            "table": after["transit_gateway_route_table_id"],
            "before": before["transit_gateway_attachment_id"],
            "after": after["transit_gateway_attachment_id"],
        })
    return mode, routes


def aws_json(profile, region, service, operation, *args):
    result = subprocess.run(
        ["aws", "--profile", profile, "--region", region, "--no-cli-pager", service, operation, *args, "--output", "json"],
        capture_output=True, text=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)


def execute(plan, profile, call=aws_json):
    mode, routes = route_changes(plan)
    region = plan["variables"]["region"]["value"]
    expected_account = plan["variables"]["central_account_id"]["value"]

    def aws(service, operation, *args):
        return call(profile, region, service, operation, *args)

    if aws("sts", "get-caller-identity")["Account"] != expected_account:
        raise ValueError("Authenticated account differs from the reviewed plan.")
    if mode == "enabled" and routes:
        firewall_arn = plan["planned_values"]["outputs"]["firewall_arn"]["value"]
        status = aws("network-firewall", "describe-firewall", "--firewall-arn", firewall_arn)["FirewallStatus"]
        endpoints = status.get("SyncStates", {})
        if (status["Status"] != "READY" or status["ConfigurationSyncStateSummary"] != "IN_SYNC"
                or len(endpoints) != len(plan["variables"]["inspection_azs"]["value"])
                or any(endpoint["Attachment"]["Status"] != "READY" for endpoint in endpoints.values())):
            raise ValueError("All firewall endpoints must be READY and IN_SYNC before restoring inspection.")

    def current(route):
        found = aws("ec2", "search-transit-gateway-routes", "--transit-gateway-route-table-id", route["table"],
                    "--filters", f'Name=route-search.exact-match,Values={route["cidr"]}')["Routes"]
        if len(found) != 1 or found[0]["State"] != "active" or found[0]["Type"] != "static":
            raise ValueError("Expected exactly one active static route.")
        return found[0]["TransitGatewayAttachments"][0]["TransitGatewayAttachmentId"]

    # Check every current route before making any change. Refuse stale plans.
    snapshots = [(route, current(route)) for route in routes]
    if any(target not in (route["before"], route["after"]) for route, target in snapshots):
        raise ValueError("A live route changed after planning. Re-plan before proceeding.")

    def replace(route, target):
        aws("ec2", "replace-transit-gateway-route", "--transit-gateway-route-table-id", route["table"],
            "--destination-cidr-block", route["cidr"], "--transit-gateway-attachment-id", target)

    changed = []
    try:
        for route, target in snapshots:
            if target != route["after"]:
                changed.append((route, target))
                replace(route, route["after"])
        for route, _ in snapshots:
            for attempt in range(15):
                if current(route) == route["after"]:
                    break
                if attempt == 14:
                    raise RuntimeError("Route did not converge to the planned next hop.")
                time.sleep(1)
    except Exception:
        for route, target in reversed(changed):
            try:
                replace(route, target)
            except Exception as error:
                print(f'Rollback failed for {route["cidr"]}: {error}', file=sys.stderr)
        raise
    return len(changed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_json")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--execute", action="store_true", help="Replace route next hops after live account/readiness/staleness checks.")
    args = parser.parse_args()
    try:
        with open(args.plan_json) as source:
            plan = json.load(source)
        mode, routes = route_changes(plan)
        print(json.dumps({"mode": mode, "routes": routes}, indent=2))
        if args.execute:
            print(f"Replaced {execute(plan, args.profile)} routes. Discard this plan and create a fresh Terraform plan.")
    except (KeyError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
