"""
Polyglot AST/Pattern Fact Extractor for MDAAR v2.0.0.
Deterministically extracts symbols, dependencies, frameworks, RBAC rules,
events/behaviors, background jobs, and external integrations across all major
programming languages without relying on LLMs to guess basic repository facts.
"""

import ast
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
import re


MANIFEST_FRAMEWORKS = {
    "yiisoft/yii2": "Yii2 (PHP)",
    "laravel/framework": "Laravel (PHP)",
    "symfony/framework-bundle": "Symfony (PHP)",
    "slim/slim": "Slim (PHP)",
    "fastapi": "FastAPI (Python)",
    "django": "Django (Python)",
    "flask": "Flask (Python)",
    "celery": "Celery (Python)",
    "sqlalchemy": "SQLAlchemy (Python)",
    "next": "Next.js (React/TS)",
    "react": "React",
    "vue": "Vue.js",
    "nuxt": "Nuxt.js",
    "express": "Express.js (Node)",
    "@nestjs/core": "NestJS (Node/TS)",
    "prisma": "Prisma ORM",
    "typeorm": "TypeORM",
    "spring-boot": "Spring Boot (Java/Kotlin)",
    "microsoft.aspnetcore": "ASP.NET Core (C#)",
    "github.com/gin-gonic/gin": "Gin (Go)",
    "github.com/gofiber/fiber": "Fiber (Go)",
    "github.com/labstack/echo": "Echo (Go)",
    "rails": "Ruby on Rails",
    "actix-web": "Actix Web (Rust)",
    "axum": "Axum (Rust)",
    "flutter": "Flutter (Dart)",
}

INTEGRATION_HINTS = {
    "firebase": re.compile(r"\b(?:firebase|fcm\.googleapis|service_account|messaging)\b", re.IGNORECASE),
    "stripe_payment": re.compile(r"\bstripe\b", re.IGNORECASE),
    "twilio_sms": re.compile(r"\btwilio\b", re.IGNORECASE),
    "smtp_email": re.compile(r"\b(?:Swift_Mailer|PHPMailer|nodemailer|smtplib|SendGrid|Mailgun)\b", re.IGNORECASE),
    "s3_object_storage": re.compile(r"\b(?:boto3|s3client|minio|aws-sdk)\b", re.IGNORECASE),
    "redis_cache_queue": re.compile(r"\b(?:redis|predis|ioredis|aioredis)\b", re.IGNORECASE),
    "jwt_oauth": re.compile(r"\b(?:jwt|oauth2|passport|sanctum|firebase/php-jwt|PyJWT)\b", re.IGNORECASE),
    "chat_messaging": re.compile(r"\b(?:whatsapp|telegram|slack|discord)\b", re.IGNORECASE),
}

RBAC_PATTERN = re.compile(
    r"(?:"
    r"AccessControl|behaviors\s*\(|Yii::\$app->user->can\s*\("
    r"|Gate::define|@middleware|->middleware\(|@login_required|@permission_required"
    r"|@PreAuthorize|@Secured|\[Authorize|UseGuards\("
    r"|role_based|roles\s*=>|allow\s*=>\s*true|matchCallback"
    r")",
    re.IGNORECASE,
)

EVENT_BEHAVIOR_PATTERN = re.compile(
    r"(?:"
    r"Event::on\(|->trigger\(|->on\(|public function behaviors\b|public function init\b"
    r"|afterSave|beforeSave|afterFind|beforeDelete|afterValidate|beforeAction"
    r"|@receiver\(|Signal\(|EventEmitter|\.addEventListener\(|@OnEvent\("
    r"|Observer::|Listener\b|Dispatchable|ShouldQueue"
    r")",
)

BACKGROUND_JOB_PATTERN = re.compile(
    r"(?:"
    r"implements\s+JobInterface|yii\\queue|ShouldQueue|@celery\.task|@shared_task"
    r"|APScheduler|CronJob|cron\.schedule|node-cron|BackgroundTasks|Hangfire|IHostedService"
    r"|public function actionCron|crontab"
    r")",
    re.IGNORECASE,
)

