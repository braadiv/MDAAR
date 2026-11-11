"""
LLM Model Gateway, Multi-Model Auto-Failover, Colab Secret Resolver,
and Dual-Model VRAM Orchestrator for MDAAR v2.0.0.

Supports:
- Google Gemini API (`gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3.5-flash-lite`,
  `gemini-3.1-flash-lite`, `gemini-3-flash-preview`, `gemma-4-31b-it`, `gemma-4-26b-a4b-it`,
  or `auto-free-chain` with key rotation + model failover)
- OpenAI-Compatible External Gateways / Routers (`OpenAI-Gateway` / `OpenRouter` /
  Cloudflare AI Gateway / Portkey / Groq / `FreeLLMAPI` / `One-API` / 2nd-Colab Router)
- Local HuggingFace Transformers on Colab T4 (4-bit NF4 float16 + Gemma chat-template guard)
- DualModelManager separating Planner Model and Executor Model with VRAM swap on T4
"""

import gc
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from mdaar.security.sanitizer import SecretSanitizer


DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
FREE_GEMINI_FALLBACK_CHAIN = (
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemma-4-31b-it",
    "gemma-4-26b-a4b-it",
)
DEFAULT_OPENAI_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENAI_MODEL = "openrouter/free"

_AUTO_PREFIXES = ("AUTO", "FROM_ENV", "FROM_SECRET")


def _lookup_colab_secret(name):
    """Safely look up a secret from google.colab.userdata when running inside Colab."""
    try:
        from google.colab import userdata

        val = userdata.get(name)
        return str(val).strip() if val is not None else ""
    except Exception:
        return ""


def _parse_bool_value(raw, default=False):
    if isinstance(raw, bool):
        return raw
    txt = str(raw or "").strip().lower()
    if txt in ("1", "true", "yes", "on") or txt.startswith("true "):
        return True
    if txt in ("0", "false", "no", "off") or txt.startswith("false "):
        return False
    return bool(default)


def resolve_setting(
    form_value=None,
    env_names=(),
    default="",
    is_bool=False,
    return_source=False,
):
    """
    Resolve a configuration value with priority:
    1. Explicit non-empty Colab Form value (unless blank or starting with AUTO/FROM_ENV/FROM_SECRET)
    2. Google Colab Secrets (`google.colab.userdata.get`)
    3. OS environment variables (`os.environ.get`)
    4. Fallback `default`
    """
    if isinstance(env_names, str):
        env_names = (env_names,)

    if isinstance(form_value, bool) and is_bool:
        val = form_value
        src = "form"
        return (val, src) if return_source else val

    raw_form = "" if form_value is None else str(form_value).strip()
    is_auto = (not raw_form) or raw_form.upper().startswith(_AUTO_PREFIXES)

    if not is_auto:
        val = _parse_bool_value(raw_form, default=default) if is_bool else raw_form
        return (val, "form") if return_source else val

    for name in env_names:
        if not name:
            continue
        sec_val = _lookup_colab_secret(name)
        if sec_val:
            val = _parse_bool_value(sec_val, default=default) if is_bool else sec_val
            return (val, f"colab-secret:{name}") if return_source else val

        env_val = os.environ.get(name, "").strip()
        if env_val:
            val = _parse_bool_value(env_val, default=default) if is_bool else env_val
            return (val, f"env:{name}") if return_source else val

    val = _parse_bool_value(default, default=False) if is_bool else default
    return (val, "default") if return_source else val


def split_csv_values(raw_value):
    """Split comma/newline separated values while preserving order and removing duplicates."""
    if not raw_value:
        return []
    if isinstance(raw_value, (list, tuple)):
        items = raw_value
    else:
        items = str(raw_value).replace("\n", ",").split(",")

    cleaned = []
    seen = set()
    for item in items:
        s = str(item).strip()
        if s and s not in seen:
            seen.add(s)
            cleaned.append(s)
    return cleaned


def redact_secrets(text, known_keys=()):
    """Remove both known runtime keys and pattern-matched secrets from log/error strings."""
    cleaned = "" if text is None else str(text)
    for key in known_keys or ():
        if key and len(str(key)) >= 4:
            cleaned = cleaned.replace(str(key), "[REDACTED_KEY]")
    return SecretSanitizer.sanitize(cleaned)


