#!/usr/bin/env python3
"""Switch reviewed Spoke routes, preserving propagated VPN fallback routes.

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
    vpn_cidrs = {cidr for network in plan["variables"].get("vpn_networks", {}).get("value", {}).values()
                 for cidr in network["cidrs"]}
    preserve_bgp = plan["variables"].get("preserve_vpn_direct_routes", {}).get("value", False)
    routes = []
    for resource in plan.get("resource_changes", []):
        if not resource["address"].startswith('aws_ec2_transit_gateway_route.before_inspection['):
            continue
        change = resource["change"]
        if change["actions"] == ["no-op"]:
            continue
        actions = change["actions"]
        before, after = change["before"], change["after"]
        route = after or before
        vpn_transition = route["destination_cidr_block"] in vpn_cidrs and preserve_bgp
        creating = vpn_transition and mode == "enabled" and actions == ["create"] and before is None
        deleting = vpn_transition and mode == "bypass" and actions == ["delete"] and after is None
        if actions != ["delete", "create"] and not (creating or deleting):
            raise ValueError("Expected an existing route replacement, not a new route or deletion.")
        for key in ("destination_cidr_block", "transit_gateway_route_table_id", "blackhole"):
            if before is not None and after is not None and before.get(key) != after.get(key):
                raise ValueError(f"Route replacement may not change {key}.")
        if route.get("blackhole"):
            raise ValueError("Blackhole routes are not supported.")
        for value in [item.get("transit_gateway_attachment_id") for item in (before, after) if item is not None]:
            if not isinstance(value, str) or not re.fullmatch(r"tgw-attach-[0-9a-f]{17}", value):
                raise ValueError("Both attachment IDs must be known before switching routes.")
        routes.append({
            "cidr": route["destination_cidr_block"],
            "table": route["transit_gateway_route_table_id"],
            "before": before["transit_gateway_attachment_id"] if before else None,
            "after": after["transit_gateway_attachment_id"] if after else None,
        })
    return mode, routes


def aws_json(profile, region, service, operation, *args):
    result = subprocess.run(
        ["aws", "--profile", profile, "--region", region, "--no-cli-pager", service, operation, *args, "--output", "json"],
        capture_output=True, text=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else {}


def association_changes(plan):
    mode = plan['variables']['inspection_mode']['value']
    selected = plan['variables'].get('isolated_inspection_spokes', {}).get('value', [])
    outputs = plan['planned_values']['outputs']
    changes = []
    for resource in plan.get('resource_changes', []):
        if not resource['address'].startswith('aws_ec2_transit_gateway_route_table_association.spoke['):
            continue
        change = resource['change']
        if change['actions'] == ['no-op']:
            continue
        key = json.loads(resource['address'].split('[', 1)[1][:-1])
        if key not in selected or change['actions'] != ['delete', 'create']:
            raise ValueError('Only a selected spoke association can move between prepared route tables.')
        before, after = change['before'], change['after']
        attachment = plan['variables']['spoke_attachments']['value'][key]['attachment_id']
        shared = outputs['spoke_route_table_id']['value']
        isolated = outputs['isolated_inspection_route_table_ids']['value'][key]
        expected = (shared, isolated) if mode == 'enabled' else (isolated, shared)
        if (before['transit_gateway_attachment_id'] != attachment or after['transit_gateway_attachment_id'] != attachment or
                (before['transit_gateway_route_table_id'], after['transit_gateway_route_table_id']) != expected):
            raise ValueError('Spoke attachment or prepared route tables differ from reviewed scope.')
        changes.append({'address': resource['address'], 'attachment': attachment, 'before': expected[0], 'after': expected[1]})
    return changes


def execute(plan, profile, call=aws_json):
    mode, routes = route_changes(plan)
    associations = association_changes(plan)
    region = plan["variables"]["region"]["value"]
    expected_account = plan["variables"]["central_account_id"]["value"]

    def aws(service, operation, *args):
        return call(profile, region, service, operation, *args)

    if aws("sts", "get-caller-identity")["Account"] != expected_account:
        raise ValueError("Authenticated account differs from the reviewed plan.")
    if mode == "enabled" and (routes or associations):
        firewall_arn = plan["planned_values"]["outputs"]["firewall_arn"]["value"]
        status = aws("network-firewall", "describe-firewall", "--firewall-arn", firewall_arn)["FirewallStatus"]
        endpoints = status.get("SyncStates", {})
        if (status["Status"] != "READY" or status["ConfigurationSyncStateSummary"] != "IN_SYNC"
                or len(endpoints) != len(plan["variables"]["inspection_azs"]["value"])
                or any(endpoint["Attachment"]["Status"] != "READY" for endpoint in endpoints.values())):
            raise ValueError("All firewall endpoints must be READY and IN_SYNC before restoring inspection.")

    vpn_ids = {resource["change"]["after"]["transit_gateway_attachment_id"]
               for resource in plan.get("resource_changes", [])
               if resource.get("type") == "aws_vpn_connection" and resource["change"].get("after")}
    if any(route["before"] is None or route["after"] is None for route in routes) and not vpn_ids:
        raise ValueError("VPN route transitions require known VPN attachments in the reviewed plan.")

    def current(route):
        found = aws("ec2", "search-transit-gateway-routes", "--transit-gateway-route-table-id", route["table"],
                    "--filters", f'Name=route-search.exact-match,Values={route["cidr"]}')["Routes"]
        if len(found) == 1 and found[0]["State"] == "active" and found[0]["Type"] == "propagated":
            actual = {item["TransitGatewayAttachmentId"] for item in found[0]["TransitGatewayAttachments"]}
            if (route["before"] is None or route["after"] is None) and actual == vpn_ids:
                return None
        if len(found) != 1 or found[0]["State"] != "active" or found[0]["Type"] != "static":
            raise ValueError("Expected exactly one active static route.")
        return found[0]["TransitGatewayAttachments"][0]["TransitGatewayAttachmentId"]

    # Check every current route before making any change. Refuse stale plans.
    snapshots = [(route, current(route)) for route in routes]
    if any(target not in (route["before"], route["after"]) for route, target in snapshots):
        raise ValueError("A live route changed after planning. Re-plan before proceeding.")

    def attachment_scope(item):
        found = aws('ec2', 'describe-transit-gateway-attachments', '--transit-gateway-attachment-ids', item['attachment'])['TransitGatewayAttachments']
        if len(found) != 1 or found[0]['ResourceType'] != 'vpc' or found[0]['State'] != 'available':
            raise ValueError('Selected VPC attachment must remain available.')
        return found[0].get('Association', {}).get('TransitGatewayRouteTableId')

    def association_state(item, table):
        found = aws('ec2', 'get-transit-gateway-route-table-associations', '--transit-gateway-route-table-id', table,
                    '--filters', 'Name=transit-gateway-attachment-id,Values=' + item['attachment'])['Associations']
        if len(found) > 1:
            raise ValueError('Expected a single selected spoke association per route table.')
        return found[0]['State'] if found else 'disassociated'

    def current_association(item):
        attachment_scope(item)
        active = [(table, association_state(item, table)) for table in (item['before'], item['after'])]
        active = [(table, state) for table, state in active if state != 'disassociated']
        if len(active) != 1:
            return None, 'transitioning'
        return active[0]

    association_snapshots = [(item, current_association(item)) for item in associations]
    if any(state != 'associated' or target not in (item['before'], item['after']) for item, (target, state) in association_snapshots):
        raise ValueError('A live spoke association changed after planning. Re-plan before proceeding.')

    def move_association(item, target):
        if association_state(item, target) == 'associated':
            return
        other = item['before'] if target == item['after'] else item['after']
        state = association_state(item, other)
        if state not in ('disassociated', 'disassociating'):
            aws('ec2', 'disassociate-transit-gateway-route-table', '--transit-gateway-attachment-id', item['attachment'], '--transit-gateway-route-table-id', other)
        for attempt in range(60):
            if association_state(item, other) == 'disassociated':
                break
            if attempt == 59:
                raise RuntimeError('Spoke disassociation did not converge.')
            time.sleep(1)
        for attempt in range(60):
            try:
                aws('ec2', 'associate-transit-gateway-route-table', '--transit-gateway-attachment-id', item['attachment'], '--transit-gateway-route-table-id', target)
                break
            except RuntimeError as error:
                if 'AlreadyAssociated' not in str(error):
                    raise
                if association_state(item, target) in ('associated', 'associating'):
                    break
                if attachment_scope(item) not in (None, item['before'], item['after']):
                    raise ValueError('Another actor moved the selected spoke to an unrelated route table.') from error
                if attempt == 59:
                    raise RuntimeError('AWS still reports the prior association; inspect before proceeding.') from error
                time.sleep(1)
        for attempt in range(60):
            if association_state(item, target) == 'associated':
                return
            if attempt == 59:
                raise RuntimeError('Spoke association did not converge.')
            time.sleep(1)

    def replace(route, target):
        base = ["--transit-gateway-route-table-id", route["table"], "--destination-cidr-block", route["cidr"]]
        live = current(route)
        if target is None:
            if live is not None:
                aws("ec2", "delete-transit-gateway-route", *base)
        else:
            operation = "create-transit-gateway-route" if live is None else "replace-transit-gateway-route"
            aws("ec2", operation, *base, "--transit-gateway-attachment-id", target)

    changed = []
    changed_associations = []
    try:
        for route, target in snapshots:
            if target != route["after"]:
                changed.append((route, target))
                replace(route, route["after"])
        for item, (target, _) in association_snapshots:
            if target != item['after']:
                changed_associations.append((item, target))
                move_association(item, item['after'])
        for route, _ in snapshots:
            for attempt in range(15):
                if current(route) == route["after"]:
                    break
                if attempt == 14:
                    raise RuntimeError("Route did not converge to the planned next hop.")
                time.sleep(1)
    except Exception:
        for item, target in reversed(changed_associations):
            try:
                move_association(item, target)
            except Exception as error:
                print(f'Association rollback failed for {item["attachment"]}: {error}', file=sys.stderr)
        for route, target in reversed(changed):
            try:
                replace(route, target)
            except Exception as error:
                print(f'Rollback failed for {route["cidr"]}: {error}', file=sys.stderr)
        raise
    return len(changed) + len(changed_associations)


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
        associations = association_changes(plan)
        if associations:
            print(json.dumps({'associations': associations}, indent=2))
        if args.execute:
            print(f"Switched {execute(plan, args.profile)} routes. Discard this plan and create a fresh Terraform plan.")
            imports = [{"address": f'aws_ec2_transit_gateway_route.before_inspection["{route["cidr"]}"]',
                        "id": route["table"] + "_" + route["cidr"]}
                       for route in routes if route["before"] is None]
            if imports:
                print(json.dumps({"import_before_replanning": imports}, indent=2))
            if associations:
                print(json.dumps({'reconcile_existing_associations': [dict(address=item['address'], old_id=item['before']+'_'+item['attachment'], new_id=item['after']+'_'+item['attachment']) for item in associations]}, indent=2))
    except (KeyError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
