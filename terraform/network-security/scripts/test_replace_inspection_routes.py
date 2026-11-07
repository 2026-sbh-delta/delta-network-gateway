import copy
import unittest
from unittest.mock import patch

from replace_inspection_routes import association_changes, execute, route_changes


INSPECTION = "tgw-attach-00000000000000001"
CLIENT = "tgw-attach-00000000000000011"
SERVER = "tgw-attach-00000000000000012"


def fixture():
    routes = []
    for cidr, target in [("10.10.0.0/16", CLIENT), ("10.20.0.0/16", SERVER)]:
        before = {"destination_cidr_block": cidr, "transit_gateway_route_table_id": "tgw-rtb-00000000000000001",
                  "blackhole": False, "transit_gateway_attachment_id": INSPECTION}
        routes.append({"address": f'aws_ec2_transit_gateway_route.before_inspection["{cidr}"]',
                       "change": {"actions": ["delete", "create"], "before": before,
                                  "after": dict(before, transit_gateway_attachment_id=target)}})
    return {"variables": {"inspection_mode": {"value": "bypass"}, "region": {"value": "ap-northeast-2"},
                          "central_account_id": {"value": "111122223333"},
                          "inspection_azs": {"value": {"apne2-az1": {}, "apne2-az2": {}}}},
            "planned_values": {"outputs": {"firewall_arn": {"value": "arn:aws:network-firewall:ap-northeast-2:111122223333:firewall/test"}}},
            "resource_changes": routes}


class FakeAWS:
    def __init__(self):
        self.account = "111122223333"
        self.routes = {"10.10.0.0/16": INSPECTION, "10.20.0.0/16": INSPECTION}
        self.writes = []
        self.fail_once = None
        self.ready = True

    def __call__(self, profile, region, service, operation, *args):
        if operation == "get-caller-identity":
            return {"Account": self.account}
        if operation == "describe-firewall":
            return {"FirewallStatus": {"Status": "READY" if self.ready else "PROVISIONING",
                                       "ConfigurationSyncStateSummary": "IN_SYNC",
                                       "SyncStates": {az: {"Attachment": {"Status": "READY"}}
                                                      for az in ("a", "b")}}}
        if operation == "search-transit-gateway-routes":
            cidr = args[args.index("--filters") + 1].split("Values=")[1]
            return {"Routes": [{"State": "active", "Type": "static", "TransitGatewayAttachments": [
                {"TransitGatewayAttachmentId": self.routes[cidr]}]}]}
        if operation == "replace-transit-gateway-route":
            cidr = args[args.index("--destination-cidr-block") + 1]
            target = args[args.index("--transit-gateway-attachment-id") + 1]
            self.writes.append((cidr, target))
            if cidr == self.fail_once:
                self.fail_once = None
                raise RuntimeError("simulated second-route failure")
            self.routes[cidr] = target
            return {}
        raise AssertionError(operation)


