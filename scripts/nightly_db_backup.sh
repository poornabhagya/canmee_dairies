#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Canmee Dairies - Production Offsite Disaster Recovery Daemon
# Encrypted Non-Blocking Atomic Dump -> S3 Multi-Tenant Cold Storage
# ==============================================================================

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
YEAR=$(date +"%Y")
MONTH=$(date +"%m")

BACKUP_DIR="/tmp/canmee_backups"
S3_BUCKET="canmee-central-enterprise-backups-4cbcc7c3"
S3_PREFIX="tenant-a-canmee/db/${YEAR}/${MONTH}"

# GPG Symmetric Key (Environment variable එකෙන් හෝ fallback key එක)
GPG_PASSPHRASE="${BACKUP_GPG_KEY:-CanmeeEnterpriseSecretKey2026!}"

mkdir -p "${BACKUP_DIR}"
DUMP_FILE="${BACKUP_DIR}/canmee_db_${TIMESTAMP}.sql.gz.gpg"

echo "[$(date)] Starting atomic database backup for Canmee Dairies..."

# 1. Atomic, Non-blocking consistent dump via MariaDB/MySQL container
# 2. In-stream Gzip compression
# 3. In-stream AES-256 Symmetric GPG Encryption
docker exec canmee_mariadb mariadb-dump \
  -u root -p"${DB_ROOT_PASSWORD:-canmeepassword}" \
  --single-transaction \
  --quick \
  --all-databases \
  2>/dev/null | gzip -c | gpg --symmetric --batch --yes --passphrase "${GPG_PASSPHRASE}" --cipher-algo AES256 -o "${DUMP_FILE}"

echo "[$(date)] Database dumped and GPG AES-256 encrypted successfully: ${DUMP_FILE}"

# 4. Push to Scoped S3 Prefix using EC2 IAM Role credentials
aws s3 cp "${DUMP_FILE}" "s3://${S3_BUCKET}/${S3_PREFIX}/canmee_db_${TIMESTAMP}.sql.gz.gpg" --region ap-south-1

echo "[$(date)] Offsite backup uploaded to s3://${S3_BUCKET}/${S3_PREFIX}/canmee_db_${TIMESTAMP}.sql.gz.gpg"

# 5. Local temp file cleanup
rm -f "${DUMP_FILE}"
echo "[$(date)] Local temporary files cleaned up. Disaster Recovery pipeline finished."