output "scanners" {
  value = { for name, vm in aws_instance.scanner : name => {
    instance_id       = vm.id, private_ip = vm.private_ip, api_url = "https://${vm.private_ip}",
    security_group_id = aws_security_group.scanner[name].id, data_volume_id = aws_ebs_volume.data[name].id
  } }
}
output "account_id" { value = var.account_id }
output "region" { value = var.region }
