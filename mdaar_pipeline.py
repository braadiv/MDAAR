"""
MDAAR (Multi-role Dynamic Architecture for Automated Repositories)
Core Engine & Automation Module

An enterprise-grade, 100% free, privacy-first, AI-driven documentation framework.
Supports:
1. Multi-role AI Agents (Architecture, Database, API, Operations, User Guides, QA).
2. Model Selection: Gemini API (with key rotation) or Local Offline Models (GGUF / llama-cpp / transformers).
3. Dual Git Synchronization (Source Repo & Docs Repo via SSH / Deploy Keys or HTTPS).
4. Automated Schema & Route Parsers (SQL DDL -> Mermaid ERD, Code Routes -> API Reference).
5. Continuous Living Documentation (Git Diff Impact Analysis).
6. Zero-Cost Semantic Search & Interactive AI Q&A Assistant (RAG over docs).
7. MkDocs Material interactive portal with instant Cloudflare Tunnel.
"""

import os
import sys
import json
import glob
import time
import re
import shutil
import subprocess
from pathlib import Path

# ==============================================================================
# 1. SPECIALIZED PARSERS (SCHEMA & ROUTE EXTRACTORS)
# ==============================================================================

class SchemaParser:
    """Extracts tables, columns, and relationships from SQL / migrations to build Mermaid ERD."""
    
    @staticmethod
    def parse_sql_to_mermaid(sql_text):
        tables = {}
        relationships = []
        
        # Regex for CREATE TABLE tbl_name (...)
        table_matches = re.finditer(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[`"\[]?(\w+)[`"\]]?\.?[`"\[]?(\w+)[`"\]]?|[`"\[]?(\w+)[`"\]]?)\s*\((.*?)\);', sql_text, re.DOTALL | re.IGNORECASE)
        
        for tm in table_matches:
            tbl_name = tm.group(2) or tm.group(3) or tm.group(1)
            # Avoid single char artifact
            if len(tbl_name) == 1 and tm.group(1) and len(tm.group(1)) > 1:
                tbl_name = tm.group(1)
            body = tm.group(4)
            columns = []
            
            for line in body.splitlines():
                line = line.strip().rstrip(',')
                if not line or line.upper().startswith(('PRIMARY KEY', 'KEY', 'INDEX', 'CONSTRAINT', 'FOREIGN KEY')):
                    # Check for inline or table-level FOREIGN KEY ... REFERENCES other_table(id)
                    fk_m = re.search(r'FOREIGN\s+KEY.*?REFERENCES\s+[`"\[]?(\w+)[`"\]]?', line, re.IGNORECASE)
                    if fk_m:
                        ref_tbl = fk_m.group(1)
                        relationships.append(f"    {tbl_name} }}|--|| {ref_tbl} : references")
                    continue
                
                parts = line.split()
                if len(parts) >= 2:
                    col_name = re.sub(r'[`"\[\]]', '', parts[0])
                    col_type = re.sub(r'[^a-zA-Z0-9_()]', '', parts[1])
                    columns.append(f"        {col_type} {col_name}")
            
            tables[tbl_name] = columns

        if not tables:
            return None

        mermaid_lines = ["```mermaid", "erDiagram"]
        for tbl, cols in tables.items():
            mermaid_lines.append(f"    {tbl} {{")
            for col in cols[:15]: # Limit for clean visualization
                mermaid_lines.append(col)
            mermaid_lines.append("    }")
        
        for rel in set(relationships):
            mermaid_lines.append(rel)
            
        mermaid_lines.append("```")
        return "\n".join(mermaid_lines)


class RouteParser:
    """Scans code to extract REST API endpoints (FastAPI, Flask, Express, Laravel/PHP, etc.)"""

    @staticmethod
    def extract_routes_from_code(code_text, file_ext):
        endpoints = []
        
        if file_ext in ('.py',):
            # Matches @app.get('/path'), @router.post('/path')
            matches = re.finditer(r'@(?:\w+\.)?(get|post|put|delete|patch|options)\s*\(\s*[\'"]([^\'"]+)[\'"]', code_text, re.IGNORECASE)
            for m in matches:
                endpoints.append({"method": m.group(1).upper(), "path": m.group(2)})
                
        elif file_ext in ('.js', '.ts'):
            # Matches router.get('/path', ...), app.post('/path', ...)
            matches = re.finditer(r'(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*[\'"]([^\'"]+)[\'"]', code_text, re.IGNORECASE)
            for m in matches:
                endpoints.append({"method": m.group(1).upper(), "path": m.group(2)})
                
        elif file_ext in ('.php',):
            # Matches Route::get('/path', ...), $routes->post('/path', ...)
            matches = re.finditer(r'(?:Route::|\$routes->)(get|post|put|delete|patch)\s*\(\s*[\'"]([^\'"]+)[\'"]', code_text, re.IGNORECASE)
            for m in matches:
                endpoints.append({"method": m.group(1).upper(), "path": m.group(2)})

        return endpoints


