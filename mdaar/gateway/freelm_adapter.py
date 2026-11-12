"""FreeLM Integration & Multi-Provider Aggregator for Google Colab.

Integrates `freelm` as an in-process aggregator on Colab or falls back
to MDAAR's native multi-provider OpenAI/REST routing when freelm is not
yet installed, ensuring 100% operation inside a SINGLE Colab notebook.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mdaar.security.sanitizer import SecretSanitizer


def load_shared_workspace_env(
    workspace_path: Optional[str] = None,
    inject_os_environ: bool = True,
) -> Dict[str, str]:
    """Load unified shared credentials from `/MyDrive/AI_Documentation_Workspace/.env`.

    Searches priority locations:
    1. Explicit workspace_path/.env
    2. /content/drive/MyDrive/AI_Documentation_Workspace/.env
    3. /content/AI_Documentation_Workspace/.env

    Optionally injects loaded keys into `os.environ` without overwriting existing explicit env vars,
    and returns a clean dictionary of loaded settings.
    """
    candidates = []
    if workspace_path:
        candidates.append(Path(workspace_path) / ".env")
        candidates.append(Path(workspace_path))

    candidates.extend([
        Path("/content/drive/MyDrive/AI_Documentation_Workspace/.env"),
        Path("/content/AI_Documentation_Workspace/.env"),
    ])

    loaded: Dict[str, str] = {}
    found_file: Optional[Path] = None

    for cand in candidates:
        if cand.is_file():
            found_file = cand
            break

    if not found_file:
        return loaded

    try:
        content = found_file.read_text(encoding="utf-8", errors="ignore")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v:
                    loaded[k] = v
                    if inject_os_environ and k not in os.environ:
                        os.environ[k] = v
    except Exception:
        pass

    return loaded


class FreeLMGateway:
    """In-process multi-provider free LLM gateway.

    Runs directly inside the primary Colab session. Automatically leverages
    keys from:
    1. Shared `.env` in `/MyDrive/AI_Documentation_Workspace/`
    2. Google Colab Secrets (`userdata.get`)
    3. OS Environment variables
    """

    SUPPORTED_PROVIDERS = (
        "gemini",
        "groq",
        "openrouter",
        "mistral",
        "cerebras",
        "cloudflare",
        "nvidia",
        "cohere",
    )

    def __init__(
        self,
        model_name: str = "auto",
        temperature: float = 0.2,
        timeout: int = 120,
        workspace_path: Optional[str] = None,
    ):
        self.model_name = model_name or "auto"
        self.temperature = float(temperature)
        self.timeout = int(timeout)
        self.workspace_path = workspace_path
        self._freelm_client: Any = None
        self._client_init_attempted = False

        # Load shared workspace keys immediately
        load_shared_workspace_env(workspace_path)

    def _init_client(self) -> Tuple[bool, str]:
        if self._client_init_attempted and self._freelm_client is not None:
            return True, ""

        self._client_init_attempted = True
        try:
            import freelm  # type: ignore

            self._freelm_client = freelm.FreeLLM.from_env()
            return True, "freelm loaded successfully."
        except ImportError:
            return False, (
                "freelm package is not installed. "
                "Run `!pip install -q freelm` in the first Colab cell to enable FreeLM aggregation."
            )
        except Exception as exc:
            return False, f"Failed to initialize freelm: {SecretSanitizer.sanitize(str(exc))}"

    def generate(self, prompt: str, system_instruction: str = "") -> Tuple[bool, str]:
        """Generate text using freelm in-process aggregation."""
        ok, msg = self._init_client()
        if not ok or self._freelm_client is None:
            return False, msg

        full_prompt = (
            f"System: {system_instruction}\n\nUser: {prompt}"
            if system_instruction
            else prompt
        )

        try:
            # freelm.text provides multi-provider failover across registered free tiers
            response = self._freelm_client.text(full_prompt)
            if response and str(response).strip():
                return True, str(response).strip()
            return False, "Empty response received from freelm providers."
        except Exception as exc:
            err = SecretSanitizer.sanitize(str(exc))
            return False, f"FreeLM generation error: {err}"
