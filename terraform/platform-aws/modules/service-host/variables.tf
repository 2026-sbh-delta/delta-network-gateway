variable "name" {
  description = "리소스 이름(예: delta-cp-dast)."
  type        = string
}

variable "service" {
  description = "서비스 키(예: dast). ECR 저장소 이름에 쓴다."
  type        = string
}

variable "region" {
  type = string
}

variable "account_id" {
  type = string
}

variable "security_group_id" {
  description = "루트 모듈이 만들고 규칙을 붙인 보안 그룹. 규칙이 다 붙은 뒤 인스턴스가 부팅된다."
  type        = string
}

variable "subnet_id" {
  description = "인스턴스를 둘 서비스 서브넷(NAT 경로)."
  type        = string
}

variable "ami_id" {
  type = string
}

variable "instance_type" {
  type = string
}

variable "root_volume_gb" {
  type    = number
  default = 30
}

variable "parameter_paths" {
  description = "읽을 수 있는 Parameter Store 경로들(슬래시로 시작·끝)."
  type        = list(string)
  validation {
    condition     = alltrue([for p in var.parameter_paths : can(regex("^/.+/$", p))])
    error_message = "parameter_paths는 /로 시작하고 끝나야 한다."
  }
}

variable "extra_parameter_names" {
  description = "자기 경로 밖에서 읽어야 하는 파라미터 이름(정확한 이름, 예: /delta-cp/jenkins/staging_read_token)."
  type        = list(string)
  default     = []
}

variable "llm_role_arn" {
  description = "LLM을 쓰는 서비스만: 위임받을 공용 LLM 신원 역할 ARN(없으면 null)."
  type        = string
  default     = null
}

variable "extra_policy_json" {
  description = "서비스에만 필요한 추가 권한(없으면 null)."
  type        = string
  default     = null
}

variable "user_data" {
  type = string
}

variable "spot" {
  description = "Spot으로 띄운다(온디맨드 vCPU 한도가 모자랄 때 임시). 중단되면 지우지 않고 정지한다."
  type        = bool
  default     = false
}
