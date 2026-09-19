# 1. Private Container Registry (ECR) එක සෑදීම
resource "aws_ecr_repository" "canmee_repo" {
  name                 = "canmee-dairies-erp"
  image_tag_mutability = "MUTABLE" # Images වලට tags (උදා: latest, v1) මාරු කිරීමට ඉඩ දීම

  tags = {
    Name = "canmee-ecr-repo"
  }
}

# 2. Storage එක පිරෙන එක වළක්වා ගැනීමට Lifecycle Policy එකක් දැමීම
resource "aws_ecr_lifecycle_policy" "canmee_repo_policy" {
  repository = aws_ecr_repository.canmee_repo.name

  # JSON ආකෘතියෙන් නීති (Rules) සකස් කිරීම
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Tag එකක් නොමැති (Untagged) අසාර්ථක builds දින 1කින් මකා දැමීම"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "අවසන් සාර්ථක Docker images 3 පමණක් ඉතිරි කර පරණ ඒවා මකා දැමීම (Free Tier 500MB සීමාව රැකගැනීමට)"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 3
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# 3. අලුතින් හැදුණු ECR එකේ URL එක (GitHub Actions වලට ලබා දීමට) Output කිරීම
output "repository_url" {
  value = aws_ecr_repository.canmee_repo.repository_url
}
