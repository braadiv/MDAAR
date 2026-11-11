"""
Multi-Agent Documentation Authors, Full-File Mapper, Master Planner, and Impact Analyzer
for MDAAR v2.0.0.
Separates Planner roles ("إعدادات مودل الخطة") from Executor roles ("إعدادات مودل التنفيذ")
and removes the 25k char context cap in favor of Map-Reduce full-repository coverage.
"""

from collections import defaultdict
from pathlib import Path
import re

from mdaar.skills.loader import SkillLoader


# Declarative document catalog across all MDAAR packages
DOC_SPECIFICATIONS = [
    # 00_Governance
    ("00_Governance/Documentation-Policy.md", "governance", "صياغة سياسة التوثيق وحوكمة الاعتماد والتحديث الحي وفق بنية المشروع الفعلية."),
    ("00_Governance/Documentation-Standards.md", "governance", "تحديد معايير كتابة الوثائق البرمجية، التسمية، المخططات، وقواعد منع الهلوسة."),
    ("00_Governance/Glossary.md", "governance", "بناء قاموس المصطلحات الموحد (عربي/إنجليزي) من مسميات الجداول والكيانات والوحدات الفعلية في الكود."),
    ("00_Governance/AI-Governance.md", "governance", "توثيق ضوابط استخدام نماذج الذكاء الاصطناعي، تعقيم الأسرار، ومراجعة L0-L4 في هذا المشروع."),
    # 01_Administrative
    ("01_Administrative/Project-Charter.md", "governance", "بناء ميثاق المشروع وأهدافه وأصحاب المصلحة والمستفيدين بناءً على الوحدات الفعلية في الكود."),
    ("01_Administrative/Business-Case.md", "governance", "تلخيص القيمة العملية والمشكلات التي يعالجها النظام ومؤشرات النجاح المستنتجة من المزايا المنفذة."),
    ("01_Administrative/Scope.md", "governance", "تحديد نطاق المشروع الدقيق (In-Scope / Out-of-Scope / Blind Spots) من الملفات والواجهات المكتشفة."),
    ("01_Administrative/BRD.md", "governance", "توثيق متطلبات الأعمال وتدفق العمليات التشغيلية من منظور إداري غير تقني."),
    ("01_Administrative/Risk-Register.md", "governance", "رصد المخاطر التقنية والأمنية والتشغيلية الفعلية المستخرجة من الكود والإعدادات."),
    ("01_Administrative/Decision-Log.md", "governance", "توثيق القرارات المعمارية والتقنية الرئيسية (ADRs) المتخذة في الكود وتبعاتها."),
    # 02_Technical
    ("02_Technical/SRS.md", "architecture", "صياغة مواصفات متطلبات النظام الوظيفية (لكل وحدة حقيقية) وغير الوظيفية والأدوار والصلاحيات."),
    ("02_Technical/SAD.md", "architecture", "بناء وثيقة معمارية النظام الشاملة، الطبقات الفعلية، المكونات، وتدفق البيانات بمخططات Mermaid."),
    ("02_Technical/HLD.md", "architecture", "توثيق التصميم عالي المستوى وتكامل الوحدات والخدمات الخارجية والواجهات."),
    ("02_Technical/LLD.md", "architecture", "توثيق التصميم التفصيلي للوحدات الأساسية، الكلاسات، النماذج، الأحداث (Behaviors/Events)، وسلسلة التنفيذ."),
    ("02_Technical/Database-ERD.md", "database", "توثيق معمارية قاعدة البيانات، الجداول الحقيقية، المفاتيح والعلاقات، والهجرات."),
    ("02_Technical/Data-Dictionary.md", "database", "توثيق قاموس البيانات التفصيلي لجميع الأعمدة والأنواع والقيود لكل جدول حقيقي."),
    ("02_Technical/API-Reference.md", "api", "توثيق مرجع واجهات البرمجة (Endpoints) والمصادقة وقواعد التحقق وحالات الخطأ من الكود وPostman."),
    ("02_Technical/Integrations.md", "api", "توثيق التكاملات الخارجية (مثل الإشعارات، البريد، بوابات الدفع، المهام المجدولة، والخدمات الطرفية)."),
    # 03_Operations
    ("03_Operations/Installation-Guide.md", "operations", "إعداد دليل التثبيت والاعتماديات (PHP/Python/Node/Composer/Docker) خطوة بخطوة."),
    ("03_Operations/Configuration-Guide.md", "operations", "توثيق ملفات التكوين ومتغيرات البيئة المطلوبة بدون كشف أي أسرار حقيقية."),
    ("03_Operations/Deployment-Guide.md", "operations", "توثيق إجراءات النشر وخطوط CI/CD أو رصد نقاط النقص [⚠️ BLIND SPOT] إن لم توجد ملفات نشر."),
    ("03_Operations/Security-Guide.md", "operations", "توثيق آليات المصادقة، الصلاحيات (RBAC)، التشفير، وتوصيات الأمن المستخرجة من الكود."),
    ("03_Operations/Troubleshooting.md", "operations", "دليل استكشاف الأعطال الشائعة، السجلات، وأخطاء التكوين أو الصلاحيات بناءً على الكود."),
    ("03_Operations/Backup-Recovery.md", "operations", "توثيق خطة النسخ الاحتياطي والاستعادة وتحديد أي فجوات غير موجودة في الكود بـ [⚠️ BLIND SPOT]."),
    # 04_User-Guides
    ("04_User-Guides/User-Manual.md", "user_guide", "صياغة دليل المستخدم النهائي بلغة واضحة تشرح الشاشات أو الوظائف المتاحة فعلياً خطوة بخطوة."),
    ("04_User-Guides/Admin-Manual.md", "user_guide", "صياغة دليل مسؤول النظام لإدارة الصلاحيات، المستخدمين، والإعدادات الرقابية."),
    ("04_User-Guides/FAQs.md", "user_guide", "تجميع الأسئلة الشائعة وإجاباتها العملية استناداً إلى وظائف النظام الفعلية."),
    # 05_Release-Documentation
    ("05_Release-Documentation/Release-Notes.md", "governance", "توثيق ملاحظات الإصدار الحالية المرتبطة بـ Git Commit الفعلي وتاريخ التغييرات."),
]