# ==============================================================================
# 2. MODEL GATEWAY (GEMINI / LOCAL OFFLINE / ROTATION)
# ==============================================================================

class ModelGateway:
    """Handles LLM invocation with multi-provider support, key rotation, and offline fallback."""

    def __init__(
        self,
        provider="Gemini",
        model_name="gemini-2.5-flash",
        api_keys=None,
        local_model_path=None,
        hf_token=None,
        quantization="4bit"
    ):
        self.provider = provider
        self.model_name = model_name
        self.api_keys = [k.strip() for k in (api_keys or []) if k.strip()]
        self.current_key_idx = 0
        self.local_model_path = local_model_path
        self.hf_token = hf_token
        self.quantization = quantization
        self._local_pipeline = None

    def _get_active_key(self):
        if not self.api_keys:
            return None
        return self.api_keys[self.current_key_idx % len(self.api_keys)]

    def _rotate_key(self):
        if len(self.api_keys) > 1:
            self.current_key_idx = (self.current_key_idx + 1) % len(self.api_keys)
            print(f"🔄 Rotating API Key to index {self.current_key_idx}...")

    def generate(self, prompt, system_instruction=""):
        if self.provider == "Gemini":
            return self._generate_gemini(prompt, system_instruction)
        elif self.provider in ("Local-Offline", "Transformers"):
            return self._generate_local(prompt, system_instruction)
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

    def _generate_gemini(self, prompt, system_instruction):
        attempts = max(1, len(self.api_keys))
        last_error = None
        
        for _ in range(attempts):
            key = self._get_active_key()
            if not key:
                return "⚠️ No Gemini API Key configured."
            try:
                from google import genai
                client = genai.Client(api_key=key)
                full_prompt = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt
                resp = client.models.generate_content(
                    model=self.model_name or 'gemini-2.5-flash',
                    contents=full_prompt
                )
                return resp.text
            except Exception as e:
                last_error = e
                print(f"⚠️ Gemini request error: {e}")
                self._rotate_key()

        return f"⚠️ Generation failed across all keys: {last_error}"

    def _generate_local(self, prompt, system_instruction):
        """Offline / GPU inference using Transformers with 4-bit NF4 quantization on Colab T4."""
        try:
            if not self._local_pipeline:
                import torch
                from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline

                model_id = self.local_model_path or "Qwen/Qwen2.5-Coder-7B-Instruct"
                is_cuda = torch.cuda.is_available()
                device_desc = f"GPU ({torch.cuda.get_device_name(0)})" if is_cuda else "CPU"
                print(f"⏳ Loading local model ({model_id}) on {device_desc}...")

                tokenizer = AutoTokenizer.with_pretrained(
                    model_id,
                    token=self.hf_token,
                    trust_remote_code=True
                )
                if tokenizer.pad_token_id is None:
                    tokenizer.pad_token_id = tokenizer.eos_token_id

                model_kwargs = {
                    "token": self.hf_token,
                    "trust_remote_code": True,
                }

                if is_cuda and self.quantization == "4bit":
                    from transformers import BitsAndBytesConfig
                    # For Nvidia T4 (Turing CC 7.5), float16 is native and faster than bfloat16
                    compute_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
                    bnb_config = BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_compute_dtype=compute_dtype,
                        bnb_4bit_use_double_quant=True
                    )
                    model_kwargs["quantization_config"] = bnb_config
                    model_kwargs["device_map"] = "auto"
                elif is_cuda:
                    model_kwargs["device_map"] = "auto"
                    model_kwargs["torch_dtype"] = torch.float16
                else:
                    model_kwargs["device_map"] = "cpu"
                    model_kwargs["torch_dtype"] = torch.float32

                model = AutoModelForCausalLM.with_pretrained(model_id, **model_kwargs)
                self._local_pipeline = pipeline(
                    "text-generation",
                    model=model,
                    tokenizer=tokenizer
                )
                print(f"✅ Local model ({model_id}) loaded successfully!")

            messages = []
            if system_instruction:
                messages.append({"role": "system", "content": system_instruction})
            messages.append({"role": "user", "content": prompt})

            out = self._local_pipeline(
                messages,
                max_new_tokens=2048,
                do_sample=True,
                temperature=0.3
            )
            return out[0]["generated_text"][-1]["content"]
        except Exception as e:
            return (
                f"⚠️ Local generation error: {e}\n"
                "(Tip: For 7B/8B/9B models on Colab, ensure T4 GPU is selected and run: "
                "pip install -U transformers accelerate bitsandbytes)"
            )


