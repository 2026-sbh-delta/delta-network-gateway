variable "central_account_id" {
  type        = string
  description = "Expected central AWS account, verified using STS."
  validation {
    condition     = can(regex("^[0-9]{12}$", var.central_account_id))
    error_message = "Supply the expected 12-digit central account ID."
  }
}
variable "region" {
  type        = string
  default     = "ap-northeast-2"
  description = "Central AWS region."
}
variable "name" {
  type        = string
  default     = "delta-lab"
  description = "Function and IAM role prefix. Must match the GCP trusted role."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,24}$", var.name))
    error_message = "Use 3-25 lowercase characters, beginning with a letter."
  }
}
variable "subscription" {
  type        = string
  description = "Exact GCP pull subscription resource name."
  validation {
    condition     = can(regex("^projects/term2-delta/subscriptions/[A-Za-z][A-Za-z0-9._~+%-]{2,254}$", var.subscription))
    error_message = "Use a subscription in the approved term2-delta project."
  }
}
variable "wif_audience" {
  type        = string
  description = "Numeric-project Google AWS WIF provider audience from GCP log_handoff."
  validation {
    condition     = can(regex("^//iam.googleapis.com/projects/[0-9]+/locations/global/workloadIdentityPools/[a-z0-9-]+/providers/aws-collector$", var.wif_audience))
    error_message = "Supply the exact GCP AWS WIF provider audience."
  }
}
variable "collector_zip" {
  type        = string
  description = "Built Linux x86_64 Python 3.13 Lambda ZIP path."
  validation {
    condition     = fileexists(var.collector_zip) && endswith(var.collector_zip, ".zip")
    error_message = "Build the Lambda ZIP first, then supply its existing path."
  }
}
variable "log_retention_days" {
  type        = number
  default     = 30
  description = "Finite CloudWatch retention for original and collector execution logs."
  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365], var.log_retention_days)
    error_message = "Choose a supported finite CloudWatch retention period."
  }
}
