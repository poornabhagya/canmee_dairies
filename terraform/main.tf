# 1. Network එක හැදීම
module "vpc" {
  source = "./modules/vpc"
}

# 2. Firewall Rules හැදීම
module "security_groups" {
  source = "./modules/security_groups"
  vpc_id = module.vpc.vpc_id
}

# 3. IAM (OIDC සහ SSM) Roles හැදීම
module "iam" {
  source            = "./modules/iam"
  backup_bucket_arn = module.storage.bucket_arn
}

module "oidc" {
  source = "./modules/oidc"
}

# 4. EC2 සර්වර් හැදීම (Network, Firewall, IAM Role එකට කනෙක්ට් කිරීම)
module "compute" {
  source                = "./modules/compute"
  production_subnet_id  = module.vpc.production_subnet_id
  staging_subnet_id     = module.vpc.staging_subnet_id
  security_group_id     = module.security_groups.security_group_id
  instance_profile_name = module.iam.instance_profile_name
}

# 5. ECR (Container Registry) එක හැදීම
module "ecr" {
  source = "./modules/ecr"
}

# CI/CD එකට අවශ්‍ය දත්ත Output කිරීම
output "github_actions_role_arn" {
  value = module.oidc.github_actions_role_arn
}

# =======================================================
# Phase 8: CloudFront Edge & Custom TLS (ACM in us-east-1)
# =======================================================

# CloudFront SSL සඳහා අනිවාර්ය us-east-1 provider එක
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

# Staging Custom Domain එක සඳහා ACM Certificate එක
resource "aws_acm_certificate" "staging_cert" {
  provider          = aws.us_east_1
  domain_name       = "staging.canmeedairies.lk"
  validation_method = "DNS"

  tags = {
    Environment = "Staging"
    Project     = "Canmee Dairies"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# 6. CloudFront Edge Module එක සම්බන්ධ කිරීම
module "cloudfront" {
  source             = "./modules/cloudfront"
  origin_domain_name = "ec2-13-235-202-88.ap-south-1.compute.amazonaws.com"
  certificate_arn    = aws_acm_certificate.staging_cert.arn
  domain_aliases     = ["staging.canmeedairies.lk"]
}

# Root Outputs
output "cloudfront_url" {
  value = module.cloudfront.cloudfront_domain_name
}

output "staging_portal_url" {
  value = "https://staging.canmeedairies.lk"
}

# =======================================================
# Phase 9: Centralized S3 Storage & Disaster Recovery
# =======================================================

module "storage" {
  source = "./modules/storage"
}

output "central_backup_bucket_name" {
  description = "Disaster Recovery S3 Bucket Name"
  value       = module.storage.bucket_name
}


