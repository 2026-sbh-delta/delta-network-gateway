locals {
  prefix   = substr(var.prefix, 0, 24)
  services = toset(["frontend", "backend"])
  cidrs    = concat(var.existing_subnet_cidrs, values(var.config.public_subnet_cidrs), values(var.config.db_subnet_cidrs))
  ranges = [for cidr in local.cidrs : {
    start = try(sum([for i, octet in split(".", cidrhost(cidr, 0)) : tonumber(octet) * pow(256, 3 - i)]), -1)
    end   = try(sum([for i, octet in split(".", cidrhost(cidr, -1)) : tonumber(octet) * pow(256, 3 - i)]), -1)
  }]
  vpc_start = sum([for i, octet in split(".", cidrhost(var.vpc_cidr, 0)) : tonumber(octet) * pow(256, 3 - i)])
  vpc_end   = sum([for i, octet in split(".", cidrhost(var.vpc_cidr, -1)) : tonumber(octet) * pow(256, 3 - i)])
}

data "aws_ssm_parameter" "al2023" {
  count = var.config.ami_id == null ? 1 : 0
  name  = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}
data "aws_ami" "selected" {
  owners = ["amazon"]
  filter {
    name   = "image-id"
    values = [var.config.ami_id == null ? nonsensitive(data.aws_ssm_parameter.al2023[0].value) : var.config.ami_id]
  }
}
data "aws_ec2_instance_type" "selected" {
  instance_type = var.config.instance_type
}

resource "terraform_data" "contract" {
  input = "delta-sample-app-x86-v1"
  lifecycle {
    precondition {
      condition = (
        length(var.workload_subnets) == 2 &&
        toset(keys(var.config.public_subnet_cidrs)) == toset(keys(var.workload_subnets)) &&
        toset(keys(var.config.db_subnet_cidrs)) == toset(keys(var.workload_subnets)) &&
        contains(keys(var.workload_subnets), var.instance_az_id)
      )
      error_message = "Public and DB subnet maps must cover the existing two AZ IDs; instance AZ must be registered."
    }
    precondition {
      condition = alltrue([for cidr in concat(values(var.config.public_subnet_cidrs), values(var.config.db_subnet_cidrs)) :
        try(cidrnetmask(cidr) != "" && split("/", cidr)[1] == "24" && cidrhost(cidr, 0) == split("/", cidr)[0], false)
        ]) && alltrue([for range in local.ranges : range.start >= local.vpc_start && range.end <= local.vpc_end]) && alltrue(flatten([
          for i, first in local.ranges : [for j, second in local.ranges : !(first.start <= second.end && second.start <= first.end) if i < j]
      ]))
      error_message = "Application subnets must be canonical /24s inside the VPC and disjoint from every existing and new subnet."
    }
    precondition {
      condition     = data.aws_ami.selected.architecture == "x86_64" && contains(data.aws_ec2_instance_type.selected.supported_architectures, "x86_64")
      error_message = "Sample application EC2 instances require an x86_64 AMI and instance type."
    }
    precondition {
      condition     = can(regex("^db\\.(t3|t3a|m[0-9]+[id]*|r[0-9]+[id]*)\\.", var.config.db_instance_class))
      error_message = "The RDS instance class must use an x86 family, not Graviton."
    }
  }
}
