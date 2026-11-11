"""
MDAAR v2.0.0 Core Orchestrator (`MDAARPipeline`).
Coordinates:
1. Fail-fast Source/Docs Git sync (SSH/HTTPS)
2. 100% Full-Repository Scanning & Secret Redaction
3. Deterministic Polyglot, SQL/Migration Schema, & API/Postman Extraction
4. Executor Map Phase (lossless full-file inspection, fresh per session)
5. Planner Master Blueprint ("إعدادات مودل الخطة")
6. Executor Full-Package Authoring ("إعدادات مودل التنفيذ")
7. Quality Gate & Document Register (L0 -> L1)
8. Persistent Hybrid RAG Index stored in project_dir/.rag_index/
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from mdaar.analyzers import (
    APISpecParser,
    PolyglotFactExtractor,
    RepoScanner,
    SchemaParser,
)
from mdaar.agents import (
    DOC_SPECIFICATIONS,
    ImpactAnalyzer,
    MapperAgent,
    PlannerAgent,
    QualityAgent,
    create_agent_for_role,
)
from mdaar.gateway import DualModelManager, ModelGateway, launch_cloudflared_tunnel
from mdaar.rag import DocumentationChatEngine
from mdaar.skills import SkillLoader


class MDAARPipeline:
    """End-to-end orchestrator for MDAAR Automated Living Documentation & Hybrid RAG."""

    def __init__(
        self,
        project_name="MyProject",
        base_workspace=None,
        git_source_url=None,
        git_docs_url=None,
        source_auth="HTTPS",
        docs_auth="HTTPS",
        gateway=None,
        planner_gateway=None,
        executor_gateway=None,
        max_chunk_chars=12000,
        map_batch_chars=7500,
    ):
        self.project_name = project_name
        self.git_source_url = git_source_url
        self.git_docs_url = git_docs_url
        self.source_auth = source_auth
        self.docs_auth = docs_auth
        self.max_chunk_chars = int(max_chunk_chars)
        self.map_batch_chars = int(map_batch_chars)

        default_gw = gateway or planner_gateway or ModelGateway()
        self.planner_gateway = planner_gateway or default_gw
        self.executor_gateway = executor_gateway or default_gw
        self.gateway = self.executor_gateway  # Backwards compatibility attribute
        self.model_manager = DualModelManager(self.planner_gateway, self.executor_gateway)

        if base_workspace:
            self.base_workspace = Path(base_workspace)
        elif Path("/content/drive/MyDrive").exists():
            self.base_workspace = Path("/content/drive/MyDrive/AI_Documentation_Workspace")
        else:
            self.base_workspace = Path.cwd() / "AI_Documentation_Workspace"

        self.project_dir = self.base_workspace / self.project_name
        self.config_file = self.project_dir / ".config.json"
        self.state_file = self.project_dir / ".state.json"
        self.rag_index_dir = self.project_dir / ".rag_index"
        self.ssh_dir = self.project_dir / ".ssh"
        self.source_repo_dir = self.project_dir / "source_repo"
        self.docs_repo_dir = self.project_dir / "docs_repo"
        self.docs_src_dir = self.docs_repo_dir / "docs"

        self.source_ssh_key = self.ssh_dir / "id_ed25519_source"
        self.docs_ssh_key = self.ssh_dir / "id_ed25519_docs"

        self.skill_loader = SkillLoader()
        self.chat_engine = None
        self.last_scan_result = None
        self.last_fact_sheet = None
        self.last_tables = []
        self.last_relations = []
        self.last_endpoints = []
        self.last_audit = []

        self._init_dirs()

    def _init_dirs(self):
        for d in (
            self.project_dir,
            self.ssh_dir,
            self.source_repo_dir,
            self.docs_repo_dir,
            self.docs_src_dir,
            self.rag_index_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def setup_source_ssh(self):
        if not self.source_ssh_key.exists():
            subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", str(self.source_ssh_key), "-N", ""], check=True)
        self._prepare_ssh_key(self.source_ssh_key)
        return (self.ssh_dir / "id_ed25519_source.pub").read_text(encoding="utf-8").strip()

    def setup_docs_ssh(self):
        if not self.docs_ssh_key.exists():
            subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", str(self.docs_ssh_key), "-N", ""], check=True)
        self._prepare_ssh_key(self.docs_ssh_key)
        return (self.ssh_dir / "id_ed25519_docs.pub").read_text(encoding="utf-8").strip()

    @staticmethod
    def _prepare_ssh_key(key_path):
        """Ensure private SSH key has strict 0600 permissions (staging in temp on Drive FUSE if needed)."""
        kpath = Path(key_path)
        if not kpath.exists() or os.name == "nt":
            return str(kpath)
        try:
            os.chmod(kpath.parent, 0o700)
            os.chmod(kpath, 0o600)
        except OSError:
            pass
        try:
            mode = os.stat(kpath).st_mode & 0o777
            if (mode & 0o077) != 0:
                digest = hashlib.sha256(str(kpath).encode("utf-8")).hexdigest()[:10]
                staged = Path(tempfile.gettempdir()) / f"mdaar_{kpath.name}_{digest}"
                shutil.copy2(kpath, staged)
                os.chmod(staged, 0o600)
                return str(staged)
        except OSError:
            pass
        return str(kpath)

    @classmethod
    def _build_git_env(cls, auth_mode, key_path):
        """Build an isolated subprocess environment for Git commands with non-interactive SSH/HTTPS."""
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        if auth_mode == "SSH" and key_path and Path(key_path).exists():
            effective_key = cls._prepare_ssh_key(key_path)
            env["GIT_SSH_COMMAND"] = (
                f'ssh -i "{effective_key}" -o StrictHostKeyChecking=no '
                "-o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10"
            )
        elif "GIT_SSH_COMMAND" in env:
            del env["GIT_SSH_COMMAND"]
        return env

    @classmethod
    def _configure_git_auth(cls, auth_mode, key_path):
        env = cls._build_git_env(auth_mode, key_path)
        os.environ["GIT_TERMINAL_PROMPT"] = env["GIT_TERMINAL_PROMPT"]
        if "GIT_SSH_COMMAND" in env:
            os.environ["GIT_SSH_COMMAND"] = env["GIT_SSH_COMMAND"]
        elif "GIT_SSH_COMMAND" in os.environ:
            del os.environ["GIT_SSH_COMMAND"]

    def test_repo_connection(self, git_url, auth_mode="HTTPS", key_path=None, timeout=15):
        """Verify live Git repository connectivity and authentication via non-interactive `git ls-remote`."""
        clean_url = (git_url or "").strip()
        redacted_url = re.sub(r"(https?://)[^/@]+@", r"\1[REDACTED_CREDENTIAL]@", clean_url)
        if not clean_url:
            return {
                "success": False,
                "url": redacted_url,
                "auth_mode": auth_mode,
                "refs_count": 0,
                "empty_repo": False,
                "error": "لم يتم تحديد رابط المستودع (Git URL).",
                "message": "❌ فشل الفحص: رابط المستودع فارغ.",
            }

        env = self._build_git_env(auth_mode, key_path)
        try:
            res = subprocess.run(
                ["git", "ls-remote", clean_url],
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "url": redacted_url,
                "auth_mode": auth_mode,
                "refs_count": 0,
                "empty_repo": False,
                "error": f"انتهت مهلة الاتصال بعد {timeout} ثانية.",
                "message": f"❌ فشل الاتصال بالمستودع ({redacted_url}): انتهت المهلة.",
            }
        except Exception as e:
            return {
                "success": False,
                "url": redacted_url,
                "auth_mode": auth_mode,
                "refs_count": 0,
                "empty_repo": False,
                "error": str(e),
                "message": f"❌ فشل الاتصال بالمستودع ({redacted_url}): {e}",
            }

        if res.returncode == 0:
            refs = [ln for ln in res.stdout.splitlines() if ln.strip()]
            empty_note = " (مستودع جديد فارغ)" if len(refs) == 0 else f" (عدد المراجع/الفروع المكتشفة: {len(refs)})"
            return {
                "success": True,
                "url": redacted_url,
                "auth_mode": auth_mode,
                "refs_count": len(refs),
                "empty_repo": len(refs) == 0,
                "error": "",
                "message": f"✅ الاتصال ناجح بالمستودع [{redacted_url}] عبر {auth_mode}{empty_note}.",
            }

        raw_err = (res.stderr or res.stdout or "Authentication or repository access error").strip()
        safe_err = re.sub(r"(https?://)[^/@]+@", r"\1[REDACTED_CREDENTIAL]@", raw_err)
        return {
            "success": False,
            "url": redacted_url,
            "auth_mode": auth_mode,
            "refs_count": 0,
            "empty_repo": False,
            "error": safe_err,
            "message": f"❌ فشل الاتصال بالمستودع [{redacted_url}] عبر {auth_mode}: {safe_err}",
        }

    def test_source_connection(self, timeout=15):
        """Test live connectivity and credentials for the configured Source Repository."""
        return self.test_repo_connection(
            self.git_source_url,
            auth_mode=self.source_auth,
            key_path=self.source_ssh_key,
            timeout=timeout,
        )

    def test_docs_connection(self, timeout=15):
        """Test live connectivity and credentials for the configured External Docs Repository."""
        return self.test_repo_connection(
            self.git_docs_url,
            auth_mode=self.docs_auth,
            key_path=self.docs_ssh_key,
            timeout=timeout,
        )

    def sync_source_repo(self):
        """
        Clone or pull the source repository cleanly.
        Fails fast with RuntimeError if git clone fails or leaves an empty directory,
        preventing downstream LLMs from hallucinating over 0 files.
        """
        self._configure_git_auth(self.source_auth, self.source_ssh_key)

        has_git = (self.source_repo_dir / ".git").exists()
        if not has_git and self.git_source_url:
            # Remove empty placeholder dir if created before clone
            if self.source_repo_dir.exists() and not any(self.source_repo_dir.iterdir()):
                self.source_repo_dir.rmdir()
            res = subprocess.run(
                ["git", "clone", self.git_source_url, str(self.source_repo_dir)],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                err = (res.stderr or res.stdout or "Unknown git clone error").strip()
                raise RuntimeError(
                    f"❌ فشل استنساخ مستودع الكود ({self.git_source_url}):\n{err}\n"
                    "تأكد من رابط المستودع، أو تفعيل Deploy Key / Personal Access Token، ثم أعد المحاولة."
                )
        elif has_git:
            pull_res = subprocess.run(
                ["git", "-C", str(self.source_repo_dir), "pull", "--ff-only"],
                capture_output=True,
                text=True,
            )
            if pull_res.returncode != 0 and pull_res.stderr:
                print(f"ℹ️ Git pull note: {pull_res.stderr.strip()}")

        if not self.source_repo_dir.exists() or not any(self.source_repo_dir.iterdir()):
            raise RuntimeError(
                f"❌ مجلد مستودع الكود فارغ ({self.source_repo_dir}). يرجى ربط المستودع بنجاح قبل التحليل."
            )

        current_commit = ""
        changed_files = []
        if (self.source_repo_dir / ".git").exists():
            cur_res = subprocess.run(
                ["git", "-C", str(self.source_repo_dir), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
            )
            if cur_res.returncode == 0:
                current_commit = cur_res.stdout.strip()
            state = self.load_state()
            last_commit = state.get("last_commit")
            if last_commit and current_commit and last_commit != current_commit:
                diff_res = subprocess.run(
                    ["git", "-C", str(self.source_repo_dir), "diff", "--name-status", f"{last_commit}..{current_commit}"],
                    capture_output=True,
                    text=True,
                )
                changed_files = [line.strip() for line in diff_res.stdout.splitlines() if line.strip()]

        return current_commit, changed_files

    def sync_docs_repo(self, commit_message="docs: automated documentation update [skip ci]"):
        """Push generated docs to remote Git documentation hub under projects/<project_name>/."""
        if not self.git_docs_url:
            return "No remote docs Git URL configured (Local workspace mode)."

        self._configure_git_auth(self.docs_auth, self.docs_ssh_key)

        project_hub_dir = self.docs_repo_dir / "projects" / self.project_name
        project_hub_dir.mkdir(parents=True, exist_ok=True)

        if not (self.docs_repo_dir / ".git").exists():
            print(f"📥 Cloning external documentation repository from: {self.git_docs_url}...")
            res = subprocess.run(["git", "clone", self.git_docs_url, str(self.docs_repo_dir)], capture_output=True, text=True)
            if res.returncode != 0:
                subprocess.run(["git", "-C", str(self.docs_repo_dir), "init"], check=True)
                subprocess.run(["git", "-C", str(self.docs_repo_dir), "remote", "add", "origin", self.git_docs_url], check=True)
        else:
            subprocess.run(["git", "-C", str(self.docs_repo_dir), "pull", "origin", "main"], capture_output=True, text=True)

        shutil.copytree(str(self.docs_src_dir), str(project_hub_dir / "docs"), dirs_exist_ok=True)
        if (self.docs_repo_dir / "mkdocs.yml").exists():
            shutil.copy2(str(self.docs_repo_dir / "mkdocs.yml"), str(project_hub_dir / "mkdocs.yml"))

        subprocess.run(["git", "-C", str(self.docs_repo_dir), "config", "user.name", "MDAAR AI Bot"], check=True)
        subprocess.run(["git", "-C", str(self.docs_repo_dir), "config", "user.email", "mdaar-bot@users.noreply.github.com"], check=True)
        subprocess.run(["git", "-C", str(self.docs_repo_dir), "add", "."], check=True)
        status_res = subprocess.run(["git", "-C", str(self.docs_repo_dir), "status", "--porcelain"], capture_output=True, text=True)

        if status_res.stdout.strip():
            subprocess.run(
                ["git", "-C", str(self.docs_repo_dir), "commit", "-m", f"docs({self.project_name}): {commit_message}"],
                check=True,
            )
            push_res = subprocess.run(
                ["git", "-C", str(self.docs_repo_dir), "push", "-u", "origin", "HEAD"],
                capture_output=True,
                text=True,
            )
            if push_res.returncode == 0:
                return f"✅ Successfully pushed updated documentation for [{self.project_name}] to remote Git!"
            return f"⚠️ Git Push notice: {push_res.stderr.strip()}"
        return f"ℹ️ No documentation changes to commit for [{self.project_name}]."

    def load_state(self):
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def save_state(self, commit_hash):
        state = self.load_state()
        state["last_commit"] = commit_hash
        state["mdaar_version"] = "2.0.0"
        state["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        return state

    def extract_and_generate_specialized(self, scan_result=None):
        """
        Deterministically extract database schema (SQL DDL + ORM migrations) and
        polyglot API specs (Postman + OpenAPI + Yii2/Laravel/FastAPI/Express/Spring/Go).
        """
        if scan_result is None:
            scan_result = RepoScanner(self.source_repo_dir, max_chunk_chars=self.max_chunk_chars).scan()
        fact_sheet = PolyglotFactExtractor().extract(scan_result, project_name=self.project_name)
        tables, relations = SchemaParser.parse_repository(scan_result)
        endpoints = APISpecParser.parse_repository(scan_result)

        self.last_scan_result = scan_result
        self.last_fact_sheet = fact_sheet
        self.last_tables = tables
        self.last_relations = relations
        self.last_endpoints = endpoints

        # Ensure directories exist
        tech_dir = self.docs_src_dir / "02_Technical"
        tech_dir.mkdir(parents=True, exist_ok=True)

        # Deterministic Database-ERD.md & Data-Dictionary.md
        erd_blocks = SchemaParser.build_mermaid_erd_chunks(tables, relations, max_tables_per_block=14)
        erd_md = "\n\n".join(
            [
                f"# مخطط قاعدة البيانات المعتمد (Database Schema & ERD) — {self.project_name}",
                "",
                f"- **عدد الجداول المستخرجة:** {len(tables)}",
                f"- **عدد العلاقات (Foreign Keys):** {len(relations)}",
                "",
                "## مخططات العلاقات",
                "",
            ]
            + erd_blocks
        )
        (tech_dir / "Database-ERD.md").write_text(erd_md, encoding="utf-8")

        dict_md = SchemaParser.render_data_dictionary_markdown(tables, relations, self.project_name)
        (tech_dir / "Data-Dictionary.md").write_text(dict_md, encoding="utf-8")

        api_md = APISpecParser.render_api_summary_markdown(endpoints, self.project_name)
        (tech_dir / "API-Reference.md").write_text(api_md, encoding="utf-8")

        print(
            f"✅ Deterministic Extraction Complete: {len(scan_result.files)} files, "
            f"{len(tables)} DB tables, {len(endpoints)} API endpoints."
        )
        return scan_result, fact_sheet, tables, relations, endpoints

    def init_framework_structure(self):
        """Initialize the MDAAR 4-package structure and MkDocs configuration."""
        doc_defaults = {
            "00_Governance/Documentation-Policy.md": "# سياسة التوثيق الموحدة (Documentation Policy)\n\nسياسة الحوكمة وتحديث الوثائق.",
            "00_Governance/Documentation-Standards.md": "# معايير كتابة الوثائق البرمجية (Documentation Standards)\n\nضوابط Markdown ومخططات Mermaid وقواعد منع الهلوسة.",
            "00_Governance/Glossary.md": "# قاموس المصطلحات الموحد (Glossary)\n\n| المصطلح العربي | English Term | التعريف والتوصيف |\n|---|---|---|",
            "00_Governance/AI-Governance.md": "# حوكمة الذكاء الاصطناعي (AI Governance)\n\nضوابط تعقيم الأسرار وفصل نموذج الخطة عن نموذج التنفيذ واعتماد L0-L4.",
            "00_Governance/Document-Register.md": "# سجل الوثائق المعتمد (Document Register)\n\nسيُحدّث تلقائياً بواسطة بوابة الجودة.",
            "00_Governance/Quality-Audit-Report.md": "# تقرير فحص الجودة والحوكمة\n\nسيُحدّث تلقائياً بعد التشغيل.",
            "01_Administrative/Project-Charter.md": f"# ميثاق المشروع: {self.project_name}\n\n[TBD]",
            "01_Administrative/Business-Case.md": "# دراسة الجدوى والقيمة العملية (Business Case)\n\n[TBD]",
            "01_Administrative/Scope.md": "# نطاق المشروع وحدود العمل (Scope)\n\n[TBD]",
            "01_Administrative/BRD.md": "# وثيقة متطلبات الأعمال (BRD)\n\n[TBD]",
            "01_Administrative/Risk-Register.md": "# سجل إدارة المخاطر (Risk Register)\n\n[TBD]",
            "01_Administrative/Decision-Log.md": "# سجل القرارات المعمارية (Decision Log)\n\n[TBD]",
            "02_Technical/SRS.md": "# مواصفات متطلبات النظام (SRS)\n\n[TBD]",
            "02_Technical/SAD.md": "# وثيقة معمارية النظام (SAD)\n\n[TBD]",
            "02_Technical/HLD.md": "# التصميم عالي المستوى (HLD)\n\n[TBD]",
            "02_Technical/LLD.md": "# التصميم التفصيلي (LLD)\n\n[TBD]",
            "02_Technical/Database-ERD.md": "# مخطط قاعدة البيانات (Database ERD)\n\n[TBD]",
            "02_Technical/Data-Dictionary.md": "# قاموس البيانات (Data Dictionary)\n\n[TBD]",
            "02_Technical/API-Reference.md": "# مرجع واجهات البرمجة (API Reference)\n\n[TBD]",
            "02_Technical/Integrations.md": "# التكاملات الخارجية (External Integrations)\n\n[TBD]",
            "03_Operations/Installation-Guide.md": "# دليل التثبيت والبيئة (Installation Guide)\n\n[TBD]",
            "03_Operations/Configuration-Guide.md": "# دليل التكوين ومتغيرات البيئة (Configuration Guide)\n\n[TBD]",
            "03_Operations/Deployment-Guide.md": "# دليل النشر وخطوط الإنتاج (Deployment Guide)\n\n[TBD]",
            "03_Operations/Security-Guide.md": "# دليل الأمن والصلاحيات (Security & RBAC Guide)\n\n[TBD]",
            "03_Operations/Troubleshooting.md": "# دليل استكشاف الأخطاء وحلها (Troubleshooting)\n\n[TBD]",
            "03_Operations/Backup-Recovery.md": "# خطة النسخ الاحتياطي واستعادة الكوارث (Backup & Recovery)\n\n[TBD]",
            "04_User-Guides/User-Manual.md": "# دليل المستخدم النهائي (User Manual)\n\n[TBD]",
            "04_User-Guides/Admin-Manual.md": "# دليل مسؤول النظام (Admin Manual)\n\n[TBD]",
            "04_User-Guides/FAQs.md": "# الأسئلة الشائعة (FAQs)\n\n[TBD]",
            "05_Release-Documentation/Release-Notes.md": f"# ملاحظات الإصدار (Release Notes)\n\nمشروع: {self.project_name}",
        }

        created = 0
        for rel_path, default_text in doc_defaults.items():
            fpath = self.docs_src_dir / rel_path
            fpath.parent.mkdir(parents=True, exist_ok=True)
            if not fpath.exists():
                fpath.write_text(default_text, encoding="utf-8")
                created += 1

        index_file = self.docs_src_dir / "index.md"
        if not index_file.exists():
            index_file.write_text(
                f"# 📚 بوابة التوثيق المرجعي: {self.project_name}\n\n"
                "بوابة التوثيق الهندسي والإداري الشاملة المبنية بمنظومة **MDAAR v2.0**.\n",
                encoding="utf-8",
            )

        self._generate_mkdocs_config()
        if any(self.source_repo_dir.iterdir()):
            self.extract_and_generate_specialized()
        return created

    def _generate_mkdocs_config(self):
        content = f"""site_name: 'توثيق {self.project_name}'