class BaseDocAgent:
    """Grounded documentation agent powered by MDAAR bundled skills and dual-model routing."""

    def __init__(
        self,
        name,
        gateway,
        project_name,
        role="architecture",
        skill_loader=None,
        repo_instructions="",
    ):
        self.name = name
        self.gateway = gateway
        self.project_name = project_name
        self.role = role
        self.skill_loader = skill_loader or SkillLoader()
        self.repo_instructions = repo_instructions

    def _build_system_prompt(self, doc_name):
        skills_text = self.skill_loader.load_for_role(self.role, self.repo_instructions)
        return (
            f"أنت وكيل توثيق هندسي محترف ({self.name}) ضمن منظومة MDAAR v2.0.\n"
            f"المشروع المستهدف: {self.project_name}\n"
            f"الوثيقة المطلوبة: {doc_name}\n\n"
            "التعليمات والمهارات الملزمة (اتبعها حرفياً ولا تخترع أي معلومة غير موجودة في الأدلة):\n"
            f"{skills_text}"
        )

    def execute(self, doc_name, context, specific_goal=""):
        sys_prompt = self._build_system_prompt(doc_name)
        user_prompt = (
            f"الهدف المحدد للوثيقة `{doc_name}`:\n{specific_goal}\n\n"
            f"الأدلة والحقائق الموثقة من الكود المصدري:\n{context}\n\n"
            "المطلوب: كتابة الوثيقة كاملة بتنسيق Markdown احترافي جاهز للنشر، "
            "مع الالتزام التام بذكر المسارات والكلاسات والجداول الحقيقية فقط، "
            "ووضع `[TBD]` أو `[⚠️ BLIND SPOT]` لأي معلومة غير متوفرة في المستودع."
        )
        return self._invoke_gateway(user_prompt, sys_prompt)

    def _invoke_gateway(self, user_prompt, sys_prompt):
        # Supports both raw ModelGateway and DualModelManager transparently
        if hasattr(self.gateway, "generate_as_executor"):
            return self.gateway.generate_as_executor(user_prompt, sys_prompt)
        return self.gateway.generate(user_prompt, sys_prompt)


class ArchitectureAgent(BaseDocAgent):
    def __init__(self, gateway, project_name, **kwargs):
        super().__init__("Architecture & System Design Agent", gateway, project_name, role="architecture", **kwargs)


