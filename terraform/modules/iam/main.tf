# 1. EC2 සර්වර් එකට AWS සේවාවන් සමඟ කතා කිරීමට ඉඩ දෙන IAM Role එක සෑදීම
resource "aws_iam_role" "ec2_ssm_role" {
  name = "canmee-ec2-ssm-role"

  # මේ role එක පාවිච්චි කරන්න පුළුවන් EC2 සේවාවට පමණක් බව තහවුරු කිරීම
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name = "canmee-ec2-ssm-role"
  }
}

# 2. AmazonSSMManagedInstanceCore Policy එක සම්බන්ධ කිරීම
# මේකෙන් තමයි Port 22 නැතුව, AWS Systems Manager (SSM) හරහා සර්වර් එකට ආරක්ෂිතව ලොග් වෙන්න ඉඩ දෙන්නේ
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.ec2_ssm_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# 3. AmazonEC2ContainerRegistryReadOnly Policy එක සම්බන්ධ කිරීම
# මේකෙන් අපේ EC2 සර්වර් එකට ECR (Container Registry) එකේ තියෙන Docker images ටික pull කරගන්න අවසර දෙනවා
resource "aws_iam_role_policy_attachment" "ecr_read" {
  role       = aws_iam_role.ec2_ssm_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# 4. EC2 Instance Profile එක සෑදීම
# සර්වර් එකක් හදද්දි කෙළින්ම Role එකක් අමුණන්න බෑ, ඒකට මේ වගේ Instance Profile එකක් හරහා යන්න ඕනේ
resource "aws_iam_instance_profile" "ec2_profile" {
  name = "canmee-ec2-instance-profile"
  role = aws_iam_role.ec2_ssm_role.name
}

# මේ හැදුව Profile එකේ නම වෙනත් modules වලට පාවිච්චි කරන්න පුළුවන් වෙන්න Output කිරීම
output "instance_profile_name" {
  value = aws_iam_instance_profile.ec2_profile.name
}
# S3 Central Backup Policy (Strict Tenant-A Prefix-Level Scoping)
variable "backup_bucket_arn" {
  description = "Central S3 Bucket ARN"
  type        = string
  default     = ""
}

resource "aws_iam_policy" "ec2_s3_backup_policy" {
  name        = "canmee-ec2-s3-backup-policy"
  description = "Strict least-privilege policy for Canmee database offsite backups"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowTenantAPrefixOnly"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          var.backup_bucket_arn,
          "${var.backup_bucket_arn}/tenant-a-canmee/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ec2_s3_backup_attach" {
  role       = aws_iam_role.ec2_ssm_role.name
  policy_arn = aws_iam_policy.ec2_s3_backup_policy.arn
}