site_description: 'بوابة التوثيق التفاعلية - MDAAR Framework v2.0'
site_author: 'MDAAR Documentation Engine'

theme:
  name: material
  language: ar
  palette:
    - scheme: default
      primary: indigo
      accent: blue
      toggle:
        icon: material/brightness-7
        name: التبديل إلى الوضع الليلي
    - scheme: slate
      primary: indigo
      accent: blue
      toggle:
        icon: material/brightness-4
        name: التبديل إلى الوضع النهاري
  features:
    - navigation.tabs
    - navigation.sections
    - navigation.expand
    - search.suggest
    - search.highlight
    - content.code.copy

markdown_extensions:
  - pymdownx.highlight:
      anchor_linenums: true
  - pymdownx.inlinehilite
  - pymdownx.snippets
  - pymdownx.superfences:
      custom_fences:
        - name: mermaid
          class: mermaid
          format: !!python/name:pymdownx.superfences.fence_code_format
  - pymdownx.tabbed:
      alternate_style: true
  - tables
  - admonition

nav:
  - الرئيسية: index.md
  - الحوكمة وإدارة المعرفة:
      - سياسة التوثيق: 00_Governance/Documentation-Policy.md
      - معايير التوثيق: 00_Governance/Documentation-Standards.md
      - قاموس المصطلحات: 00_Governance/Glossary.md
      - حوكمة AI: 00_Governance/AI-Governance.md
      - سجل الوثائق: 00_Governance/Document-Register.md
      - تقرير فحص الجودة: 00_Governance/Quality-Audit-Report.md
  - الوثائق الإدارية والتجارية:
      - ميثاق المشروع: 01_Administrative/Project-Charter.md
      - دراسة الجدوى: 01_Administrative/Business-Case.md
      - النطاق: 01_Administrative/Scope.md
      - متطلبات الأعمال BRD: 01_Administrative/BRD.md
      - سجل المخاطر: 01_Administrative/Risk-Register.md
      - سجل القرارات: 01_Administrative/Decision-Log.md
  - الوثائق الفنية والهندسية:
      - متطلبات النظام SRS: 02_Technical/SRS.md
      - معمارية النظام SAD: 02_Technical/SAD.md
      - التصميم عالي المستوى HLD: 02_Technical/HLD.md
      - التصميم التفصيلي LLD: 02_Technical/LLD.md
      - قاعدة البيانات ERD: 02_Technical/Database-ERD.md
      - قاموس البيانات: 02_Technical/Data-Dictionary.md
      - توثيق الواجهات APIs: 02_Technical/API-Reference.md
      - التكاملات الخارجية: 02_Technical/Integrations.md
  - الوثائق التشغيلية:
      - دليل التثبيت: 03_Operations/Installation-Guide.md
      - دليل الإعداد: 03_Operations/Configuration-Guide.md
      - دليل النشر: 03_Operations/Deployment-Guide.md
      - الأمن والصلاحيات: 03_Operations/Security-Guide.md
      - استكشاف الأخطاء: 03_Operations/Troubleshooting.md
      - النسخ والاستعادة: 03_Operations/Backup-Recovery.md
  - أدلة الاستخدام:
      - دليل المستخدم: 04_User-Guides/User-Manual.md
      - دليل المسؤول: 04_User-Guides/Admin-Manual.md
      - الأسئلة الشائعة: 04_User-Guides/FAQs.md
  - الإصدارات والتغييرات:
      - ملاحظات الإصدار: 05_Release-Documentation/Release-Notes.md
