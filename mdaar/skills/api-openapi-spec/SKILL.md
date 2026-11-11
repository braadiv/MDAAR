---
name: api-openapi-spec
description: Standards for documenting REST/GraphQL/RPC endpoints, authentication, request/response schemas, and error codes from code and Postman specs.
---

# API & Integration Documentation Standard

1. **Deterministic Catalog First**: Always incorporate every endpoint extracted by `APISpecParser` (Postman collections, OpenAPI specs, `urlManager`, `ActiveController`, `actionXxx`, FastAPI/Laravel/Express/Spring routes) — never omit discovered endpoints or invent nonexistent ones.
2. **Per-Endpoint Documentation Structure**:
   For each endpoint group / module, provide:
   - **HTTP Method & Path** (e.g., `` `POST /api/auth/login` ``)
   - **Source Controller / Handler** (e.g., `` `controllers/api/AuthController::actionLogin` ``)
   - **Authentication & RBAC** (Bearer Token / JWT / Session / Public, plus required role/permission if defined in code; otherwise `[TBD]`)
   - **Request Parameters & Body Fields** (extracted directly from controller `$request->post(...)`, Model `rules()`, Pydantic/Zod schemas, or Postman body; do NOT guess field types if absent — mark `[TBD]`)
   - **Validation & Business Rules** (required fields, uniqueness, constraints)
   - **Response Status Codes** (e.g., `200/201`, `400`, `401`, `403`, `404`, `422` validation errors, `500`)
   - **Example Sanitized Payload** (never include real tokens or passwords; keep `[REDACTED_*]` placeholders).