def normalize_chat_completions_url(base_url):
    """Normalize an OpenAI-compatible base URL cleanly to its /chat/completions endpoint."""
    url = (base_url or DEFAULT_OPENAI_BASE_URL).strip().rstrip("/")
    if not url:
        url = DEFAULT_OPENAI_BASE_URL
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions"


def _extract_openai_message_content(choice_obj):
    msg = choice_obj.get("message") if isinstance(choice_obj, dict) else None
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text", "")))
                elif isinstance(part, str):
                    parts.append(part)
            joined = "".join(parts).strip()
            if joined:
                return joined
        # Fallback for reasoning-only outputs from some routers
        reasoning = msg.get("reasoning") or msg.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning
    if isinstance(choice_obj, dict) and isinstance(choice_obj.get("text"), str):
        return choice_obj["text"]
    return ""


def post_openai_chat_completion(
    base_url,
    api_key,
    model,
    messages,
    max_tokens=None,
    temperature=None,
    extra_headers=None,
    timeout=120,
):
    """
    Send a standard OpenAI-compatible POST /chat/completions request using Python stdlib.
    Returns `(ok: bool, text_or_error: str, status_code: int)`.
    """
    endpoint = normalize_chat_completions_url(base_url)
    payload = {
        "model": model,
        "messages": messages,
    }
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    if temperature is not None:
        payload["temperature"] = float(temperature)

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "MDAAR/2.0",
    }
    if api_key and str(api_key).strip():
        headers["Authorization"] = f"Bearer {str(api_key).strip()}"
    if "openrouter.ai" in endpoint.lower():
        headers.setdefault("HTTP-Referer", "https://github.com/braadiv/MDAAR")
        headers.setdefault("X-Title", "MDAAR Documentation Framework")
    if extra_headers:
        for k, v in extra_headers.items():
            headers[str(k)] = str(v)

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=int(timeout)) as resp:
            status = getattr(resp, "status", 200)
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            choices = data.get("choices") or []
            if choices:
                text = _extract_openai_message_content(choices[0])
                if text:
                    return True, text, status
            err_msg = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else None
            return False, err_msg or "Empty choices returned by OpenAI-compatible gateway.", status
    except urllib.error.HTTPError as http_err:
        status = getattr(http_err, "code", 500)
        err_body = ""
        try:
            err_body = http_err.read().decode("utf-8", errors="replace")
            parsed = json.loads(err_body)
            if isinstance(parsed.get("error"), dict):
                err_body = parsed["error"].get("message") or err_body
            elif isinstance(parsed.get("detail"), str):
                err_body = parsed["detail"]
        except Exception:
            pass
        safe_err = redact_secrets(err_body or str(http_err), [api_key])
        return False, f"HTTP {status}: {safe_err[:300]}", status
    except Exception as exc:
        safe_err = redact_secrets(str(exc), [api_key])
        return False, safe_err[:300], 0


def classify_provider_error(status_code, err_text):
    """Classify provider failure into 'dead' (404/retired model) vs 'cooldown' (429/transient/auth)."""
    low = str(err_text or "").lower()
    if status_code == 404 or any(
        marker in low
        for marker in (
            "no longer available",
            "model_not_found",
            "does not exist",
            "is deprecated",
            "shut down",
            "not found for api version",
        )
    ):
        return "dead"
    return "cooldown"


def ensure_local_llm_dependencies(quantization="4bit"):
    """Install local HuggingFace GPU dependencies only on demand when Local-Offline is used."""
    packages_to_install = []

    if importlib.util.find_spec("transformers") is None:
        packages_to_install.append("transformers>=4.43.1")
    else:
        try:
            ver_str = importlib.metadata.version("transformers")
            ver_parts = tuple(int(p) for p in ver_str.split(".")[:3] if p.isdigit())
            if ver_parts < (4, 43, 1):
                packages_to_install.append("transformers>=4.43.1")
        except Exception:
            pass

    if importlib.util.find_spec("accelerate") is None:
        packages_to_install.append("accelerate>=0.28.0")

    if quantization == "4bit" and importlib.util.find_spec("bitsandbytes") is None:
        has_cuda = False
        if importlib.util.find_spec("torch") is not None:
            try:
                import torch

                has_cuda = bool(torch.cuda.is_available())
            except Exception:
                has_cuda = False
        if has_cuda:
            packages_to_install.append("bitsandbytes>=0.43.0")

    if packages_to_install:
        print(f"⏳ Installing local GPU model dependencies on demand: {', '.join(packages_to_install)}...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "--upgrade", *packages_to_install],
            check=True,
        )
    return packages_to_install