class DatabaseAgent(BaseDocAgent):
    def __init__(self, gateway, project_name, **kwargs):
        super().__init__("Database & Schema Agent", gateway, project_name, role="database", **kwargs)


class APIAgent(BaseDocAgent):
    def __init__(self, gateway, project_name, **kwargs):
        super().__init__("API & Integration Documentation Agent", gateway, project_name, role="api", **kwargs)


class OperationsAgent(BaseDocAgent):
    def __init__(self, gateway, project_name, **kwargs):
        super().__init__("Operations, Security & DevOps Agent", gateway, project_name, role="operations", **kwargs)


class UserGuideAgent(BaseDocAgent):
    def __init__(self, gateway, project_name, **kwargs):
        super().__init__("User Experience & Manuals Agent", gateway, project_name, role="user_guide", **kwargs)


class GovernanceAgent(BaseDocAgent):
    def __init__(self, gateway, project_name, **kwargs):
        super().__init__("Governance, Planning & Charter Agent", gateway, project_name, role="governance", **kwargs)


class MapperAgent(BaseDocAgent):
    """
    Runs the 100% Full-File Map Pass on the Executor Model ("إعدادات مودل التنفيذ").
    Smartly batches small related files within the same module/layer up to batch_char_limit,
    while iterating large files chunk-by-chunk so zero lines and zero files are ever skipped.
    """

    def __init__(self, gateway, project_name, **kwargs):
        super().__init__("Full-Codebase Inspector (Map Agent)", gateway, project_name, role="mapper", **kwargs)

    def inspect_scan_result(self, scan_result, fact_sheet, batch_char_limit=7500, progress_callback=None):
        """
        Inspect EVERY chunk of EVERY file in scan_result during this session.
        Returns a dict keyed by top module/directory containing deep factual summaries.
        """
        batches = self._build_lossless_batches(scan_result, batch_char_limit=batch_char_limit)
        module_findings = defaultdict(list)
        total_batches = len(batches)

        sys_prompt = self._build_system_prompt("Codebase Map Inspection")
        header_context = (
            f"Project: {fact_sheet.project_name} | Commit: {fact_sheet.git_trace} | "
            f"Languages: {fact_sheet.language_counts} | Frameworks: {fact_sheet.detected_frameworks}"
        )

        for idx, batch in enumerate(batches, 1):
            module_name = batch["module"]
            files_desc = ", ".join(item["label"] for item in batch["items"])
            code_blocks = "\n\n".join(
                f"=== FILE CHUNK: {item['label']} (Lines {item['start_line']}-{item['end_line']}) ===\n{item['content']}"
                for item in batch["items"]
            )
            prompt = (
                f"{header_context}\n\n"
                f"حلل بدقة الكود الكامل التالي ضمن الوحدة `{module_name}` ({files_desc}):\n\n"
                f"{code_blocks}\n\n"
                "أخرج ملخصاً هندسياً مركزاً ودقيقاً يشمل فقط ما ورد في هذا الكود:\n"
                "1. وظيفة كل ملف وأهم الكلاسات/الدوال.\n"
                "2. قواعد التحقق (Validation)، الصلاحيات (RBAC/Auth)، والأحداث (Behaviors/Events).\n"
                "3. الجداول أو نقاط الاتصال أو التكاملات الخارجية المشار إليها.\n"
                "4. أي ثغرات أو اعتماديات غير واضحة [⚠️ BLIND SPOT]."
            )

            res = self._invoke_gateway(prompt, sys_prompt)
            # If the LLM call fails or is unavailable, still preserve deterministic FileFactCards
            if res and not str(res).lstrip().startswith("⚠️ "):
                entry = f"#### [{files_desc}]\n{res.strip()}"
            else:
                entry = self._fallback_card_summary(batch["items"], fact_sheet)

            module_findings[module_name].append(entry)
            if progress_callback:
                progress_callback(idx, total_batches, module_name, files_desc)

        return dict(module_findings)

    @staticmethod
    def _fallback_card_summary(items, fact_sheet):
        card_map = {c.rel_path: c for c in fact_sheet.file_cards}
        lines = []
        seen = set()
        for item in items:
            rp = item["rel_path"]
            if rp in seen:
                continue
            seen.add(rp)
            card = card_map.get(rp)
            lines.append(card.to_compact_line() if card else f"- `{rp}` (Lines {item['start_line']}-{item['end_line']})")
        return "\n".join(lines)

    @staticmethod
    def _build_lossless_batches(scan_result, batch_char_limit=7500):
        """Group small files in the same module while keeping every line of every chunk intact."""
        by_module = defaultdict(list)
        for sf in scan_result.files:
            parts = sf.rel_path.split("/")
            mod = parts[0] if len(parts) > 1 else "(root)"
            for ch in sf.chunks:
                label = (
                    f"{sf.rel_path}"
                    if ch.total_chunks == 1
                    else f"{sf.rel_path} [part {ch.chunk_index}/{ch.total_chunks}]"
                )
                by_module[mod].append(
                    {
                        "rel_path": sf.rel_path,
                        "label": label,
                        "start_line": ch.start_line,
                        "end_line": ch.end_line,
                        "content": ch.content,
                    }
                )

        batches = []
        for mod in sorted(by_module.keys()):
            current_items = []
            current_chars = 0
            for item in by_module[mod]:
                item_len = len(item["content"])
                if current_items and current_chars + item_len > batch_char_limit:
                    batches.append({"module": mod, "items": current_items})
                    current_items = []
                    current_chars = 0
                current_items.append(item)
                current_chars += item_len
                if current_chars >= batch_char_limit:
                    batches.append({"module": mod, "items": current_items})
                    current_items = []
                    current_chars = 0
            if current_items:
                batches.append({"module": mod, "items": current_items})
        return batches


