# Canmee Dairies ERP — Enterprise Cloud Platform & Operations Portal

Production-grade, highly available, and automated Cloud ERP platform. This project re-engineers the legacy `2026.xlsm` workbook workflow into a normalized enterprise Django application, fully provisioned on AWS using modular Infrastructure as Code (Terraform) and deployed via a zero-trust, passwordless CI/CD pipeline.

---

## System Architecture Overview

The platform uses a **Multi-Tenant Dedicated VPC Architecture (Silo Pattern)**. Each tenant operates within an isolated networking and compute boundary, while governance, CI/CD, and disaster recovery remain centralized.

[ End User / Browser ]
│
( HTTPS / TLS 1.3 )
▼
[ AWS CloudFront CDN (Global Edge) ]
├── Static/Media Cache (/static/_, /media/_)
└── Dynamic Origin Pass-Through
│
( AWS Internet Gateway )
▼
[ Production EC2 Node (Tenant VPC: 10.0.0.0/16) ]
┌──────────────────────────────────────────────────────────┐
│ Docker Host Networking Plane (127.0.0.1) │
│ │
│ [ Nginx Reverse Proxy (Port 80/443, Security Headers) ]│
│ │ │
│ ( proxy_pass: 127.0.0.1:8000 ) │
│ ▼ │
│ [ Django Web Service (Gunicorn WSGI) ] │
│ │ │ │
│ ( Localhost Loopback ) ( Cache / Broker ) │
│ ▼ ▼ │
│ [ MariaDB 10.11 (InnoDB) ] [ Redis 7.2 Engine ] │
└──────────────────────────────────────────────────────────┘
│
( Daily Encrypted Nightly Cron )
▼
[ Central S3 Bucket ] ──( 30 Days )──> [ S3 Glacier Cold Vault ]

---

## Key Cloud Engineering Implementations

### 1. Networking & Perimeter Security (VPC & Edge)

- **Dedicated CIDR Isolation:** Provisioned an isolated tenant VPC (`10.0.0.0/16`) divided into Production (`10.0.1.0/24`) and Staging (`10.0.2.0/24`) subnets.
- **Zero-Cost Routing:** Eliminated costly NAT Gateways by routing traffic directly through an AWS Internet Gateway (`0.0.0.0/0 -> IGW`).
- **Strict Ingress Firewalls:** Security groups permit only Port 80 (HTTP) and Port 443 (HTTPS). Sensitive ports (Port 22 SSH, Port 3306 MariaDB, and Port 6379 Redis) are blocked from public ingress.
- **Global Edge Acceleration:** Integrated AWS CloudFront with ACM TLS 1.2/1.3 certificates, caching static and media assets at edge locations while passing transactional requests directly to the origin.

### 2. Passwordless CI/CD & Gatekeeping (GitHub Actions)

- **OIDC STS Authentication:** Replaced long-lived AWS IAM access keys with GitHub Actions OpenID Connect (OIDC) using short-lived AWS STS temporary tokens.
- **Immutable Container Promotion:** Re-tagging verified staging image digests directly in Amazon ECR for production releases (`v*`), eliminating production compile drift.
- **Bastionless SSM Deployments:** Eliminated bastion hosts and open SSH ports; deployments execute over AWS Systems Manager (`ssm:SendCommand` / Run-ShellScript).
- **Automated Smoke Test Gatekeeping:** Integrated `scan_urls.py` into workflows to scan 357 static endpoints, requiring 0 HTTP 500 errors and strict RBAC enforcement before code promotion.

### 3. Database Resilience & Sanitization

- **Engine & Charset Sanitization:** Migrated all legacy MyISAM tables to `InnoDB` (`ROW_FORMAT=DYNAMIC`) with `utf8mb4` / `utf8mb4_unicode_ci` collation to support Sinhala/Tamil Unicode.
- **Host Loopback Connectivity:** Configured containers under `network_mode: host` communicating over local loopback (`127.0.0.1:3306`), resolving internal Docker container name resolution collisions.
- **Race Condition Prevention:** Secured sequence generators (`Farmer`, `Route`) against Time-of-Check to Time-of-Use (TOCTOU) concurrency bugs using `select_for_update()` and `transaction.atomic()`.

### 4. Automated Offsite Disaster Recovery (DR)

- **Non-Blocking Atomic Dumps:** Automated hot database backups via `mariadb-dump --single-transaction --quick`.
- **Client-Side Symmetric Encryption:** Stream-compressed with Gzip and encrypted on the fly with GPG AES-256 before leaving the server.
- **Prefix-Scoped Cold Archival:** Encrypted artifacts stream to a centralized multi-tenant S3 bucket (`tenant-a-canmee/db/...`) and automatically transition to AWS Glacier Flexible Retrieval after 30 days (expiring after 365 days).

### 5. Telemetry, Observability & Healthchecks

