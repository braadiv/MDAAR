---
name: docs-technical-writer
description: Standards for MDAAR documentation packages, Audience Views (Developer/Management/End-User), SRS, SAD, ADRs, and Document Register management.
---

# MDAAR Technical Documentation & Package Standards

1. **Structured Four-Package Layout**:
   - `01_Administrative/`: Charter, Scope, Stakeholders, Glossary (Arabic + English domain terminology + enum/constant definitions), Architecture Decision Records (`ADR-Template.md` & derived ADRs).
   - `02_Technical/`: SRS (Functional & Non-Functional Requirements derived from actual modules), SAD (C4 System Architecture), Backend/Frontend Code Architecture, Database Schema & Data Dictionary, API Reference & Integration Specs.
   - `03_Operations/`: Environment Setup, CI/CD & Deployment Runbook, Security & Access Control Guide, Backup/Disaster Recovery, Observability & Troubleshooting.
   - `04_User-Guides/` & `05_Release-Documentation/`: Admin Manual, End-User Quickstart, FAQ, Onboarding Guide, Changelog & Release Notes grounded in Git history.
   - `00_Governance/`: `Document-Register.md` and `Quality-Audit-Report.md`.
2. **One Source – Multiple Audience Views**:
   Include clear structured sections tailored appropriately:
   - **Developer Docs**: Exact classes, methods, DB tables, queries, routes, behaviors, and code paths.
   - **Management / Charter Docs**: Business capabilities, user roles, workflows, module inventory, and risks/blind spots without raw implementation clutter.
   - **End-User / Admin Guides**: Step-by-step workflows, screens/actions, roles, and error troubleshooting derived from real UI/views and API responses.
3. **Traceability Footer**: End technical documents with a short **المصادر المرجعية في الكود (Code Traceability)** list naming the exact repository files inspected.
