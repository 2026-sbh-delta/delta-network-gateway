variable "security_account_id" {
  description = "The approved Security account."
  type        = string
  default     = "482840608926"
  validation {
    condition     = var.security_account_id == "482840608926"
    error_message = "Use Security account 482840608926."
  }
}
variable "aws_region" {
  description = "The source groups and platform Destination share Seoul Region."
  type        = string
  default     = "ap-northeast-2"
  validation {
    condition     = var.aws_region == "ap-northeast-2"
    error_message = "Use ap-northeast-2 for the approved Destination."
  }
}
variable "gcp_project_id" {
  description = "The only source project."
  type        = string
  default     = "term2-delta"
  validation {
    condition     = var.gcp_project_id == "term2-delta"
    error_message = "Use term2-delta."
  }
}
variable "name" {
  description = "Dedicated collector, Sink and Pub/Sub resource prefix."
  type        = string
  default     = "delta-security-gcp-logs"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{3,23}$", var.name))
    error_message = "Use a lowercase prefix of 4 through 24 characters."
  }
}
variable "collector_zip" {
  description = "Verified Linux x86_64 Python 3.13 collector archive."
  type        = string
}
variable "retention_days" {
  description = "Finite source and collector runtime retention in days."
  type        = number
  default     = 30
  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90], var.retention_days)
    error_message = "Choose a supported finite retention period."
  }
}
variable "sink_enabled" {
  description = "Enable source routing after the relay receiver is ready."
  type        = bool
  default     = true
  nullable    = false
}
variable "collector_enabled" {
  description = "Enable the dedicated one-minute Pub/Sub pull schedule."
  type        = bool
  default     = true
  nullable    = false
}
variable "forwarding_enabled" {
  description = "Control only the two platform subscriptions, retaining source groups."
  type        = bool
  default     = true
  nullable    = false
}
