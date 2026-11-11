"""Tests for bundled MDAAR SkillLoader, ModelGateway failover, FreeRouterServer, and Colab settings."""

import http.server
import json
import sys
import threading
import types
import urllib.error

from mdaar.gateway import (
    FREE_GEMINI_FALLBACK_CHAIN,
    DualModelManager,
    FreeRouterServer,
    ModelGateway,
    resolve_setting,
)
from mdaar.gateway import free_router_server as frs_mod
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


def test_resolve_setting_priority_and_booleans(monkeypatch):
    fake_secrets = {
        "MDAAR_TEST_KEY": "secret-value",
        "MDAAR_TEST_FLAG": "True",
    }
    fake_colab = types.ModuleType("google.colab")
    fake_userdata = types.ModuleType("google.colab.userdata")
    fake_userdata.get = lambda name: fake_secrets.get(name)
    fake_colab.userdata = fake_userdata

    fake_google = sys.modules.get("google") or types.ModuleType("google")
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.colab", fake_colab)
    monkeypatch.setitem(sys.modules, "google.colab.userdata", fake_userdata)
    monkeypatch.setenv("MDAAR_TEST_KEY", "env-value")
    monkeypatch.setenv("MDAAR_ENV_ONLY", "from-os-env")

    # 1. Explicit form value overrides Colab Secret & Env
    val, src = resolve_setting("explicit-val", ("MDAAR_TEST_KEY",), default="fallback", return_source=True)
    assert val == "explicit-val"
    assert src == "form"

    # 2. Blank or AUTO(...) resolves Colab Secret before Env
    val, src = resolve_setting("AUTO (from Colab Secrets/Env)", ("MDAAR_TEST_KEY",), return_source=True)
    assert val == "secret-value"
    assert src == "colab-secret:MDAAR_TEST_KEY"

    # 3. Missing secret falls back to os.environ, then default
    val, src = resolve_setting("", ("MDAAR_MISSING", "MDAAR_ENV_ONLY"), default="fallback", return_source=True)
    assert val == "from-os-env"
    assert src == "env:MDAAR_ENV_ONLY"

    val, src = resolve_setting("", ("MDAAR_NONE",), default="fallback", return_source=True)
    assert val == "fallback"
    assert src == "default"

    # 4. Boolean handling from AUTO dropdown & Secret
    bval, bsrc = resolve_setting("AUTO (from Colab Secrets/Env)", ("MDAAR_TEST_FLAG",), default=False, is_bool=True, return_source=True)
    assert bval is True
    assert bsrc == "colab-secret:MDAAR_TEST_FLAG"

    assert resolve_setting("False", ("MDAAR_TEST_FLAG",), default=True, is_bool=True) is False


