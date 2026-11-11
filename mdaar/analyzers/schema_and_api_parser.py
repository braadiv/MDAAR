"""
Database Schema (SQL DDL + ORM Migrations) and Polyglot API Spec Parser for MDAAR v2.0.0.
Supports MySQL/Postgres/SQLite DDL, Yii2/Laravel migrations, Prisma, Postman v2 collections,
OpenAPI/Swagger specs, Yii2 urlManager + ActiveController + actionXxx, Laravel, FastAPI,
Django, Flask, Express, NestJS, Next.js App Router, Spring Boot, ASP.NET Core, and Go.
"""

from collections import defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
import re

from mdaar.security.sanitizer import SecretSanitizer


@dataclass
class ColumnDef:
    name: str
    data_type: str
    is_pk: bool = False
    is_fk: bool = False
    nullable: bool = True


@dataclass
class TableDef:
    name: str
    source_file: str = ""
    columns: list = field(default_factory=list)
    primary_keys: list = field(default_factory=list)


@dataclass
class RelationDef:
    from_table: str
    to_table: str
    from_col: str = ""
    to_col: str = ""
    label: str = "references"


@dataclass
class EndpointDef:
    method: str
    path: str
    source_file: str
    framework: str = ""
    handler: str = ""
    group: str = ""
    auth_hint: str = ""
    request_hint: str = ""


