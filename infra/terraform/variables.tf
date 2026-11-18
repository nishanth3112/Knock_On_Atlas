variable "region" {
  type    = string
  default = "us-east-1"
}

variable "ssm_prefix" {
  description = "Where the secrets live in SSM Parameter Store (aws-guide section 6)."
  type        = string
  default     = "/koa/prod"
}

variable "allowed_origin" {
  description = "The UI's origin, the only one the API's function URL answers in browsers (aws-guide section 9)."
  type        = string
  default     = "http://localhost:5173"
}

variable "jev_route" {
  description = "off, typesafe or openrouter. Off: runs always pause for a reviewer and spend nothing on Jev."
  type        = string
  default     = "off"
  validation {
    condition     = contains(["off", "typesafe", "openrouter"], var.jev_route)
    error_message = "jev_route must be off, typesafe or openrouter."
  }
}

variable "with_logfire" {
  description = "Send traces to Logfire; needs the /koa/prod/LOGFIRE_TOKEN parameter."
  type        = bool
  default     = false
}

variable "github_repo" {
  description = "owner/name; IAM compares the OIDC subject case-sensitively."
  type        = string
  default     = "nishanth3112/Knock_On_Atlas"
}