class PlannerAgent(BaseDocAgent):
    """
    Runs the Strategic Master Documentation Plan using the Planner Model ("إعدادات مودل الخطة").
    Synthesizes the deterministic ProjectFactSheet, DB schema, API catalog, and Executor Map insights
    into a comprehensive execution blueprint for the authoring agents.
    """

    def __init__(self, gateway, project_name, **kwargs):
        super().__init__("Master Documentation Planner", gateway, project_name, role="planner", **kwargs)

    def _invoke_gateway(self, user_prompt, sys_prompt):
        if hasattr(self.gateway, "generate_as_planner"):
            return self.gateway.generate_as_planner(user_prompt, sys_prompt, max_new_tokens=3072)
        return self.gateway.generate(user_prompt, sys_prompt)

    def build_master_plan(self, fact_sheet, module_findings, db_summary_hint="", api_summary_hint=""):
        """Create the Master Architectural & Documentation Plan guiding every Executor document."""
        sys_prompt = self._build_system_prompt("Master Documentation Blueprint")
        digest_lines = []
        for mod, notes in sorted(module_findings.items()):
            joined = "\n".join(notes)
            # Keep planner prompt well-structured while surfacing every module
            if len(joined) > 1800:
                joined = joined[:1800] + "\n... [تفاصيل الوحدة الكاملة متاحة في مرحله التنفيذ]"
            digest_lines.append(f"### الوحدة `{mod}`\n{joined}")

        modules_digest = "\n\n".join(digest_lines) or "لا توجد ملفات إضافية."
        prompt = (
            f"{fact_sheet.to_prompt_context(max_cards=180)}\n\n"
            f"ملخص قاعدة البيانات المكتشفة:\n{db_summary_hint or 'لم تُكتشف جداول SQL.'}\n\n"
            f"ملخص نقاط الاتصال API المكتشفة:\n{api_summary_hint or 'لم تُكتشف نقاط API.'}\n\n"
            f"نتائج الفحص الشامل (Map Pass) لوحدات المشروع:\n{modules_digest}\n\n"
            "المطلوب بصفتك مهندس الخطة (Planner Model):\n"
            "ضع خطة توثيق هندسية متكاملة ومقيدة بالحقائق الفعلية للمشروع تشمل:\n"
            "1. الهوية الحقيقية للنظام ومجاله الوظيفي ومكدسه التقني الفعلي.\n"
            "2. تفصيل المكونات الرئيسية وأدوار المستخدمين (RBAC) وتدفقات العمل الأساسية.\n"
            "3. خريطة توزيع المحتوى على الحزم الأربع (الإدارية، الفنية، التشغيلية، أدلة الاستخدام).\n"
            "4. قائمة نقاط الضعف والمناطق العمياء `[⚠️ BLIND SPOT]` التي يجب تنبيه الكُتّاب إليها لمنع الاختراع.\n"
            "5. قائمة المصطلحات الموحدة المعتمدة في المشروع."
        )
        res = self._invoke_gateway(prompt, sys_prompt)
        if not res or str(res).lstrip().startswith("⚠️ "):
            return self._deterministic_fallback_plan(fact_sheet, db_summary_hint, api_summary_hint)
        return res

    @staticmethod
    def _deterministic_fallback_plan(fact_sheet, db_summary_hint, api_summary_hint):
        return (
            f"# خطة التوثيق المرجعية — {fact_sheet.project_name}\n\n"
            f"- **اللغات والبيئة الفعلية:** {fact_sheet.language_counts}\n"
            f"- **الأطر المكتشفة:** {', '.join(fact_sheet.detected_frameworks) or 'غير محددة'}\n"
            f"- **نقاط الدخول:** {', '.join(fact_sheet.entry_points) or '[TBD]'}\n"
            f"- **الوحدات الرئيسية:** {', '.join(fact_sheet.top_directories.keys())}\n"
            f"- **قاعدة البيانات:** {db_summary_hint or 'لا يوجد'}\n"
            f"- **الواجهات البرمجية:** {api_summary_hint or 'لا يوجد'}\n"
            "- `[⚠️ BLIND SPOT]` تحقّق من البنية التحتية السحابية وأسرار البيئة خارج المستودع.\n"
        )


