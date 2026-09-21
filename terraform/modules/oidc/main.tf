# GitHub Repo එකේ නම ලබාගැනීමට variable එකක්
variable "github_repo" {
  description = "GitHub repository (e.g., username/repo)"
  type        = string
  default     = "poornabhagya/canmee_dairies"
}

# 1. AWS සහ GitHub අතර විශ්වාසය ගොඩනැගීම (OIDC Provider)
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd"
  ]
}

# 2. OIDC Assume Role Trust Policy Document (Case-insensitive & Wildcard Sub Matching)
data "aws_iam_policy_document" "github_oidc_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:*canmee_dairies*:*",
        "repo:poornabhagya*/canmee_dairies*:*",
        "repo:poornabhagya@87825044/canmee_dairies@1377019655:*",
        "repo:poornabhagya/canmee_dairies:*"
      ]
    }
  }
}

# 3. GitHub Actions වලට AWS එකට සම්බන්ධ වීමට දෙන IAM Role එක
resource "aws_iam_role" "github_actions" {
  name               = "canmee-github-actions-deploy-role"
  assume_role_policy = data.aws_iam_policy_document.github_oidc_assume.json
}

# 4. GitHub එකට AWS ECR එකට Image Push කිරීමට බලතල දීම
resource "aws_iam_role_policy_attachment" "github_ecr_poweruser" {
  role       = aws_iam_role.github_actions.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser"
}

# 5. GitHub එකට EC2 සර්වර් එක ඇතුළේ කමාන්ඩ් Run කිරීමට (SSM) බලතල දෙන Policy එක
resource "aws_iam_policy" "github_ssm_deploy" {
  name        = "canmee-github-ssm-deploy-policy"
  description = "Permissions for GitHub Actions to trigger SSM deployments"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:SendCommand",
          "ssm:GetCommandInvocation"
        ]
        Resource = "*"
      }
    ]
  })
}

# 6. SSM Policy එක Role එකට සම්බන්ධ කිරීම
resource "aws_iam_role_policy_attachment" "github_ssm_attach" {
  role       = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.github_ssm_deploy.arn
}

# 7. Role ARN Output කිරීම
output "github_actions_role_arn" {
  value = aws_iam_role.github_actions.arn
}
