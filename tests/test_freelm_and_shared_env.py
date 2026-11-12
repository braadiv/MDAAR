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
