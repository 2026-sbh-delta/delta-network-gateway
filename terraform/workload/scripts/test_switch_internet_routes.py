import copy
import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

from switch_internet_routes import aws_json, switch


def fixture():
    info = {
        'vpc_id': 'vpc-board', 'firewall_arn': 'arn:firewall', 'internet_gateway_id': 'igw-original',
        'ingress_route_table_id': 'rtb-edge',
        'endpoint_ids_by_az_id': {'az1': 'vpce-one', 'az2': 'vpce-two'},
        'association_arns': {'az1': 'association-one', 'az2': 'association-two'},
        'az_names': {'az1': 'account-zone-one', 'az2': 'account-zone-two'},
        'firewall_subnet_ids': {'az1': 'subnet-fw-one', 'az2': 'subnet-fw-two'},
        'firewall_route_tables': {'az1': 'rtb-fw-one', 'az2': 'rtb-fw-two'},
        'public_subnets': {'az1': {'id': 'subnet-public-one', 'cidr': '10.10.10.0/24', 'route_table_id': 'rtb-public-one'},
                           'az2': {'id': 'subnet-public-two', 'cidr': '10.10.11.0/24', 'route_table_id': 'rtb-public-two'}}}
    changes = [{'address': 'aws_route_table_association.internet_ingress[0]', 'change': {
        'actions': ['create'], 'before': None, 'after': {'gateway_id': 'igw-original', 'route_table_id': 'rtb-edge'}}}]
    for az, subnet in info['public_subnets'].items():
        before = {'route_table_id': subnet['route_table_id'], 'destination_cidr_block': '0.0.0.0/0', 'gateway_id': 'igw-original'}
        after = dict(before, gateway_id=None, vpc_endpoint_id=info['endpoint_ids_by_az_id'][az])
        changes.append({'address': f'module.sample_app[0].aws_route.public["{az}"]',
                        'change': {'actions': ['update'], 'before': before, 'after': after}})
    return {'variables': {key: {'value': value} for key, value in {
        'internet_inspection': {'mode': 'enabled', 'expected_vpc_id': 'vpc-board', 'subnet_cidrs': {'az1': '', 'az2': ''}},
        'region': 'ap-northeast-2', 'workload_account_id': '616723161368', 'vpc_cidr': '10.10.0.0/16',
        'vpc_name': 'delta-board-vpc'}.items()},
        'planned_values': {'outputs': {'internet_inspection': {'value': info}}}, 'resource_changes': changes}


class FakeAWS:
    def __init__(self):
        self.info = fixture()['planned_values']['outputs']['internet_inspection']['value']
        self.public_targets = {'rtb-public-one': 'igw-original', 'rtb-public-two': 'igw-original'}
        self.edge_association = None
        self.writes = []
        self.fail_route = None
        self.fail_association_after_commit = False
        self.ready = True
        self.name = 'delta-board-vpc'
        self.account = '616723161368'
        self.endpoint_route_field = 'GatewayId'
        self.stale_subnet = False

    def __call__(self, profile, region, service, operation, *args):
        if operation == 'get-caller-identity':
            return {'Account': self.account}
        if operation == 'describe-vpcs':
            return {'Vpcs': [{'CidrBlock': '10.10.0.0/16', 'Tags': [{'Key': 'Name', 'Value': self.name}]}]}
        if operation == 'describe-firewall':
            return {'FirewallStatus': {'Status': 'READY', 'ConfigurationSyncStateSummary': 'IN_SYNC'}}
        if operation == 'describe-vpc-endpoint-association':
            arn = args[1]
            az = next(k for k, v in self.info['association_arns'].items() if v == arn)
            return {'VpcEndpointAssociation': {'VpcId': 'vpc-board', 'FirewallArn': 'arn:firewall'},
                    'VpcEndpointAssociationStatus': {'Status': 'READY' if self.ready else 'PROVISIONING',
                    'AssociationSyncState': {self.info['az_names'][az]: {'Attachment': {
                        'Status': 'READY', 'SubnetId': self.info['firewall_subnet_ids'][az],
                        'EndpointId': self.info['endpoint_ids_by_az_id'][az]}}}}}
        if operation == 'describe-route-tables':
            tables = []
            for table, target in self.public_targets.items():
                key = 'GatewayId' if target.startswith('igw-') else self.endpoint_route_field
                subnet = next(s['id'] for s in self.info['public_subnets'].values() if s['route_table_id'] == table)
                tables.append({'RouteTableId': table, 'Associations': [] if self.stale_subnet else [{'SubnetId': subnet, 'AssociationState': {'State': 'associated'}}], 'Routes': [{'DestinationCidrBlock': '0.0.0.0/0', key: target, 'State': 'active'}]})
            for az, table in self.info['firewall_route_tables'].items():
                tables.append({'RouteTableId': table, 'Associations': [{'SubnetId': self.info['firewall_subnet_ids'][az], 'AssociationState': {'State': 'associated'}}], 'Routes': [{'DestinationCidrBlock': '0.0.0.0/0', 'GatewayId': 'igw-original', 'State': 'active'}]})
            tables.append({'RouteTableId': 'rtb-edge', 'Associations': [] if self.edge_association is None else [self.edge_association],
                           'Routes': [{'DestinationCidrBlock': subnet['cidr'], self.endpoint_route_field: self.info['endpoint_ids_by_az_id'][az], 'State': 'active'}
                                      for az, subnet in self.info['public_subnets'].items()]})
            return {'RouteTables': copy.deepcopy(tables)}
        if operation == 'replace-route':
            table, target = args[1], args[-1]
            self.writes.append((operation, table, target))
            if self.fail_route == table:
                self.fail_route = None
                raise RuntimeError('Injected second route failure')
            self.public_targets[table] = target
            return {}
        if operation == 'associate-route-table':
            self.writes.append((operation,))
            self.edge_association = {'GatewayId': 'igw-original', 'RouteTableAssociationId': 'rtbassoc-new', 'AssociationState': {'State': 'associated'}}
            if self.fail_association_after_commit:
                raise RuntimeError('Ambiguous association response')
            return {'AssociationId': 'rtbassoc-new'}
        if operation == 'disassociate-route-table':
            self.writes.append((operation,))
            self.edge_association = None
            return {}
        raise AssertionError(operation)