- **CloudWatch Infrastructure Alarms:** Automated alarms track EC2 CPU Utilization ($\ge$ 85%) and Hardware Status Check failures.
- **SNS Alerting Relay:** Dispatches infrastructure incident notifications to the engineering team via Amazon SNS.
- **Headless Healthcheck Harness:** An automated bash harness (`healthcheck_harness.sh`) validates container runtimes, Redis PING responses, MariaDB utf8mb4 collation, and web endpoint HTTP status codes (200/302).

---

## Technology Stack

| Domain                       | Technology / Service                                         |
| :--------------------------- | :----------------------------------------------------------- |
| **Backend Framework**        | Django 5+ (Python 3.12) running under Gunicorn WSGI          |
| **Relational Database**      | MariaDB 10.11 LTS (InnoDB, `utf8mb4_unicode_ci`)             |
| **In-Memory Cache / Broker** | Redis 7.2 Alpine (`appendonly yes`, `allkeys-lru`)           |
| **Reverse Proxy**            | Nginx 1.25 Alpine (TLS 1.3, Rate Limiting, Security Headers) |
| **Infrastructure as Code**   | Terraform (Modular Architecture, Remote S3 Backend)          |
| **Container Registry**       | Amazon ECR (Immutable Tags, Lifecycle Prune Policies)        |
| **Edge CDN & SSL**           | AWS CloudFront + AWS Certificate Manager (ACM)               |
| **Compute Plane**            | AWS EC2 (Ubuntu 22.04 LTS, NVMe Swap, Optimized Sysctl)      |
| **Systems Management**       | AWS Systems Manager (SSM Session Manager & Run Command)      |

---

## Repository & Infrastructure Layout

```plaintext
.
├── .github/
│   └── workflows/
│       ├── staging.yml          # Staging deployment, smoke test, and notification workflow
│       └── deploy.yml           # Production promotion, ECR retag, and release verification
├── canmee_dairies/              # Django application core
│   ├── settings.py              # Decoupled 12-factor configuration (os.getenv)
│   ├── urls.py                  # Root route configurations
│   └── wsgi.py                  # WSGI entry point
├── authentication/              # RBAC and employee credential management
├── masters/                     # Farmers, routes, and collection points masters
├── milk_collections/            # Real-time milk collection transactions
├── dispatch/                    # Buyer distribution and dispatch logging
├── reports/                     # Pandas aggregation & ReportLab PDF generators
├── scripts/
│   ├── healthcheck_harness.sh   # Live production telemetry verification script
│   ├── nightly_db_backup.sh     # Encrypted GPG AES-256 S3 backup automation
│   └── scan_urls.py             # Automated route auditing QA test harness
├── terraform/                   # Modular Infrastructure as Code (IaC)
│   ├── backend.tf               # S3 state configuration
│   ├── provider.tf              # AWS provider initialization
│   ├── main.tf                  # Root orchestration layer
│   ├── variables.tf             # Input variables
│   ├── outputs.tf               # Infrastructure outputs
│   └── modules/
│       ├── vpc/                 # Dedicated tenant VPC & subnets
│       ├── security_groups/     # Port 80/443 firewall policies
│       ├── compute/             # EC2 nodes & Elastic IPs
│       ├── iam/                 # SSM roles & prefix-scoped S3 IAM policies
│       ├── oidc/                # GitHub Actions passwordless STS role
│       ├── ecr/                 # Private container registry & prune rules
│       ├── cloudfront/          # Edge CDN distribution & caching
│       ├── storage/             # Disaster recovery S3 bucket & Glacier lifecycle
│       └── monitoring/          # CloudWatch alarms & SNS notification topics
├── Dockerfile                   # Hardened multi-stage non-root container builder
├── docker-compose.yml           # Multi-container orchestration specification
└── nginx.conf                   # Reverse proxy routing & TLS header configurations

---


## Operational Procedures & Maintenance
   1. Manual Backup Execution
   Run on-demand atomic dumps and stream them directly to S3:
   /opt/canmee/scripts/nightly_db_backup.sh

   2. Comprehensive System Health Check
   Verify overall runtime state and connectivity:
   /opt/canmee/scripts/healthcheck_harness.sh

   3. Media Assets Synchronization
   Sync user avatars and media uploads between local storage, S3, and the active container volume:

   Upload from local machine to central S3
   aws s3 sync media/ s3://canmee-central-enterprise-backups-4cbcc7c3/media/

   Sync from S3 to web container on EC2
   aws s3 sync s3://canmee-central-enterprise-backups-4cbcc7c3/media/ /tmp/media/
   docker cp /tmp/media/. canmee_web:/app/media/
   docker exec -u 0 canmee_web chown -R 1000:1000 /app/media
   rm -rf /tmp/media

---

## Security & Hardening Assurances

   Zero Attack Surface on SSH: No bastion hosts or open inbound SSH ports (Port 22); host management is handled via AWS SSM.
   Container Security: Containers run with unprivileged system users (canmee:1000) rather than root.
   Data Protection: Enforces non-blocking transactional consistency, client-side GPG AES-256 backup encryption, and strict TLS 1.3 transport security.

```
