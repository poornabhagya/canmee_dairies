# VPC Module එකෙන් එන vpc_id එක ලබා ගැනීමට variable එකක්
variable "vpc_id" {
  description = "VPC ID from the vpc module"
  type        = string
}

resource "aws_security_group" "production" {
  name        = "canmee-production-sg"
  description = "Allow HTTP and HTTPS inbound traffic, completely block SSH and Databases"
  vpc_id      = var.vpc_id # කලින් හැදූ VPC එකට මෙය සම්බන්ධ කිරීම

  # 1. Port 80 (HTTP) ලෝකේ ඕනෑම තැනකට (0.0.0.0/0) විවෘත කිරීම
  ingress {
    description = "HTTP from anywhere"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # 2. Port 443 (HTTPS) ලෝකේ ඕනෑම තැනකට (0.0.0.0/0) විවෘත කිරීම
  ingress {
    description = "HTTPS from anywhere"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # විශේෂ සටහන: Port 22, 3306, 6379 සඳහා Ingress rules කිසිවක් මෙහි නොමැති බැවින් ඒවා Default Block වේ.

  # 3. Egress: සර්වර් එකෙන් එළියට යන ඕනෑම traffic එකකට (Outbound) ඉඩ දීම 
  # (උදා: Updates ඩවුන්ලෝඩ් කරන්න, Telegram API එකට කතා කරන්න)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1" # "-1" යනු සියලුම Protocols (TCP/UDP/ICMP) බවයි
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "canmee-production-sg"
  }
}

# --- Outputs ---

output "security_group_id" {
  value = aws_security_group.production.id
}
