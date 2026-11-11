---
name: ops-and-user-guides
description: Playbooks for generating accurate Deployment, Security, Onboarding, Admin, and End-User manuals grounded in repo configs and UI views.
---

# Operations, Security & User Guide Standards

1. **Environment & Deployment Runbook**:
   - Base prerequisites strictly on real manifest files (`composer.json` PHP extensions, `package.json` Node scripts, `requirements.txt`/`pyproject.toml`, `Dockerfile`, `docker-compose.yml`).
   - List required environment variables / config files (`config/db.php`, `.env.example`, `params.php`) WITHOUT ever printing real credentials or API keys.
   - Clearly mark infrastructure missing from the repo (cloud provider, DNS, SSL certificates, external backup cronjobs) as `[⚠️ BLIND SPOT]`.
2. **Security & Access Control Audit Doc**:
   - Document authentication flow (sessions, JWT, API keys), password hashing/validation, RBAC/permissions found in code, and input validation patterns.
   - Highlight security findings factually (e.g., tracked config files or hardcoded non-redacted structures detected during sanitization).
3. **Admin & End-User Guides**:
   - Derive workflows ONLY from real controllers, view templates, screens, or mobile/API endpoints found in the repo.
   - Structure tasks cleanly as numbered step-by-step instructions grouped by User Role (Admin, Manager, Customer, API Consumer).