class ModelGateway:
    """Handles LLM invocation with multi-provider routing, multi-model failover, key rotation, and VRAM release."""

    ERROR_PREFIX = "⚠️ "
    DEFAULT_GEMINI_MODEL = DEFAULT_GEMINI_MODEL
    FREE_GEMINI_FALLBACK_CHAIN = FREE_GEMINI_FALLBACK_CHAIN
    DEFAULT_OPENAI_BASE_URL = DEFAULT_OPENAI_BASE_URL
    DEFAULT_OPENAI_MODEL = DEFAULT_OPENAI_MODEL

    def __init__(
        self,
        provider="Gemini",
        model_name=None,
        api_keys=None,
        local_model_path=None,
        hf_token=None,
        quantization="4bit",
        max_new_tokens=2048,
        temperature=0.2,
        base_url=None,
        extra_headers=None,
        timeout=120,
        fallback_gateways=None,
        cooldown_seconds=45,
    ):
        self.provider = provider
        self.base_url = (base_url or DEFAULT_OPENAI_BASE_URL).strip()
        if model_name is None:
            self.model_name = DEFAULT_OPENAI_MODEL if self.is_openai_compatible else DEFAULT_GEMINI_MODEL
        else:
            self.model_name = model_name

        if isinstance(api_keys, str):
            self.api_keys = split_csv_values(api_keys)
        else:
            self.api_keys = [str(k).strip() for k in (api_keys or []) if str(k).strip()]

        self.current_key_idx = 0
        self.local_model_path = local_model_path
        self.hf_token = hf_token or None
        self.quantization = quantization
        self.max_new_tokens = int(max_new_tokens)
        self.temperature = float(temperature)
        self.extra_headers = dict(extra_headers or {})
        self.timeout = int(timeout)
        self.fallback_gateways = list(fallback_gateways or [])
        self.cooldown_seconds = int(cooldown_seconds)

        self._local_pipeline = None
        self._unavailable_models = set()
        self._cooldown_until = {}

    @property
    def is_local(self):
        return self.provider in ("Local-Offline", "Transformers")

    @property
    def is_openai_compatible(self):
        return self.provider in (
            "OpenAI-Gateway",
            "OpenAI",
            "OpenRouter",
            "Custom-Gateway",
            "Auto-Router",
        )

    @property
    def descriptor(self):
        if self.is_local:
            return f"{self.provider}:{self.local_model_path or 'Qwen/Qwen2.5-Coder-7B-Instruct'} ({self.quantization})"
        if self.is_openai_compatible:
            clean_url = normalize_chat_completions_url(self.base_url).removesuffix("/chat/completions")
            return f"{self.provider}:{self.model_name} @ {clean_url}"
        return f"{self.provider}:{self.model_name}"

    def is_loaded_in_vram(self):
        return self._local_pipeline is not None

    def unload(self):
        """Explicitly release loaded HuggingFace model and free GPU VRAM safely."""
        unloaded = False
        if self._local_pipeline is not None:
            pipe = self._local_pipeline
            self._local_pipeline = None
            try:
                if hasattr(pipe, "model"):
                    del pipe.model
                if hasattr(pipe, "tokenizer"):
                    del pipe.tokenizer
            except Exception:
                pass
            del pipe
            gc.collect()

            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    if hasattr(torch.cuda, "ipc_collect"):
                        torch.cuda.ipc_collect()
            except Exception:
                pass

            print(f"🧹 Unloaded local model ({self.local_model_path or self.model_name}) to free VRAM.")
            unloaded = True

        for fb in self.fallback_gateways:
            fb.unload()
        return unloaded

    def _resolve_model_candidates(self):
        raw = (self.model_name or "").strip()
        if not raw:
            raw = DEFAULT_OPENAI_MODEL if self.is_openai_compatible else DEFAULT_GEMINI_MODEL

        parts = split_csv_values(raw)
        if not parts:
            parts = [raw]

        expanded = []
        seen = set()
        for part in parts:
            items = list(FREE_GEMINI_FALLBACK_CHAIN) if (not self.is_openai_compatible and part == "auto-free-chain") else [part]
            for item in items:
                if item and item not in seen:
                    seen.add(item)
                    expanded.append(item)
        return expanded

    def _is_in_cooldown(self, model_id, key_idx):
        until = self._cooldown_until.get((model_id, key_idx), 0.0)
        return time.monotonic() < until

    def _run_with_candidate_failover(self, keys, call_fn, require_key=True):
        """
        Shared candidate rotation + model failover engine for Gemini and OpenAI-Gateway.
        Rotates API keys across requests and skips 404-dead models or temporarily cooled-down pairs.
        """
        if require_key and not keys:
            return f"{self.ERROR_PREFIX}No API keys configured for {self.provider}."

        key_pool = keys if keys else [""]
        models = self._resolve_model_candidates()
        active_models = [m for m in models if m not in self._unavailable_models]
        if not active_models:
            return f"{self.ERROR_PREFIX}All configured models ({', '.join(models)}) returned unavailable/404 errors."

        now = time.monotonic()
        candidate_pairs = []
        n_keys = len(key_pool)
        start_idx = self.current_key_idx % n_keys

        for model_id in active_models:
            for offset in range(n_keys):
                k_idx = (start_idx + offset) % n_keys
                if not self._is_in_cooldown(model_id, k_idx):
                    candidate_pairs.append((model_id, k_idx, key_pool[k_idx]))

        if not candidate_pairs:
            # Everything is temporarily in cooldown: wait briefly then try active models once more
            soonest = min(
                self._cooldown_until.get((m, i), now)
                for m in active_models
                for i in range(n_keys)
            )
            wait_s = max(0.0, min(2.0, soonest - now))
            if wait_s > 0:
                time.sleep(wait_s)
            for model_id in active_models:
                candidate_pairs.append((model_id, start_idx, key_pool[start_idx]))

        last_error = "Unknown failure"
        total = len(candidate_pairs)
        for attempt_idx, (model_id, k_idx, key) in enumerate(candidate_pairs, start=1):
            ok, payload, status_code = call_fn(model_id, key)
            if ok:
                self.current_key_idx = (k_idx + 1) % n_keys
                return payload

            last_error = redact_secrets(payload, key_pool)
            kind = classify_provider_error(status_code, last_error)
            if kind == "dead":
                self._unavailable_models.add(model_id)
                print(f"⚠️ Model `{model_id}` unavailable ({status_code or '404'}); disabling for session and switching...")
            else:
                self._cooldown_until[(model_id, k_idx)] = time.monotonic() + max(1, self.cooldown_seconds)
                print(
                    f"⚠️ {self.provider} request failed on `{model_id}` "
                    f"(key #{k_idx + 1}, try {attempt_idx}/{total}): {last_error[:160]}"
                )

        return f"{self.ERROR_PREFIX}Generation failed across all keys/models: {last_error}"

    def generate(self, prompt, system_instruction="", max_new_tokens=None):
        tokens = int(max_new_tokens) if max_new_tokens is not None else self.max_new_tokens

        if self.provider == "Gemini":
            res = self._generate_gemini(prompt, system_instruction, max_new_tokens=tokens)
        elif self.is_openai_compatible:
            res = self._generate_openai_gateway(prompt, system_instruction, max_new_tokens=tokens)
        elif self.is_local:
            res = self._generate_local(prompt, system_instruction, max_new_tokens=tokens)
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

        if str(res).lstrip().startswith(self.ERROR_PREFIX) and self.fallback_gateways:
            for fb in self.fallback_gateways:
                print(f"🔀 Primary gateway failed; routing fallback to `{fb.descriptor}`...")
                fb_res = fb.generate(prompt, system_instruction=system_instruction, max_new_tokens=tokens)
                if not str(fb_res).lstrip().startswith(self.ERROR_PREFIX):
                    return fb_res
                res = fb_res

        return res

    @staticmethod
    def _supports_custom_temperature(model_id):
        low = str(model_id or "").lower()
        return not any(prefix in low for prefix in ("gemini-3.6", "gemini-3.7", "gemini-3.8", "gemini-4"))

    @staticmethod
    def _prefers_inline_system(model_id):
        return "gemma" in str(model_id or "").lower()

    def _call_gemini_once(self, model_id, api_key, prompt, system_instruction, max_new_tokens):
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            inline_sys = bool(system_instruction) and self._prefers_inline_system(model_id)
            contents = f"{system_instruction}\n\n{prompt}" if inline_sys else prompt

            cfg_kwargs = {}
            if system_instruction and not inline_sys:
                cfg_kwargs["system_instruction"] = system_instruction
            if self._supports_custom_temperature(model_id):
                cfg_kwargs["temperature"] = self.temperature
            if max_new_tokens:
                cfg_kwargs["max_output_tokens"] = int(max_new_tokens)

            try:
                cfg = types.GenerateContentConfig(**cfg_kwargs) if cfg_kwargs else None
                resp = client.models.generate_content(
                    model=model_id,
                    contents=contents,
                    config=cfg,
                )
            except (TypeError, Exception) as cfg_err:
                err_low = str(cfg_err).lower()
                if system_instruction and (isinstance(cfg_err, TypeError) or "instruction" in err_low or "system" in err_low):
                    full_prompt = f"{system_instruction}\n\n{prompt}"
                    resp = client.models.generate_content(model=model_id, contents=full_prompt)
                else:
                    raise

            text = getattr(resp, "text", None)
            if text:
                return True, text, 200
            return False, "Empty response returned by Gemini.", 500
        except Exception as exc:
            err_str = redact_secrets(str(exc), self.api_keys)
            status = 429 if ("429" in err_str or "quota" in err_str.lower() or "resource_exhausted" in err_str.lower()) else (
                404 if ("404" in err_str or "not found" in err_str.lower() or "no longer available" in err_str.lower()) else 500
            )
            return False, err_str, status

    def _generate_gemini(self, prompt, system_instruction, max_new_tokens=None):
        return self._run_with_candidate_failover(
            self.api_keys,
            lambda model_id, key: self._call_gemini_once(model_id, key, prompt, system_instruction, max_new_tokens),
            require_key=True,
        )

    def _generate_openai_gateway(self, prompt, system_instruction, max_new_tokens=None):
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        return self._run_with_candidate_failover(
            self.api_keys,
            lambda model_id, key: post_openai_chat_completion(
                base_url=self.base_url,
                api_key=key,
                model=model_id,
                messages=messages,
                max_tokens=max_new_tokens,
                temperature=self.temperature,
                extra_headers=self.extra_headers,
                timeout=self.timeout,
            ),
            require_key=False,
        )

    def _load_local_pipeline(self):
        ensure_local_llm_dependencies(quantization=self.quantization)
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline

        model_id = self.local_model_path or "Qwen/Qwen2.5-Coder-7B-Instruct"
        is_cuda = torch.cuda.is_available()
        device_desc = f"GPU ({torch.cuda.get_device_name(0)})" if is_cuda else "CPU"
        print(f"⏳ Loading local model ({model_id}) on {device_desc}...")

        tokenizer = AutoTokenizer.from_pretrained(
            model_id,
            token=self.hf_token,
            trust_remote_code=True,
        )
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token_id = tokenizer.eos_token_id

        model_kwargs = {
            "token": self.hf_token,
            "trust_remote_code": True,
        }

        if is_cuda and self.quantization == "4bit":
            from transformers import BitsAndBytesConfig

            # Nvidia T4 (Turing CC 7.5) runs float16 compute natively; avoid bfloat16 slowdown
            compute_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            model_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=compute_dtype,
                bnb_4bit_use_double_quant=True,
            )
            model_kwargs["device_map"] = "auto"
        elif is_cuda:
            model_kwargs["device_map"] = "auto"
            model_kwargs["torch_dtype"] = torch.float16
        else:
            model_kwargs["device_map"] = "cpu"
            model_kwargs["torch_dtype"] = torch.float32

        model = AutoModelForCausalLM.from_pretrained(model_id, **model_kwargs)
        self._local_pipeline = pipeline(
            "text-generation",
            model=model,
            tokenizer=tokenizer,
        )
        print(f"✅ Local model ({model_id}) loaded successfully!")

    def _generate_local(self, prompt, system_instruction, max_new_tokens=2048):
        """Offline / GPU inference using Transformers with T4 4-bit NF4 quantization."""
        try:
            if not self._local_pipeline:
                self._load_local_pipeline()

            messages = []
            if system_instruction:
                messages.append({"role": "system", "content": system_instruction})
            messages.append({"role": "user", "content": prompt})

            # Handle models whose chat_template rejects the 'system' role (e.g., Gemma-2)
            tok = self._local_pipeline.tokenizer
            if system_instruction and getattr(tok, "chat_template", None):
                try:
                    tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
                except Exception:
                    messages = [{"role": "user", "content": f"{system_instruction}\n\n{prompt}"}]

            do_sample = self.temperature > 0.0
            gen_kwargs = {
                "max_new_tokens": max_new_tokens,
                "do_sample": do_sample,
            }
            if do_sample:
                gen_kwargs["temperature"] = self.temperature

            out = self._local_pipeline(messages, **gen_kwargs)
            generated = out[0]["generated_text"]
            if isinstance(generated, list) and generated:
                return generated[-1].get("content", str(generated[-1]))
            return str(generated)
        except Exception as e:
            return (
                f"{self.ERROR_PREFIX}Local generation error: {e}\n"
                "(Tip: For 7B/8B/9B models on Colab, ensure T4 GPU is selected and run: "
                "pip install -U transformers accelerate bitsandbytes)"
            )


