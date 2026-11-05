variable "project_id" {
  type        = string
  default     = "term2-delta"
  description = "Approved GCP project."
  validation {
    condition     = var.project_id == "term2-delta"
    error_message = "Only term2-delta is approved."
  }
}
variable "project_number" {
  type        = string
  default     = "366844544284"
  description = "Expected numeric project identity."
}
variable "name" {
  type        = string
  default     = "delta-board-gcp"
  description = "Application resource prefix, separate from old demo and staging resources."
}
variable "region" {
  type        = string
  default     = "asia-northeast3"
  description = "Application and Cloud SQL region."
}
variable "zone" {
  type        = string
  default     = "asia-northeast3-a"
  description = "FE and BE VM zone."
}
variable "network" {
  type        = object({ network = string, subnets = map(string), cidrs = map(string) })
  description = "Nonsecret application_network output from network-security-gcp; existing network/subnet ownership stays there."
}
variable "machine_type" {
  type        = string
  default     = "e2-small"
  description = "x86-only FE/BE Compute Engine machine type."
  validation {
    condition     = can(regex("^e2-(micro|small|medium|standard-[0-9]+)$", var.machine_type))
    error_message = "Use an approved x86 E2 machine type; ARM types are not allowed."
  }
}
variable "image" {
  type        = string
  default     = "ubuntu-2404-noble-amd64-v20261024"
  description = "Pinned ubuntu-os-cloud X86_64 image name."
  validation {
    condition     = can(regex("^ubuntu-2404-noble-amd64-v[0-9]{8}$", var.image))
    error_message = "Use a pinned Ubuntu 24.04 AMD64 image."
  }
}
variable "db_tier" {
  type        = string
  default     = "db-g1-small"
  description = "Approved Cloud SQL Enterprise single-zone shared-core tier."
}
variable "deletion_protection" {
  type        = bool
  default     = true
  description = "Protect the retained FE VM and SQL instance; the old BE has a separate protection input."
}

variable "compose_host_access" {
  type        = bool
  default     = false
  description = "Grant the reused frontend host access to the app secret and the application Cloud SQL instance."
}
variable "single_vm_compose" {
  type        = bool
  default     = false
  description = "Keep only the existing frontend VM after verified FE/BE Compose cutover."
}
variable "backend_deletion_protection" {
  type        = bool
  default     = true
  description = "Separate old BE VM deletion protection for the reviewed two-stage retirement."
}
