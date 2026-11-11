"""Tests for bundled MDAAR SkillLoader and DualModelManager VRAM switching."""

from mdaar.gateway import DualModelManager, ModelGateway
from mdaar.skills import SkillLoader


def test_bundled_skills_and_progressive_disclosure(tmp_path):
    loader = SkillLoader()
    bundled = loader.list_bundled_skills()
    names = {s.name for s in bundled}

    expected = {
        "core-grounding-qa",
        "reverse-engineering",
        "architecture-mermaid",
        "api-openapi-spec",
        "docs-technical-writer",
        "ops-and-user-guides",
        "rag-qa-grounding",
    }
    assert expected <= names

    # Progressive disclosure loads role-appropriate skills
    api_prompt = loader.load_for_role("api")
    assert "api-openapi-spec" in api_prompt
    assert "core-grounding-qa" in api_prompt

    # Target repo instruction discovery must sanitize embedded secrets
    (tmp_path / "AGENTS.md").write_text(
        "Follow Yii2 patterns.\nAPI_KEY=AIzaSyDummyKey123456789012345678901234567\n",
        encoding="utf-8",
    )
    repo_notes = loader.discover_repo_instructions(tmp_path)
    assert "Follow Yii2 patterns." in repo_notes
    assert "AIzaSy" not in repo_notes
    assert "[REDACTED_GOOGLE_API_KEY]" in repo_notes


class FakeLocalGateway(ModelGateway):
    """Lightweight test double simulating a loaded local HF model on Colab T4."""

    def __init__(self, local_model_path):
        super().__init__(provider="Local-Offline", local_model_path=local_model_path, quantization="4bit")
        self.unload_count = 0

    def generate(self, prompt, system_instruction="", max_new_tokens=None):
        self._local_pipeline = object()
        return f"[{self.local_model_path}] OK"

    def unload(self):
        if self._local_pipeline is not None:
            self._local_pipeline = None
            self.unload_count += 1
            return True
        return False


def test_dual_model_manager_unloads_inactive_local_model_for_vram_safety():
    planner = FakeLocalGateway("google/gemma-2-9b-it")
    executor = FakeLocalGateway("Qwen/Qwen2.5-Coder-7B-Instruct")
    mgr = DualModelManager(planner, executor)

    assert not mgr.is_single_model_mode

    # 1. Executor runs heavy inspection -> loads executor into VRAM
    mgr.generate_as_executor("inspect file")
    assert executor.is_loaded_in_vram()
    assert not planner.is_loaded_in_vram()

    # 2. Planner runs next -> must unload executor cleanly first before loading planner
    mgr.generate_as_planner("build plan")
    assert executor.unload_count == 1
    assert not executor.is_loaded_in_vram()
    assert planner.is_loaded_in_vram()

    # 3. Switching back to Executor unloads Planner
    mgr.generate_as_executor("write doc")
    assert planner.unload_count == 1
    assert mgr.stats["vram_switches"] == 2

    # 4. Single-model mode shares instance and performs zero VRAM switches
    same_mgr = DualModelManager(planner, planner)
    assert same_mgr.is_single_model_mode
    before_unloads = planner.unload_count
    same_mgr.generate_as_executor("a")
    same_mgr.generate_as_planner("b")
    assert planner.unload_count == before_unloads

