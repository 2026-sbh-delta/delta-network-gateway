output "inspection_mode" {
  description = "Configured GCP inspection and endpoint cost mode."
  value       = var.inspection_mode
}

output "project" {
  description = "Verified GCP identity."
  value       = { id = var.project_id, number = data.google_project.current.number }
}
output "networks" {
  description = "Separate central detection/connectivity and workload VPC IDs."
  value       = { for key, network in google_compute_network.vpc : key => network.id }
}
output "ncc_hub" {
  description = "NCC hub ID."
  value       = google_network_connectivity_hub.central.id
}
output "firewall_endpoint" {
  description = "Project-scoped shared managed endpoint, not a VM in the central VPC."
  value       = try(google_network_security_firewall_endpoint.central[0].id, null)
}
output "gateway_handoff" {
  description = "Nonsecret values for the AWS two-peer VPN input contract."
  value       = { project_id = var.project_id, project_number = var.project_number, router_asn = var.router_asn, remote_cidrs = local.advertised_vpn_cidrs, aws_cidrs = sort(tolist(var.aws_cidrs)), interfaces = google_compute_ha_vpn_gateway.aws.vpn_interfaces }
}
output "application_network" {
  description = "Existing VPC and distinct application subnet IDs/CIDRs for the workload-gcp root."
  value = {
    network = google_compute_network.vpc["workload"].id
    subnets = merge({ frontend = google_compute_subnetwork.subnet["workload"].id }, { for k, v in google_compute_subnetwork.application : k => v.id })
    cidrs   = merge({ frontend = var.subnets.workload }, var.application_subnets)
  }
}