"""
        (self.docs_repo_dir / "mkdocs.yml").write_text(content, encoding="utf-8")

    @staticmethod
    def _assemble_role_context(doc_rel, fact_sheet, master_plan, module_findings, tables, endpoints):
        """Build targeted rich evidence bundle for each doc without truncating repo truth."""
        db_section = SchemaParser.render_data_dictionary_markdown(tables, [], fact_sheet.project_name) if "Database" in doc_rel or "Data-Dictionary" in doc_rel else f"الجداول ({len(tables)}): {', '.join(t.name for t in tables[:40])}"
        api_section = APISpecParser.render_api_summary_markdown(endpoints, fact_sheet.project_name) if "API" in doc_rel or "Integrations" in doc_rel else f"نقاط الاتصال ({len(endpoints)}): " + ", ".join(f"{e.method} {e.path}" for e in endpoints[:35])

        # Select relevant module findings matching the doc domain, plus overview of rest
        domain_keywords = {
            "02_Technical/Database": ("model", "models", "migration", "migrations", "sql", "schema", "database"),
            "02_Technical/Data": ("model", "models", "migration", "migrations", "sql", "schema"),
            "02_Technical/API": ("api", "controller", "controllers", "routes", "postman", "v1"),
            "02_Technical/Integrations": ("components", "services", "helpers", "firebase", "mail", "queue", "config"),
            "03_Operations": ("config", "docker", ".github", "console", "commands", "(root)"),
            "04_User-Guides": ("views", "controller", "controllers", "frontend", "pages", "api"),
        }
        preferred = ()
        for prefix, kws in domain_keywords.items():
            if doc_rel.startswith(prefix):
                preferred = kws
                break

        prioritized_notes = []
        secondary_notes = []
        for mod, notes in sorted(module_findings.items()):
            block = f"### الوحدة `{mod}`\n" + "\n".join(notes)
            if preferred and any(kw in mod.lower() for kw in preferred):
                prioritized_notes.append(block)
            else:
                secondary_notes.append(block)

        combined_findings = "\n\n".join(prioritized_notes + secondary_notes)

        return (
            f"{fact_sheet.to_prompt_context(max_cards=220)}\n\n"
            f"=== الخطة المرجعية الشاملة (Planner Blueprint) ===\n{master_plan}\n\n"
            f"=== ملخص قاعدة البيانات ===\n{db_section[:7000]}\n\n"
            f"=== ملخص نقاط الاتصال API ===\n{api_section[:7000]}\n\n"
            f"=== خلاصة الفحص الكامل لجميع الملفات (Map Findings) ===\n{combined_findings[:12000]}"
        )

    def run_multi_agent_generation(self, changed_files=None, force_full=False, progress_callback=None):
        """
        Execute the full MDAAR v2.0 Map-Reduce pipeline:
        1. Fresh 100% Full-Repo Scan (no cross-session stale cache)
        2. Deterministic Schema + API + Polyglot Fact Extraction
        3. Executor Model Map Pass (reads 100% of files & chunks)
        4. Planner Model Blueprint ("إعدادات مودل الخطة")
        5. Executor Model Full-Package Generation ("إعدادات مودل التنفيذ")
        6. Quality Gate & Document Register update
        7. Persistent Hybrid RAG Index built in project_dir/.rag_index/
        """
        self.init_framework_structure()

        # 1 + 2: Full scan + deterministic extraction (fails cleanly if repo empty)
        scan_result, fact_sheet, tables, relations, endpoints = self.extract_and_generate_specialized()
        if not scan_result.files:
            raise RuntimeError("❌ لم يتم العثور على أي ملفات مصدرية صالحة للتحليل في مستودع الكود.")

        repo_instructions = self.skill_loader.discover_repo_instructions(self.source_repo_dir)

        # Pre-flight readiness check so missing keys / Colab Secret permissions are surfaced immediately
        for role_label, gw in (("Planner", self.planner_gateway), ("Executor", self.executor_gateway)):
            ok_cfg, reason_cfg = gw.check_configuration()
            if not ok_cfg:
                print(f"⚠️ {role_label} Model readiness notice ({gw.descriptor}): {reason_cfg}")

        # 3: Fresh Map Pass on Executor Model ("إعدادات مودل التنفيذ")
        print(f"🔎 Map Pass (100% full files) via Executor Model: {self.executor_gateway.descriptor}")
        mapper = MapperAgent(
            self.model_manager,
            self.project_name,
            skill_loader=self.skill_loader,
            repo_instructions=repo_instructions,
        )
        module_findings = mapper.inspect_scan_result(
            scan_result,
            fact_sheet,
            batch_char_limit=self.map_batch_chars,
            progress_callback=progress_callback,
        )

        # 4: Planner Master Blueprint ("إعدادات مودل الخطة")
        print(f"🧠 Planning Master Architecture Blueprint via Planner Model: {self.planner_gateway.descriptor}")
        db_hint = f"{len(tables)} tables ({', '.join(t.name for t in tables[:30])})" if tables else "None"
        api_hint = f"{len(endpoints)} endpoints ({', '.join(e.method + ' ' + e.path for e in endpoints[:25])})" if endpoints else "None"
        planner = PlannerAgent(
            self.model_manager,
            self.project_name,
            skill_loader=self.skill_loader,
            repo_instructions=repo_instructions,
        )
        master_plan = planner.build_master_plan(fact_sheet, module_findings, db_hint, api_hint)

        # Save Master Plan alongside index for transparency
        plan_doc = (
            f"# الخطة الهندسية الشاملة وبصمة الفحص — {self.project_name}\n\n"
            f"- **المرجع البرمجي (Git Trace):** `{fact_sheet.git_trace}`\n"
            f"- **نموذج الخطة:** `{self.planner_gateway.descriptor}`\n"
            f"- **نموذج التنفيذ:** `{self.executor_gateway.descriptor}`\n\n"
            f"{master_plan}"
        )
        (self.rag_index_dir / "master_plan.md").write_text(plan_doc, encoding="utf-8")

        # 5: Determine which documents to author (full bootstrap vs incremental Living Docs)
        if changed_files and not force_full:
            impacted = ImpactAnalyzer.analyze_impact(changed_files)
            target_paths = set()
            for grp in impacted:
                target_paths.update(grp["docs"])
            specs_to_run = [spec for spec in DOC_SPECIFICATIONS if spec[0] in target_paths]
            print(f"🔄 Living Documentation mode: regenerating {len(specs_to_run)} impacted documents.")
        else:
            specs_to_run = list(DOC_SPECIFICATIONS)
            print(f"🚀 Generating full MDAAR documentation across {len(specs_to_run)} documents via Executor Model...")

        agent_cache = {}
        for doc_rel, role, goal in specs_to_run:
            if role not in agent_cache:
                agent_cache[role] = create_agent_for_role(
                    role,
                    self.model_manager,
                    self.project_name,
                    skill_loader=self.skill_loader,
                    repo_instructions=repo_instructions,
                )
            agent = agent_cache[role]
            context = self._assemble_role_context(doc_rel, fact_sheet, master_plan, module_findings, tables, endpoints)
            goal_full = goal
            if changed_files and not force_full:
                goal_full += f"\n(ركز أيضاً على عكس تغييرات الملفات التالية: {', '.join(changed_files[:12])})"

            print(f"✍️ Authoring `{doc_rel}` via {agent.name}...")
            out_path = self.docs_src_dir / doc_rel
            out_path.parent.mkdir(parents=True, exist_ok=True)
            generated = agent.execute(doc_rel, context, goal_full)

            # Preserve deterministic Data-Dictionary / Database-ERD / API-Reference tables when augmenting
            if DualModelManager.is_error(generated):
                reason = DualModelManager.summarize_error(generated)
                print(f"⚠️ Keeping deterministic/baseline content for `{doc_rel}` — {reason}")
                continue

            if doc_rel == "02_Technical/Database-ERD.md" and tables:
                det_erd = out_path.read_text(encoding="utf-8")
                out_path.write_text(f"{det_erd}\n\n---\n\n## التحليل المعماري لقاعدة البيانات\n\n{generated}", encoding="utf-8")
            elif doc_rel == "02_Technical/Data-Dictionary.md" and tables:
                det_dict = out_path.read_text(encoding="utf-8")
                out_path.write_text(f"{det_dict}\n\n---\n\n## ملاحظات التصميم والقيود التجارية\n\n{generated}", encoding="utf-8")
            elif doc_rel == "02_Technical/API-Reference.md" and endpoints:
                det_api = out_path.read_text(encoding="utf-8")
                out_path.write_text(f"{det_api}\n\n---\n\n## تفاصيل المعالجة وحالات الخطأ والمصادقة\n\n{generated}", encoding="utf-8")
            else:
                out_path.write_text(generated, encoding="utf-8")

        # Update landing index.md with authentic project telemetry
        self._update_landing_index(fact_sheet, tables, endpoints)

        # 6: Quality Gate & Document Register update
        print("🛡️ Running Quality Gate (Mermaid validation, grounding check, L0/L1 Document Register)...")
        qa = QualityAgent(
            self.model_manager,
            self.project_name,
            skill_loader=self.skill_loader,
            repo_instructions=repo_instructions,
        )
        self.last_audit = qa.run_quality_gate(self.docs_src_dir, fact_sheet, run_planner_summary=True)

        # 7: Hybrid RAG Persistent Index in project_dir/.rag_index/
        print(f"🔍 Building Hybrid RAG Index in `{self.rag_index_dir}`...")
        chat_engine = self.get_chat_engine()
        indexed_chunks = chat_engine.build_index(
            fact_sheet=fact_sheet,
            tables=tables,
            endpoints=endpoints,
            module_findings=module_findings,
            save=True,
        )
        print(f"✅ Pipeline Complete! {indexed_chunks} RAG chunks persisted in `{self.rag_index_dir}`.")
        return {
            "files_scanned": len(scan_result.files),
            "tables_extracted": len(tables),
            "endpoints_extracted": len(endpoints),
            "docs_audited": len(self.last_audit),
            "rag_chunks_indexed": indexed_chunks,
            "model_stats": dict(self.model_manager.stats),
        }

    def _update_landing_index(self, fact_sheet, tables, endpoints):
        top_langs = ", ".join(f"{k} ({v})" for k, v in fact_sheet.language_counts.items())
        fws = ", ".join(fact_sheet.detected_frameworks) or "Custom Stack"
        index_md = (
            f"# 📚 بوابة التوثيق المرجعي: {self.project_name}\n\n"
            "تم توليد هذا التوثيق تلقائياً عبر منظومة **MDAAR v2.0** بالاعتماد على فحص الكود المصدري بالكامل (100%).\n\n"
            "## بطاقة المشروع الفعلية\n\n"
            f"- **المرجع البرمجي (Git Trace):** `{fact_sheet.git_trace}` (فرع: `{fact_sheet.git_branch}`)\n"
            f"- **نطاق الفحص:** {fact_sheet.total_files} ملف ({fact_sheet.total_lines:,} سطر برمجي)\n"
            f"- **اللغات المستخدمة:** {top_langs}\n"
            f"- **الأطر والمكتبات:** {fws}\n"
            f"- **الجداول المستخرجة:** {len(tables)}\n"
            f"- **نقاط الاتصال API:** {len(endpoints)}\n"
            f"- **التكاملات المكتشفة:** {', '.join(fact_sheet.integrations) or 'لا يوجد'}\n\n"
            "تصفح الحزم الأربع من القائمة العليا أو الجانبية، وراجع `00_Governance/Quality-Audit-Report.md` لمتابعة حالة الاعتماد.\n"
        )
        (self.docs_src_dir / "index.md").write_text(index_md, encoding="utf-8")

    def get_chat_engine(self):
        if not self.chat_engine:
            self.chat_engine = DocumentationChatEngine(
                self.docs_src_dir,
                self.model_manager,
                index_dir=self.rag_index_dir,
                skill_loader=self.skill_loader,
            )
            self.chat_engine.load_documents(prefer_saved_index=True)
        return self.chat_engine

    def serve_and_tunnel(self, port=8000, timeout=30):
        if os.name != "nt":
            subprocess.run(
                "pkill -f 'mkdocs serve'",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        subprocess.Popen(
            ["mkdocs", "serve", "-a", f"127.0.0.1:{port}"],
            cwd=str(self.docs_repo_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        time.sleep(3)

        public_url, _cf_proc = launch_cloudflared_tunnel(
            port=port,
            timeout=timeout,
            stop_existing=True,
        )
        return public_url
