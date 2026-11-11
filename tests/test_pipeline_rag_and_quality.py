"""End-to-end tests for fail-fast Git sync, Dual-Model Map-Reduce pipeline, Quality Gate, and Hybrid RAG in .rag_index/."""

import json
import subprocess
import pytest

from mdaar.agents import DOC_SPECIFICATIONS, QualityAgent
from mdaar.analyzers import ProjectFactSheet
from mdaar.gateway import ModelGateway
from mdaar.orchestrator import MDAARPipeline
from mdaar.rag import DocumentationChatEngine


class StubGateway(ModelGateway):
    """Deterministic stub gateway tracking prompts for Planner vs Executor assertions."""

    def __init__(self, tag="executor"):
        super().__init__(provider="Gemini", model_name=f"stub-{tag}", api_keys=["dummy-key"])
        self.tag = tag
        self.calls = []

    def generate(self, prompt, system_instruction="", max_new_tokens=None):
        self.calls.append({"prompt": prompt, "system": system_instruction})
        if self.tag == "planner" and "Planner Model" in prompt:
            return (
                "# الخطة المرجعية\n\n"
                "- النظام يعتمد على PHP و SQL.\n"
                "- الجداول الحقيقية: `orders`, `invoices`.\n"
                "- `[⚠️ BLIND SPOT]` تفاصيل الخادم السحابي غير موجودة في المستودع.\n"
            )
        if self.tag == "planner":
            return "توصية المراجعة: يوصى بمراجعة قسم الصلاحيات واعتماد الوثائق (L2)."
        return (
            "# وثيقة تحليل النظام\n\n"
            "## النظرة العامة والتفاصيل\n\n"
            "تم بناء هذا القسم استناداً إلى الملفات الفعلية `controllers/OrderController.php` "
            "و `schema.sql` التي تدير جدول `orders` ونقطة الاتصال `/api/orders`.\n\n"
            "```mermaid\n"
            "flowchart TD\n"
            "    Client[\"تطبيق المستخدم (Client)\"] --> API[\"واجهة البرمجة (API)\"]\n"
            "    API --> DB[\"قاعدة البيانات (MySQL)\"]\n"
            "```\n\n"
            "- ملاحظة: بعض بيانات الاستضافة الخارجية غير متوفرة `[⚠️ BLIND SPOT]`.\n"
        )


def test_sync_source_repo_fails_fast_when_clone_fails_or_empty(tmp_path):
    pipeline = MDAARPipeline(
        project_name="BrokenProject",
        base_workspace=tmp_path,
        git_source_url="https://invalid.local/nonexistent-repo-xyz.git",
        gateway=StubGateway("executor"),
    )
    with pytest.raises(RuntimeError):
        pipeline.sync_source_repo()

    # Empty folder with no URL also fails cleanly instead of hallucinating over 0 files
    pipeline2 = MDAARPipeline(
        project_name="EmptyLocal",
        base_workspace=tmp_path,
        git_source_url=None,
        gateway=StubGateway("executor"),
    )
    with pytest.raises(RuntimeError):
        pipeline2.sync_source_repo()


def test_repo_connection_check_reports_success_and_sanitized_failure(tmp_path):
    # 1. Valid local bare git repo (empty -> empty_repo=True, success=True)
    bare_repo = tmp_path / "remote_docs.git"
    subprocess.run(["git", "init", "--bare", str(bare_repo)], check=True, capture_output=True)

    pipeline = MDAARPipeline(
        project_name="ConnTest",
        base_workspace=tmp_path / "ws",
        git_source_url=str(bare_repo),
        git_docs_url="https://user:secretpat123@127.0.0.1:1/org/private.git",
        source_auth="HTTPS",
        docs_auth="HTTPS",
        gateway=StubGateway("executor"),
    )
    src_check = pipeline.test_source_connection(timeout=5)
    assert src_check["success"] is True
    assert src_check["empty_repo"] is True
    assert "✅" in src_check["message"]

    # 2. Failed connection redacts embedded credentials and reports clear failure
    docs_check = pipeline.test_docs_connection(timeout=5)
    assert docs_check["success"] is False
    assert "secretpat123" not in docs_check["message"]
    assert "secretpat123" not in docs_check["url"]
    assert "[REDACTED_CREDENTIAL]" in docs_check["url"]
    assert "❌" in docs_check["message"]