class RouteTransitionTests(unittest.TestCase):
    def test_updates_both_directions_and_is_idempotent(self):
        aws = FakeAWS()
        self.assertEqual(execute(fixture(), "test", call=aws), 2)
        self.assertEqual(aws.routes, {"10.10.0.0/16": CLIENT, "10.20.0.0/16": SERVER})
        self.assertEqual(execute(fixture(), "test", call=aws), 0)
        self.assertEqual(len(aws.writes), 2)

    def test_wrong_account_cannot_write(self):
        aws = FakeAWS()
        aws.account = "999900001111"
        with self.assertRaisesRegex(ValueError, "account"):
            execute(fixture(), "test", call=aws)
        self.assertEqual(aws.writes, [])

    def test_stale_second_route_prevents_all_writes(self):
        aws = FakeAWS()
        aws.routes["10.20.0.0/16"] = "tgw-attach-00000000000000099"
        with self.assertRaisesRegex(ValueError, "Re-plan"):
            execute(fixture(), "test", call=aws)
        self.assertEqual(aws.writes, [])

    def test_partial_failure_restores_previous_routes(self):
        aws = FakeAWS()
        aws.fail_once = "10.20.0.0/16"
        with self.assertRaisesRegex(RuntimeError, "second-route"):
            execute(fixture(), "test", call=aws)
        self.assertEqual(aws.routes, {"10.10.0.0/16": INSPECTION, "10.20.0.0/16": INSPECTION})

    def test_restore_requires_ready_firewall(self):
        plan = fixture()
        plan["variables"]["inspection_mode"]["value"] = "enabled"
        aws = FakeAWS()
        aws.ready = False
        with self.assertRaisesRegex(ValueError, "READY"):
            execute(plan, "test", call=aws)
        self.assertEqual(aws.writes, [])

    def test_restore_reuses_ready_firewall_and_inspection_hop(self):
        plan = fixture()
        plan["variables"]["inspection_mode"]["value"] = "enabled"
        aws = FakeAWS()
        aws.routes = {"10.10.0.0/16": CLIENT, "10.20.0.0/16": SERVER}
        for resource in plan["resource_changes"]:
            change = resource["change"]
            change["before"], change["after"] = change["after"], change["before"]
        self.assertEqual(execute(plan, "test", call=aws), 2)
        self.assertEqual(aws.routes, {"10.10.0.0/16": INSPECTION, "10.20.0.0/16": INSPECTION})

    def test_rejects_disabled_transition(self):
        plan = fixture()
        plan["variables"]["inspection_mode"]["value"] = "disabled"
        with self.assertRaisesRegex(ValueError, "enabled/bypass"):
            route_changes(plan)

    def test_rejects_destination_or_table_or_blackhole_change(self):
        for key, value in [("destination_cidr_block", "10.30.0.0/16"),
                           ("transit_gateway_route_table_id", "tgw-rtb-00000000000000099"), ("blackhole", True)]:
            with self.subTest(key=key):
                plan = fixture()
                plan["resource_changes"][0]["change"]["after"][key] = value
                with self.assertRaisesRegex(ValueError, key):
                    route_changes(plan)

    def test_rejects_new_or_removed_route(self):
        for actions in (["create"], ["delete"]):
            plan = fixture()
            plan["resource_changes"][0]["change"]["actions"] = actions
            with self.assertRaisesRegex(ValueError, "existing route"):
                route_changes(plan)

    def test_rejects_unknown_attachment(self):
        plan = fixture()
        plan["resource_changes"][0]["change"]["after"]["transit_gateway_attachment_id"] = None
        with self.assertRaisesRegex(ValueError, "known"):
            route_changes(plan)

    def test_ignores_unchanged_routes_and_other_resources(self):
        plan = fixture()
        for resource in plan["resource_changes"]:
            resource["change"]["actions"] = ["no-op"]
        extra = copy.deepcopy(plan["resource_changes"][0])
        extra["address"] = "aws_ec2_transit_gateway_route.other"
        extra["change"]["actions"] = ["create"]
        plan["resource_changes"].append(extra)
        self.assertEqual(route_changes(plan), ("bypass", []))


def vpn_fixture():
    plan = fixture()
    plan["variables"]["inspection_mode"]["value"] = "enabled"
    plan["variables"]["preserve_vpn_direct_routes"] = {"value": True}
    plan["variables"]["vpn_networks"] = {"value": {"gcp": {"cidrs": ["10.32.1.0/24"]}}}
    for resource in plan["resource_changes"]:
        change = resource["change"]
        change["before"], change["after"] = change["after"], change["before"]
    route = dict(plan["resource_changes"][0]["change"]["after"], destination_cidr_block="10.32.1.0/24")
    plan["resource_changes"].insert(0, {
        "address": 'aws_ec2_transit_gateway_route.before_inspection["10.32.1.0/24"]',
        "change": {"actions": ["create"], "before": None, "after": route},
    })
    for attachment in [CLIENT, SERVER]:
        plan["resource_changes"].append({"address": f'aws_vpn_connection.peer["{attachment}"]',
                                         "type": "aws_vpn_connection", "change": {"actions": ["no-op"],
                                         "after": {"transit_gateway_attachment_id": attachment}}})
    return plan


