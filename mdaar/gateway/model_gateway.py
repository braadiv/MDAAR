"""
LLM Model Gateway and Dual-Model VRAM Orchestrator for MDAAR v2.0.0.
Supports:
- Gemini API (key rotation + exponential backoff + native system_instruction)
- Local HuggingFace Transformers on Colab T4 (4-bit NF4 float16 + Gemma-2 guard)
- DualModelManager separating Planner Model ("إعدادات مودل الخطة") and
  Executor Model ("إعدادات مودل التنفيذ"), with explicit VRAM unloading between
  two distinct local models to stay safely within Colab T4 15GB VRAM.
"""

import gc
import time


class ModelGateway:
    """Handles LLM invocation with multi-provider support, key rotation, and clean VRAM release."""

    ERROR_PREFIX = "⚠️ "

    def __init__(
        self,
        provider="Gemini",
        model_name="gemini-2.5-flash",
        api_keys=None,
        local_model_path=None,
        hf_token=None,
        quantization="4bit",
        max_new_tokens=2048,
        temperature=0.2,
    ):
        self.provider = provider
        self.model_name = model_name
        self.api_keys = [k.strip() for k in (api_keys or []) if str(k).strip()]
        self.current_key_idx = 0
        self.local_model_path = local_model_path
        self.hf_token = hf_token or None
        self.quantization = quantization
        self.max_new_tokens = int(max_new_tokens)
        self.temperature = float(temperature)
        self._local_pipeline = None

    @property
    def is_local(self):
        return self.provider in ("Local-Offline", "Transformers")

    @property
    def descriptor(self):
        if self.is_local:
            return f"{self.provider}:{self.local_model_path or 'Qwen/Qwen2.5-Coder-7B-Instruct'} ({self.quantization})"
        return f"{self.provider}:{self.model_name}"

    def is_loaded_in_vram(self):
        return self._local_pipeline is not None

    def unload(self):
        """Explicitly release loaded HuggingFace model and free GPU VRAM safely."""
        if self._local_pipeline is None:
            return False

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
        return True

    def _get_active_key(self):
        if not self.api_keys:
            return None
        return self.api_keys[self.current_key_idx % len(self.api_keys)]

    def _rotate_key(self):
        if len(self.api_keys) > 1:
            self.current_key_idx = (self.current_key_idx + 1) % len(self.api_keys)
            print(f"🔄 Rotating API Key to index {self.current_key_idx}...")

    def generate(self, prompt, system_instruction="", max_new_tokens=None):
        tokens = int(max_new_tokens) if max_new_tokens is not None else self.max_new_tokens
        if self.provider == "Gemini":
            return self._generate_gemini(prompt, system_instruction)
        if self.is_local:
            return self._generate_local(prompt, system_instruction, max_new_tokens=tokens)
        raise ValueError(f"Unknown provider: {self.provider}")

    def _generate_gemini(self, prompt, system_instruction):
        if not self.api_keys:
            return f"{self.ERROR_PREFIX}No Gemini API Key configured."

        attempts = max(2, len(self.api_keys) * 2)
        last_error = None

        for attempt in range(attempts):
            key = self._get_active_key()
            try:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=key)
                cfg = types.GenerateContentConfig(
                    system_instruction=system_instruction or None,
                    temperature=self.temperature,
                )
                resp = client.models.generate_content(
                    model=self.model_name or "gemini-2.5-flash",
                    contents=prompt,
                    config=cfg,
                )
                text = getattr(resp, "text", None)
                if text:
                    return text
                return f"{self.ERROR_PREFIX}Empty response returned by Gemini."
            except TypeError:
                # Fallback if older google-genai signature differs
                try:
                    from google import genai
                    client = genai.Client(api_key=key)
                    full_prompt = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt
                    resp = client.models.generate_content(
                        model=self.model_name or "gemini-2.5-flash",
                        contents=full_prompt,
                    )
                    return getattr(resp, "text", None) or f"{self.ERROR_PREFIX}Empty response."
                except Exception as inner_e:
                    last_error = inner_e
            except Exception as e:
                last_error = e
                err_str = str(e)
                print(f"⚠️ Gemini request error (attempt {attempt + 1}/{attempts}): {err_str[:180]}")
                self._rotate_key()
                if "429" in err_str or "quota" in err_str.lower() or "rate" in err_str.lower():
                    time.sleep(min(4.0, 0.8 * (attempt + 1)))

        return f"{self.ERROR_PREFIX}Generation failed across all keys: {last_error}"

    def _load_local_pipeline(self):
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
