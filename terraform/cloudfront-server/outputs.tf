output "workload_account_id" {
  description = "Verified Workload account."
  value       = data.aws_caller_identity.current.account_id
}
output "region" {
  description = "Internal ALB and Server Region."
  value       = var.region
}
output "distribution_id" {
  description = "CloudFront distribution ID."
  value       = aws_cloudfront_distribution.server.id
}
output "distribution_arn" {
  description = "CloudFront distribution ARN."
  value       = aws_cloudfront_distribution.server.arn
}
output "domain_name" {
  description = "Public CloudFront hostname."
  value       = aws_cloudfront_distribution.server.domain_name
}
output "health_url" {
  description = "Public HTTPS test URL."
  value       = "https://${aws_cloudfront_distribution.server.domain_name}/health"
}
output "vpc_origin_id" {
  description = "Internal ALB VPC Origin ID."
  value       = aws_cloudfront_vpc_origin.alb.id
}
output "internet_gateway_id" {
  description = "CloudFront prerequisite IGW attachment, without internet routes."
  value       = aws_internet_gateway.ingress.id
}
output "cloudfront_service_security_group_id" {
  description = "AWS-owned VPC Origin service SG; never edited by this root."
  value       = data.aws_security_group.alb_cloudfront_service.id
}
output "site_url" {
  description = "Public HTTPS two-button demo page."
  value       = "https://${aws_cloudfront_distribution.server.domain_name}/"
}
output "alb_arn" {
  description = "Internal ALB ARN."
  value       = aws_lb.alb.arn
}
output "target_group_arns" {
  description = "Allow and deny target groups; both use health port 8080."
  value       = { for key, group in aws_lb_target_group.test : key => group.arn }
}
output "central_allowed_flow" {
  description = "Merge this into existing central allowed_flows; it never permits port 8081."
  value = {
    cloudfront_alb_to_server = {
      source_cidrs      = sort(values(var.alb_subnets))
      destination_cidrs = ["${var.expected_server_private_ip}/32"]
      protocol          = "tcp"
      destination_ports = [8080]
      sid               = 1000002
    }
  }
}
output "alb_subnet_ids" {
  description = "Dedicated internal ALB subnet IDs by physical AZ ID."
  value       = { for az, subnet in aws_subnet.alb : az => subnet.id }
}
output "site_bucket_name" {
  description = "Private OAC-only static site bucket."
  value       = aws_s3_bucket.site.id
}
output "server_instance_id" {
  description = "Existing, unchanged EC2 instance ID."
  value       = data.aws_instance.server.id
}
output "bootstrap_origin_access" {
  description = "Must be false after initial deployment/hardening."
  value       = var.bootstrap_origin_access
}
