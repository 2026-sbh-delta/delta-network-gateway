output "deployment" {
  description = "Non-secret runtime/deployment inventory. Secret ARNs are identifiers only."
  value = {
    name                   = local.prefix
    region                 = var.region
    account_id             = var.account_id
    vpc_id                 = var.vpc_id
    management_endpoint_id = var.management_endpoint_id
    url                    = "${var.config.certificate_arn == null ? "http" : "https"}://${coalesce(var.config.hostname, aws_lb.app.dns_name)}"
    artifact_bucket        = aws_s3_bucket.artifacts.id
    instance_architecture  = data.aws_ami.selected.architecture
    deployment_mode        = var.config.deployment_mode
    service_hosts          = { for role in local.services : role => local.compose ? "frontend" : role }
    instances = { for role, instance in aws_instance.app : role => {
      id            = instance.id, private_ip = instance.private_ip, az = instance.availability_zone,
      instance_type = instance.instance_type, public_ip_enabled = instance.associate_public_ip_address
    } }
    database = {
      identifier          = aws_db_instance.app.identifier, endpoint = aws_db_instance.app.address, port = aws_db_instance.app.port,
      name                = aws_db_instance.app.db_name, instance_class = aws_db_instance.app.instance_class,
      master_secret_arn   = aws_db_instance.app.master_user_secret[0].secret_arn,
      app_secret_arn      = aws_secretsmanager_secret.app.arn, app_user = "delta_notes_app",
      publicly_accessible = aws_db_instance.app.publicly_accessible, encrypted = aws_db_instance.app.storage_encrypted
    }
    logs = { for role, group in aws_cloudwatch_log_group.app : role => group.name }
  }
}

output "inspection_network" {
  description = "Existing IGW and public subnet/route-table identities for shared firewall routing; no runtime resource dependency."
  value = {
    internet_gateway_id = aws_internet_gateway.app.id
    public_subnets = { for az, subnet in aws_subnet.public : az => {
      id = subnet.id, cidr = subnet.cidr_block, route_table_id = aws_route_table.public[az].id
    } }
  }
}
