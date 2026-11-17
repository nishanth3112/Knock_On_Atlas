terraform {
  required_version = ">= 1.17"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.8"
    }
  }
  # Local state, gitignored; back it up to _local/infra/ after every apply.
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "knock-on-atlas" }
  }
}

data "aws_caller_identity" "current" {}