def test_gemini_auto_free_chain_failover_and_sampling_rules(monkeypatch):
    calls = []
    secret_k1 = "AIzaSyFakeKeyOne123456789012345678901234"
    secret_k2 = "AIzaSyFakeKeyTwo123456789012345678901234"

    class _FakeGenConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeModels:
        def __init__(self, api_key):
            self.api_key = api_key

        def generate_content(self, model, contents, config=None):
            cfg_kwargs = getattr(config, "kwargs", {}) if config is not None else {}
            calls.append((model, self.api_key, contents, dict(cfg_kwargs)))
            if model == "gemini-3.8-flash":
                raise RuntimeError("404 models/gemini-3.8-flash is no longer available")
            if model in ("gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3-flash-preview"):
                raise RuntimeError(f"429 RESOURCE_EXHAUSTED for key {self.api_key}")
            if model == "gemma-4-31b-it":
                return types.SimpleNamespace(text="Gemma 4 OK")
            raise AssertionError(f"Unexpected model: {model}")

    class _FakeClient:
        def __init__(self, api_key):
            self.models = _FakeModels(api_key)

    fake_google = sys.modules.get("google") or types.ModuleType("google")
    fake_genai = types.ModuleType("google.genai")
    fake_types = types.ModuleType("google.genai.types")
    fake_types.GenerateContentConfig = _FakeGenConfig
    fake_genai.Client = _FakeClient
    fake_genai.types = fake_types
    fake_google.genai = fake_genai

    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)

    gw = ModelGateway(
        provider="Gemini",
        model_name="auto-free-chain",
        api_keys=[secret_k1, secret_k2],
        temperature=0.35,
    )
    out = gw.generate("Analyze app", system_instruction="Be strictly accurate")
    assert out == "Gemma 4 OK"
    assert "gemini-3.8-flash" in gw._unavailable_models

    # Gemini 3.6/3.8 calls must omit custom temperature per Gemini 3.6+ API spec
    gemini38_calls = [c for c in calls if c[0] == "gemini-3.8-flash"]
    gemini36_calls = [c for c in calls if c[0] == "gemini-3.6-flash"]
    assert gemini38_calls and "temperature" not in gemini38_calls[0][3]
    assert gemini36_calls and "temperature" not in gemini36_calls[0][3]

    # Gemma must fold system_instruction inline instead of passing config.system_instruction
    gemma_calls = [c for c in calls if c[0] == "gemma-4-31b-it"]
    assert len(gemma_calls) == 1
    assert "Be strictly accurate" in gemma_calls[0][2]
    assert "system_instruction" not in gemma_calls[0][3]
    assert gemma_calls[0][3].get("temperature") == 0.35

    # Next call skips dead gemini-3.8-flash immediately
    before_count = len(calls)
    gw2_calls = gw.generate("Second prompt")
    assert gw2_calls == "Gemma 4 OK"
    second_turn_models = [c[0] for c in calls[before_count:]]
    assert "gemini-3.8-flash" not in second_turn_models
    assert FREE_GEMINI_FALLBACK_CHAIN[0] == "gemini-3.8-flash"


