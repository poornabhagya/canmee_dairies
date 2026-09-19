terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# AWS Provider එක සහ Region එක (ap-south-1) declare කිරීම
provider "aws" {
  region = "ap-south-1"
}