def create_agent_for_role(role, gateway, project_name, skill_loader=None, repo_instructions=""):
    """Factory returning the specialized doc agent class for a given role."""
    mapping = {
        "architecture": ArchitectureAgent,
        "database": DatabaseAgent,
        "api": APIAgent,
        "operations": OperationsAgent,
        "user_guide": UserGuideAgent,
        "governance": GovernanceAgent,
    }
    cls = mapping.get(role, ArchitectureAgent)
    return cls(gateway, project_name, skill_loader=skill_loader, repo_instructions=repo_instructions)


class ImpactAnalyzer:
    """Maps changed source files from Git diff to corresponding documentation artifacts."""

    @staticmethod
    def analyze_impact(changed_files):
        impact_map = {
            "Database": {
                "role": "database",
                "agent": "DatabaseAgent",
                "docs": ["02_Technical/Database-ERD.md", "02_Technical/Data-Dictionary.md"],
            },
            "API": {
                "role": "api",
                "agent": "APIAgent",
                "docs": ["02_Technical/API-Reference.md", "02_Technical/Integrations.md"],
            },
            "Architecture": {
                "role": "architecture",
                "agent": "ArchitectureAgent",
                "docs": ["02_Technical/SAD.md", "02_Technical/HLD.md", "02_Technical/LLD.md", "02_Technical/SRS.md"],
            },
            "Operations": {
                "role": "operations",
                "agent": "OperationsAgent",
                "docs": [
                    "03_Operations/Installation-Guide.md",
                    "03_Operations/Configuration-Guide.md",
                    "03_Operations/Deployment-Guide.md",
                    "03_Operations/Security-Guide.md",
                ],
            },
            "UserGuides": {
                "role": "user_guide",
                "agent": "UserGuideAgent",
                "docs": ["04_User-Guides/User-Manual.md", "04_User-Guides/Admin-Manual.md"],
            },
            "Governance": {
                "role": "governance",
                "agent": "GovernanceAgent",
                "docs": ["05_Release-Documentation/Release-Notes.md", "01_Administrative/Scope.md"],
            },
        }

        affected = {"Governance"} if changed_files else set()
        for f in changed_files:
            lower_f = f.lower()
            if any(kw in lower_f for kw in ("migration", "schema", ".sql", "model", "entities", "prisma")):
                affected.add("Database")
            if any(kw in lower_f for kw in ("controller", "route", "api", "endpoint", "router", "postman", "openapi")):
                affected.add("API")
            if any(kw in lower_f for kw in ("docker", "ci", "workflow", ".github", "deploy", "compose", "config", ".env", "composer.json", "package.json", "requirements")):
                affected.add("Operations")
            if any(kw in lower_f for kw in ("view", "page", "ui", "frontend", "template", "screen", "widget")):
                affected.add("UserGuides")
            if any(kw in lower_f for kw in ("core", "service", "module", "app", "src", "components", "helpers")):
                affected.add("Architecture")

        return [impact_map[cat] for cat in sorted(affected)]
