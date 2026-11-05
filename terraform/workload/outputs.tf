output "workload_account_id" {
  description = "Verified workload account ID."
  value       = data.aws_caller_identity.current.account_id
}

output "guardduty" {
  description = "Account/Region detector identity and managed protection states; no finding contents or credentials."
  value = {
    account_id                   = data.aws_caller_identity.current.account_id
    region                       = var.region
    detector_id                  = aws_guardduty_detector.account.id
    arn                          = aws_guardduty_detector.account.arn
    enabled                      = aws_guardduty_detector.account.enable
    finding_publishing_frequency = aws_guardduty_detector.account.finding_publishing_frequency
    features = { for name, feature in aws_guardduty_detector_feature.protection : name => {
      status                   = feature.status
      additional_configuration = { for config in feature.additional_configuration : config.name => config.status }
    } }
  }
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
  value       = aws_vpc.client.id
}
output "vpc_cidr" {
  description = "Workload VPC CIDR."
  value       = var.vpc_cidr
}
output "subnet_ids_by_az_id" {
  description = "TGW/client subnet IDs indexed by physical AZ ID."
  value       = { for id in keys(var.azs) : id => { tgw = aws_subnet.private["${id}/tgw"].id, client = aws_subnet.private["${id}/client"].id } }
}
output "route_table_ids_by_az_id" {
  description = "TGW/client route-table IDs indexed by physical AZ ID."
  value       = { for id in keys(var.azs) : id => { tgw = aws_route_table.private["${id}/tgw"].id, client = aws_route_table.private["${id}/client"].id } }
}
output "management_endpoint_id" {
  description = "EC2 Instance Connect Endpoint ID, distinct from ANF endpoints."
  value       = aws_ec2_instance_connect_endpoint.management.id
}
output "central_handoff" {
  description = "Non-secret central spoke_attachments input; empty until the attachment is enabled. reported_state is read live before onboarding."
  value = var.transit_gateway_id == null ? {} : {
    aws_client = {
      account_id      = var.workload_account_id
      role            = "Client"
      vpc_cidrs       = [var.vpc_cidr]
      attachment_id   = aws_ec2_transit_gateway_vpc_attachment.client[0].id
      acceptance_mode = "accept"
      az_ids          = sort(keys(var.azs))
    }
  }
}
