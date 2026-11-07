module "sample_app" {
  count                        = var.application_enabled ? 1 : 0
  source                       = "./modules/sample-app"
  prefix                       = "${var.name}-${var.environment}-notes"
  region                       = var.region
  account_id                   = var.workload_account_id
  vpc_id                       = aws_vpc.client.id
  vpc_cidr                     = var.vpc_cidr
  existing_subnet_cidrs        = [for subnet in local.subnets : subnet.cidr]
  management_security_group_id = aws_security_group.management.id
  management_endpoint_id       = aws_ec2_instance_connect_endpoint.management.id
  instance_az_id               = var.client_az_id
  internet_firewall_endpoints  = local.internet_inspection_active ? local.internet_endpoint_ids : {}
  remote_service_cidrs         = var.application_remote_cidrs
  remote_service_ports         = var.application_remote_ports
  workload_subnets = { for az_id in keys(var.azs) : az_id => {
    id             = aws_subnet.private["${az_id}/client"].id
    route_table_id = aws_route_table.private["${az_id}/client"].id
    az_name        = data.aws_availability_zone.selected[az_id].name
  } }
  config = var.application
}

output "application" {
  description = "Sample app deployment inventory; null when disabled. Contains secret ARNs, never secret values."
  value       = var.application_enabled ? module.sample_app[0].deployment : null
}