class DualModelManager:
    """
    Coordinates Planner Model ("إعدادات مودل الخطة") and Executor Model ("إعدادات مودل التنفيذ").
    When two different Local-Offline HuggingFace models are configured, automatically unloads
    the inactive model before loading the active one so Colab T4 (15GB VRAM) never OOMs.
    """

    def __init__(self, planner_gateway, executor_gateway=None):
        self.planner_gateway = planner_gateway
        self.executor_gateway = executor_gateway if executor_gateway is not None else planner_gateway
        self.stats = {
            "planner_calls": 0,
            "executor_calls": 0,
            "planner_errors": 0,
            "executor_errors": 0,
            "vram_switches": 0,
        }

    @property
    def is_single_model_mode(self):
        if self.executor_gateway is self.planner_gateway:
            return True
        if self.planner_gateway.provider != self.executor_gateway.provider:
            return False
        if self.planner_gateway.is_local:
            return self.planner_gateway.local_model_path == self.executor_gateway.local_model_path
        if self.planner_gateway.is_openai_compatible:
            return (
                self.planner_gateway.base_url == self.executor_gateway.base_url
                and self.planner_gateway.model_name == self.executor_gateway.model_name
            )
        return self.planner_gateway.model_name == self.executor_gateway.model_name

    def _prepare_vram_for(self, target_gateway, other_gateway):
        """Ensure only one local model occupies GPU VRAM at a time when using dual local models."""
        if self.is_single_model_mode:
            return
        if target_gateway is other_gateway:
            return
        if target_gateway.is_local and other_gateway.is_local and other_gateway.is_loaded_in_vram():
            if other_gateway.unload():
                self.stats["vram_switches"] += 1

    def generate_as_planner(self, prompt, system_instruction="", max_new_tokens=None):
        self._prepare_vram_for(self.planner_gateway, self.executor_gateway)
        self.stats["planner_calls"] += 1
        out = self.planner_gateway.generate(prompt, system_instruction=system_instruction, max_new_tokens=max_new_tokens)
        if self.is_error(out):
            self.stats["planner_errors"] += 1
        return out

    def generate_as_executor(self, prompt, system_instruction="", max_new_tokens=None):
        self._prepare_vram_for(self.executor_gateway, self.planner_gateway)
        self.stats["executor_calls"] += 1
        out = self.executor_gateway.generate(prompt, system_instruction=system_instruction, max_new_tokens=max_new_tokens)
        if self.is_error(out):
            self.stats["executor_errors"] += 1
        return out

    def cleanup_all(self):
        self.planner_gateway.unload()
        if self.executor_gateway is not self.planner_gateway:
            self.executor_gateway.unload()

    @staticmethod
    def is_error(text):
        return not text or str(text).lstrip().startswith(ModelGateway.ERROR_PREFIX)