# Uniform per-language symbol extraction specifications
LANGUAGE_SYMBOL_PATTERNS = {
    "php": {
        "namespace": re.compile(r"^\s*namespace\s+([A-Za-z0-9_\\]+);", re.MULTILINE),
        "classes": re.compile(
            r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait|enum)\s+([A-Za-z_][A-Za-z0-9_]*)"
            r"(?:\s+extends\s+([A-Za-z0-9_\\]+))?(?:\s+implements\s+([A-Za-z0-9_\\,\s]+))?",
            re.MULTILINE,
        ),
        "functions": re.compile(
            r"^\s*(?:public|protected|private)?\s*(?:static\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            re.MULTILINE,
        ),
        "db_tables": re.compile(
            r"return\s+['\"]\{\{?%?([A-Za-z0-9_]+)%?\}?\}['\"]|"
            r"(?:FROM|INTO|UPDATE|JOIN|TABLE)\s+[`'\"]?([A-Za-z0-9_]+)[`'\"]?",
            re.IGNORECASE,
        ),
        "relations": re.compile(r"->(hasOne|hasMany|belongsTo|hasOneThrough)\s*\(\s*([A-Za-z0-9_\\]+)::class"),
    },
    "javascript": {
        "classes": re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)", re.MULTILINE),
        "functions": re.compile(
            r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\("
            r"|(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>",
        ),
    },
    "typescript": {
        "classes": re.compile(
            r"^\s*(?:export\s+)?(?:abstract\s+)?(?:class|interface|type|enum)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
            re.MULTILINE,
        ),
        "functions": re.compile(
            r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*[<(]"
            r"|(?:export\s+)?(?:const|let)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>"
            r"|^\s*(?:public|private|protected|async)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(",
            re.MULTILINE,
        ),
    },
    "java": {
        "namespace": re.compile(r"^\s*package\s+([A-Za-z0-9_.]+);", re.MULTILINE),
        "classes": re.compile(
            r"(?:public|private|protected)?\s*(?:abstract\s+|final\s+)?(?:class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)",
        ),
        "functions": re.compile(
            r"^\s*(?:public|protected|private)\s+(?:static\s+)?[A-Za-z0-9_<>\[\],\s]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            re.MULTILINE,
        ),
    },
    "kotlin": {
        "namespace": re.compile(r"^\s*package\s+([A-Za-z0-9_.]+)", re.MULTILINE),
        "classes": re.compile(r"(?:data\s+|sealed\s+|open\s+)?(?:class|interface|object|enum\s+class)\s+([A-Za-z_][A-Za-z0-9_]*)"),
        "functions": re.compile(r"\bfun\s+(?:<[^>]+>\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\("),
    },
    "csharp": {
        "namespace": re.compile(r"^\s*namespace\s+([A-Za-z0-9_.]+)", re.MULTILINE),
        "classes": re.compile(r"(?:public|internal|private|protected)\s+(?:partial\s+|sealed\s+|abstract\s+|static\s+)?(?:class|interface|record|struct|enum)\s+([A-Za-z_][A-Za-z0-9_]*)"),
        "functions": re.compile(
            r"^\s*(?:public|private|protected|internal)\s+(?:static\s+|async\s+|virtual\s+|override\s+)*[A-Za-z0-9_<>\[\]?]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            re.MULTILINE,
        ),
    },
    "go": {
        "namespace": re.compile(r"^\s*package\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE),
        "classes": re.compile(r"^\s*type\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:struct|interface)\b", re.MULTILINE),
        "functions": re.compile(r"^\s*func\s+(?:\([^)]+\)\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE),
    },
    "ruby": {
        "classes": re.compile(r"^\s*(?:class|module)\s+([A-Za-z_][A-Za-z0-9_:]*)", re.MULTILINE),
        "functions": re.compile(r"^\s*def\s+(?:self\.)?([A-Za-z_][A-Za-z0-9_?!]*)", re.MULTILINE),
    },
    "rust": {
        "classes": re.compile(r"^\s*(?:pub(?:\([^)]+\))?\s+)?(?:struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE),
        "functions": re.compile(r"^\s*(?:pub(?:\([^)]+\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE),
    },
    "dart": {
        "classes": re.compile(r"^\s*(?:abstract\s+)?(?:class|mixin|enum)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE),
        "functions": re.compile(r"^\s*(?:Future<[^>]+>|void|String|int|bool|dynamic|Widget)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE),
    },
}


@dataclass
class FileFactCard:
    """Compact deterministic structural card extracted from a single ScannedFile."""

    rel_path: str
    language: str
    layer: str
    line_count: int
    namespaces: list = field(default_factory=list)
    classes: list = field(default_factory=list)
    functions: list = field(default_factory=list)
    imports_or_deps: list = field(default_factory=list)
    db_tables: list = field(default_factory=list)
    relations: list = field(default_factory=list)
    rbac_signals: list = field(default_factory=list)
    events_or_behaviors: list = field(default_factory=list)
    background_jobs: list = field(default_factory=list)
    integrations: list = field(default_factory=list)

    def to_compact_line(self):
        parts = [f"- `{self.rel_path}` ({self.language}/{self.layer}, {self.line_count} lines)"]
        if self.classes:
            parts.append(f"classes=[{', '.join(self.classes[:12])}]")
        if self.functions:
            parts.append(f"funcs=[{', '.join(self.functions[:15])}]")
        if self.db_tables:
            parts.append(f"tables=[{', '.join(self.db_tables[:10])}]")
        if self.relations:
            parts.append(f"relations=[{', '.join(self.relations[:8])}]")
        if self.rbac_signals:
            parts.append("RBAC=yes")
        if self.events_or_behaviors:
            parts.append(f"behaviors/events=[{', '.join(self.events_or_behaviors[:6])}]")
        if self.background_jobs:
            parts.append("jobs=yes")
        return " | ".join(parts)


@dataclass
class ProjectFactSheet:
    """Unified verified project snapshot built deterministically from 100% of files."""

    project_name: str
    git_trace: str
    git_branch: str
    total_files: int
    total_lines: int
    language_counts: dict = field(default_factory=dict)
    layer_counts: dict = field(default_factory=dict)
    detected_frameworks: list = field(default_factory=list)
    package_dependencies: dict = field(default_factory=dict)
    entry_points: list = field(default_factory=list)
    config_files: list = field(default_factory=list)
    devops_files: list = field(default_factory=list)
    top_directories: dict = field(default_factory=dict)
    integrations: list = field(default_factory=list)
    rbac_files: list = field(default_factory=list)
    event_behavior_files: list = field(default_factory=list)
    background_job_files: list = field(default_factory=list)
    file_cards: list = field(default_factory=list)
    verified_identifiers: set = field(default_factory=set)

    def to_dict(self):
        return {
            "project_name": self.project_name,
            "git_trace": self.git_trace,
            "git_branch": self.git_branch,
            "total_files": self.total_files,
            "total_lines": self.total_lines,
            "language_counts": self.language_counts,
            "layer_counts": self.layer_counts,
            "detected_frameworks": self.detected_frameworks,
            "package_dependencies": self.package_dependencies,
            "entry_points": self.entry_points,
            "config_files": self.config_files,
            "devops_files": self.devops_files,
            "top_directories": self.top_directories,
            "integrations": self.integrations,
            "rbac_files": self.rbac_files,
            "event_behavior_files": self.event_behavior_files,
            "background_job_files": self.background_job_files,
            "file_count_in_cards": len(self.file_cards),
        }

    def to_prompt_context(self, max_cards=250):
        """Render an exact ground-truth brief for LLM prompts (never fabricates symbols)."""
        langs = ", ".join(f"{k} ({v})" for k, v in self.language_counts.items()) or "None detected"
        layers = ", ".join(f"{k} ({v})" for k, v in self.layer_counts.items()) or "None detected"
        fws = ", ".join(self.detected_frameworks) or "Custom / Pure Language Stack"
        top_dirs = ", ".join(f"{d}/ ({c} files)" for d, c in list(self.top_directories.items())[:25])

        lines = [
            "=== VERIFIED PROJECT FACT SHEET (DETERMINISTIC SOURCE OF TRUTH) ===",
            f"Project: {self.project_name}",
            f"Git Commit Trace: {self.git_trace} (branch: {self.git_branch})",
            f"Scanned Scope: {self.total_files} non-ignored files, {self.total_lines:,} lines total (100% coverage)",
            f"Languages: {langs}",
            f"Frameworks / Runtime Stack: {fws}",
            f"Architectural Layers: {layers}",
            f"Top Directories: {top_dirs or '(root only)'}",
            f"Entry Points: {', '.join(self.entry_points[:20]) or '[TBD - inspect codebase]'}",
            f"Config Files: {', '.join(self.config_files[:25]) or 'None'}",
            f"DevOps/CI Files: {', '.join(self.devops_files[:15]) or 'None'}",
            f"External Integrations Detected: {', '.join(self.integrations) or 'None detected'}",
            f"RBAC/Auth Signals in {len(self.rbac_files)} files: {', '.join(self.rbac_files[:20]) or 'None detected'}",
            f"Behaviors/Events Signals in {len(self.event_behavior_files)} files: {', '.join(self.event_behavior_files[:20]) or 'None detected'}",
            f"Background Jobs/Cron Signals in {len(self.background_job_files)} files: {', '.join(self.background_job_files[:15]) or 'None detected'}",
        ]

        if self.package_dependencies:
            lines.append("Dependencies by Manifest:")
            for manifest, deps in list(self.package_dependencies.items())[:8]:
                dep_preview = ", ".join(deps[:25])
                if len(deps) > 25:
                    dep_preview += f" ... (+{len(deps) - 25} more)"
                lines.append(f"  - {manifest}: {dep_preview}")

        lines.append("\nExtracted File Fact Cards:")
        for card in self.file_cards[:max_cards]:
            lines.append(card.to_compact_line())
        if len(self.file_cards) > max_cards:
            lines.append(f"... (+{len(self.file_cards) - max_cards} additional files analyzed in full Map pass)")

        return "\n".join(lines)


class PolyglotFactExtractor:
    """Extracts deterministic symbol, dependency, and architectural facts for any codebase."""

    @staticmethod
    def _extract_python_ast(text):
        """Use Python's built-in ast module for zero-guesswork Python extraction."""
        classes, functions, imports = [], [], []
        try:
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    bases = []
                    for b in node.bases:
                        if isinstance(b, ast.Name):
                            bases.append(b.id)
                        elif isinstance(b, ast.Attribute):
                            bases.append(b.attr)
                    label = f"{node.name}({', '.join(bases)})" if bases else node.name
                    classes.append(label)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions.append(node.name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module.split(".")[0])
        except SyntaxError:
            classes = re.findall(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.MULTILINE)
            functions = re.findall(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.MULTILINE)
        return _unique(classes), _unique(functions), _unique(imports)

    @staticmethod
    def _extract_regex_symbols(text, language):
        spec = LANGUAGE_SYMBOL_PATTERNS.get(language, {})
        namespaces, classes, functions, db_tables, relations = [], [], [], [], []

        if "namespace" in spec:
            namespaces = _unique(spec["namespace"].findall(text))

        if "classes" in spec:
            for match in spec["classes"].findall(text):
                if isinstance(match, tuple):
                    cname = match[0]
                    extends = match[1] if len(match) > 1 else ""
                    classes.append(f"{cname} -> {extends}" if extends else cname)
                else:
                    classes.append(match)

        if "functions" in spec:
            for match in spec["functions"].findall(text):
                name = next((m for m in match if m), "") if isinstance(match, tuple) else match
                if name and name not in ("if", "for", "while", "switch", "catch", "function"):
                    functions.append(name)

        if "db_tables" in spec:
            for match in spec["db_tables"].findall(text):
                tname = next((m for m in match if m), "") if isinstance(match, tuple) else match
                if tname and not tname.isupper() and len(tname) > 1:
                    db_tables.append(tname)

        if "relations" in spec:
            for rel_type, target in spec["relations"].findall(text):
                relations.append(f"{rel_type}->{target.split(chr(92))[-1]}")

        return _unique(namespaces), _unique(classes), _unique(functions), _unique(db_tables), _unique(relations)

    @staticmethod
    def _parse_manifest_dependencies(rel_path, content):
        """Parse composer.json, package.json, requirements.txt, pyproject.toml, pom.xml, go.mod."""
        name_lower = Path(rel_path).name.lower()
        deps = []
        frameworks = []

        if name_lower == "composer.json":
            try:
                data = json.loads(content)
                for sec in ("require", "require-dev"):
                    if isinstance(data.get(sec), dict):
                        deps.extend(list(data[sec].keys()))
            except Exception:
                pass
        elif name_lower == "package.json":
            try:
                data = json.loads(content)
                for sec in ("dependencies", "devDependencies"):
                    if isinstance(data.get(sec), dict):
                        deps.extend(list(data[sec].keys()))
            except Exception:
                pass
        elif name_lower.startswith("requirements") and name_lower.endswith(".txt"):
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                pkg = re.split(r"[=<>!~\[\];\s]", line)[0].strip()
                if pkg:
                    deps.append(pkg)
        elif name_lower == "pyproject.toml":
            for m in re.findall(r'["\']([A-Za-z0-9_.\-]+)(?:[=<>!~][^"\']*)?["\']', content):
                if len(m) > 1:
                    deps.append(m)
        elif name_lower == "go.mod":
            for m in re.findall(r"^\s*require\s+([^\s]+)|^\s+([a-z0-9.\-]+/[^\s]+)\s+v", content, re.MULTILINE):
                pkg = next((x for x in m if x), "")
                if pkg:
                    deps.append(pkg)
        elif name_lower == "pom.xml":
            deps = re.findall(r"<artifactId>([^<]+)</artifactId>", content)
        elif name_lower == "cargo.toml":
            deps = re.findall(r"^([A-Za-z0-9_\-]+)\s*=\s*(?:[\"']|\{)", content, re.MULTILINE)
        elif name_lower == "pubspec.yaml":
            deps = re.findall(r"^\s{2}([a-z0-9_]+)\s*:", content, re.MULTILINE)

        joined_lower = " ".join(deps).lower()
        for key, fw_label in MANIFEST_FRAMEWORKS.items():
            if key.lower() in joined_lower:
                frameworks.append(fw_label)

        return _unique(deps), _unique(frameworks)

    def extract(self, scan_result, project_name="MyProject"):
        """Build a complete ProjectFactSheet from a ScanResult."""
        cards = []
        package_dependencies = {}
        frameworks = []
        entry_points = []
        config_files = []
        devops_files = []
        integrations = set()
        rbac_files = []
        event_behavior_files = []
        background_job_files = []
        top_dirs = Counter()
        verified_identifiers = set()

        entry_names = {
            "index.php", "main.py", "app.py", "wsgi.py", "asgi.py", "manage.py",
            "server.js", "server.ts", "index.js", "index.ts", "main.ts", "app.ts",
            "program.cs", "startup.cs", "main.go", "application.java", "yii", "artisan",
        }

        for sf in scan_result.files:
            rel = sf.rel_path
            parts = rel.split("/")
            top_dir = parts[0] if len(parts) > 1 else "(root)"
            top_dirs[top_dir] += 1
            verified_identifiers.add(rel)
            verified_identifiers.add(Path(rel).name)

            if Path(rel).name.lower() in entry_names:
                entry_points.append(rel)
            if sf.layer == "config":
                config_files.append(rel)
            if sf.layer == "devops":
                devops_files.append(rel)

            deps, fws = self._parse_manifest_dependencies(rel, sf.content)
            if deps:
                package_dependencies[rel] = deps
            frameworks.extend(fws)

            if sf.language == "python":
                classes, functions, imports = self._extract_python_ast(sf.content)
                namespaces, db_tables, relations = [], [], []
            else:
                namespaces, classes, functions, db_tables, relations = self._extract_regex_symbols(
                    sf.content, sf.language
                )
                imports = []

            # Implicit framework hints from source files (e.g. legacy Yii2 without composer.json at root)
            if sf.language == "php":
                if "yii\\" in sf.content or "Yii::" in sf.content:
                    frameworks.append("Yii2 (PHP)")
                elif "Illuminate\\" in sf.content:
                    frameworks.append("Laravel (PHP)")

            rbac_hits = _unique(RBAC_PATTERN.findall(sf.content))
            event_hits = _unique(EVENT_BEHAVIOR_PATTERN.findall(sf.content))
            job_hits = _unique(BACKGROUND_JOB_PATTERN.findall(sf.content))

            file_integrations = []
            for label, pat in INTEGRATION_HINTS.items():
                if pat.search(sf.content):
                    file_integrations.append(label)
                    integrations.add(label)

            if rbac_hits:
                rbac_files.append(rel)
            if event_hits:
                event_behavior_files.append(rel)
            if job_hits:
                background_job_files.append(rel)

            for c in classes:
                verified_identifiers.add(c.split(" ")[0])
            for fn in functions:
                verified_identifiers.add(fn)
            for tb in db_tables:
                verified_identifiers.add(tb)

            cards.append(
                FileFactCard(
                    rel_path=rel,
                    language=sf.language,
                    layer=sf.layer,
                    line_count=sf.line_count,
                    namespaces=namespaces,
                    classes=classes,
                    functions=functions,
                    imports_or_deps=imports,
                    db_tables=db_tables,
                    relations=relations,
                    rbac_signals=rbac_hits,
                    events_or_behaviors=event_hits,
                    background_jobs=job_hits,
                    integrations=file_integrations,
                )
            )

        return ProjectFactSheet(
            project_name=project_name,
            git_trace=scan_result.git_meta.trace_stamp,
            git_branch=scan_result.git_meta.branch,
            total_files=len(scan_result.files),
            total_lines=scan_result.total_lines,
            language_counts=dict(scan_result.language_counts),
            layer_counts=dict(scan_result.layer_counts),
            detected_frameworks=_unique(frameworks),
            package_dependencies=package_dependencies,
            entry_points=_unique(entry_points),
            config_files=_unique(config_files),
            devops_files=_unique(devops_files),
            top_directories=dict(top_dirs.most_common()),
            integrations=sorted(integrations),
            rbac_files=_unique(rbac_files),
            event_behavior_files=_unique(event_behavior_files),
            background_job_files=_unique(background_job_files),
            file_cards=cards,
            verified_identifiers=verified_identifiers,
        )


def _unique(seq):
    """Preserve order while deduplicating empty/whitespace strings."""
    seen = set()
    out = []
    for item in seq:
        clean = str(item).strip()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out