def test_quality_gate_auto_fixes_mermaid_and_flags_language_hallucination():
    facts = ProjectFactSheet(
        project_name="ShopApp",
        git_trace="abc1234",
        git_branch="main",
        total_files=5,
        total_lines=100,
        language_counts={"php": 4, "sql": 1},
        verified_identifiers={"controllers/OrderController.php", "schema.sql", "orders"},
    )
    bad_doc = (
        "# Architecture\n\n"
        "النظام مبني بلغة Python باستخدام Django، راجع `controllers/OrderController.php`.\n\n"
        "```mermaid\n"
        "flowchart TD\n"
        "    A[بوابة (API)]<br/> --> B[Database]\n"
        "```\n"
    )
    cleaned, item = QualityAgent.audit_document("02_Technical/SAD.md", bad_doc, facts)
    assert 'A["بوابة (API)"]' in cleaned
    assert "<br/>" not in cleaned
    assert any("Python/Django" in w for w in item.warnings)
    assert not item.passed


def test_full_dual_model_pipeline_and_persistent_rag_index(tmp_path):
    ws = tmp_path / "workspace"
    planner_gw = StubGateway("planner")
    executor_gw = StubGateway("executor")

    pipeline = MDAARPipeline(
        project_name="StoreDemo",
        base_workspace=ws,
        planner_gateway=planner_gw,
        executor_gateway=executor_gw,
    )

    # Populate synthetic source repository directly in source_repo_dir
    src = pipeline.source_repo_dir
    (src / "controllers").mkdir(parents=True)
    (src / "controllers" / "OrderController.php").write_text(
        "<?php\nclass OrderController extends Controller {\n"
        "    public function actionListOrders() { return ['orders']; }\n"
        "}\n",
        encoding="utf-8",
    )
    (src / "schema.sql").write_text(
        "CREATE TABLE `orders` (\n  `id` INT PRIMARY KEY,\n  `customer_name` VARCHAR(120) NOT NULL\n);\n",
        encoding="utf-8",
    )
    (src / "README.md").write_text("# Store Demo\n\nنظام إدارة الطلبات والفواتير الإلكتروني.\n", encoding="utf-8")

    stats = pipeline.run_multi_agent_generation()

    # 1. Verified full codebase scan and deterministic extraction
    assert stats["files_scanned"] == 3
    assert stats["tables_extracted"] == 1
    assert stats["endpoints_extracted"] >= 1
    assert stats["docs_audited"] >= len(DOC_SPECIFICATIONS)

    # 2. Executor handled heavy Map inspection + Doc authoring; Planner handled Plan + QA summary
    assert len(executor_gw.calls) > len(planner_gw.calls)
    assert len(planner_gw.calls) >= 1

    # 3. Quality Gate created Document-Register and Quality-Audit-Report with L1 status
    reg_text = (pipeline.docs_src_dir / "00_Governance" / "Document-Register.md").read_text(encoding="utf-8")
    assert "L1 (AI-Verified)" in reg_text
    assert (pipeline.docs_src_dir / "00_Governance" / "Quality-Audit-Report.md").is_file()

    # 4. Persistent RAG index exists in project_dir/.rag_index/index.json (outside docs_repo)
    index_json = pipeline.rag_index_dir / "index.json"
    assert index_json.is_file()
    assert (pipeline.rag_index_dir / "master_plan.md").is_file()
    assert not (pipeline.docs_repo_dir / ".rag_index").exists()

    # 5. Reload RAG from disk cleanly and verify bilingual + code + audience search
    reloaded_rag = DocumentationChatEngine(
        pipeline.docs_src_dir,
        executor_gw,
        index_dir=pipeline.rag_index_dir,
    )
    loaded_count = reloaded_rag.load_documents(prefer_saved_index=True)
    assert loaded_count == stats["rag_chunks_indexed"]

    dev_hits = reloaded_rag.search_relevant("OrderController orders", top_k=5, audience="Developer")
    assert any("orders" in txt.lower() for _, txt in dev_hits)

    user_hits = reloaded_rag.search_relevant("OrderController", top_k=5, audience="End-User")
    # End-User view should prefer 04_User-Guides/ over internal code_fact / LLD unless needed
    assert any(src_lbl.startswith(("04_User-Guides/", "05_Release-Documentation/", "index.md")) for src_lbl, _ in user_hits)

    answer = reloaded_rag.ask("ما هي الجداول والوحدات في النظام؟", audience="Developer")
    assert "orders" in answer or "OrderController" in answer