def test_free_router_server_and_openai_gateway_end_to_end():
    upstream_secret_a = "sk-upstream-secret-alpha-12345"
    upstream_secret_b = "sk-upstream-secret-bravo-67890"
    received_auth = []

    class _MockUpstreamHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            received_auth.append((self.path, self.headers.get("Authorization", ""), body.get("model")))

            if self.path.startswith("/route_a"):
                err = json.dumps({"error": {"message": f"429 rate limited on {upstream_secret_a}"}}).encode("utf-8")
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
                return

            if self.path.startswith("/route_b"):
                if self.headers.get("Authorization") != f"Bearer {upstream_secret_b}":
                    self.send_response(401)
                    self.end_headers()
                    return
                ok_payload = json.dumps(
                    {
                        "id": "cmpl-test",
                        "object": "chat.completion",
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Routed OK via route_b"}}],
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(ok_payload)))
                self.end_headers()
                self.wfile.write(ok_payload)
                return

            self.send_response(404)
            self.end_headers()

    upstream_httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _MockUpstreamHandler)
    upstream_port = upstream_httpd.server_address[1]
    threading.Thread(target=upstream_httpd.serve_forever, daemon=True).start()

    router = FreeRouterServer(
        routes=[
            {
                "name": "route_a",
                "kind": "openai",
                "base_url": f"http://127.0.0.1:{upstream_port}/route_a",
                "api_keys": [upstream_secret_a],
                "models": ["model-a"],
            },
            {
                "name": "route_b",
                "kind": "openai",
                "base_url": f"http://127.0.0.1:{upstream_port}/route_b",
                "api_keys": [upstream_secret_b],
                "models": ["model-b"],
            },
        ],
        auth_token="router-token-xyz",
        port=0,
        cooldown_seconds=30,
    )

    try:
        router_url = router.start_in_background()

        # 1. Bad token gets rejected with 401 and secret redacted
        bad_gw = ModelGateway(
            provider="OpenAI-Gateway",
            base_url=router_url,
            api_keys=["wrong-token-abc"],
            model_name="auto",
        )
        bad_res = bad_gw.generate("hello")
        assert bad_res.startswith(ModelGateway.ERROR_PREFIX)
        assert "401" in bad_res
        assert "wrong-token-abc" not in bad_res

        # 2. Valid token automatically fails over route_a (429) -> route_b (200)
        good_gw = ModelGateway(
            provider="OpenAI-Gateway",
            base_url=router_url,
            api_keys=["router-token-xyz"],
            model_name="auto",
        )
        ok_res = good_gw.generate("Explain project", system_instruction="Be concise")
        assert ok_res == "Routed OK via route_b"

        health = router.get_health_payload()
        assert health["stats"]["successes_by_route"] == {"route_b": 1}
        assert upstream_secret_a not in json.dumps(health)
        assert upstream_secret_b not in json.dumps(health)

        # 3. fallback_gateways works cleanly when primary gateway fails
        fallback_local = FakeLocalGateway("fallback-model")
        primary_with_fallback = ModelGateway(
            provider="OpenAI-Gateway",
            base_url=router_url,
            api_keys=["wrong-token-fallback-test"],
            model_name="auto",
            fallback_gateways=[fallback_local],
        )
        fb_out = primary_with_fallback.generate("Need fallback")
        assert fb_out == "[fallback-model] OK"

        # 4. DualModelManager treats different OpenAI base URLs as distinct models
        gw_one = ModelGateway(provider="OpenAI-Gateway", base_url="https://openrouter.ai/api/v1", model_name="auto")
        gw_two = ModelGateway(provider="OpenAI-Gateway", base_url=router_url, model_name="auto")
        gw_two_copy = ModelGateway(provider="OpenAI-Gateway", base_url=router_url, model_name="auto")
        assert not DualModelManager(gw_one, gw_two).is_single_model_mode
        assert DualModelManager(gw_two, gw_two_copy).is_single_model_mode
    finally:
        router.stop()
        upstream_httpd.shutdown()
        upstream_httpd.server_close()


def test_post_gemini_rest_completion_headers_and_gemma_handling(monkeypatch):
    captured = []

    class _FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {"candidates": [{"content": {"parts": [{"text": "Gemini REST OK"}]}, "finishReason": "STOP"}]}
            ).encode("utf-8")

    def _fake_urlopen(req, timeout=120):
        captured.append((req.full_url, dict(req.header_items()), json.loads(req.data.decode("utf-8"))))
        return _FakeResp()

    monkeypatch.setattr(frs_mod.urllib.request, "urlopen", _fake_urlopen)

    ok, text, status = frs_mod.post_gemini_rest_completion(
        api_key="AIzaSyRestTestKey",
        model="gemini-3.8-flash",
        messages=[
            {"role": "system", "content": "Sys rule"},
            {"role": "user", "content": "Hi"},
        ],
        max_tokens=512,
        temperature=0.4,
    )
    assert ok and status == 200 and text == "Gemini REST OK"
    url1, hdrs1, body1 = captured[-1]
    assert "key=" not in url1
    assert any(k.lower() == "x-goog-api-key" and v == "AIzaSyRestTestKey" for k, v in hdrs1.items())
    assert "systemInstruction" in body1
    assert "temperature" not in body1.get("generationConfig", {})

    ok2, text2, _ = frs_mod.post_gemini_rest_completion(
        api_key="AIzaSyRestTestKey",
        model="gemma-4-31b-it",
        messages=[
            {"role": "system", "content": "Sys rule"},
            {"role": "user", "content": "Hi"},
        ],
        temperature=0.4,
    )
    assert ok2 and text2 == "Gemini REST OK"
    _, _, body2 = captured[-1]
    assert "systemInstruction" not in body2
    assert "Sys rule" in body2["contents"][0]["parts"][0]["text"]
    assert body2["generationConfig"]["temperature"] == 0.4

