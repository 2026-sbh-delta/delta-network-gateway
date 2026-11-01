output "workload_account_id" {
  description = "Verified workload account ID."
  value       = data.aws_caller_identity.current.account_id
}
output "region" {
  description = "Workload Region."
  value       = var.region
}
output "az_ids" {
  description = "Physical AZ IDs for the VPC attachment."
  value       = sort(keys(var.azs))
}
output "vpc_id" {
  description = "Private workload VPC ID."
  value       = aws_vpc.server.id
}
output "vpc_cidr" {
  description = "Workload VPC CIDR."
  value       = var.vpc_cidr
}
output "subnet_ids_by_az_id" {
  description = "TGW/server subnet IDs indexed by physical AZ ID."
  value       = { for id in keys(var.azs) : id => { tgw = aws_subnet.private["${id}/tgw"].id, server = aws_subnet.private["${id}/server"].id } }
}
output "route_table_ids_by_az_id" {
  description = "TGW/server route-table IDs indexed by physical AZ ID."
  value       = { for id in keys(var.azs) : id => { tgw = aws_route_table.private["${id}/tgw"].id, server = aws_route_table.private["${id}/server"].id } }
}
output "server_instance_id" {
  description = "Single private EC2 server ID."
  value       = aws_instance.server.id
}
output "server_private_ip" {
  description = "Server private IPv4 for traffic/log correlation."
  value       = aws_instance.server.private_ip
}
output "management_endpoint_id" {
  description = "EC2 Instance Connect Endpoint ID, distinct from ANF endpoints."
  value       = aws_ec2_instance_connect_endpoint.management.id
}
output "central_handoff" {
  description = "Non-secret central spoke_attachments input; empty until the attachment is enabled. reported_state is read live before onboarding."
  value = var.transit_gateway_id == null ? {} : {
    aws_server = {
      account_id      = var.workload_account_id
      role            = "Backend"
      vpc_cidrs       = [var.vpc_cidr]
      attachment_id   = aws_ec2_transit_gateway_vpc_attachment.server[0].id
      acceptance_mode = "accept"
      az_ids          = sort(keys(var.azs))
    }
  }
}

output "test_ports" {
  description = "Both server listeners; the central ANF permits only the selected allowed control."
  value       = [8080, 8081]
}