class FakeVPN(FakeAWS):
    def __init__(self):
        super().__init__()
        self.routes = {"10.10.0.0/16": CLIENT, "10.20.0.0/16": SERVER, "10.32.1.0/24": None}
        self.advertisements = [CLIENT, SERVER]

    def __call__(self, profile, region, service, operation, *args):
        if operation == "search-transit-gateway-routes":
            cidr = args[args.index("--filters") + 1].split("Values=")[1]
            if self.routes.get(cidr) is None:
                return {"Routes": [{"State": "active", "Type": "propagated", "TransitGatewayAttachments": [
                    {"TransitGatewayAttachmentId": attachment} for attachment in self.advertisements]}]}
        if operation in ["create-transit-gateway-route", "delete-transit-gateway-route"]:
            cidr = args[args.index("--destination-cidr-block") + 1]
            target = args[args.index("--transit-gateway-attachment-id") + 1] if operation.startswith("create") else None
            self.writes.append((cidr, target))
            self.routes[cidr] = target
            return {}
        return super().__call__(profile, region, service, operation, *args)


class VPNTransitionTests(unittest.TestCase):
    def test_adds_inspection_over_bgp_and_is_idempotent(self):
        aws = FakeVPN()
        self.assertEqual(execute(vpn_fixture(), "test", call=aws), 3)
        self.assertEqual(aws.routes["10.32.1.0/24"], INSPECTION)
        self.assertEqual(execute(vpn_fixture(), "test", call=aws), 0)

    def test_partial_failure_reveals_original_bgp(self):
        aws = FakeVPN()
        aws.fail_once = "10.10.0.0/16"
        before = aws.routes.copy()
        with self.assertRaisesRegex(RuntimeError, "second-route"):
            execute(vpn_fixture(), "test", call=aws)
        self.assertEqual(aws.routes, before)

    def test_unknown_bgp_attachment_prevents_all_writes(self):
        aws = FakeVPN()
        aws.advertisements = ["tgw-attach-00000000000000099"]
        with self.assertRaisesRegex(ValueError, "active static"):
            execute(vpn_fixture(), "test", call=aws)
        self.assertEqual(aws.writes, [])

    def test_new_routes_require_preserved_bgp(self):
        plan = vpn_fixture()
        plan["variables"]["preserve_vpn_direct_routes"]["value"] = False
        with self.assertRaisesRegex(ValueError, "existing route"):
            route_changes(plan)

    def test_bypass_removes_static_and_reveals_bgp(self):
        plan = vpn_fixture()
        aws = FakeVPN()
        execute(plan, "test", call=aws)
        plan["variables"]["inspection_mode"]["value"] = "bypass"
        route = plan["resource_changes"][0]["change"]
        route.update(actions=["delete"], before=route["after"], after=None)
        for resource in plan["resource_changes"][1:3]:
            change = resource["change"]
            change["before"], change["after"] = change["after"], change["before"]
        self.assertEqual(execute(plan, "test", call=aws), 3)
        self.assertIsNone(aws.routes["10.32.1.0/24"])
        self.assertEqual(aws.routes["10.10.0.0/16"], CLIENT)


def scoped_fixture():
    plan = fixture();plan['resource_changes'] = plan['resource_changes'][:1]
    plan['variables']['inspection_mode']['value'] = 'enabled'
    change = plan['resource_changes'][0]['change'];change['before'], change['after'] = change['after'], change['before']
    plan['variables']['isolated_inspection_spokes'] = {'value': ['client']}
    plan['variables']['spoke_attachments'] = {'value': {'client': {'attachment_id': CLIENT}}}
    outputs = plan['planned_values']['outputs']
    outputs['spoke_route_table_id'] = {'value': 'tgw-rtb-00000000000000001'}
    outputs['isolated_inspection_route_table_ids'] = {'value': {'client': 'tgw-rtb-00000000000000041'}}
    plan['resource_changes'].append({'address': 'aws_ec2_transit_gateway_route_table_association.spoke["client"]', 'change': {
        'actions': ['delete', 'create'], 'before': {'transit_gateway_attachment_id': CLIENT, 'transit_gateway_route_table_id': 'tgw-rtb-00000000000000001'},
        'after': {'transit_gateway_attachment_id': CLIENT, 'transit_gateway_route_table_id': 'tgw-rtb-00000000000000041'}}})
    return plan


