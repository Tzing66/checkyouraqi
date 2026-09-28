terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Partial config: bucket comes from backend.hcl (git-ignored, see backend.hcl.example).
  #   terraform init -backend-config=backend.hcl
  backend "s3" {
    key          = "checkyouraqi/main.tfstate"
    region       = "ap-south-1"
    profile      = "checkyouraqi"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile
  default_tags {
    tags = { project = var.project }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id  = data.aws_caller_identity.current.account_id
  bucket_name = "${var.project}-${local.account_id}"
}
