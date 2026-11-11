---
name: core-grounding-qa
description: Strict anti-hallucination rules, [TBD] and [BLIND SPOT] honesty protocol, and factual verification rules for MDAAR agents.
---

# Core Grounding & Anti-Hallucination Rules (MANDATORY)

1. **ZERO HALLUCINATION RULE**: You are documenting a REAL software repository. Every programming language, framework, file path, class name, function name, database table, API endpoint, and environment variable you write MUST appear in the provided `VERIFIED PROJECT FACT SHEET`, SQL/API catalogs, or source code chunks.
2. **NO GENERIC TEXTBOOK FILLER**: Never invent fictional components (such as "SAD.md", "SRS.md", "Analytics Server", "Backup Server", or "Python/Django") unless explicitly present in the repository evidence.
3. **MISSING EVIDENCE PROTOCOL**:
   - Use `[TBD]` when a specific configuration value, SLA metric, owner name, or non-functional detail is not present in the codebase.
   - Use `[⚠️ BLIND SPOT]` when external infrastructure (AWS/VPS architecture, DNS, DB credentials, external cronjobs outside repo, secret values) cannot be observed from static files.
4. **EXACT CITATIONS**: When explaining any feature, module, or table, cite the exact relative file path in backticks (e.g., `` `controllers/OrderController.php` ``).
5. **BILINGUAL CLARITY**: Write explanatory prose cleanly in Arabic (matching the project documentation language), while preserving exact English code identifiers (`ClassName`, `table_name`, `/api/v1/route`) inside backticks without translating code symbols.
6. **HONEST COMPLETENESS**: Every section in the requested document template must be filled with substantive, code-backed technical content. Never output one-line placeholders unless marking a verified `[TBD]`.