# ==============================================================================
# 3. SPECIALIZED DOCUMENTATION AGENTS (MULTI-ROLE ARCHITECTURE)
# ==============================================================================

class BaseDocAgent:
    def __init__(self, name, gateway, project_name):
        self.name = name
        self.gateway = gateway
        self.project_name = project_name

    def execute(self, doc_name, context, specific_goal=""):
        sys_prompt = f"""أنت وكيل ذكي متخصص ({self.name}) ضمن منظومة MDAAR لتوثيق البرمجيات.
المشروع: {self.project_name}
مهمتك: صياغة وتحديث الوثيقة ({doc_name}) بأعلى معايير الهندسة البرمجية.

القواعد الإلزامية:
1. صياغة تقنية دقيقة باللغة العربية مع الحفاظ على المصطلحات والأسماء البرمجية بالإنجليزية.
2. استخدام جداول ومخططات Mermaid التفاعلية لتوضيح التدفق والمعمارية.
3. الاستناد حصراً إلى الكود والبيانات المزودة وعدم اختراع معلومات، ووضع علامة TBD عند الحاجة لمدخلات بشرية.
4. إخراج النص بتنسيق Markdown احترافي وجاهز للنشر المباشر.
"""
        user_prompt = f"""الهدف من الوثيقة: {specific_goal}

سياق الكود والمعلومات المستخلصة من المشروع:
{context[:25000]}

المطلوب: كتابة محتوى الوثيقة كاملاً ومنظماً."""
        return self.gateway.generate(user_prompt, sys_prompt)


class ArchitectureAgent(BaseDocAgent):
    def __init__(self, gateway, project_name):
        super().__init__("Architecture & System Design Agent", gateway, project_name)

class DatabaseAgent(BaseDocAgent):
    def __init__(self, gateway, project_name):
        super().__init__("Database & Schema Agent", gateway, project_name)

class APIAgent(BaseDocAgent):
    def __init__(self, gateway, project_name):
        super().__init__("API Documentation Agent", gateway, project_name)

class OperationsAgent(BaseDocAgent):
    def __init__(self, gateway, project_name):
        super().__init__("Operations & DevOps Agent", gateway, project_name)

class UserGuideAgent(BaseDocAgent):
    def __init__(self, gateway, project_name):
        super().__init__("User Experience & Manuals Agent", gateway, project_name)

class QualityAgent(BaseDocAgent):
    def __init__(self, gateway, project_name):
        super().__init__("Documentation QA & Consistency Agent", gateway, project_name)


# ==============================================================================
# 4. LIVING DOCUMENTATION IMPACT ANALYZER (GIT DIFF TRACKER)
# ==============================================================================

class ImpactAnalyzer:
    """Maps changed source files from Git diff to corresponding documentation artifacts."""

    @staticmethod
    def analyze_impact(changed_files):
        impact_map = {
            "Database": {"agent": "DatabaseAgent", "docs": ["02_Technical/Database-ERD.md", "02_Technical/Data-Dictionary.md"]},
            "API": {"agent": "APIAgent", "docs": ["02_Technical/API-Reference.md", "02_Technical/Integrations.md"]},
            "Architecture": {"agent": "ArchitectureAgent", "docs": ["02_Technical/SAD.md", "02_Technical/HLD.md"]},
            "Operations": {"agent": "OperationsAgent", "docs": ["03_Operations/Deployment-Guide.md", "03_Operations/Configuration-Guide.md"]},
            "UserGuides": {"agent": "UserGuideAgent", "docs": ["04_User-Guides/User-Manual.md", "05_Release-Documentation/Release-Notes.md"]}
        }

        affected = set()
        for f in changed_files:
            lower_f = f.lower()
            if any(kw in lower_f for kw in ['migration', 'schema', 'sql', 'model', 'entities']):
                affected.add("Database")
            if any(kw in lower_f for kw in ['controller', 'route', 'api', 'endpoint', 'router']):
                affected.add("API")
            if any(kw in lower_f for kw in ['docker', 'ci', 'workflow', '.github', 'deploy', 'compose']):
                affected.add("Operations")
            if any(kw in lower_f for kw in ['view', 'page', 'ui', 'frontend', 'template', 'screen']):
                affected.add("UserGuides")
            if any(kw in lower_f for kw in ['core', 'service', 'module', 'architecture', 'app']):
                affected.add("Architecture")

        results = []
        for cat in affected:
            results.append(impact_map[cat])
        return results


