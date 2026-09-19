terraform {
  backend "s3" {
    bucket       = "canmee-enterprise-terraform-state"
    key          = "prod/terraform.tfstate"
    region       = "ap-south-1"
    use_lockfile = true
  }
}
