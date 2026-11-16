"""Unit tests for FreeLM Gateway and Shared Workspace .env resolution."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mdaar.gateway.freelm_adapter import FreeLMGateway, load_shared_workspace_env
from mdaar.gateway.model_gateway import ModelGateway, resolve_setting


def test_load_shared_workspace_env_loads_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "GEMINI_API_KEY=test_gemini_shared_123\n"
        "GROQ_API_KEY=test_groq_shared_456\n"
        "# Commented line\n"
        "OPENROUTER_API_KEY='test_or_789'\n",
        encoding="utf-8",
    )

    loaded = load_shared_workspace_env(str(tmp_path), inject_os_environ=False)
    assert loaded.get("GEMINI_API_KEY") == "test_gemini_shared_123"
    assert loaded.get("GROQ_API_KEY") == "test_groq_shared_456"
    assert loaded.get("OPENROUTER_API_KEY") == "test_or_789"


def test_resolve_setting_prefers_shared_workspace_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SHARED_CUSTOM_KEY=val_from_workspace_env\n", encoding="utf-8")

    val, src = resolve_setting(
        form_value="",
        env_names=("SHARED_CUSTOM_KEY",),
        return_source=True,
        workspace_path=str(tmp_path),
    )
    assert val == "val_from_workspace_env"
    assert "workspace-env" in src


def test_freelm_gateway_uninstalled_returns_friendly_error():
    gw = FreeLMGateway()
    with patch.dict("sys.modules", {"freelm": None}):
        gw._client_init_attempted = False
        gw._freelm_client = None
        ok, res = gw.generate("Hello test")
        assert not ok
        assert "freelm" in res.lower()


def test_freelm_gateway_with_mock_client():
    gw = FreeLMGateway()
    mock_client = MagicMock()
    mock_client.text.return_value = "Generated documentation content via FreeLM."
    gw._freelm_client = mock_client
    gw._client_init_attempted = True

    ok, res = gw.generate("Generate architecture overview", system_instruction="You are an architect.")
    assert ok
    assert res == "Generated documentation content via FreeLM."
    assert mock_client.text.called


def test_model_gateway_freelm_provider_dispatch():
    gw = ModelGateway(provider="FreeLM", model_name="auto")
    assert gw.is_freelm
    assert "FreeLM" in gw.descriptor

    mock_flm = MagicMock()
    mock_flm.generate.return_value = (True, "Success via FreeLM provider.")
    with patch("mdaar.gateway.freelm_adapter.FreeLMGateway", return_value=mock_flm):
        out = gw.generate("Test prompt")
        assert out == "Success via FreeLM provider."


def test_build_authenticated_git_url_and_docs_token():
    from mdaar.orchestrator import build_authenticated_git_url, MDAARPipeline

    # Bitbucket SSH -> HTTPS conversion with x-token-auth
    bb_ssh = "git@ssh.bitbucket.org:example-org/sample-docs.git"
    auth_bb = build_authenticated_git_url(bb_ssh, "tok_123456")
    assert auth_bb == "https://x-token-auth:tok_123456@bitbucket.org/example-org/sample-docs.git"

    # Bitbucket with custom username (App password)
    auth_bb_user = build_authenticated_git_url(bb_ssh, "app_pass", username="myuser")
    assert auth_bb_user == "https://myuser:app_pass@bitbucket.org/example-org/sample-docs.git"

    # GitHub SSH -> HTTPS conversion with x-access-token
    gh_ssh = "git@github.com:myorg/my-docs.git"
    auth_gh = build_authenticated_git_url(gh_ssh, "ghp_secret")
    assert auth_gh == "https://x-access-token:ghp_secret@github.com/myorg/my-docs.git"

    # HTTPS URL with embedded token
    gh_https = "https://github.com/myorg/my-docs.git"
    auth_gh_https = build_authenticated_git_url(gh_https, "ghp_secret")
    assert auth_gh_https == "https://x-access-token:ghp_secret@github.com/myorg/my-docs.git"

    # Pipeline resolves docs_access_token and get_effective_docs_url
    pipe = MDAARPipeline(
        project_name="vaccine",
        git_docs_url=bb_ssh,
        docs_access_token="tok_123456",
    )
    assert pipe.docs_access_token == "tok_123456"
    assert pipe.get_effective_docs_url() == auth_bb


def test_free_router_server_multi_platform_routes_and_selection():
    from mdaar.gateway.free_router_server import FreeRouterServer

    srv = FreeRouterServer.from_providers(
        gemini_keys="gemini_key_1",
        groq_keys="gsk_key_1",
        sambanova_keys="samba_key_1",
        hyperbolic_keys="hyp_key_1",
        nvidia_keys="nvapi_key_1",
        zai_keys="zai_key_1",
        cohere_keys="cohere_key_1",
        openrouter_keys="or_key_1",
        mistral_keys="mistral_key_1",
        cerebras_keys="cerebras_key_1",
    )

    route_names = {r.name for r in srv.routes}
    assert "gemini" in route_names
    assert "groq" in route_names
    assert "sambanova" in route_names
    assert "hyperbolic" in route_names
    assert "nvidia" in route_names
    assert "zai" in route_names
    assert "cohere" in route_names
    assert "openrouter" in route_names
    assert "mistral" in route_names
    assert "cerebras" in route_names

    # Auto routes to all registered routes
    auto_candidates = srv._select_candidates("auto")
    assert len(auto_candidates) >= 10

    # Specific route targeting by route name
    samba_candidates = srv._select_candidates("sambanova")
    assert all(r.name == "sambanova" for r, _ in samba_candidates)

    cohere_candidates = srv._select_candidates("cohere")
    assert all(r.name == "cohere" for r, _ in cohere_candidates)

    zai_candidates = srv._select_candidates("zai")
    assert all(r.name == "zai" for r, _ in zai_candidates)


def test_sync_docs_repo_eliminates_duplicate_root_docs_and_structures_projects(tmp_path):
    from mdaar.orchestrator import MDAARPipeline
    import subprocess

    # 1. Create a bare remote Git repository to simulate Bitbucket/GitHub
    remote_repo_dir = tmp_path / "remote_docs.git"
    remote_repo_dir.mkdir()
    subprocess.run(["git", "init", "--bare"], cwd=str(remote_repo_dir), check=True)

    # 2. Seed remote with an initial commit containing a legacy duplicate root docs/ and mkdocs.yml
    seed_clone = tmp_path / "seed_clone"
    subprocess.run(["git", "clone", str(remote_repo_dir), str(seed_clone)], check=True)
    subprocess.run(["git", "-C", str(seed_clone), "config", "user.name", "Tester"], check=True)
    subprocess.run(["git", "-C", str(seed_clone), "config", "user.email", "tester@example.com"], check=True)
    (seed_clone / "docs").mkdir()
    (seed_clone / "docs" / "old_duplicate.md").write_text("# Old Duplicate", encoding="utf-8")
    (seed_clone / "mkdocs.yml").write_text("site_name: Old Duplicate", encoding="utf-8")
    subprocess.run(["git", "-C", str(seed_clone), "add", "."], check=True)
    subprocess.run(["git", "-C", str(seed_clone), "commit", "-m", "legacy commit with duplicate root docs"], check=True)
    subprocess.run(["git", "-C", str(seed_clone), "push", "-u", "origin", "HEAD"], check=True)

    # 3. Setup MDAARPipeline pointing to this remote
    workspace = tmp_path / "ws"
    workspace.mkdir()
    pipe = MDAARPipeline(
        project_name="vaccine",
        base_workspace=str(workspace),
        git_docs_url=str(remote_repo_dir),
        docs_auth="HTTPS",
    )

    # Create dummy local docs
    (pipe.docs_src_dir / "00_Governance").mkdir(parents=True, exist_ok=True)
    (pipe.docs_src_dir / "00_Governance" / "Document-Register.md").write_text("# Register", encoding="utf-8")
    (pipe.docs_repo_dir / "mkdocs.yml").write_text("site_name: Vaccine Docs", encoding="utf-8")

    # 4. Run sync_docs_repo
    res = pipe.sync_docs_repo(commit_message="test sync docs")
    assert "Successfully pushed" in res

    # 5. Clone remote again to inspect committed file tree
    verify_clone = tmp_path / "verify_clone"
    subprocess.run(["git", "clone", str(remote_repo_dir), str(verify_clone)], check=True)

    # Must NOT have root docs/ or root mkdocs.yml (duplicates removed!)
    assert not (verify_clone / "docs").exists()
    assert not (verify_clone / "mkdocs.yml").exists()

    # Must have project-scoped docs under projects/vaccine/
    assert (verify_clone / "projects" / "vaccine" / "docs" / "00_Governance" / "Document-Register.md").is_file()
    assert (verify_clone / "projects" / "vaccine" / "mkdocs.yml").is_file()
    assert (verify_clone / "README.md").is_file()
    readme_content = (verify_clone / "README.md").read_text(encoding="utf-8")
    assert "vaccine" in readme_content

