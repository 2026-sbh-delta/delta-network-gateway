#!/usr/bin/env python3
"""Insert same-AZ shared firewall endpoints using a reviewed Terraform plan.

Dry-run by default. After execution import the returned edge association and
discard the old plan. Existing route next hops are replaced, never deleted.
"""

import argparse
import json
import subprocess
import sys
import time


def aws_json(profile, region, service, operation, *args):
    result = subprocess.run(['aws', '--profile', profile, '--region', region, '--no-cli-pager',
                             service, operation, *args, '--output', 'json'], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else {}


def reviewed_changes(plan):
    config = plan['variables']['internet_inspection']['value']
    info = plan['planned_values']['outputs']['internet_inspection']['value']
    if config['mode'] != 'enabled' or info['vpc_id'] != config['expected_vpc_id']:
        raise ValueError('Only a reviewed enabled plan for the expected VPC is supported.')
    if set(info['endpoint_ids_by_az_id']) != set(config['subnet_cidrs']):
        raise ValueError('Every physical AZ requires a known endpoint.')
    routes = []
    for item in plan.get('resource_changes', []):
        change = item['change']
        if change['actions'] == ['no-op']:
            continue
        if item['address'] == 'aws_route_table_association.internet_ingress[0]':
            if change['actions'] != ['create']:
                raise ValueError('Existing edge association must not be replaced or deleted.')
            if change['after']['gateway_id'] != info['internet_gateway_id'] or change['after']['route_table_id'] != info['ingress_route_table_id']:
                raise ValueError('Edge association differs from reviewed output.')
            continue
        if not item['address'].startswith('module.sample_app[0].aws_route.public['):
            raise ValueError('Unexpected resource change in cutover plan: ' + item['address'])
        if change['actions'] not in (['update'], ['delete', 'create']):
            raise ValueError('Expected existing public default route update.')
        before, after = change['before'], change['after']
        azs = [az for az, subnet in info['public_subnets'].items() if subnet['route_table_id'] == after['route_table_id']]
        if len(azs) != 1:
            raise ValueError('Public route table must belong to one physical AZ.')
        az = azs[0]
        if (before['route_table_id'] != after['route_table_id'] or
                before['destination_cidr_block'] != '0.0.0.0/0' or after['destination_cidr_block'] != '0.0.0.0/0' or
                before.get('gateway_id') != info['internet_gateway_id'] or
                after.get('vpc_endpoint_id') != info['endpoint_ids_by_az_id'][az]):
            raise ValueError('Only IGW-to-same-AZ endpoint default changes are supported.')
        routes.append({'az': az, 'table': after['route_table_id'], 'before': before['gateway_id'], 'after': after['vpc_endpoint_id']})
    if len(routes) != len(info['endpoint_ids_by_az_id']):
        raise ValueError('The cutover must include every public AZ route.')
    return info, routes


def switch(plan, profile, firewall_profile, call=aws_json, execute=False):
    info, routes = reviewed_changes(plan)
    region = plan['variables']['region']['value']

    def aws(service, operation, *args):
        return call(profile, region, service, operation, *args)

    if aws('sts', 'get-caller-identity')['Account'] != plan['variables']['workload_account_id']['value']:
        raise ValueError('Authenticated account differs from reviewed workload account.')
    vpc = aws('ec2', 'describe-vpcs', '--vpc-ids', info['vpc_id'])['Vpcs'][0]
    if vpc['CidrBlock'] != plan['variables']['vpc_cidr']['value'] or not any(
            t['Key'] == 'Name' and t['Value'] == plan['variables']['vpc_name']['value'] for t in vpc.get('Tags', [])):
        raise ValueError('Live VPC name/CIDR differs from reviewed scope.')
    status = call(firewall_profile, region, 'network-firewall', 'describe-firewall',
                  '--firewall-arn', info['firewall_arn'])['FirewallStatus']
    if status['Status'] != 'READY' or status['ConfigurationSyncStateSummary'] != 'IN_SYNC':
        raise ValueError('Primary firewall must be READY and IN_SYNC.')
    for az, arn in info['association_arns'].items():
        reply = aws('network-firewall', 'describe-vpc-endpoint-association', '--vpc-endpoint-association-arn', arn)
        association, state = reply['VpcEndpointAssociation'], reply['VpcEndpointAssociationStatus']
        attachment = state.get('AssociationSyncState', {}).get(info['az_names'][az], {}).get('Attachment', {})
        if (association['VpcId'] != info['vpc_id'] or association['FirewallArn'] != info['firewall_arn'] or
                state['Status'] != 'READY' or attachment.get('Status') != 'READY' or
                attachment.get('SubnetId') != info['firewall_subnet_ids'][az] or
                attachment.get('EndpointId') != info['endpoint_ids_by_az_id'][az]):
            raise ValueError('Secondary endpoint is not READY in the reviewed VPC/subnet/AZ.')

    def tables():
        return {t['RouteTableId']: t for t in aws('ec2', 'describe-route-tables', '--filters',
                'Name=vpc-id,Values=' + info['vpc_id'])['RouteTables']}

    def default(table):
        selected = [r for r in table['Routes'] if r.get('DestinationCidrBlock') == '0.0.0.0/0']
        if len(selected) != 1 or selected[0]['State'] != 'active':
            raise ValueError('Exactly one active default route is required.')
        route = selected[0]
        return route.get('GatewayId') or route.get('VpcEndpointId')

    def edge_association(current):
        selected = [(table['RouteTableId'], a) for table in current.values() for a in table.get('Associations', [])
                    if a.get('GatewayId') == info['internet_gateway_id']]
        if len(selected) > 1:
            raise ValueError('Unexpected multiple IGW edge associations.')
        if selected and selected[0][0] != info['ingress_route_table_id']:
            raise ValueError('IGW already has an unrelated edge route table.')
        return selected[0][1]['RouteTableAssociationId'] if selected else None

    current = tables()
    previous_association = edge_association(current)
    snapshots = [(route, default(current[route['table']])) for route in routes]
    if any(target not in (route['before'], route['after']) for route, target in snapshots):
        raise ValueError('A public route changed after planning. Re-plan before switching.')
    edge = current[info['ingress_route_table_id']]
    for az, subnet in info['public_subnets'].items():
        public_table = current[subnet['route_table_id']]
        if not any(a.get('SubnetId') == subnet['id'] and a.get('AssociationState', {}).get('State') == 'associated'
                   for a in public_table.get('Associations', [])):
            raise ValueError('The public subnet association changed after planning.')
        matches = [r for r in edge['Routes'] if r.get('DestinationCidrBlock') == subnet['cidr']]
        if len(matches) != 1 or (matches[0].get('VpcEndpointId') or matches[0].get('GatewayId')) != info['endpoint_ids_by_az_id'][az] or matches[0]['State'] != 'active':
            raise ValueError('Prepared ingress route must match the public subnet and its AZ endpoint.')
        firewall_table = current[info['firewall_route_tables'][az]]
        if not any(a.get('SubnetId') == info['firewall_subnet_ids'][az] and a.get('AssociationState', {}).get('State') == 'associated'
                   for a in firewall_table.get('Associations', [])):
            raise ValueError('The firewall subnet association changed after planning.')
        if default(firewall_table) != info['internet_gateway_id']:
            raise ValueError('Firewall return route must use the original IGW.')
    if not execute:
        return {'execute': False, 'vpc_id': info['vpc_id'], 'routes': routes, 'edge_route_table': info['ingress_route_table_id']}

    def replace(route, target):
        key = '--gateway-id' if target.startswith('igw-') else '--vpc-endpoint-id'
        aws('ec2', 'replace-route', '--route-table-id', route['table'], '--destination-cidr-block', '0.0.0.0/0', key, target)

    changed = []
    try:
        for route, target in snapshots:
            if target != route['after']:
                changed.append((route, target))
                replace(route, route['after'])
        if previous_association is None:
            aws('ec2', 'associate-route-table', '--route-table-id', info['ingress_route_table_id'], '--gateway-id', info['internet_gateway_id'])
        for attempt in range(15):
            current = tables()
            association_id = edge_association(current)
            associated = any(a.get('RouteTableAssociationId') == association_id and
                             a.get('AssociationState', {}).get('State') == 'associated'
                             for a in current[info['ingress_route_table_id']].get('Associations', []))
            if associated and all(default(current[r['table']]) == r['after'] for r in routes):
                break
            if attempt == 14:
                raise RuntimeError('Paired ingress/egress cutover did not converge.')
            time.sleep(1)
    except Exception:
        errors = []
        for route, target in reversed(changed):
            try:
                replace(route, target)
            except Exception as error:
                errors.append(str(error))
        if previous_association is None:
            try:
                association_id = edge_association(tables())
                if association_id:
                    aws('ec2', 'disassociate-route-table', '--association-id', association_id)
            except Exception as error:
                errors.append(str(error))
        try:
            restored = tables()
            if any(default(restored[route['table']]) != target for route, target in snapshots):
                errors.append('A public default did not return to its original target.')
            if edge_association(restored) != previous_association:
                errors.append('The original IGW edge association was not restored.')
        except Exception as error:
            errors.append(str(error))
        if errors:
            print('Rollback needs inspection: ' + '; '.join(errors), file=sys.stderr)
        raise
    return {'execute': True, 'routes_changed': len(changed), 'association_id': association_id,
            'import_id': info['internet_gateway_id'] + '/' + info['ingress_route_table_id']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan_json');parser.add_argument('--profile', required=True)
    parser.add_argument('--firewall-profile', required=True);parser.add_argument('--execute', action='store_true')
    a = parser.parse_args()
    try:
        with open(a.plan_json) as f:plan = json.load(f)
        print(json.dumps(switch(plan, a.profile, a.firewall_profile, execute=a.execute), indent=2))
    except (KeyError, ValueError, RuntimeError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