class SchemaParser:
    """Extracts tables, columns, and relationships from SQL DDL and ORM migrations."""

    CREATE_TABLE_RE = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[`\"\[]?[A-Za-z0-9_]+[`\"\]]?\.)?[`\"\[]?([A-Za-z0-9_]+)[`\"\]]?\s*\((.*?)\)\s*(?:ENGINE|;|COMMENT|$)",
        re.IGNORECASE | re.DOTALL,
    )
    FK_CLAUSE_RE = re.compile(
        r"FOREIGN\s+KEY\s*\([`\"\[]?([A-Za-z0-9_]+)[`\"\]]?\)\s*REFERENCES\s+(?:[`\"\[]?[A-Za-z0-9_]+[`\"\]]?\.)?[`\"\[]?([A-Za-z0-9_]+)[`\"\]]?\s*\([`\"\[]?([A-Za-z0-9_]+)[`\"\]]?\)",
        re.IGNORECASE,
    )
    ALTER_FK_RE = re.compile(
        r"ALTER\s+TABLE\s+(?:[`\"\[]?[A-Za-z0-9_]+[`\"\]]?\.)?[`\"\[]?([A-Za-z0-9_]+)[`\"\]]?.*?ADD\s+(?:CONSTRAINT\s+\S+\s+)?FOREIGN\s+KEY\s*\([`\"\[]?([A-Za-z0-9_]+)[`\"\]]?\)\s*REFERENCES\s+(?:[`\"\[]?[A-Za-z0-9_]+[`\"\]]?\.)?[`\"\[]?([A-Za-z0-9_]+)[`\"\]]?\s*\([`\"\[]?([A-Za-z0-9_]+)[`\"\]]?\)",
        re.IGNORECASE | re.DOTALL,
    )
    YII_MIGRATION_TABLE_RE = re.compile(
        r"\$this->createTable\s*\(\s*['\"]\{\{?%?([A-Za-z0-9_]+)%?\}?\}['\"]\s*,\s*\[(.*?)\]",
        re.DOTALL,
    )
    YII_MIGRATION_FK_RE = re.compile(
        r"\$this->addForeignKey\s*\(\s*['\"][^'\"]+['\"]\s*,\s*['\"]\{\{?%?([A-Za-z0-9_]+)%?\}?\}['\"]\s*,\s*['\"]([A-Za-z0-9_]+)['\"]\s*,\s*['\"]\{\{?%?([A-Za-z0-9_]+)%?\}?\}['\"]\s*,\s*['\"]([A-Za-z0-9_]+)['\"]",
        re.DOTALL,
    )
    LARAVEL_TABLE_RE = re.compile(
        r"Schema::create\s*\(\s*['\"]([A-Za-z0-9_]+)['\"]\s*,.*?\)\s*\{(.*?)\}\s*\);",
        re.DOTALL,
    )
    PRISMA_MODEL_RE = re.compile(
        r"model\s+([A-Za-z0-9_]+)\s*\{(.*?)\}",
        re.DOTALL,
    )

    @classmethod
    def parse_sql_text(cls, sql_text, source_file=""):
        tables = {}
        relations = []

        for match in cls.CREATE_TABLE_RE.finditer(sql_text):
            tname = match.group(1)
            body = match.group(2)
            tdef = tables.get(tname) or TableDef(name=tname, source_file=source_file)

            pk_cols = set()
            pk_match = re.search(r"PRIMARY\s+KEY\s*\(([^)]+)\)", body, re.IGNORECASE)
            if pk_match:
                for p in pk_match.group(1).split(","):
                    pk_cols.add(p.strip().strip("`\"[] "))

            fk_cols = set()
            for fk in cls.FK_CLAUSE_RE.finditer(body):
                from_col, ref_table, ref_col = fk.group(1), fk.group(2), fk.group(3)
                fk_cols.add(from_col)
                relations.append(RelationDef(tname, ref_table, from_col, ref_col, from_col))

            for raw_line in _split_sql_clauses(body):
                line = raw_line.strip()
                if not line:
                    continue
                upper = line.upper()
                if upper.startswith(("PRIMARY KEY", "KEY ", "UNIQUE ", "INDEX ", "CONSTRAINT ", "FOREIGN KEY", "FULLTEXT ", "CHECK ")):
                    continue
                col_m = re.match(r"^[`\"\[]?([A-Za-z0-9_]+)[`\"\]]?\s+([A-Za-z0-9_]+(?:\([^)]+\))?)", line)
                if not col_m:
                    continue
                cname = col_m.group(1)
                ctype = re.sub(r"\(.*?\)", "", col_m.group(2)).upper()
                is_pk = cname in pk_cols or "PRIMARY KEY" in upper
                if is_pk:
                    pk_cols.add(cname)
                nullable = "NOT NULL" not in upper and not is_pk
                inline_fk = re.search(r"REFERENCES\s+[`\"\[]?([A-Za-z0-9_]+)[`\"\]]?\s*(?:\([`\"\[]?([A-Za-z0-9_]+)[`\"\]]?\))?", line, re.IGNORECASE)
                is_fk = cname in fk_cols or inline_fk is not None
                if inline_fk:
                    relations.append(RelationDef(tname, inline_fk.group(1), cname, inline_fk.group(2) or "id", cname))
                tdef.columns.append(ColumnDef(cname, ctype, is_pk=is_pk, is_fk=is_fk, nullable=nullable))

            tdef.primary_keys = sorted(pk_cols)
            tables[tname] = tdef

        for fk in cls.ALTER_FK_RE.finditer(sql_text):
            relations.append(RelationDef(fk.group(1), fk.group(3), fk.group(2), fk.group(4), fk.group(2)))

        return list(tables.values()), relations

    @classmethod
    def parse_orm_migrations(cls, content, rel_path):
        tables = []
        relations = []

        # Yii2 migrations
        for m in cls.YII_MIGRATION_TABLE_RE.finditer(content):
            tname, body = m.group(1), m.group(2)
            cols = []
            for cm in re.finditer(r"['\"]([A-Za-z0-9_]+)['\"]\s*=>\s*\$this->([A-Za-z0-9_]+)", body):
                cname, dtype = cm.group(1), cm.group(2).upper()
                is_pk = "PRIMARYKEY" in dtype
                cols.append(ColumnDef(cname, dtype, is_pk=is_pk, nullable=not is_pk))
            tables.append(TableDef(name=tname, source_file=rel_path, columns=cols))

        for fk in cls.YII_MIGRATION_FK_RE.finditer(content):
            relations.append(RelationDef(fk.group(1), fk.group(3), fk.group(2), fk.group(4), fk.group(2)))

        # Laravel migrations
        for m in cls.LARAVEL_TABLE_RE.finditer(content):
            tname, body = m.group(1), m.group(2)
            cols = []
            for cm in re.finditer(r"\$table->([A-Za-z0-9_]+)\s*\(\s*(?:['\"]([A-Za-z0-9_]+)['\"])?", body):
                dtype = cm.group(1).upper()
                cname = cm.group(2) or ("id" if dtype == "ID" else "")
                if cname:
                    cols.append(ColumnDef(cname, dtype, is_pk=(dtype == "ID" or cname == "id")))
            for fm in re.finditer(r"->foreign\s*\(\s*['\"]([A-Za-z0-9_]+)['\"]\s*\).*?->on\s*\(\s*['\"]([A-Za-z0-9_]+)['\"]", body):
                relations.append(RelationDef(tname, fm.group(2), fm.group(1), "id", fm.group(1)))
            tables.append(TableDef(name=tname, source_file=rel_path, columns=cols))

        # Prisma schemas
        if rel_path.endswith(".prisma"):
            for m in cls.PRISMA_MODEL_RE.finditer(content):
                tname, body = m.group(1), m.group(2)
                cols = []
                for line in body.splitlines():
                    line = line.strip()
                    if not line or line.startswith("//") or line.startswith("@@"):
                        continue
                    pm = re.match(r"^([A-Za-z0-9_]+)\s+([A-Za-z0-9_\[\]?]+)", line)
                    if pm:
                        cols.append(ColumnDef(pm.group(1), pm.group(2), is_pk="@id" in line))
                tables.append(TableDef(name=tname, source_file=rel_path, columns=cols))

        return tables, relations

    @classmethod
    def parse_repository(cls, scan_result):
        """Parse every non-ignored SQL file, migration, and Prisma schema across the codebase."""
        table_map = {}
        all_relations = []

        for sf in scan_result.files:
            rel_lower = sf.rel_path.lower()
            if sf.language == "sql":
                tlist, rels = cls.parse_sql_text(sf.content, source_file=sf.rel_path)
                for t in tlist:
                    existing = table_map.get(t.name)
                    if existing is None or len(t.columns) > len(existing.columns):
                        table_map[t.name] = t
                all_relations.extend(rels)
            elif (
                sf.extension in (".php", ".prisma")
                and ("migration" in rel_lower or "schema" in rel_lower or "createTable" in sf.content)
            ):
                tlist, rels = cls.parse_orm_migrations(sf.content, sf.rel_path)
                for t in tlist:
                    if t.name not in table_map:
                        table_map[t.name] = t
                all_relations.extend(rels)

        # Deduplicate relations cleanly
        dedup_rels = []
        seen_rel = set()
        for r in all_relations:
            key = (r.from_table, r.to_table, r.from_col)
            if key not in seen_rel:
                seen_rel.add(key)
                dedup_rels.append(r)

        tables = sorted(table_map.values(), key=lambda t: t.name)
        return tables, dedup_rels

    @classmethod
    def build_mermaid_erd_chunks(cls, tables, relations, max_tables_per_block=14):
        """Render one or more clean Mermaid erDiagram blocks sized safely for UI rendering."""
        if not tables:
            return ["```mermaid\nerDiagram\n    %% No SQL tables detected in repository\n```"]

        blocks = []
        for idx in range(0, len(tables), max_tables_per_block):
            chunk = tables[idx : idx + max_tables_per_block]
            chunk_names = {t.name for t in chunk}
            lines = ["```mermaid", "erDiagram"]
            for t in chunk:
                safe_t = _mermaid_id(t.name)
                lines.append(f"    {safe_t} {{")
                for col in t.columns[:25]:
                    flags = []
                    if col.is_pk:
                        flags.append("PK")
                    if col.is_fk:
                        flags.append("FK")
                    flag_str = f" {','.join(flags)}" if flags else ""
                    safe_type = re.sub(r"[^A-Za-z0-9_]", "", col.data_type) or "STRING"
                    lines.append(f"        {safe_type} {_mermaid_id(col.name)}{flag_str}")
                if len(t.columns) > 25:
                    lines.append(f"        INFO and_{len(t.columns) - 25}_more_columns")
                lines.append("    }")

            for r in relations:
                if r.from_table in chunk_names or r.to_table in chunk_names:
                    label = re.sub(r"[^A-Za-z0-9_ ]", "", r.label or "fk") or "fk"
                    lines.append(f'    {_mermaid_id(r.to_table)} ||--o{{ {_mermaid_id(r.from_table)} : "{label}"')

            lines.append("```")
            blocks.append("\n".join(lines))

        return blocks

    @staticmethod
    def parse_sql_to_mermaid(sql_text):
        """Backwards-compatible static helper used by legacy callers."""
        tables, relations = SchemaParser.parse_sql_text(sql_text)
        if not tables:
            return "```mermaid\nerDiagram\n    %% No tables found\n```"
        return "\n\n".join(SchemaParser.build_mermaid_erd_chunks(tables, relations, max_tables_per_block=25))

    @classmethod
    def render_data_dictionary_markdown(cls, tables, relations, project_name):
        """Render complete deterministic Database Schema & Data Dictionary document."""
        if not tables:
            return (
                f"# Data Dictionary — {project_name}\n\n"
                "لم يتم العثور على ملفات SQL DDL أو هجرات قاعدة بيانات (`migrations`) في المستودع.\n"
                "`[⚠️ BLIND SPOT]` إذا كانت قاعدة البيانات تُدار خارج هذا المستودع، يلزم توفير ملف الـ DDL أو التوثيق الخارجي.\n"
            )

        lines = [
            f"# قاعدة البيانات وقاموس البيانات — {project_name}",
            "",
            f"- **إجمالي الجداول المستخرجة:** {len(tables)}",
            f"- **إجمالي العلاقات (Foreign Keys):** {len(relations)}",
            "",
            "## 1. مخططات العلاقات (Entity Relationship Diagrams)",
            "",
        ]
        blocks = cls.build_mermaid_erd_chunks(tables, relations, max_tables_per_block=14)
        for i, block in enumerate(blocks, 1):
            if len(blocks) > 1:
                lines.append(f"### مخطط الجداول — المجموعة {i}")
            lines.append(block)
            lines.append("")

        lines.append("## 2. قاموس الجداول التفصيلي (Data Dictionary)")
        lines.append("")
        for t in tables:
            lines.append(f"### الجدول `{t.name}`")
            if t.source_file:
                lines.append(f"- **ملف المصدر:** `{t.source_file}`")
            if t.primary_keys:
                lines.append(f"- **المفتاح الأساسي (PK):** `{', '.join(t.primary_keys)}`")
            lines.append("")
            if t.columns:
                lines.append("| العمود | النوع | PK | FK | يقبل NULL |")
                lines.append("| :--- | :--- | :---: | :---: | :---: |")
                for c in t.columns:
                    lines.append(
                        f"| `{c.name}` | `{c.data_type}` | {'نعم' if c.is_pk else '-'} | "
                        f"{'نعم' if c.is_fk else '-'} | {'نعم' if c.nullable else 'لا'} |"
                    )
            else:
                lines.append("_لم تُستخرج تفاصيل الأعمدة تلقائياً — `[TBD]`_")
            lines.append("")

        if relations:
            lines.append("## 3. خريطة العلاقات المرجعية (Foreign Key Map)")
            lines.append("")
            lines.append("| الجدول التابع (Child) | العمود | يشير إلى الجدول (Parent) | العمود المرجعي |")
            lines.append("| :--- | :--- | :--- | :--- |")
            for r in relations:
                lines.append(f"| `{r.from_table}` | `{r.from_col}` | `{r.to_table}` | `{r.to_col}` |")

        return "\n".join(lines)


