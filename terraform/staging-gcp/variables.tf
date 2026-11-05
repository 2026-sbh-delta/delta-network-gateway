variable "name" {
  description = "Resource name prefix."
  type        = string
  default     = "delta"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,9}$", var.name))
    error_message = "name must be a lowercase resource prefix of at most 10 characters."
  }
}

variable "environment" {
  description = "Environment name and label."
  type        = string
  default     = "lab"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,7}$", var.environment))
    error_message = "environment must be a lowercase name of at most 8 characters."
  }
}

variable "project_id" {
  description = "Staging project. The Provider and the tool touch only this project."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a GCP project ID."
  }
}

variable "region" {
  description = "Cloud Run, serverless NEG and Artifact Registry region."
  type        = string
  default     = "asia-northeast3"
}

variable "enable_services" {
  description = "Enable Cloud Run, Artifact Registry, Compute, IAM, SQL Admin, Secret Manager and Logging APIs. Destroy leaves them enabled."
  type        = bool
  default     = true
}

variable "dynamic_scanner_access" {
  description = "Let the staging tool allow the runner's current public /32 in Cloud Armor at deploy time."
  type        = bool
  default     = true
}

variable "scanner_public_cidrs" {
  description = "Fixed public egress CIDRs of the DAST runner allowed in Cloud Armor."
  type        = set(string)
  default     = []
  validation {
    condition = length(var.scanner_public_cidrs) <= 10 && alltrue([for cidr in var.scanner_public_cidrs : try(
      cidrhost(cidr, 0) == split("/", cidr)[0] && tonumber(split("/", cidr)[1]) >= 24 &&
      !startswith(cidr, "10.") && !startswith(cidr, "192.168.") && !can(regex("^172\\.(1[6-9]|2[0-9]|3[01])\\.", cidr)),
      false
    )])
    error_message = "scanner_public_cidrs must be at most 10 canonical public CIDRs of /24 or narrower; the load balancer is never open to everyone."
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
  description = "HTTP port the application listens on. Cloud Run also passes it as PORT."
  type        = number
  default     = 8080
  validation {
    condition     = var.container_port >= 1024 && var.container_port <= 65535 && floor(var.container_port) == var.container_port
    error_message = "container_port must be an unprivileged TCP port."
  }
}

variable "health_check_path" {
  description = "Path of the startup probe and of the tool's reachability check through the load balancer."
  type        = string
  default     = "/"
  validation {
    condition     = can(regex("^/[A-Za-z0-9._~/-]{0,127}$", var.health_check_path))
    error_message = "health_check_path must be an absolute URL path."
  }
}

variable "cpu" {
  description = "Cloud Run CPU limit."
  type        = string
  default     = "1"
  validation {
    condition     = contains(["1", "2", "4"], var.cpu)
    error_message = "cpu must be 1, 2 or 4."
  }
}

variable "memory" {
  description = "Cloud Run memory limit."
  type        = string
  default     = "512Mi"
  validation {
    condition     = can(regex("^[0-9]+(Mi|Gi)$", var.memory))
    error_message = "memory must look like 512Mi or 1Gi."
  }
}

variable "min_instances" {
  description = "Instances kept warm. 1 keeps staging always up so a swap does not wait for a cold start."
  type        = number
  default     = 1
  validation {
    condition     = contains([0, 1], var.min_instances)
    error_message = "min_instances must be 0 or 1."
  }
}

variable "bootstrap_image" {
  description = "Placeholder image for the first revision. The staging tool replaces it on the first deploy."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "create_repository" {
  description = "Create an Artifact Registry repository the build step pushes staging images to."
  type        = bool
  default     = true
}

variable "allowed_image_prefixes" {
  description = "Registries the staging tool accepts. Empty means this project's staging repository."
  type        = list(string)
  default     = []
}

variable "labels" {
  description = "Extra or overriding common labels."
  type        = map(string)
  default     = {}
}

variable "database_enabled" {
  description = "게시판용 스테이징 PostgreSQL(Cloud SQL 최소 구성)과 배포 전 초기화 Job을 만든다. 시연 뒤 false로 지운다."
  type        = bool
  default     = true
}

variable "dual_container" {
  description = "frontend:3000과 backend:8080을 한 task/revision에 둔다(Node 게시판, 11/6 기본). false는 예전 단일 컨테이너 앱."
  type        = bool
  default     = true
}

variable "bootstrap_backend_image" {
  description = "Backend image including /app/scripts and migrations; replace the placeholder before deployment."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "schema_prepared" {
  description = "true면 초기화와 앱 배포를 병렬로 돌린다. false여도 스테이징 도구가 첫 초기화 성공 뒤 스스로 true로 바꾼다."
  type        = bool
  default     = false
}

variable "cloud_sql_proxy_image" {
  description = "Official Cloud SQL Auth Proxy v2 image; pin an approved digest before deployment."
  type        = string
  default     = "gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.26.2"
}