# ==============================================================================
# 5. ZERO-COST SEMANTIC SEARCH & CHAT ENGINE (RAG OVER DOCS)
# ==============================================================================

class DocumentationChatEngine:
    """Interactive Natural Language Q&A over generated documentation (100% Free / In-Memory)."""

    def __init__(self, docs_src_dir, gateway):
        self.docs_src_dir = Path(docs_src_dir)
        self.gateway = gateway
        self.documents = []
        self.load_documents()

    def load_documents(self):
        self.documents = []
        for md_path in self.docs_src_dir.glob("**/*.md"):
            try:
                text = md_path.read_text(encoding="utf-8", errors="ignore")
                rel = md_path.relative_to(self.docs_src_dir)
                # Split into sections by headers
                sections = re.split(r'\n(?=#{1,3}\s)', text)
                for sec in sections:
                    if sec.strip():
                        self.documents.append({"source": str(rel), "content": sec.strip()})
            except Exception:
                pass

    def search_relevant(self, query, top_k=4):
        """Lightweight keyword and token overlap retrieval without external SaaS."""
        query_words = set(re.findall(r'\w+', query.lower()))
        scored = []
        for doc in self.documents:
            doc_words = set(re.findall(r'\w+', doc["content"].lower()))
            overlap = len(query_words.intersection(doc_words))
            if overlap > 0:
                scored.append((overlap, doc))
        
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:top_k]]

    def ask(self, question, audience="Developer"):
        relevant_docs = self.search_relevant(question)
        if not relevant_docs:
            context_text = "لم يتم العثور على مقاطع مرتبطة مباشرة في الوثائق."
        else:
            context_text = "\n\n".join([f"--- المصدر: {d['source']} ---\n{d['content'][:1200]}" for d in relevant_docs])

        prompt = f"""السياق المسترجع من وثائق المشروع:
{context_text}

السؤال المطروح:
{question}

الجمهور المستهدف: {audience} (قدم الشرح بالأسلوب المناسب لهذه الفئة).

المطلوب:
1. إجابة دقيقة ومباشرة مستندة حصراً إلى الوثائق أعلاه.
2. الاستشهاد بالوثائق والمصادر المستخدمة في نهاية الإجابة.
3. إذا كانت المعلومة غير موجودة بوضوح، اذكر ذلك صراحة مع الإشارة إلى الوثيقة المقترحة لإضافتها.
"""
        sys_prompt = "أنت مساعد ذكي خبير في قاعدة معرفة النظام البرمجي. تجيب بدقة واستشهاد موثوق بمصادر الوثائق."
        return self.gateway.generate(prompt, sys_prompt)


# ==============================================================================
# 6. PIPELINE ORCHESTRATOR (MAIN WORKFLOW)
# ==============================================================================

