terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Backend note: no remote backend is configured here (local state by default).
  # For real use, configure a remote backend (e.g. S3 + DynamoDB lock table) before
  # running `terraform apply` against a shared/production account.
}

provider "aws" {
  region = var.aws_region
}

locals {
  common_tags = {
    Project = var.project_tag
    Iac     = var.iac_tag
  }
}