class InternetCutoverTests(unittest.TestCase):
    def test_successful_cli_mutation_can_have_an_empty_response(self):
        with patch('switch_internet_routes.subprocess.run', return_value=CompletedProcess([], 0, '', '')):
            self.assertEqual(aws_json('workload', 'region', 'ec2', 'replace-route'), {})

    def test_cli_failure_is_never_treated_as_empty_success(self):
        with patch('switch_internet_routes.subprocess.run', return_value=CompletedProcess([], 1, '', 'Denied')):
            with self.assertRaisesRegex(RuntimeError, 'Denied'):
                aws_json('workload', 'region', 'ec2', 'replace-route')

    def test_dry_run_cannot_change_routes(self):
        aws = FakeAWS()
        result = switch(fixture(), 'workload', 'central', call=aws)
        self.assertFalse(result['execute'])
        self.assertEqual(aws.writes, [])

    def test_switches_both_routes_and_inserts_edge(self):
        aws = FakeAWS()
        result = switch(fixture(), 'workload', 'central', call=aws, execute=True)
        self.assertEqual(aws.public_targets, {'rtb-public-one': 'vpce-one', 'rtb-public-two': 'vpce-two'})
        self.assertEqual(result['import_id'], 'igw-original/rtb-edge')
        self.assertEqual(result['routes_changed'], 2)

    def test_second_route_failure_restores_both_original_defaults(self):
        aws = FakeAWS();aws.fail_route = 'rtb-public-two'
        with self.assertRaisesRegex(RuntimeError, 'second route'):
            switch(fixture(), 'workload', 'central', call=aws, execute=True)
        self.assertEqual(set(aws.public_targets.values()), {'igw-original'})
        self.assertIsNone(aws.edge_association)

    def test_ambiguous_association_failure_restores_paired_paths(self):
        aws = FakeAWS();aws.fail_association_after_commit = True
        with self.assertRaisesRegex(RuntimeError, 'Ambiguous'):
            switch(fixture(), 'workload', 'central', call=aws, execute=True)
        self.assertEqual(set(aws.public_targets.values()), {'igw-original'})
        self.assertIsNone(aws.edge_association)

    def test_unready_endpoint_prevents_every_write(self):
        aws = FakeAWS();aws.ready = False
        with self.assertRaisesRegex(ValueError, 'Secondary endpoint'):
            switch(fixture(), 'workload', 'central', call=aws, execute=True)
        self.assertEqual(aws.writes, [])

    def test_changed_vpc_or_account_prevents_every_write(self):
        for field, value in [('name', 'other-vpc'), ('account', '999900001111')]:
            with self.subTest(field=field):
                aws = FakeAWS();setattr(aws, field, value)
                with self.assertRaises(ValueError):
                    switch(fixture(), 'workload', 'central', call=aws, execute=True)
                self.assertEqual(aws.writes, [])

    def test_stale_public_route_prevents_every_write(self):
        aws = FakeAWS();aws.public_targets['rtb-public-two'] = 'vpce-unrelated'
        with self.assertRaisesRegex(ValueError, 'Re-plan'):
            switch(fixture(), 'workload', 'central', call=aws, execute=True)
        self.assertEqual(aws.writes, [])

    def test_another_changed_resource_is_rejected(self):
        plan = fixture();plan['resource_changes'].append({'address': 'aws_vpc.other', 'change': {'actions': ['delete']}})
        aws = FakeAWS()
        with self.assertRaisesRegex(ValueError, 'Unexpected resource'):
            switch(plan, 'workload', 'central', call=aws, execute=True)
        self.assertEqual(aws.writes, [])

    def test_wrong_az_endpoint_in_plan_is_rejected(self):
        plan = fixture();plan['resource_changes'][1]['change']['after']['vpc_endpoint_id'] = 'vpce-two'
        aws = FakeAWS()
        with self.assertRaisesRegex(ValueError, 'same-AZ'):
            switch(plan, 'workload', 'central', call=aws, execute=True)
        self.assertEqual(aws.writes, [])

    def test_endpoint_id_field_variant_is_supported(self):
        aws = FakeAWS();aws.endpoint_route_field = 'VpcEndpointId'
        self.assertTrue(switch(fixture(), 'workload', 'central', call=aws, execute=True)['execute'])

    def test_stale_subnet_association_prevents_every_write(self):
        aws = FakeAWS();aws.stale_subnet = True
        with self.assertRaisesRegex(ValueError, 'subnet association'):
            switch(fixture(), 'workload', 'central', call=aws, execute=True)
        self.assertEqual(aws.writes, [])


if __name__ == '__main__':
    unittest.main()
