resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# 1. Centralized Disaster Recovery S3 Bucket එක
resource "aws_s3_bucket" "central_backups" {
  bucket        = "canmee-central-enterprise-backups-${random_id.bucket_suffix.hex}"
  force_destroy = false

  tags = {
    Name        = "Canmee Central Backups"
    Environment = "DisasterRecovery"
    Project     = "Canmee Dairies"
  }
}

# 2. S3 Bucket Versioning සක්‍රීය කිරීම
resource "aws_s3_bucket_versioning" "backup_versioning" {
  bucket = aws_s3_bucket.central_backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

# 3. Server-Side Encryption (AES-256)
resource "aws_s3_bucket_server_side_encryption_configuration" "backup_encryption" {
  bucket = aws_s3_bucket.central_backups.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# 4. Public Access සම්පූර්ණයෙන්ම Block කිරීම
resource "aws_s3_bucket_public_access_block" "backup_public_block" {
  bucket = aws_s3_bucket.central_backups.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# 5. FinOps Lifecycle Policy (දින 30න් Glacier, දින 365න් Expiration)
resource "aws_s3_bucket_lifecycle_configuration" "backup_lifecycle" {
  bucket = aws_s3_bucket.central_backups.id

  rule {
    id     = "archive-canmee-db-to-glacier"
    status = "Enabled"

    filter {
      prefix = "tenant-a-canmee/db/"
    }

    transition {
      days          = 30
      storage_class = "GLACIER"
    }

    expiration {
      days = 365
    }
  }
}

# Outputs
output "bucket_name" {
  description = "Central Backups S3 Bucket Name"
  value       = aws_s3_bucket.central_backups.id
}

output "bucket_arn" {
  description = "Central Backups S3 Bucket ARN"
  value       = aws_s3_bucket.central_backups.arn
}
