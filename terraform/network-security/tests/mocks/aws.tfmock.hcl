# All values in this directory are synthetic test fixtures, never live inputs.
mock_data "aws_caller_identity" {
  defaults = { account_id = "111122223333" }
}
mock_data "aws_partition" {
  defaults = { partition = "aws" }
}
mock_data "aws_availability_zone" {
  defaults = { region = "ap-northeast-2", zone_type = "availability-zone" }
}
mock_data "aws_ec2_transit_gateway_vpc_attachment" {
  defaults = {
    transit_gateway_id = "tgw-00000000000000001"
    vpc_owner_id       = "444455556666"
  }
}
mock_resource "aws_ec2_transit_gateway" {
  defaults = {
    id  = "tgw-00000000000000001"
    arn = "arn:aws:ec2:ap-northeast-2:111122223333:transit-gateway/tgw-00000000000000001"
  }
}
mock_resource "aws_ec2_transit_gateway_vpc_attachment" {
  defaults = { id = "tgw-attach-00000000000000001" }
}
mock_resource "aws_cloudwatch_log_group" {
  defaults = { arn = "arn:aws:logs:ap-northeast-2:111122223333:log-group:mock-firewall" }
}
mock_resource "aws_networkfirewall_firewall" {
  defaults = {
    arn = "arn:aws:network-firewall:ap-northeast-2:111122223333:firewall/mock"
    firewall_status = [{
      sync_states = [
        {
          availability_zone = "ap-northeast-2a"
          attachment = [{
            endpoint_id = "vpce-00000000000000002"
            subnet_id   = "subnet-00000000000000002"
          }]
        },
        {
          availability_zone = "ap-northeast-2d"
          attachment = [{
            endpoint_id = "vpce-00000000000000001"
            subnet_id   = "subnet-00000000000000001"
          }]
        }
      ]
    }]
  }
}
