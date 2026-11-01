output "project_id" {
  description = "Verified authorized project ID."
  value       = data.google_project.current.project_id
}
output "project_number" {
  description = "Verified authorized project number."
  value       = tostring(data.google_project.current.number)
}
output "region" {
  description = "GCP region."
  value       = var.region
}
output "network_id" {
  description = "New custom VPC network ID."
  value       = google_compute_network.server.id
}
output "server_subnet_cidr" {
  description = "Only GCP prefix advertised to AWS; not the reserved /16."
  value       = google_compute_subnetwork.server.ip_cidr_range
}
output "server" {
  description = "Private server identity and IAP management target. No external IP."
  value = {
    name       = google_compute_instance.server.name
    zone       = google_compute_instance.server.zone
    private_ip = google_compute_address.server.address
    test_ports = [8080, 8081]
  }
}
output "gateway_ips_by_interface" {
  description = "HA VPN transport IPs by explicit interface ID, independent of interface list order."
  value       = { for nic in google_compute_ha_vpn_gateway.aws.vpn_interfaces : tostring(nic.id) => nic.ip_address }
  precondition {
    condition     = toset([for nic in google_compute_ha_vpn_gateway.aws.vpn_interfaces : nic.id]) == toset([0, 1])
    error_message = "HA VPN must expose exactly interface IDs 0 and 1."
  }
}
output "aws_vpn_handoff" {
  description = "Nonsecret central input; inside CIDRs must be selected after TGW overlap checks."
  value = {
    network = { kind = "gcp", cidrs = [var.server_subnet_cidr] }
    peers = { for nic in google_compute_ha_vpn_gateway.aws.vpn_interfaces : "gcp_if${nic.id}" => {
      public_ip     = nic.ip_address
      bgp_asn       = var.router_asn
      gcp_interface = nic.id
    } }
  }
}
output "router_name" {
  description = "Cloud Router name for BGP status and learned-route checks."
  value       = google_compute_router.aws.name
}
output "tunnel_names" {
  description = "Configured GCP tunnel names by stable input key; no PSKs or PSK hashes."
  value       = { for key, tunnel in google_compute_vpn_tunnel.aws : key => tunnel.name }
}
