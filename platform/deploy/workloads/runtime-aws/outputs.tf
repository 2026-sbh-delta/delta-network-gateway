output "deployer_instance_id" { value = aws_instance.deployer.id }
output "deployer_private_ip" { value = aws_instance.deployer.private_ip }
output "deployer_role" { value = aws_iam_role.role["deployer"].name }
output "customer_subnet_id" { value = aws_subnet.customer.id }
output "customer_security_group_id" { value = aws_security_group.role["customer"].id }
output "customer_instance_profile" { value = aws_iam_instance_profile.role["customer"].name }
output "database_subnet_ids" { value = [for s in aws_subnet.database : s.id] }
output "database_security_group_id" { value = aws_security_group.role["database"].id }
