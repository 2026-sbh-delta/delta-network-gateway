variable "name" {
  description = "Resource name prefix."
  type        = string
  default     = "delta"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,19}$", var.name))
    error_message = "name must be a lowercase resource prefix of at most 20 characters."
  }
}

variable "environment" {
  description = "Environment name and tag."
  type        = string
  default     = "lab"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,15}$", var.environment))
    error_message = "environment must be a lowercase name of at most 16 characters."
  }
}

variable "region" {
  description = "Staging Region."
  type        = string
  default     = "ap-northeast-2"
}

variable "account_id" {
  description = "Expected staging account; the Provider refuses other credentials."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id must be a 12-digit account ID."
  }
}

variable "vpc_id" {
  description = "Existing private VPC for the staging service."
  type        = string
  validation {
    condition     = can(regex("^vpc-[0-9a-f]{8,17}$", var.vpc_id))
    error_message = "vpc_id must be a VPC ID."
  }
}

variable "subnet_ids" {
  description = "At least two private subnets in different AZs for the internal ALB and tasks."
  type        = set(string)
  validation {
    condition     = length(var.subnet_ids) >= 2 && alltrue([for id in var.subnet_ids : can(regex("^subnet-[0-9a-f]{8,17}$", id))])
    error_message = "subnet_ids must contain at least two subnet IDs."
  }
}

variable "route_table_ids" {
  description = "Route tables of subnet_ids for the S3 gateway endpoint used by ECR image layers."
  type        = set(string)
  default     = []
  validation {
    condition     = alltrue([for id in var.route_table_ids : can(regex("^rtb-[0-9a-f]{8,17}$", id))])
    error_message = "route_table_ids must contain route table IDs."
  }
}

variable "create_vpc_endpoints" {
  description = "Create ECR API/DKR, Logs, S3 endpoints and, in dual DB mode, Secrets Manager for private task egress."
  type        = bool
  default     = true
}

variable "endpoint_client_security_group_ids" {
  description = "Security groups of other hosts in the VPC that reach ECR and Logs; private DNS sends them to the endpoints."
  type        = set(string)
  default     = []
  validation {
    condition     = alltrue([for id in var.endpoint_client_security_group_ids : can(regex("^sg-[0-9a-f]{8,17}$", id))])
    error_message = "endpoint_client_security_group_ids must contain security group IDs."
  }
}

variable "scanner_cidrs" {
  description = "Private CIDRs of the DAST runner allowed to reach the internal ALB."
  type        = set(string)
  default     = []
  validation {
    condition = alltrue([for cidr in var.scanner_cidrs : try(
      cidrhost(cidr, 0) == split("/", cidr)[0] && (
        (startswith(cidr, "10.") && tonumber(split("/", cidr)[1]) >= 8) ||
        (startswith(cidr, "192.168.") && tonumber(split("/", cidr)[1]) >= 16) ||
        (can(regex("^172\\.(1[6-9]|2[0-9]|3[01])\\.", cidr)) && tonumber(split("/", cidr)[1]) >= 12)
      ), false
    )])
    error_message = "scanner_cidrs must be canonical RFC1918 CIDRs; the staging ALB is never open to the internet."
  }
}

variable "alb_internet_facing" {
  description = "Expose the ALB to the internet. The security group still admits only scanner sources; the staging tool adds the runner's current public /32 at deploy time."
  type        = bool
  default     = false
}

variable "scanner_public_cidrs" {
  description = "Fixed public egress CIDRs of the DAST runner (for example a NAT IP) for an internet-facing ALB."
  type        = set(string)
  default     = []
  validation {
    condition = alltrue([for cidr in var.scanner_public_cidrs : try(
      cidrhost(cidr, 0) == split("/", cidr)[0] && tonumber(split("/", cidr)[1]) >= 24 &&
      !startswith(cidr, "10.") && !startswith(cidr, "192.168.") && !can(regex("^172\\.(1[6-9]|2[0-9]|3[01])\\.", cidr)),
      false
    )])
    error_message = "scanner_public_cidrs must be canonical public CIDRs of /24 or narrower; the ALB is never open to everyone."
  }
}

