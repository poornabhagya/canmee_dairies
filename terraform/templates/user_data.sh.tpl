#!/bin/bash
# සර්වර් එක ඔන් වූ විගස ක්‍රියාත්මක වන ප්‍රධාන Bootstrap Script එක

echo "=========================================="
echo "1. 4GB Swap Space එක හැදීම"
echo "=========================================="
# Graviton2 4GB RAM එක පිරුණොත් සර්වර් එක crash වීම වැළැක්වීමට Hard Disk එකෙන් 4GB ක මතකයක් වෙන් කරයි
fallocate -l 4G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
# සර්වර් එක රීස්ටාට් වුණත් මේ Swap එක මැකෙන්නේ නැති වෙන්න fstab එකට ලියා තැබීම
echo '/swapfile none swap sw 0 0' >> /etc/fstab

echo "=========================================="
echo "2. OS Kernel එක ටියුන් කිරීම (Memory Tuning)"
echo "=========================================="
# RAM එක සහ අලුත් Network Connections කළමනාකරණය සඳහා නීති සැකසීම
cat <<EOF > /etc/sysctl.d/99-canmee-memory.conf
# සැබෑ RAM එක පිරෙනකම් Swap එක පාවිච්චි කිරීම අවම කිරීම
vm.swappiness=10
# Cache අතහැරීම පාලනය කර performance වැඩි කිරීම
vm.vfs_cache_pressure=50
# එකවර පැමිණිය හැකි connections ප්‍රමාණය 4096 දක්වා වැඩි කිරීම (Nginx සඳහා)
net.core.somaxconn=4096
EOF
# සකස් කළ නීති OS එකට apply කිරීම
sysctl -p /etc/sysctl.d/99-canmee-memory.conf

echo "=========================================="
echo "3. අත්‍යවශ්‍ය මෘදුකාංග සහ AWS CLI v2 ඉන්ස්ටෝල් කිරීම"
echo "=========================================="
apt-get update && apt-get upgrade -y
apt-get install -y ca-certificates curl gnupg unzip ufw

# Graviton2 (ARM64) සඳහා වන AWS CLI v2 භාගත කර ඉන්ස්ටෝල් කිරීම
curl "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
./aws/install
rm -rf awscliv2.zip aws/

echo "=========================================="
echo "4. Docker සහ Docker Compose v2 ඉන්ස්ටෝල් කිරීම"
echo "=========================================="
# Docker නිල ගබඩාව (Repository) Ubuntu වෙත එක් කිරීම
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch="$(dpkg --print-architecture)" signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  "$(. /etc/os-release && echo "$VERSION_CODENAME")" stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null

# Docker මෘදුකාංගය ඉන්ස්ටෝල් කිරීම
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Docker ස්වයංක්‍රීයව ඔන් වීමට සැකසීම
systemctl enable docker
systemctl start docker
usermod -aG docker ubuntu

echo "=========================================="
echo "5. UFW Firewall සක්‍රීය කිරීම"
echo "=========================================="
# පිටතින් එන සියල්ල Block කර, පිටතට යන සියල්ලට ඉඩ දීම
ufw default deny incoming
ufw default allow outgoing

# වෙබ් අඩවියට අවශ්‍ය Port 80 (HTTP) සහ 443 (HTTPS) පමණක් විවෘත කිරීම
ufw allow 80/tcp
ufw allow 443/tcp

# Firewall එක සක්‍රීය කිරීම
ufw --force enable

echo "Cloud-Init Bootstrap සම්පූර්ණයි!"