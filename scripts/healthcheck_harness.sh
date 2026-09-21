cat << 'EOF' > /opt/canmee/scripts/healthcheck_harness.sh
#!/usr/bin/env bash
set -euo pipefail

echo "=================================================="
echo "Canmee Dairies - Production Healthcheck Harness"
echo "=================================================="

ERRORS=0

# 1. Container Status Check
echo -n "[*] Checking Containers Status... "
RUNNING_CONTAINERS=$(docker ps --format '{{.Names}}')
for c in canmee_mariadb canmee_redis canmee_web canmee_nginx; do
  if echo "$RUNNING_CONTAINERS" | grep -q "$c"; then
    echo -n "$c(OK) "
  else
    echo -e "\n[-] FAILED: Container $c is not running!"
    ERRORS=$((ERRORS + 1))
  fi
done
echo ""

# 2. Redis Ping Check
echo -n "[*] Checking Redis Service... "
REDIS_PING=$(docker exec canmee_redis redis-cli ping 2>/dev/null || echo "FAIL")
if [ "$REDIS_PING" = "PONG" ]; then
  echo "OK (PONG received)"
else
  echo "FAILED (Redis not responding)"
  ERRORS=$((ERRORS + 1))
fi

# 3. MariaDB InnoDB & Utf8mb4 Engine Verification
echo -n "[*] Checking MariaDB Engine & Charset... "
DB_ROOT_PASSWORD="canmee_root_password_2026"
DB_CHECK=$(docker exec canmee_mariadb mariadb -u root -p"${DB_ROOT_PASSWORD}" canmee_dairies -e "
  SELECT @@character_set_database AS charset, @@collation_database AS collation;
" 2>/dev/null | grep -E "utf8mb4" || true)

if [ -n "$DB_CHECK" ]; then
  echo "OK (utf8mb4 detected)"
else
  echo "FAILED (utf8mb4 validation error)"
  ERRORS=$((ERRORS + 1))
fi

# 4. Web Endpoint Login Check (HTTP 200 or 302 Redirect)
echo -n "[*] Checking Django Web Application... "
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/auth/login/ || curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/)
if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "302" ]; then
  echo "OK (HTTP $HTTP_CODE)"
else
  echo "FAILED (Received HTTP $HTTP_CODE)"
  ERRORS=$((ERRORS + 1))
fi

# 5. Nginx Reverse Proxy Ingress (Port 80)
echo -n "[*] Checking Nginx Ingress... "
NGINX_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost/)
if [ "$NGINX_CODE" = "200" ] || [ "$NGINX_CODE" = "301" ] || [ "$NGINX_CODE" = "302" ]; then
  echo "OK (HTTP $NGINX_CODE)"
else
  echo "FAILED (Nginx Ingress HTTP $NGINX_CODE)"
  ERRORS=$((ERRORS + 1))
fi

echo "=================================================="
if [ "$ERRORS" -eq 0 ]; then
  echo "[SUCCESS] All Production health checks passed with ZERO errors!"
  exit 0
else
  echo "[FAILURE] $ERRORS health checks failed!"
  exit 1
fi
EOF

chmod +x /opt/canmee/scripts/healthcheck_harness.sh
/opt/canmee/scripts/healthcheck_harness.sh