variable "scanner_security_group_ids" {
  description = "Security groups of the DAST runner allowed to reach the internal ALB."
  type        = set(string)
  default     = []
  validation {
    condition     = alltrue([for id in var.scanner_security_group_ids : can(regex("^sg-[0-9a-f]{8,17}$", id))])
    error_message = "scanner_security_group_ids must contain security group IDs."
  }
}

variable "container_name" {
  description = "Container name the staging tool replaces the image of."
  type        = string
  default     = "app"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,31}$", var.container_name))
    error_message = "container_name must be a lowercase name of at most 32 characters."
  }
}

variable "container_port" {
  description = "HTTP port the application listens on."
  type        = number
  default     = 8080
  validation {
    condition     = var.container_port >= 1024 && var.container_port <= 65535 && floor(var.container_port) == var.container_port
    error_message = "container_port must be an unprivileged TCP port."
  }
}

variable "health_check_path" {
  description = "Path the target group checks before the tool reports ready."
  type        = string
  default     = "/"
  validation {
    condition     = can(regex("^/[A-Za-z0-9._~/-]{0,127}$", var.health_check_path))
    error_message = "health_check_path must be an absolute URL path."
  }
}

variable "cpu" {
  description = "Fargate task CPU units."
  type        = number
  default     = 256
  validation {
    condition     = contains([256, 512, 1024, 2048], var.cpu)
    error_message = "cpu must be 256, 512, 1024 or 2048."
  }
}

variable "memory" {
  description = "Fargate task memory in MiB."
  type        = number
  default     = 512
  validation {
    condition     = var.memory >= 512 && var.memory <= 16384
    error_message = "memory must be between 512 and 16384 MiB."
  }
}

variable "cpu_architecture" {
  description = "Image CPU architecture."
  type        = string
  default     = "X86_64"
  validation {
    condition     = contains(["X86_64", "ARM64"], var.cpu_architecture)
    error_message = "cpu_architecture must be X86_64 or ARM64."
  }
}

variable "bootstrap_image" {
  description = "Placeholder image for the first task definition revision. The service starts at zero tasks, so it is never pulled."
  type        = string
  default     = "public.ecr.aws/docker/library/busybox:stable"
}

variable "create_ecr_repository" {
  description = "Create an ECR repository the build step pushes staging images to."
  type        = bool
  default     = true
}

variable "allowed_image_prefixes" {
  description = "Registries the staging tool accepts. Empty means this account's ECR in region."
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  description = "CloudWatch log retention for staging tasks."
  type        = number
  default     = 7
  validation {
    condition     = contains([1, 3, 5, 7, 14, 30], var.log_retention_days)
    error_message = "log_retention_days must be 1, 3, 5, 7, 14 or 30."
  }
}

variable "tags" {
  description = "Extra or overriding common tags."
  type        = map(string)
  default     = {}
}

variable "dual_container" {
  description = "frontend:3000과 backend:8080을 한 task/revision에 둔다(Node 게시판, 11/6 기본). false는 예전 단일 컨테이너 앱."
  type        = bool
  default     = true
}

variable "bootstrap_backend_image" {
  description = "Backend image including /app/scripts and migrations; replace the placeholder before deployment."
  type        = string
  default     = "public.ecr.aws/docker/library/busybox:stable"
}

variable "schema_prepared" {
  description = "Operator attestation after separate schema/roles/secret provisioning; Terraform never performs SQL."
  type        = bool
  default     = false
}

variable "existing_nat_egress" {
  description = "Explicit operator attestation that existing private-subnet NAT routes allow HTTPS when endpoints are disabled. Creates no NAT."
  type        = bool
  default     = false
}
