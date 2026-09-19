# වෙනත් modules වලින් අපිට අවශ්‍ය කරන විස්තර (Variables) ලබා ගැනීම
variable "production_subnet_id" { type = string }
variable "staging_subnet_id" { type = string }
variable "security_group_id" { type = string }
variable "instance_profile_name" { type = string }

# 1. අලුත්ම Ubuntu 22.04 LTS (AMD64 - Free Tier) OS එක ඔටෝ හොයාගැනීම
data "aws_ami" "ubuntu_amd64" {
  most_recent = true
  owners      = ["099720109477"] # Canonical (Ubuntu) නිල AWS ගිණුම් අංකය

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
}

# 2. Production සර්වර් එක හැදීම (පාරිභෝගිකයන්ට ලයිව් දෙන එක)
resource "aws_instance" "production" {
  ami                    = data.aws_ami.ubuntu_amd64.id
  instance_type          = "t3.micro" # Free Tier Eligible
  subnet_id              = var.production_subnet_id
  vpc_security_group_ids = [var.security_group_id]
  iam_instance_profile   = var.instance_profile_name # කලින් හැදූ පාස්වර්ඩ් නැති IAM Role එක

  # වේගවත් gp3 Hard Disk එකක් 15GB වලින් වෙන් කිරීම (Free Tier උපරිමය 30GB නිසා 15x2)
  root_block_device {
    volume_type = "gp3"
    volume_size = 15
  }

  tags = {
    Name = "canmee-production-node"
  }
}

# 3. Production සර්වර් එකට ස්ථිර IP එකක් (Elastic IP) අමුණා දීම
# සර්වර් එක off කරලා ඔන් කළත් මේ IP එක කවදාවත් මාරු වෙන්නේ නෑ
resource "aws_eip" "production_eip" {
  instance = aws_instance.production.id
  domain   = "vpc"

  tags = {
    Name = "canmee-production-eip"
  }
}

# 4. Staging සර්වර් එක හැදීම (ටෙස්ට් කිරීම් සඳහා 10.0.2.0/24 subnet එකේ)
resource "aws_instance" "staging" {
  ami                    = data.aws_ami.ubuntu_amd64.id
  instance_type          = "t3.micro" # Free Tier Eligible
  subnet_id              = var.staging_subnet_id
  vpc_security_group_ids = [var.security_group_id]
  iam_instance_profile   = var.instance_profile_name

  root_block_device {
    volume_type = "gp3"
    volume_size = 15
  }

  tags = {
    Name = "canmee-staging-node"
  }
}

# අවසානයේ අපිට CloudFront සහ DNS වලට සෙට් කරන්න ඕන නිසා හැදුණු EIP එක output කිරීම
output "production_eip" {
  value = aws_eip.production_eip.public_ip
}
