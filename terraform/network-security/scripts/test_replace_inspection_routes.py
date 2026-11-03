import copy
import unittest

from replace_inspection_routes import execute, route_changes


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


if __name__ == "__main__":
    unittest.main()