class FakeScoped(FakeAWS):
    def __init__(self):
        super().__init__();self.routes['10.10.0.0/16'] = CLIENT
        self.table = 'tgw-rtb-00000000000000001';self.fail_associate_once = False
        self.conflict_once = False;self.describe_stale = False

    def __call__(self, profile, region, service, operation, *args):
        if operation == 'describe-transit-gateway-attachments':
            return {'TransitGatewayAttachments': [{'ResourceType': 'vpc', 'State': 'available',
                'Association': {} if self.table is None else {'TransitGatewayRouteTableId': 'tgw-rtb-00000000000000001' if self.describe_stale else self.table, 'State': 'associated'}}]}
        if operation == 'get-transit-gateway-route-table-associations':
            table = args[args.index('--transit-gateway-route-table-id') + 1]
            return {'Associations': [{'TransitGatewayAttachmentId': CLIENT, 'State': 'associated'}] if self.table == table else []}
        if operation == 'disassociate-transit-gateway-route-table':
            self.table = None;return {}
        if operation == 'associate-transit-gateway-route-table':
            if self.conflict_once:
                self.conflict_once = False;raise RuntimeError('Resource.AlreadyAssociated')
            if self.fail_associate_once:
                self.fail_associate_once = False;raise RuntimeError('Injected association failure')
            self.table = args[args.index('--transit-gateway-route-table-id') + 1];return {}
        return super().__call__(profile, region, service, operation, *args)


class ScopedTransitionTests(unittest.TestCase):
    def test_eventually_consistent_attachment_metadata_is_not_the_waiter(self):
        aws = FakeScoped();aws.describe_stale = True
        self.assertEqual(execute(scoped_fixture(), 'test', call=aws), 2)
        self.assertEqual(aws.table, 'tgw-rtb-00000000000000041')

    def test_prior_binding_conflict_is_retried_after_exact_table_check(self):
        aws = FakeScoped();aws.conflict_once = True
        with patch('replace_inspection_routes.time.sleep'):
            self.assertEqual(execute(scoped_fixture(), 'test', call=aws), 2)
        self.assertEqual(aws.table, 'tgw-rtb-00000000000000041')

    def test_moves_only_selected_spoke_and_return_route(self):
        aws = FakeScoped();self.assertEqual(execute(scoped_fixture(), 'test', call=aws), 2)
        self.assertEqual(aws.table, 'tgw-rtb-00000000000000041');self.assertEqual(aws.routes['10.10.0.0/16'], INSPECTION)

    def test_association_failure_restores_original_table_and_route(self):
        aws = FakeScoped();aws.fail_associate_once = True
        with self.assertRaisesRegex(RuntimeError, 'association failure'):
            execute(scoped_fixture(), 'test', call=aws)
        self.assertEqual(aws.table, 'tgw-rtb-00000000000000001');self.assertEqual(aws.routes['10.10.0.0/16'], CLIENT)

    def test_stale_association_prevents_route_write(self):
        aws = FakeScoped();aws.table = 'tgw-rtb-00000000000000099'
        with self.assertRaisesRegex(ValueError, 'association changed'):
            execute(scoped_fixture(), 'test', call=aws)
        self.assertEqual(aws.writes, [])

    def test_nonselected_spoke_cannot_move(self):
        plan = scoped_fixture();plan['variables']['isolated_inspection_spokes']['value'] = []
        with self.assertRaisesRegex(ValueError, 'selected spoke'):
            association_changes(plan)


if __name__ == "__main__":
    unittest.main()
