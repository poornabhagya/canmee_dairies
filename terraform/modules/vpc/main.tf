# 1. ප්‍රධාන Dedicated VPC එක හැදීම (හුදකලා කළ ජාලය)
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "canmee-dedicated-vpc"
  }
}

# 2. Production Subnet එක හැදීම (ප්‍රධාන සර්වර් එක සඳහා)
# ap-south-1a කලාපයේ 10.0.1.0/24 පරාසය වෙන් කරයි
resource "aws_subnet" "production" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = "ap-south-1a"
  map_public_ip_on_launch = true # සර්වර් එකට ඔටෝ Public IP එකක් ලබා දීම

  tags = {
    Name = "canmee-production-subnet"
  }
}

# 3. Staging Subnet එක හැදීම (ටෙස්ට් සර්වර් එක සඳහා)
# ap-south-1b කලාපයේ 10.0.2.0/24 පරාසය වෙන් කරයි (අමතර ආරක්ෂාවට වෙනම Zone එකක)
resource "aws_subnet" "staging" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.2.0/24"
  availability_zone       = "ap-south-1b"
  map_public_ip_on_launch = true

  tags = {
    Name = "canmee-staging-subnet"
  }
}

# 4. Internet Gateway (IGW) හැදීම (නොමිලේ අන්තර්ජාලයට සම්බන්ධ වීමට)
resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "canmee-igw"
  }
}

# 5. Route Table හැදීම (ට්‍රැෆික් යන්න ඕන පාර පෙන්නීම)
# ලෝකේ ඕනෑම තැනකට (0.0.0.0/0) යන ට්‍රැෆික් එක IGW එක හරහා යවයි
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }

  tags = {
    Name = "canmee-public-route-table"
  }
}

# 6. හදපු Subnets දෙකම අර Route Table එකට සම්බන්ධ කිරීම (Associations)
resource "aws_route_table_association" "prod_assoc" {
  subnet_id      = aws_subnet.production.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "staging_assoc" {
  subnet_id      = aws_subnet.staging.id
  route_table_id = aws_route_table.public.id
}

# --- Outputs (ප්‍රධාන main.tf එකට අවශ්‍ය දත්ත පිටතට දීම) ---

output "vpc_id" {
  value = aws_vpc.main.id
}

output "production_subnet_id" {
  value = aws_subnet.production.id
}

output "staging_subnet_id" {
  value = aws_subnet.staging.id
}
