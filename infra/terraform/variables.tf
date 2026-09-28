variable "project" {
  type    = string
  default = "checkyouraqi"
}

variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "aws_profile" {
  description = "Admin profile Terraform runs as."
  type        = string
  default     = "checkyouraqi"
}

variable "athena_scan_limit_bytes" {
  description = "Per-query scan cutoff for the Athena workgroup (plan §3: 100 MB)."
  type        = number
  default     = 100 * 1024 * 1024
}

variable "ssm_parameters" {
  description = "API keys stored as SecureString under /checkyouraqi/. Values are set out of band."
  type        = set(string)
  default = [
    "openaq_api_key",
    "firms_map_key",
    "telegram_bot_token",
    "telegram_alert_chat_id",
    "llm_api_key",
  ]
}
