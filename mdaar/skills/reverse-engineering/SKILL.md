---
name: reverse-engineering
description: Inside-Out Tracing protocol for complete file-by-file and chunk-by-chunk codebase analysis without missing side effects or RBAC logic.
---

# Inside-Out Reverse Engineering Protocol

When inspecting source files or semantic chunks during the **Map** pass or **Planner** phase:

1. **Follow the Real Execution Path (Inside-Out Tracing)**:
   - **Entrypoints & Routing**: Identify how HTTP requests, CLI commands, or background jobs enter the system (`index.php`, `main.py`, `routes`, `urlManager`, controllers).
   - **Middleware, Filters & RBAC**: Never skip `behaviors()`, `AccessControl`, middleware, guards, decorators, or role/permission checks. Document *who* is allowed to call each action.
   - **Domain Models, Behaviors & Hidden Side Effects**: Always inspect model `rules()` / validators, lifecycle hooks (`beforeSave`, `afterSave`, `afterFind`, `@receiver`, Observers, Signals), events (`Event::on`, `trigger`), and relational links (`hasOne`, `hasMany`, foreign keys).
   - **External Integrations & Side Effects**: Record emails (SMTP), SMS/WhatsApp, Firebase/FCM notifications, payment gateways, PDF/Excel exports, and third-party HTTP clients.
2. **Exact File Fact Output**:
   When summarizing a file or chunk, output structured factual bullets ONLY:
   - Purpose of this file in 1-2 sentences.
   - Key Classes / Functions / Routes / Tables touched.
   - Validations, Auth/RBAC rules, Events/Behaviors, External calls.
   - Unresolved references or `[⚠️ BLIND SPOT]` if it calls an external service not defined in the file.
3. **Never Guess Unseen Files**: Only describe symbols present in the chunk shown to you plus the `VERIFIED PROJECT FACT SHEET`.