class APISpecParser:
    """Extracts API endpoints from Postman collections, OpenAPI specs, and polyglot controllers/routers."""

    FRAMEWORK_ROUTE_PATTERNS = [
        # (framework_label, allowed_languages, compiled_regex)
        ("Python (FastAPI/Flask)", ("python",), re.compile(r"@(?:app|router|bp|blueprint)\.(get|post|put|delete|patch|options)\s*\(\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)),
        ("Python (Flask/Route)", ("python",), re.compile(r"@(?:app|bp|blueprint)\.route\s*\(\s*['\"]([^'\"]+)['\"](?:.*?methods\s*=\s*\[([^\]]+)\])?", re.IGNORECASE)),
        ("Python (Django URL)", ("python",), re.compile(r"\b(?:path|re_path)\s*\(\s*['\"]([^'\"]*)['\"]\s*,\s*([A-Za-z0-9_.]+)")),
        ("PHP (Laravel)", ("php",), re.compile(r"Route::(get|post|put|patch|delete|any|match|apiResource|resource)\s*\(\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)),
        ("PHP (Symfony)", ("php",), re.compile(r"#\[Route\(\s*['\"]([^'\"]+)['\"]")),
        ("JS/TS (Express/Router)", ("javascript", "typescript"), re.compile(r"\b(?:app|router|fastify)\.(get|post|put|delete|patch|all)\s*\(\s*['\"`]([^'\"`]+)['\"`]", re.IGNORECASE)),
        ("Java/Kotlin (Spring)", ("java", "kotlin"), re.compile(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\s*\(\s*(?:value\s*=\s*|path\s*=\s*)?['\"]([^'\"]+)['\"]")),
        ("C# (ASP.NET)", ("csharp",), re.compile(r"\[Http(Get|Post|Put|Delete|Patch)\s*(?:\(\s*['\"]([^'\"]*)['\"]\s*\))?\]")),
        ("Go (Web Router)", ("go",), re.compile(r"\.(GET|POST|PUT|PATCH|DELETE)\s*\(\s*['\"]([^'\"]+)['\"]")),
    ]

    @staticmethod
    def _parse_postman_collection(content, rel_path):
        endpoints = []
        try:
            data = json.loads(content)
        except Exception:
            return endpoints

        if not isinstance(data, dict) or "item" not in data:
            return endpoints

        def walk_items(items, folder_stack):
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = item.get("name", "")
                if "item" in item:
                    walk_items(item["item"], folder_stack + ([name] if name else []))
                    continue
                req = item.get("request")
                if not isinstance(req, dict):
                    continue
                method = str(req.get("method", "GET")).upper()
                raw_url = req.get("url", "")
                if isinstance(raw_url, dict):
                    path_parts = raw_url.get("path") or []
                    if isinstance(path_parts, list) and path_parts:
                        url_path = "/" + "/".join(str(p) for p in path_parts)
                    else:
                        url_path = str(raw_url.get("raw", ""))
                else:
                    url_path = str(raw_url)
                url_path = re.sub(r"\{\{[^}]+\}\}", "", url_path).strip() or "/"
                if not url_path.startswith("/") and not url_path.startswith("http"):
                    url_path = "/" + url_path

                auth_obj = req.get("auth") or {}
                auth_hint = auth_obj.get("type", "") if isinstance(auth_obj, dict) else ""

                body_hint = ""
                body = req.get("body") or {}
                if isinstance(body, dict):
                    mode = body.get("mode", "")
                    if mode == "raw" and body.get("raw"):
                        body_hint = SecretSanitizer.sanitize(str(body["raw"])[:180].replace("\n", " "), rel_path)
                    elif mode == "formdata" and isinstance(body.get("formdata"), list):
                        keys = [str(f.get("key", "")) for f in body["formdata"][:8] if isinstance(f, dict)]
                        body_hint = f"form-data({', '.join(keys)})"

                endpoints.append(
                    EndpointDef(
                        method=method,
                        path=url_path,
                        source_file=rel_path,
                        framework="Postman Collection",
                        handler=name,
                        group=" / ".join(folder_stack) or "Root",
                        auth_hint=auth_hint,
                        request_hint=body_hint,
                    )
                )

        walk_items(data.get("item", []), [])
        return endpoints

    @staticmethod
    def _parse_openapi_json(content, rel_path):
        endpoints = []
        try:
            data = json.loads(content)
        except Exception:
            return endpoints
        if not isinstance(data, dict) or not ("openapi" in data or "swagger" in data):
            return endpoints
        paths = data.get("paths") or {}
        if not isinstance(paths, dict):
            return endpoints
        for path, ops in paths.items():
            if not isinstance(ops, dict):
                continue
            for method, spec in ops.items():
                if method.lower() not in ("get", "post", "put", "patch", "delete", "options", "head"):
                    continue
                summary = spec.get("summary", "") if isinstance(spec, dict) else ""
                tags = ", ".join(spec.get("tags", [])) if isinstance(spec, dict) and isinstance(spec.get("tags"), list) else ""
                endpoints.append(
                    EndpointDef(
                        method=method.upper(),
                        path=path,
                        source_file=rel_path,
                        framework="OpenAPI/Swagger",
                        handler=summary,
                        group=tags or "OpenAPI",
                    )
                )
        return endpoints

    @staticmethod
    def _parse_yii2_endpoints(content, rel_path):
        endpoints = []

        # 1. urlManager explicit rules in config files (web.php, main.php, api.php)
        if "'urlManager'" in content or '"urlManager"' in content or "urlManager" in content:
            # Rest UrlRule controller mapping
            for ctrl_block in re.findall(r"['\"]controller['\"]\s*=>\s*\[(.*?)\]", content, re.DOTALL):
                for alias in re.findall(r"['\"]([A-Za-z0-9_/\-]+)['\"]", ctrl_block):
                    endpoints.append(
                        EndpointDef(
                            method="REST CRUD",
                            path=f"/{alias.lstrip('/')}",
                            source_file=rel_path,
                            framework="Yii2 UrlRule",
                            handler=f"ActiveController ({alias})",
                            group="REST Config",
                        )
                    )
            # Explicit 'VERB pattern' => 'route' rules
            for m in re.finditer(
                r"['\"]((?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)(?:\s*,\s*(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS))*\s+)?([A-Za-z0-9_<>/:\-\.\*]+)['\"]\s*=>\s*['\"]([A-Za-z0-9_/\-]+)['\"]",
                content,
            ):
                verbs = (m.group(1) or "ANY").strip()
                pattern = "/" + m.group(2).lstrip("/")
                target = m.group(3)
                if "/" in target and not target.startswith("http"):
                    endpoints.append(
                        EndpointDef(
                            method=verbs,
                            path=pattern,
                            source_file=rel_path,
                            framework="Yii2 urlManager",
                            handler=target,
                            group="URL Rules",
                        )
                    )

        # 2. Yii2 Controllers (Controller / ActiveController + public function actionXxx)
        ctrl_match = re.search(
            r"class\s+([A-Za-z0-9_]+Controller)\s+extends\s+([A-Za-z0-9_\\]+)",
            content,
        )
        if ctrl_match:
            ctrl_class = ctrl_match.group(1)
            base_class = ctrl_match.group(2)
            ctrl_slug = _camel_to_kebab(ctrl_class.replace("Controller", ""))
            prefix = "/api/" + ctrl_slug if "/api" in rel_path.lower() or "Api" in ctrl_class else "/" + ctrl_slug

            auth_hint = ""
            if "HttpBearerAuth" in content or "CompositeAuth" in content:
                auth_hint = "Bearer/CompositeAuth"
            elif "AccessControl" in content:
                auth_hint = "Yii2 AccessControl"

            if "ActiveController" in base_class:
                endpoints.append(
                    EndpointDef(
                        method="GET/POST/PUT/DELETE",
                        path=prefix,
                        source_file=rel_path,
                        framework="Yii2 ActiveController",
                        handler=f"{ctrl_class} (REST CRUD)",
                        group=ctrl_class,
                        auth_hint=auth_hint,
                    )
                )

            for am in re.finditer(r"public\s+function\s+action([A-Z][A-Za-z0-9_]*)\s*\(([^)]*)\)", content):
                action_name = am.group(1)
                params = am.group(2).strip()
                action_slug = _camel_to_kebab(action_name)
                route_path = prefix if action_slug == "index" else f"{prefix}/{action_slug}"
                if params:
                    # Hint query/path params from PHP signature without guessing behavior
                    param_names = re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", params)
                    if param_names:
                        route_path += "?" + "&".join(f"{p}={{{p}}}" for p in param_names)
                endpoints.append(
                    EndpointDef(
                        method="ANY/ACTION",
                        path=route_path,
                        source_file=rel_path,
                        framework="Yii2 Controller",
                        handler=f"{ctrl_class}::action{action_name}",
                        group=ctrl_class,
                        auth_hint=auth_hint,
                    )
                )

        return endpoints

    @staticmethod
    def _parse_nestjs_endpoints(content, rel_path):
        endpoints = []
        prefix_m = re.search(r"@Controller\s*\(\s*['\"]([^'\"]*)['\"]", content)
        if not prefix_m:
            return endpoints
        prefix = "/" + prefix_m.group(1).strip("/")
        for m in re.finditer(r"@(Get|Post|Put|Patch|Delete)\s*\(\s*(?:['\"]([^'\"]*)['\"])?\s*\)", content):
            method = m.group(1).upper()
            sub = (m.group(2) or "").strip("/")
            full_path = f"{prefix}/{sub}".replace("//", "/") if sub else prefix
            endpoints.append(
                EndpointDef(
                    method=method,
                    path=full_path or "/",
                    source_file=rel_path,
                    framework="TS (NestJS)",
                    group=prefix,
                )
            )
        return endpoints

    @classmethod
    def parse_file_endpoints(cls, content, rel_path, language):
        endpoints = []
        rel_lower = rel_path.lower()

        if rel_lower.endswith(".postman_collection.json"):
            endpoints.extend(cls._parse_postman_collection(content, rel_path))
            return endpoints

        if rel_lower.endswith(".json") and ('"openapi"' in content or '"swagger"' in content):
            openapi_eps = cls._parse_openapi_json(content, rel_path)
            if openapi_eps:
                return openapi_eps

        if language == "php":
            endpoints.extend(cls._parse_yii2_endpoints(content, rel_path))

        if language in ("typescript", "javascript") and "@Controller" in content:
            endpoints.extend(cls._parse_nestjs_endpoints(content, rel_path))

        # Next.js App Router API routes (app/api/.../route.ts)
        if re.search(r"/api/.*/route\.(ts|tsx|js|jsx)$", "/" + rel_lower):
            route_path = re.sub(r"^.*(/api/.*?)/route\.(ts|tsx|js|jsx)$", r"\1", "/" + rel_path)
            for hm in re.finditer(r"export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE)\b", content):
                endpoints.append(
                    EndpointDef(
                        method=hm.group(1),
                        path=route_path,
                        source_file=rel_path,
                        framework="Next.js App Router",
                        group="Next.js API",
                    )
                )

        # Language-scoped pattern sweep across Python, Laravel, Symfony, Express, Spring, C#, Go
        for fw_label, allowed_langs, pat in cls.FRAMEWORK_ROUTE_PATTERNS:
            if language not in allowed_langs:
                continue
            for m in pat.finditer(content):
                groups = m.groups()
                if fw_label == "Python (Flask/Route)":
                    path_str = groups[0]
                    method_str = re.sub(r"[\s'\"]", "", groups[1]) if len(groups) > 1 and groups[1] else "GET"
                elif fw_label in ("Python (Django URL)", "PHP (Symfony)"):
                    path_str = "/" + groups[0].lstrip("/")
                    method_str = "VIEW/ACTION"
                else:
                    method_str = groups[0].upper()
                    path_str = groups[1] if len(groups) > 1 and groups[1] is not None else "/"
                    if not path_str.startswith("/"):
                        path_str = "/" + path_str
                endpoints.append(
                    EndpointDef(
                        method=method_str,
                        path=path_str,
                        source_file=rel_path,
                        framework=fw_label,
                    )
                )

        return endpoints

    @classmethod
    def parse_repository(cls, scan_result):
        """Extract and deduplicate API endpoints across the entire scanned repository."""
        collected = []
        seen = set()
        for sf in scan_result.files:
            for ep in cls.parse_file_endpoints(sf.content, sf.rel_path, sf.language):
                key = (ep.method, ep.path, ep.source_file, ep.handler)
                if key not in seen:
                    seen.add(key)
                    collected.append(ep)
        return collected

    @classmethod
    def render_api_summary_markdown(cls, endpoints, project_name):
        if not endpoints:
            return (
                f"# ملخص نقاط الاتصال (API Endpoints) — {project_name}\n\n"
                "لم تُكتشف نقاط REST/HTTP صريحة في الكود أو مجموعات Postman.\n"
                "`[⚠️ BLIND SPOT]` إن وجدت واجهات برمجية خارجية أو ديناميكية، تحقق من إعدادات الخادم أو البوابة.\n"
            )

        grouped = defaultdict(list)
        for ep in endpoints:
            grp = ep.group or ep.framework or "General"
            grouped[grp].append(ep)

        lines = [
            f"# دليل نقاط الاتصال البرمجية (API Reference) — {project_name}",
            "",
            f"- **إجمالي نقاط الاتصال المستخرجة (100% حتمي من الكود و Postman):** {len(endpoints)}",
            "",
        ]
        for grp, items in grouped.items():
            lines.append(f"## {grp} ({len(items)})")
            lines.append("")
            lines.append("| Method | Path | Handler / الاسم | المصادقة | المصدر |")
            lines.append("| :--- | :--- | :--- | :--- | :--- |")
            for ep in items:
                lines.append(
                    f"| `{ep.method}` | `{ep.path}` | `{ep.handler or '-'}` | "
                    f"`{ep.auth_hint or 'TBD'}` | `{ep.source_file}` |"
                )
            lines.append("")
        return "\n".join(lines)


class RouteParser:
    """Backwards-compatible static API wrapper preserved for MDAAR v1 callers."""

    @staticmethod
    def extract_routes_from_code(code_text, file_ext):
        ext = str(file_ext).lower()
        lang_map = {
            ".py": "python",
            ".php": "php",
            ".js": "javascript",
            ".ts": "typescript",
            ".java": "java",
            ".kt": "kotlin",
            ".cs": "csharp",
            ".go": "go",
        }
        language = lang_map.get(ext, "text")
        eps = APISpecParser.parse_file_endpoints(code_text, f"inline{ext}", language)
        return [{"method": ep.method, "path": ep.path, "framework": ep.framework} for ep in eps]


def _split_sql_clauses(body):
    """Split CREATE TABLE body on top-level commas without breaking ENUM(...) or DECIMAL(10,2)."""
    clauses = []
    current = []
    depth = 0
    in_quote = None
    for ch in body:
        if ch in ("'", '"', "`") and in_quote is None:
            in_quote = ch
        elif ch == in_quote:
            in_quote = None
        elif in_quote is None:
            if ch == "(":
                depth += 1
            elif ch == ")" and depth > 0:
                depth -= 1
            elif ch == "," and depth == 0:
                clauses.append("".join(current))
                current = []
                continue
        current.append(ch)
    if current:
        clauses.append("".join(current))
    return clauses


def _mermaid_id(name):
    clean = re.sub(r"[^A-Za-z0-9_]", "_", str(name))
    if clean and clean[0].isdigit():
        clean = "t_" + clean
    return clean or "unknown"


def _camel_to_kebab(name):
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1-\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", s1).lower()