class MDAARPipeline:
    def __init__(self, project_name="MyProject", base_workspace=None, git_source_url=None, git_docs_url=None, source_auth="HTTPS", docs_auth="HTTPS", gateway=None):
        self.project_name = project_name
        self.git_source_url = git_source_url
        self.git_docs_url = git_docs_url
        self.source_auth = source_auth
        self.docs_auth = docs_auth
        self.gateway = gateway or ModelGateway()

        if base_workspace:
            self.base_workspace = Path(base_workspace)
        elif Path("/content/drive/MyDrive").exists():
            self.base_workspace = Path("/content/drive/MyDrive/AI_Documentation_Workspace")
        else:
            self.base_workspace = Path("/content/AI_Documentation_Workspace")

        self.project_dir = self.base_workspace / self.project_name
        self.config_file = self.project_dir / ".config.json"
        self.state_file = self.project_dir / ".state.json"
        self.ssh_dir = self.project_dir / ".ssh"
        self.source_repo_dir = self.project_dir / "source_repo"
        self.docs_repo_dir = self.project_dir / "docs_repo"
        self.docs_src_dir = self.docs_repo_dir / "docs"
        
        # Dedicated SSH key pairs for source repo and docs repo
        self.source_ssh_key = self.ssh_dir / "id_ed25519_source"
        self.docs_ssh_key = self.ssh_dir / "id_ed25519_docs"

        self._init_dirs()
        self.chat_engine = None

    def _init_dirs(self):
        for d in [self.project_dir, self.ssh_dir, self.source_repo_dir, self.docs_repo_dir, self.docs_src_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def setup_source_ssh(self):
        if not self.source_ssh_key.exists():
            subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", str(self.source_ssh_key), "-N", ""], check=True)
        pub_key = (self.ssh_dir / "id_ed25519_source.pub").read_text(encoding="utf-8").strip()
        return pub_key

    def setup_docs_ssh(self):
        if not self.docs_ssh_key.exists():
            subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", str(self.docs_ssh_key), "-N", ""], check=True)
        pub_key = (self.ssh_dir / "id_ed25519_docs.pub").read_text(encoding="utf-8").strip()
        return pub_key

    def sync_source_repo(self):
        if self.source_auth == "SSH" and self.source_ssh_key.exists():
            os.environ["GIT_SSH_COMMAND"] = f"ssh -i {self.source_ssh_key} -o StrictHostKeyChecking=no"
        elif "GIT_SSH_COMMAND" in os.environ and self.source_auth != "SSH":
            del os.environ["GIT_SSH_COMMAND"]

        if not (self.source_repo_dir / ".git").exists():
            if self.git_source_url:
                res = subprocess.run(["git", "clone", self.git_source_url, str(self.source_repo_dir)], capture_output=True, text=True)
                if res.returncode != 0:
                    print(f"Git clone notice: {res.stderr.strip()}")
        else:
            subprocess.run(["git", "-C", str(self.source_repo_dir), "pull"], capture_output=True, text=True)

        current_commit = ""
        changed_files = []
        if (self.source_repo_dir / ".git").exists():
            cur_res = subprocess.run(["git", "-C", str(self.source_repo_dir), "rev-parse", "HEAD"], capture_output=True, text=True)
            current_commit = cur_res.stdout.strip()
            state = self.load_state()
            last_commit = state.get("last_commit")
            if last_commit and last_commit != current_commit:
                diff_res = subprocess.run(["git", "-C", str(self.source_repo_dir), "diff", "--name-status", f"{last_commit}..{current_commit}"], capture_output=True, text=True)
                changed_files = [line.strip() for line in diff_res.stdout.splitlines() if line.strip()]

        return current_commit, changed_files

    def sync_docs_repo(self, commit_message="docs: automated documentation update [skip ci]"):
        """Syncs the docs_repo with remote Git repository (Clone or Push) using independent Docs Deploy Key.
        Automatically organizes documentation inside a subfolder named after the project (<project_name>/),
        allowing the external documentation repository to serve as a unified documentation hub for multiple projects.
        """
        if not self.git_docs_url:
            return "No remote docs Git URL configured (Local Google Drive mode)."

        if self.docs_auth == "SSH" and self.docs_ssh_key.exists():
            os.environ["GIT_SSH_COMMAND"] = f"ssh -i {self.docs_ssh_key} -o StrictHostKeyChecking=no"
        elif "GIT_SSH_COMMAND" in os.environ and self.docs_auth != "SSH":
            del os.environ["GIT_SSH_COMMAND"]

        # Dedicated subfolder for this project in the shared docs repo
        project_hub_dir = self.docs_repo_dir / "projects" / self.project_name
        project_hub_dir.mkdir(parents=True, exist_ok=True)

        if not (self.docs_repo_dir / ".git").exists():
            print(f"📥 Cloning external documentation repository from: {self.git_docs_url}...")
            res = subprocess.run(["git", "clone", self.git_docs_url, str(self.docs_repo_dir)], capture_output=True, text=True)
            if res.returncode != 0:
                # Initialize new git repo if remote repo is newly created / empty
                subprocess.run(["git", "-C", str(self.docs_repo_dir), "init"], check=True)
                subprocess.run(["git", "-C", str(self.docs_repo_dir), "remote", "add", "origin", self.git_docs_url], check=True)
        else:
            subprocess.run(["git", "-C", str(self.docs_repo_dir), "pull", "origin", "main"], capture_output=True, text=True)

        # Mirror/Sync project documentation into its dedicated subfolder
        shutil.copytree(str(self.docs_src_dir), str(project_hub_dir / "docs"), dirs_exist_ok=True)
        if (self.docs_repo_dir / "mkdocs.yml").exists() and not (project_hub_dir / "mkdocs.yml").exists():
            shutil.copy2(str(self.docs_repo_dir / "mkdocs.yml"), str(project_hub_dir / "mkdocs.yml"))

        # Commit and Push
        subprocess.run(["git", "-C", str(self.docs_repo_dir), "config", "user.name", "MDAAR AI Bot"], check=True)
        subprocess.run(["git", "-C", str(self.docs_repo_dir), "config", "user.email", "mdaar-bot@users.noreply.github.com"], check=True)
        subprocess.run(["git", "-C", str(self.docs_repo_dir), "add", "."], check=True)
        status_res = subprocess.run(["git", "-C", str(self.docs_repo_dir), "status", "--porcelain"], capture_output=True, text=True)
        
        if status_res.stdout.strip():
            subprocess.run(["git", "-C", str(self.docs_repo_dir), "commit", "-m", f"docs({self.project_name}): update documentation [skip ci]"], check=True)
            push_res = subprocess.run(["git", "-C", str(self.docs_repo_dir), "push", "-u", "origin", "HEAD"], capture_output=True, text=True)
            if push_res.returncode == 0:
                return f"✅ Successfully pushed updated documentation for project [{self.project_name}] to remote Git repository!"
            else:
                return f"⚠️ Git Push notice: {push_res.stderr.strip()}"
        return f"ℹ️ No documentation changes to commit for project [{self.project_name}]."

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
        state["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        return state

    def extract_and_generate_specialized(self):
        """Scans actual SQL migrations and code routes to build authentic ERD and API tables."""
        # 1. SQL Parser
        sql_corpus = []
        for p in glob.glob(f"{self.source_repo_dir}/**/*.sql", recursive=True):
            try:
                sql_corpus.append(Path(p).read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                pass
        
        mermaid_erd = SchemaParser.parse_sql_to_mermaid("\n".join(sql_corpus))
        erd_path = self.docs_src_dir / "02_Technical" / "Database-ERD.md"
        if mermaid_erd:
            erd_content = f"# مخطط قاعدة البيانات المعتمد (Database Schema & ERD)\n\nتم استخلاص هذا المخطط آلياً من ملفات الجداول والترحيل (Migrations):\n\n{mermaid_erd}\n"
            erd_path.write_text(erd_content, encoding="utf-8")
            print("✅ Auto-generated real Mermaid ERD from project SQL files!")

        # 2. Route Parser
        discovered_routes = []
        for ext in ('.py', '.js', '.ts', '.php'):
            for p in glob.glob(f"{self.source_repo_dir}/**/*{ext}", recursive=True):
                if any(skip in p for skip in ['.git', 'vendor', 'node_modules']):
                    continue
                try:
                    text = Path(p).read_text(encoding="utf-8", errors="ignore")
                    routes = RouteParser.extract_routes_from_code(text, ext)
                    for r in routes:
                        r["file"] = str(Path(p).relative_to(self.source_repo_dir))
                        discovered_routes.append(r)
                except Exception:
                    pass

        if discovered_routes:
            api_path = self.docs_src_dir / "02_Technical" / "API-Reference.md"
            lines = [
                "# مرجع واجهات برمجة التطبيقات (API Reference)",
                "",
                "تم استخلاص نقاط الاتصال التالية آلياً من مسارات الكود المصدري للمشروع:",
                "",
                "| الطريقة (Method) | المسار (Endpoint) | الملف المصدر (Source File) |",
                "|---|---|---|"
            ]
            for r in discovered_routes[:50]:
                lines.append(f"| `{r['method']}` | `{r['path']}` | `{r['file']}` |")
            api_path.write_text("\n".join(lines), encoding="utf-8")
            print(f"✅ Discovered and documented {len(discovered_routes)} API routes automatically!")

    def init_framework_structure(self):
        doc_structure = {
            "00_Governance": [
                ("Documentation-Policy.md", "# سياسة التوثيق الموحدة (Documentation Policy)\n\nتحدد هذه السياسة معايير وإجراءات حوكمة التوثيق في المنظومة ومسؤوليات الاعتماد والتحديث المستمر."),
                ("Documentation-Standards.md", "# معايير كتابة الوثائق البرمجية (Documentation Standards)\n\nالمعايير المعتمدة في الصياغة الفنية، تسمية الملفات، كتابة Markdown، وإدراج مخططات Mermaid."),
                ("Glossary.md", "# قاموس المصطلحات الموحد (Glossary)\n\n| المصطلح العربي | English Term | التعريف والتوصيف |\n|---|---|---|"),
                ("AI-Governance.md", "# حوكمة وتوظيف الذكاء الاصطناعي (AI Governance)\n\nضوابط التحقق، إدارة الـ Prompts، مراجعة النماذج التوليدية (Human-in-the-Loop) وحماية الخصوصية."),
                ("Document-Register.md", "# سجل الوثائق المعتمد (Document Register)\n\n| معرف الوثيقة | اسم الوثيقة | التصنيف | المالك | مستوى الاعتماد | تاريخ التحديث |\n|---|---|---|---|---|---|")
            ],
            "01_Administrative": [
                ("Project-Charter.md", f"# ميثاق المشروع: {self.project_name}\n\n## 1. أهداف المشروع\n## 2. النطاق العام\n## 3. أصحاب المصلحة والمستفيدون"),
                ("Business-Case.md", "# دراسة الجدوى والقيمة الاستثمارية (Business Case)\n\n## 1. مبررات المشروع\n## 2. العائد المتوقع ومؤشرات النجاح (KPIs)"),
                ("Scope.md", "# نطاق المشروع وحدود العمل (Project Scope Baseline)\n\n### داخل النطاق (In-Scope)\n### خارج النطاق (Out-of-Scope)"),
                ("BRD.md", "# وثيقة متطلبات الأعمال (Business Requirements Document)\n\n## متطلبات العمل وسير العمليات التشغيلية"),
                ("Risk-Register.md", "# سجل إدارة المخاطر (Risk Register)\n\n| معرف الخطر | الوصف | الاحتمالية | الأثر | خطة الاستجابة والتعامل |\n|---|---|---|---|---|"),
                ("Decision-Log.md", "# سجل القرارات الإدارية والتنظيمية (Decision Log)\n\n| الرقم | التاريخ | القرار المعتمد | السبب والسياق | المسؤول المعتمد |\n|---|---|---|---|---|")
            ],
            "02_Technical": [
                ("SRS.md", "# مواصفات متطلبات النظام (Software Requirements Specification)\n\n## 1. المتطلبات الوظيفية (Functional)\n## 2. المتطلبات غير الوظيفية (Non-Functional)"),
                ("SAD.md", "# وثيقة معمارية النظام (System Architecture Document)\n\n## 1. النمط المعماري العام\n## 2. المكونات والطبقات الرئيسية\n## 3. تدفق البيانات"),
                ("HLD.md", "# وثيقة التصميم عالي المستوى (High-Level Design)\n\n## الهيكل التنظيمي للمنظومة وتكامل الخدمات"),
                ("LLD.md", "# وثيقة التصميم التفصيلي (Low-Level Design)\n\n## الكلاسات، الواجهات البرمجية، وخوارزميات المعالجة"),
                ("Database-ERD.md", "# مخطط وتصميم قاعدة البيانات (Database Schema & ERD)\n\n```mermaid\nerDiagram\n  USERS ||--o{ LOGS : generates\n```"),
                ("API-Reference.md", "# مرجع وتوثيق واجهات البرمجة (API Documentation)\n\n## نقاط الاتصال (Endpoints) ومواصفات الطلب والاستجابة")
            ],
            "03_Operations": [
                ("Installation-Guide.md", "# دليل التثبيت والتجهيز البيئي (Installation Guide)\n\n## المتطلبات المسبقة وخطوات التثبيت خطوة بخطوة"),
                ("Configuration-Guide.md", "# دليل الإعداد وضبط المتغيرات (Configuration Guide)\n\n## المتغيرات البيئية ومفاتيح التكوين"),
                ("Deployment-Guide.md", "# دليل النشر وخطوط الإنتاج (Deployment & CI/CD Guide)\n\n## آلية النشر وأتمتة الاختبارات والتسليم"),
                ("Troubleshooting.md", "# دليل استكشاف الأخطاء وحلها (Troubleshooting & Debugging)\n\n## الأعطال الشائعة والحلول الموصى بها"),
                ("Backup-Recovery.md", "# خطة النسخ الاحتياطي واستعادة الكوارث (Backup & Disaster Recovery)\n\n## الجداول الزمنية للنسخ وإجراءات الاسترجاع الفوري")
            ],
            "04_User-Guides": [
                ("User-Manual.md", "# دليل المستخدم النهائي (End-User Manual)\n\n## شاشات النظام وطريقة الاستخدام خطوة بخطوة"),
                ("Admin-Manual.md", "# دليل مسؤول النظام (Administrator Manual)\n\n## إدارة المستخدمين، الصلاحيات، والعمليات الرقابية"),
                ("FAQs.md", "# الأسئلة الشائعة وإجاباتها (Frequently Asked Questions)\n\n## الاستفسارات الأكثر تكراراً")
            ],
            "05_Release-Documentation": [
                ("Release-Notes.md", f"# ملاحظات الإصدار (Release Notes)\n\n### الإصدار v1.0.0\n- تهيئة وتدشين منظومة التوثيق الموحدة لمشروع {self.project_name}.")
            ]
        }

        created = 0
        for section, files in doc_structure.items():
            sec_dir = self.docs_src_dir / section
            sec_dir.mkdir(parents=True, exist_ok=True)
            for fname, default_text in files:
                fpath = sec_dir / fname
                if not fpath.exists():
                    fpath.write_text(default_text, encoding="utf-8")
                    created += 1

        index_file = self.docs_src_dir / "index.md"
        if not index_file.exists():
            index_file.write_text(f"# 📚 بوابة التوثيق المرجعي: {self.project_name}\n\nمرحباً بك في بوابة التوثيق الشاملة المدعومة بـ MDAAR Framework.\n\nتصفح الحزم من القائمة الجانبية.", encoding="utf-8")

        self._generate_mkdocs_config()
        self.extract_and_generate_specialized()
        return created

    def _generate_mkdocs_config(self):
        content = f"""site_name: 'توثيق {self.project_name}'
site_description: 'بوابة التوثيق التفاعلية المدعومة بالذكاء الاصطناعي - MDAAR Framework'
site_author: 'Development Team'

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
      - توثيق الواجهات APIs: 02_Technical/API-Reference.md
  - الوثائق التشغيلية:
      - دليل التثبيت: 03_Operations/Installation-Guide.md
      - دليل الإعداد: 03_Operations/Configuration-Guide.md
      - دليل النشر: 03_Operations/Deployment-Guide.md
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

    def scan_codebase_context(self, max_files=40, max_size_kb=50):
        summary = []
        extensions = ("*.py", "*.php", "*.js", "*.ts", "*.json", "*.sql", "*.md", "*.yml", "*.yaml")
        scanned = 0
        for ext in extensions:
            for p in glob.glob(f"{self.source_repo_dir}/**/{ext}", recursive=True):
                if any(skip in p for skip in [".git", "vendor", "node_modules", "__pycache__", ".pytest_cache"]):
                    continue
                fpath = Path(p)
                if fpath.is_file() and fpath.stat().st_size <= max_size_kb * 1024:
                    try:
                        content = fpath.read_text(encoding="utf-8", errors="ignore")
                        rel = fpath.relative_to(self.source_repo_dir)
                        summary.append(f"--- FILE: {rel} ---\n" + content[:1500])
                        scanned += 1
                    except Exception:
                        pass
                if scanned >= max_files:
                    break
            if scanned >= max_files:
                break
        return "\n\n".join(summary)

    def run_multi_agent_generation(self, changed_files=None):
        """Dispatches specialized AI agents to generate or incrementally update documents."""
        context = self.scan_codebase_context()
        arch_agent = ArchitectureAgent(self.gateway, self.project_name)
        db_agent = DatabaseAgent(self.gateway, self.project_name)
        api_agent = APIAgent(self.gateway, self.project_name)
        user_agent = UserGuideAgent(self.gateway, self.project_name)
        ops_agent = OperationsAgent(self.gateway, self.project_name)

        if not changed_files:
            # Full Bootstrap
            print("🚀 Running Full Multi-Agent Documentation Generation...")
            sad_res = arch_agent.execute("SAD.md", context, "بناء وثيقة معمارية النظام ومكوناته ومخططاته")
            (self.docs_src_dir / "02_Technical" / "SAD.md").write_text(sad_res, encoding="utf-8")
            
            srs_res = arch_agent.execute("SRS.md", context, "صياغة مواصفات متطلبات النظام الوظيفية وغير الوظيفية")
            (self.docs_src_dir / "02_Technical" / "SRS.md").write_text(srs_res, encoding="utf-8")

            manual_res = user_agent.execute("User-Manual.md", context, "صياغة دليل المستخدم النهائي وشرح الوظائف بأسلوب مبسط")
            (self.docs_src_dir / "04_User-Guides" / "User-Manual.md").write_text(manual_res, encoding="utf-8")
            print("✅ Initial Multi-Agent documentation generated.")
        else:
            # Living Documentation via Git Diff
            impacted = ImpactAnalyzer.analyze_impact(changed_files)
            print(f"🔍 Living Documentation: Identified {len(impacted)} impacted areas from Git changes.")
            for group in impacted:
                for doc in group["docs"]:
                    doc_path = self.docs_src_dir / doc
                    print(f"🤖 Updating {doc} via {group['agent']}...")
                    res = arch_agent.execute(doc, context, f"تحديث الوثيقة وفقاً للملفات المعدلة: {', '.join(changed_files[:5])}")
                    doc_path.write_text(res, encoding="utf-8")

    def get_chat_engine(self):
        if not self.chat_engine:
            self.chat_engine = DocumentationChatEngine(self.docs_src_dir, self.gateway)
        return self.chat_engine

    def serve_and_tunnel(self, port=8000, timeout=30):
        subprocess.run("pkill -f 'mkdocs serve'", shell=True)
        subprocess.run("pkill -f 'cloudflared tunnel'", shell=True)

        mkdocs_proc = subprocess.Popen(
            ["mkdocs", "serve", "-a", f"127.0.0.1:{port}"],
            cwd=str(self.docs_repo_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        time.sleep(3)

        cf_proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        public_url = None
        start = time.time()
        while time.time() - start < timeout:
            line = cf_proc.stdout.readline()
            m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
            if m:
                public_url = m.group(0)
                break
            if not line and cf_proc.poll() is not None:
                break

        return public_url
