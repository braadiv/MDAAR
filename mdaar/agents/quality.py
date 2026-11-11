"""
Quality Gate, Mermaid Validator/Auto-Sanitizer, Anti-Hallucination Checker,
and Document Register (L0 -> L1 Trust Levels) Manager for MDAAR v2.0.0.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import re

from mdaar.agents.doc_agents import BaseDocAgent


VALID_MERMAID_HEADERS = (
    "flowchart",
    "graph",
    "sequencediagram",
    "erdiagram",
    "statediagram",
    "statediagram-v2",
    "classdiagram",
)

HTML_TAG_RE = re.compile(r"</?(?:br|b|i|strong|em|p|div|span)\s*/?>", re.IGNORECASE)
UNQUOTED_NODE_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\[([^\"\]\n]+)\]")


@dataclass
class DocAuditItem:
    rel_doc_path: str
    trust_level: str  # "L1 (AI-Verified)" or "L0 (Draft/Needs Review)"
    passed: bool
    char_count: int
    tbd_count: int
    blind_spot_count: int
    mermaid_blocks: int
    warnings: list = field(default_factory=list)


class QualityAgent(BaseDocAgent):
    """Audits every generated document for factual grounding, Mermaid validity, and completeness."""

    def __init__(self, gateway=None, project_name="MyProject", **kwargs):
        super().__init__(
            "Documentation QA & Consistency Agent",
            gateway,
            project_name,
            role="quality",
            **kwargs,
        )

    def _invoke_gateway(self, user_prompt, sys_prompt):
        if self.gateway is None:
            return ""
        if hasattr(self.gateway, "generate_as_planner"):
            return self.gateway.generate_as_planner(user_prompt, sys_prompt)
        return self.gateway.generate(user_prompt, sys_prompt)

    @staticmethod
    def sanitize_mermaid_blocks(markdown_text):
        """
        Clean common weak-model Mermaid syntax glitches (HTML tags, unquoted Arabic/parens labels)
        and validate block headers. Returns (cleaned_text, errors_list, block_count).
        """
        errors = []
        block_count = 0

        def replacer(match):
            nonlocal block_count
            block_count += 1
            body = match.group(1)
            lines = body.splitlines()
            non_empty = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("%%")]
            if not non_empty:
                errors.append(f"Mermaid block #{block_count} is empty.")
                return match.group(0)

            first_token = non_empty[0].split()[0].lower()
            if first_token not in VALID_MERMAID_HEADERS:
                errors.append(f"Mermaid block #{block_count} uses unsupported header `{non_empty[0]}`.")

            cleaned_lines = []
            for ln in lines:
                ln_clean = HTML_TAG_RE.sub(" ", ln)
                # Quote labels in A[Text (Info)] unless already quoted or erDiagram/sequence line
                if first_token in ("flowchart", "graph"):
                    def _quote_node(nm):
                        nid, inner = nm.group(1), nm.group(2).strip()
                        if any(c in inner for c in ("(", ")", "/", ":")):
                            inner_safe = inner.replace('"', "'")
                            return f'{nid}["{inner_safe}"]'
                        return nm.group(0)
                    ln_clean = UNQUOTED_NODE_RE.sub(_quote_node, ln_clean)
                cleaned_lines.append(ln_clean)

            return "```mermaid\n" + "\n".join(cleaned_lines) + "\n```"

        cleaned = re.sub(r"```mermaid\s*\n(.*?)```", replacer, markdown_text, flags=re.DOTALL)
        return cleaned, errors, block_count

    @staticmethod
    def audit_document(rel_doc_path, content, fact_sheet):
        """Run deterministic factual, completeness, and Mermaid checks on a single document."""
        warnings = []
        stripped = (content or "").strip()

        if not stripped or stripped.startswith("⚠️ "):
            warnings.append("المحتوى مفقود أو فشل التوليد من النموذج.")
            return stripped, DocAuditItem(
                rel_doc_path=rel_doc_path,
                trust_level="L0 (Draft/Needs Review)",
                passed=False,
                char_count=len(stripped),
                tbd_count=0,
                blind_spot_count=0,
                mermaid_blocks=0,
                warnings=warnings,
            )

        cleaned, mermaid_errors, block_count = QualityAgent.sanitize_mermaid_blocks(stripped)
        warnings.extend(mermaid_errors)

        if len(cleaned) < 260:
            warnings.append("الوثيقة قصيرة جداً وقد تكون قالباً غير مكتمل.")

        if "#" not in cleaned:
            warnings.append("تفتقر الوثيقة إلى عناوين Markdown المهيكلة.")

        # Check language hallucination guard (e.g., claiming Python/Django on pure PHP/JS codebase)
        langs = set(fact_sheet.language_counts.keys())
        lower_text = cleaned.lower()
        if "python" not in langs and re.search(r"مبني بلغة\s+python|framework:\s*django", lower_text):
            warnings.append("تم ذكر Python/Django رغم عدم وجود ملفات Python في المشروع الفعلي.")

        # Check cited file paths that clearly resemble source paths (with known code extensions)
        cited_files = re.findall(r"`([A-Za-z0-9_./\-]+\.(?:php|py|js|ts|sql|json|yml|yaml|java|cs|go))`", cleaned)
        known_names = fact_sheet.verified_identifiers
        unknown_cites = []
        # Ignore common generic config names often referenced conceptually in guides
        generic_examples = {".env", ".env.example", "docker-compose.yml", "composer.json", "package.json"}
        for cf in set(cited_files):
            base = Path(cf).name
            if cf not in known_names and base not in known_names and cf not in generic_examples:
                unknown_cites.append(cf)
        if len(unknown_cites) > 3:
            warnings.append(
                f"إشارة إلى ملفات غير مرصودة في الكود الفعلي ({', '.join(sorted(unknown_cites)[:4])}) — يلزم التحقق."
            )

        tbd_count = len(re.findall(r"\bTBD\b", cleaned))
        blind_count = cleaned.count("BLIND SPOT")

        passed = len(warnings) == 0
        trust = "L1 (AI-Verified)" if passed else "L0 (Draft/Needs Review)"
        return cleaned, DocAuditItem(
            rel_doc_path=rel_doc_path,
            trust_level=trust,
            passed=passed,
            char_count=len(cleaned),
            tbd_count=tbd_count,
            blind_spot_count=blind_count,
            mermaid_blocks=block_count,
            warnings=warnings,
        )

    def run_quality_gate(self, docs_src_dir, fact_sheet, run_planner_summary=True):
        """
        Validate and clean every Markdown doc under docs_src_dir, update Document-Register.md
        with factual L0/L1 status, and emit 00_Governance/Quality-Audit-Report.md.
        """
        docs_root = Path(docs_src_dir)
        audit_items = []
        skip_self = {
            "index.md",
            "00_Governance/Document-Register.md",
            "00_Governance/Quality-Audit-Report.md",
        }

        for md_file in sorted(docs_root.rglob("*.md")):
            rel = md_file.relative_to(docs_root).as_posix()
            if rel in skip_self:
                continue
            raw = md_file.read_text(encoding="utf-8", errors="replace")
            cleaned, item = self.audit_document(rel, raw, fact_sheet)
            if cleaned != raw:
                md_file.write_text(cleaned, encoding="utf-8")
            audit_items.append(item)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._write_document_register(docs_root, audit_items, today)

        planner_review = ""
        if run_planner_summary and self.gateway is not None:
            flagged = [i for i in audit_items if not i.passed or i.tbd_count > 0 or i.blind_spot_count > 0]
            digest = "\n".join(
                f"- `{i.rel_doc_path}` | {i.trust_level} | chars={i.char_count} | TBD={i.tbd_count} | BlindSpots={i.blind_spot_count} | warnings={i.warnings}"
                for i in flagged[:25]
            )
            prompt = (
                f"المشروع: {fact_sheet.project_name} ({fact_sheet.git_trace})\n"
                f"إجمالي الوثائق المفحوصة: {len(audit_items)} | اجتازت L1: {sum(1 for i in audit_items if i.passed)}\n"
                f"الملفات التي تحتوي ملاحظات أو فجوات:\n{digest or 'لا يوجد'}\n\n"
                "اكتب توصية مراجعة تنفيذية مقتضبة للفريق البشري: ما أهم النقاط التي تستحق الاعتماد اليدوي (L2/L3) بناءً على الفجوات المرصودة؟"
            )
            res = self._invoke_gateway(prompt, self._build_system_prompt("Quality Audit Review"))
            if res and not str(res).lstrip().startswith("⚠️ "):
                planner_review = res.strip()

        self._write_quality_report(docs_root, audit_items, fact_sheet, today, planner_review)
        return audit_items

    @staticmethod
    def _write_document_register(docs_root, audit_items, today):
        gov_dir = docs_root / "00_Governance"
        gov_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "# سجل الوثائق المعتمد (Document Register)",
            "",
            "| معرف الوثيقة | المسار / اسم الوثيقة | التصنيف | الحجم (حرف) | مستوى الاعتماد | ملاحظات [TBD/BlindSpot] | تاريخ التحديث |",
            "| :--- | :--- | :--- | :---: | :--- | :---: | :---: |",
        ]
        for idx, item in enumerate(audit_items, 1):
            doc_id = f"DOC-{idx:03d}"
            category = item.rel_doc_path.split("/")[0] if "/" in item.rel_doc_path else "General"
            notes = f"TBD:{item.tbd_count} / BS:{item.blind_spot_count}"
            lines.append(
                f"| `{doc_id}` | `{item.rel_doc_path}` | `{category}` | {item.char_count:,} | "
                f"**{item.trust_level}** | {notes} | {today} |"
            )
        (gov_dir / "Document-Register.md").write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _write_quality_report(docs_root, audit_items, fact_sheet, today, planner_review=""):
        gov_dir = docs_root / "00_Governance"
        gov_dir.mkdir(parents=True, exist_ok=True)
        passed_cnt = sum(1 for i in audit_items if i.passed)
        total_cnt = len(audit_items)

        lines = [
            f"# تقرير فحص الجودة والحوكمة — {fact_sheet.project_name}",
            "",
            f"- **تاريخ الفحص:** {today}",
            f"- **المرجع البرمجي (Git Commit):** `{fact_sheet.git_trace}`",
            f"- **الوثائق المجتازة (L1):** {passed_cnt} / {total_cnt}",
            f"- **نسبة التغطية الكودية:** 100% ({fact_sheet.total_files} ملف، {fact_sheet.total_lines:,} سطر)",
            "",
            "## تفاصيل الفحص لكل وثيقة",
            "",
            "| الوثيقة | الحالة | الحجم | Mermaid | TBD | Blind Spots | التحذيرات |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :--- |",
        ]
        for i in audit_items:
            status_icon = "✅ L1" if i.passed else "⚠️ L0"
            warn_str = "; ".join(i.warnings) if i.warnings else "لا يوجد"
            lines.append(
                f"| `{i.rel_doc_path}` | {status_icon} | {i.char_count:,} | "
                f"{i.mermaid_blocks} | {i.tbd_count} | {i.blind_spot_count} | {warn_str} |"
            )

        if planner_review:
            lines.extend(["", "## توصية نموذج الخطة والاعتماد البشري (L2/L3 Review)", "", planner_review])

        (gov_dir / "Quality-Audit-Report.md").write_text("\n".join(lines), encoding="utf-8")
