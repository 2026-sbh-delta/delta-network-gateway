output "sast_url" { value = "https://${data.aws_instance.tooling.private_ip}" }
output "tooling_instance_id" { value = var.tooling_instance_id }
output "platform_instance_id" { value = var.platform_instance_id }
output "region" { value = var.region }
output "account_id" { value = var.account_id }
output "peering_id" { value = aws_vpc_peering_connection.sast.id }
output "dast_sandbox_url" { value = var.dast_sandbox == null ? null : "http://${data.aws_lb.dast[0].dns_name}" }
