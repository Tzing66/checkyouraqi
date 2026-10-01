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

variable "ec2_enabled" {
  description = "Create the Airflow server. Off by default: it is the project's main running cost."
  type        = bool
  default     = false
}

variable "ec2_instance_type" {
  description = "Cheapest Free-plan type that fits Airflow + hourly jobs (training runs on GitHub)."
  type        = string
  default     = "t4g.small"
}

variable "ec2_volume_gb" {
  type    = number
  default = 20
}

variable "github_repo" {
  description = "owner/name of the repo (used for the clone URL)."
  type        = string
  default     = "Tzing66/checkyouraqi"
}

variable "github_oidc_subject_prefix" {
  description = <<-EOT
    The repo's OIDC `sub` prefix. This repo uses GitHub's immutable-subject format
    (owner@owner_id/repo@repo_id), so a renamed or re-created repo with the same name can't
    assume the role. Check with: gh api repos/OWNER/REPO/actions/oidc/customization/sub
  EOT
  type        = string
  default     = "repo:Tzing66@164254504/checkyouraqi@1392845505"
}

variable "api_enabled" {
  description = "Create the API's ECR repository (step 1 of the API deploy, see api.tf)."
  type        = bool
  default     = false
}

variable "api_image_tag" {
  description = "Image tag in ECR to run on Lambda. Empty = no function yet (push an image first)."
  type        = string
  default     = ""
}
