# Comprehensive DevOps, Cloud Infrastructure & CI/CD Architecture Report

**Platform:** Construction Equipment Rental Management System (CERMS)  
**Role:** Senior Cloud & DevOps Architect  
**Scope:** Complete Codebase, Infrastructure as Code (Terraform), Docker Orchestration, Shell/Python Automation, Security Perimeter, and GitHub Actions CI/CD Pipelines.

<img width="1643" height="2622" alt="cerms_enterprise_cloud_ _devops_architecture" src="https://github.com/user-attachments/assets/85a76993-8bd4-4e3f-94c4-979db7ebb9ca" />


---

## Executive Summary

The CERMS infrastructure is built as an **enterprise-grade, zero-trust cloud platform on Amazon Web Services (AWS)** using **Infrastructure as Code (Terraform)**, **multi-stage Docker containerization**, **AWS Systems Manager (SSM) bastionless operations**, and **dual GitHub Actions CI/CD pipelines**. 

The architecture strictly eliminates traditional single points of failure and security anti-patterns (such as static SSH keys and compile-in-production workflows) through **OIDC passwordless IAM authentication**, a **Zero-Rebuild immutable image promotion model**, **in-stream AES-256 encrypted offsite disaster recovery**, and an **automated post-deployment URL crawl smoke testing harness**.

---

## 1. CI/CD Pipelines & Release Engineering (`.github/workflows/`)

<img width="2132" height="1633" alt="cerms_dual_ci-cd_pipeline_workflow" src="https://github.com/user-attachments/assets/e0a49431-aeb6-484b-a434-480da34bfccc" />


The repository maintains two segregated, production-hardened workflows:

*   `staging.yml` - Continuous Integration, Staging Deploy & Automated Smoke Test (on push: main)
*   `deploy.yml` - Zero-Rebuild Promotion & Production Deployment (on push: tags: 'v*')

### 1.1 Staging Pipeline
*   **Test:** Spawns a real **MySQL 8.4** ephemeral service container within GitHub Actions runner to execute unit/syntax checks.
*   **Build & Push:** Uses **OIDC passwordless authentication** to push multi-arch Docker builds to Amazon ECR.
*   **Bastionless Deploy:** Connects to Staging EC2 via **AWS Systems Manager (SSM)**. Implements self-healing host memory checks (dynamic 4GB swapfile provisioning) and sequential microservice startups.
*   **Automated Smoke Testing:** Injects `scan_urls.py` into the live container to test all 357 registered routes, outputting `staging_smoke_summary.json` and dispatching HTML reports via Gmail SMTP.

### 1.2 Production Promotion Pipeline
*   **Zero-Rebuild Immutable Promotion:** Extracts the exact container image manifest verified in staging and re-tags it to the release tag (`v*`) directly in Amazon ECR to guarantee 100% environment parity.
*   **Production Deployment:** Executes bastionless deployment via SSM to the Production node, runs migration/static collections, and executes the test harness across all endpoints. Dispatches telemetry to AWS SNS (`cerms-devops-alerts`).

---

## 2. Cloud Architecture & Infrastructure as Code (`terraform/`)

Infrastructure is fully codified in **Terraform (~> 5.0)** with modular, multi-tier isolation in region **Mumbai (`ap-south-1`)**.

*   **Remote State Backend:** Centralized S3 bucket with DynamoDB native state locking.
*   **Network Topology:** Dedicated VPC (`10.10.0.0/16`) with Multi-AZ subnets.
*   **Compute & Bootstrapping:** Ubuntu 22.04 LTS `t3.micro` instances. Automated `user_data.sh.tpl` performs kernel memory tuning (`vm.swappiness=10`), provisions swap, and hardens UFW firewall.
*   **Zero-Trust Security Perimeter:** Strict ingress policy (80/443 only). **Zero-SSH Policy (Port 22 completely omitted).** All interactive access brokered via IAM-authenticated AWS SSM sessions.
*   **IAM & Least-Privilege:** Granular roles for EC2 SSM, S3 Scoped Backups, and GitHub Actions OIDC Trust.
*   **Storage & DR:** Automated Amazon ECR lifecycle policies. Multi-Tenant S3 Disaster Recovery bucket with automated S3 Glacier transitions.
*   **Observability:** CloudWatch Alarms for CPU ($\ge 85\%$) and StatusCheck failures, routed to SNS.

---

## 3. Containerization & Orchestration

The application uses Docker with multi-stage builds and isolated bridge networking (`172.28.0.0/16`).

*   **Multi-Stage Dockerfile:** Separates Python compilation toolchains (Builder Stage) from the hardened runtime image, enforcing unprivileged non-root execution (`cerms_user:1000`).
*   **Resource Governance (FinOps):** Strict hard caps set for all containers to comfortably fit inside a `t3.micro` instance backed by NVMe swap:
    *   `cerms_mariadb`: 500MB Limit, tuned InnoDB buffer pool.
    *   `cerms_redis`: 200MB Limit, LRU eviction.
    *   `cerms_web` (Django/Gunicorn): 1200MB Limit, configured with 3 workers and 2 threads.
    *   `cerms_celery`: 600MB Limit.
    *   `cerms_nginx`: 150MB Limit.

---

## 4. Automation Runbooks & Reliability Engineering

*   **Production Hot Disaster Recovery (`nightly_db_backup.sh`):** Daily atomic dumps via `mariadb-dump` streamed directly through Gzip and **AES-256 Symmetric Encryption (GPG)** before uploading to S3. Zero plaintext data is written to disk. Achieves RPO < 24 Hours and RTO < 30 Minutes.
*   **Automated Route Crawl Smoke Tester (`scan_urls.py`):** Dynamically discovers Django routes and executes headless HTTP calls to categorize responses and fail pipelines on HTTP 500 crashes.

---

## 5. Security Architecture & Nginx Reverse Proxy

*   **Nginx Ingress Proxy:** Enforces HTTP to HTTPS redirection, TLS 1.2/1.3 protocols, and OWASP hardened security headers (`X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`). Handles static/media offloading with edge caching.
*   **Global Edge CDN (CloudFront):** AWS CloudFront terminating SSL/TLS via modern ciphers with SNI. Edge caching for static and media assets.
*   **Application Security:** 12-factor environment decoupling via `.env`, Host Header whitelisting, and strict CSRF Origin Hardening (`CSRF_TRUSTED_ORIGINS`).
