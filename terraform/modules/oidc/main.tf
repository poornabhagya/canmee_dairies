# GitHub Repo එකේ නම ලබාගැනීමට variable එකක් (උදා: your-username/canmee_dairies)
variable "github_repo" {
  description = "GitHub repository (e.g., username/repo)"
  type        = string
  default     = "poornabhagya/canmee_dairies" # ඔයාගේ නම සහ රිපෝ එකේ නම දෙන්න
}

# 1. AWS සහ GitHub අතර විශ්වාසය ගොඩනැගීම (OIDC Provider)
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # GitHub OIDC එකේ නිල Thumbprint එක (ආරක්ෂාව තහවුරු කිරීමට)
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1", "1c58a3a8518e8759bf075b76b750d4f2df264fcd"]
}

# 2. GitHub Actions වලට AWS එක ඇතුළට එන්න දෙන IAM Role එක හැදීම
resource "aws_iam_role" "github_actions" {
  name = "canmee-github-actions-deploy-role"

  # AssumeRoleWithWebIdentity හරහා GitHub එකට පමණක් ලොග් වීමට අවසර දීම
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRoleWithWebIdentity"
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github.arn
        }
        # මේකෙන් කියන්නේ "මගේ මේ Repo එකට" විතරක් අවසර දෙන්න කියන එකයි (වෙන හැකර් කෙනෙක්ගේ Repo එකකින් ආවොත් block වෙනවා)
        Condition = {
          StringLike = {
            "token.actions.githubusercontent.com:sub" : "repo:${var.github_repo}:*"
          }
          StringEquals = {
            "token.actions.githubusercontent.com:aud" : "sts.amazonaws.com"
          }
        }
      }
    ]
  })
}

# 3. GitHub එකට AWS ECR (Container Registry) එකට අලුත් ඉමේජ් Push කරන්න බලතල දීම
resource "aws_iam_role_policy_attachment" "github_ecr_poweruser" {
  role       = aws_iam_role.github_actions.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser"
}

# 4. GitHub එකට EC2 සර්වර් එක ඇතුළේ කමාන්ඩ් රන් කරන්න (SSM) බලතල දෙන Policy එක හැදීම
resource "aws_iam_policy" "github_ssm_deploy" {
  name = "canmee-github-ssm-deploy-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "ssm:SendCommand",
          "ssm:GetCommandInvocation"
        ]
        Effect   = "Allow"
        Resource = "*"
      }
    ]
  })
}

# ඒ SSM Policy එකත් අර Role එකටම සම්බන්ධ කිරීම
resource "aws_iam_role_policy_attachment" "github_ssm_attach" {
  role       = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.github_ssm_deploy.arn
}

# GitHub Actions YML ෆයිල් එකට දෙන්න ඕන Role ARN එක Output කිරීම
output "github_actions_role_arn" {
  value = aws_iam_role.github_actions.arn
}
