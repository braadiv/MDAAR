"""Lightweight BYOK multi-provider OpenAI-compatible router and Cloudflare tunnel helper.

Allows running an unlimited-key free-model aggregation router either locally or inside
a second Google Colab notebook, exposing standard `/health`, `/v1/models`, and
`/v1/chat/completions` endpoints using Python's standard library only.
"""

from __future__ import annotations

import hmac
import http.server
import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .model_gateway import (
    FREE_GEMINI_FALLBACK_CHAIN,
    classify_provider_error,
    post_openai_chat_completion,
    redact_secrets,
    split_csv_values,
)

GEMINI_REST_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

PROVIDER_DEFAULT_URLS: Dict[str, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "mistral": "https://api.mistral.ai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
}


def launch_cloudflared_tunnel(
    port: int,
    timeout: int = 30,
    stop_existing: bool = False,
    target_host: str = "127.0.0.1",
) -> Tuple[Optional[str], Optional[subprocess.Popen]]:
    """Launch `cloudflared tunnel` pointing to `target_host:port` and wait for its URL."""
    if stop_existing and os.name != "nt":
        subprocess.run(
            "pkill -f 'cloudflared tunnel'",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    cf_proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://{target_host}:{int(port)}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    line_queue: "queue.Queue[Optional[str]]" = queue.Queue()

    def _reader() -> None:
        try:
            if cf_proc.stdout is None:
                return
            for line in cf_proc.stdout:
                line_queue.put(line)
        finally:
            line_queue.put(None)

    threading.Thread(target=_reader, daemon=True).start()

    public_url: Optional[str] = None
    deadline = time.time() + max(1, int(timeout))

    while time.time() < deadline:
        remaining = max(0.05, deadline - time.time())
        try:
            line = line_queue.get(timeout=min(0.5, remaining))
        except queue.Empty:
            if cf_proc.poll() is not None:
                break
            continue

        if line is None:
            break

        match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
        if match:
            public_url = match.group(0)
            break

    if public_url is None and cf_proc.poll() is None:
        try:
            cf_proc.terminate()
        except Exception:
            pass

    return public_url, cf_proc


def _supports_sampling_params(model_name: str) -> bool:
    """Gemini 3.6+ and Gemini 4 reject/deprecate custom temperature/top_p/top_k."""
    lower = (model_name or "").strip().lower()
    if lower.startswith("gemini-4"):
        return False
    for prefix in ("gemini-3.6", "gemini-3.7", "gemini-3.8", "gemini-3.9"):
        if lower.startswith(prefix):
            return False
    return True


def _extract_message_text(content: Any) -> str:
    """Flatten OpenAI string or multi-part text content into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(p for p in parts if p)
    return str(content or "")


def post_gemini_rest_completion(
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    timeout: int = 120,
) -> Tuple[bool, str, int]:
    """Call Google Gemini `generateContent` REST API using `x-goog-api-key`."""
    clean_model = (model or "").strip()
    if clean_model.startswith("models/"):
        clean_model = clean_model[len("models/"):]

    endpoint = f"{GEMINI_REST_BASE}/{urllib.parse.quote(clean_model, safe='')}:generateContent"

    system_parts: List[str] = []
    contents: List[Dict[str, Any]] = []

    for msg in messages or []:
        role = str(msg.get("role", "user")).strip().lower()
        text = _extract_message_text(msg.get("content", ""))
        if not text:
            continue
        if role == "system":
            system_parts.append(text)
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": text}]})
        else:
            contents.append({"role": "user", "parts": [{"text": text}]})

    system_text = "\n\n".join(system_parts).strip()
    is_gemma = "gemma" in clean_model.lower()

    if not contents:
        contents = [{"role": "user", "parts": [{"text": system_text or "Hello"}]}]
        if is_gemma:
            system_text = ""

    if is_gemma and system_text:
        if contents[0]["role"] == "user":
            first_txt = contents[0]["parts"][0]["text"]
            contents[0]["parts"][0]["text"] = f"{system_text}\n\n{first_txt}"
        else:
            contents.insert(0, {"role": "user", "parts": [{"text": system_text}]})
        system_text = ""

    payload: Dict[str, Any] = {"contents": contents}
    if system_text and not is_gemma:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}

    gen_cfg: Dict[str, Any] = {}
    if max_tokens is not None:
        gen_cfg["maxOutputTokens"] = int(max_tokens)
    if temperature is not None and _supports_sampling_params(clean_model):
        gen_cfg["temperature"] = float(temperature)
    if gen_cfg:
        payload["generationConfig"] = gen_cfg

    body_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body_bytes,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key.strip(),
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status_code = getattr(resp, "status", 200)
        parsed = json.loads(raw) if raw else {}
        candidates = parsed.get("candidates") or []
        if not candidates:
            feedback = parsed.get("promptFeedback") or {}
            return False, f"Gemini returned no candidates: {feedback}", status_code
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict) and "text" in p).strip()
        if not text:
            finish_reason = candidates[0].get("finishReason", "UNKNOWN")
            return False, f"Gemini returned empty text (finishReason={finish_reason})", status_code
        return True, text, status_code
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = str(exc)
        return False, redact_secrets(err_body or str(exc), [api_key]), int(getattr(exc, "code", 500) or 500)
    except Exception as exc:
        return False, redact_secrets(str(exc), [api_key]), 0


@dataclass
class RouterRoute:
    """Normalized route entry inside FreeRouterServer."""

    name: str
    kind: str  # "gemini" or "openai"
    base_url: str = ""
    api_keys: List[str] = field(default_factory=list)
    models: List[str] = field(default_factory=list)
    extra_headers: Dict[str, str] = field(default_factory=dict)
    timeout: Optional[int] = None
    cursor: int = 0


def _normalize_route(spec: Dict[str, Any], index: int) -> RouterRoute:
    name = str(spec.get("name") or f"route-{index}").strip()
    base_url = str(spec.get("base_url") or "").strip()
    raw_kind = str(spec.get("kind") or "").strip().lower()

    if not raw_kind:
        if name.lower().startswith("gemini") and not base_url:
            raw_kind = "gemini"
        else:
            raw_kind = "openai"

    if raw_kind == "openai" and not base_url:
        base_url = PROVIDER_DEFAULT_URLS.get(name.lower(), "")

    keys = split_csv_values(spec.get("api_keys") or spec.get("api_key"))
    models = split_csv_values(spec.get("models") or spec.get("model"))

    if raw_kind == "gemini":
        if not models or models == ["auto-free-chain"]:
            models = list(FREE_GEMINI_FALLBACK_CHAIN)

    if raw_kind == "openai" and base_url and not keys:
        keys = ["local"]

    extra_headers = dict(spec.get("extra_headers") or {})
    timeout_val = spec.get("timeout")
    timeout = int(timeout_val) if timeout_val is not None else None

    return RouterRoute(
        name=name,
        kind=raw_kind,
        base_url=base_url,
        api_keys=keys,
        models=models,
        extra_headers=extra_headers,
        timeout=timeout,
    )


class FreeRouterServer:
    """Thread-safe BYOK multi-provider OpenAI-compatible HTTP proxy server."""

    def __init__(
        self,
        routes: Optional[List[Dict[str, Any]]] = None,
        auth_token: str = "",
        host: str = "127.0.0.1",
        port: int = 8787,
        cooldown_seconds: int = 45,
        request_timeout: int = 120,
        default_model: str = "auto",
    ) -> None:
        self.routes: List[RouterRoute] = []
        for idx, spec in enumerate(routes or []):
            norm = _normalize_route(spec, idx)
            if norm.api_keys and norm.models:
                self.routes.append(norm)

        self.auth_token = (auth_token or "").strip()
        self.host = host.strip() or "127.0.0.1"
        self.port = int(port)
        self.cooldown_seconds = max(1, int(cooldown_seconds))
        self.request_timeout = max(5, int(request_timeout))
        self.default_model = (default_model or "auto").strip()

        self._lock = threading.Lock()
        self._dead_pairs: set[Tuple[str, str]] = set()
        self._cooldown_until: Dict[Tuple[str, str, int], float] = {}
        self._stats: Dict[str, Any] = {
            "total_requests": 0,
            "success_requests": 0,
            "failed_requests": 0,
            "successes_by_route": {},
            "errors": [],
        }

        self._httpd: Optional[http.server.ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._tunnel_process: Optional[subprocess.Popen] = None
        self.bound_port: int = self.port

    @classmethod
    def from_providers(
        cls,
        gemini_keys: Any = "",
        gemini_models: Any = "",
        groq_keys: Any = "",
        groq_models: Any = "",
        openrouter_keys: Any = "",
        openrouter_models: Any = "",
        mistral_keys: Any = "",
        mistral_models: Any = "",
        cerebras_keys: Any = "",
        cerebras_models: Any = "",
        custom_name: str = "custom",
        custom_base_url: str = "",
        custom_keys: Any = "",
        custom_models: Any = "",
        auth_token: str = "",
        host: str = "127.0.0.1",
        port: int = 8787,
        cooldown_seconds: int = 45,
        request_timeout: int = 120,
    ) -> "FreeRouterServer":
        """Build a FreeRouterServer directly from provider key/model fields."""
        routes: List[Dict[str, Any]] = []

        if split_csv_values(gemini_keys):
            routes.append(
                {
                    "name": "gemini",
                    "kind": "gemini",
                    "api_keys": gemini_keys,
                    "models": gemini_models or list(FREE_GEMINI_FALLBACK_CHAIN),
                }
            )

        openai_presets = [
            ("groq", groq_keys, groq_models or "llama-3.3-70b-versatile,llama-3.1-8b-instant"),
            ("openrouter", openrouter_keys, openrouter_models or "openrouter/free"),
            ("mistral", mistral_keys, mistral_models or "mistral-small-latest"),
            ("cerebras", cerebras_keys, cerebras_models or "llama-3.3-70b"),
        ]
        for pname, pkeys, pmodels in openai_presets:
            if split_csv_values(pkeys):
                routes.append(
                    {
                        "name": pname,
                        "kind": "openai",
                        "base_url": PROVIDER_DEFAULT_URLS[pname],
                        "api_keys": pkeys,
                        "models": pmodels,
                    }
                )

        if (custom_base_url or "").strip() and split_csv_values(custom_models):
            routes.append(
                {
                    "name": (custom_name or "custom").strip(),
                    "kind": "openai",
                    "base_url": custom_base_url.strip(),
                    "api_keys": split_csv_values(custom_keys) or ["local"],
                    "models": custom_models,
                }
            )

        return cls(
            routes=routes,
            auth_token=auth_token,
            host=host,
            port=port,
            cooldown_seconds=cooldown_seconds,
            request_timeout=request_timeout,
        )

    def add_route(self, spec: Dict[str, Any]) -> None:
        """Append a normalized route configuration at runtime."""
        norm = _normalize_route(spec, len(self.routes))
        if norm.api_keys and norm.models:
            with self._lock:
                self.routes.append(norm)

    @property
    def base_url(self) -> str:
        """Local OpenAI-compatible `/v1` base URL."""
        return f"http://{self.host}:{self.bound_port}/v1"

    def _all_known_secrets(self) -> List[str]:
        keys: List[str] = []
        if self.auth_token:
            keys.append(self.auth_token)
        with self._lock:
            for route in self.routes:
                keys.extend(route.api_keys)
        return keys

    def _verify_auth(self, authorization_header: str) -> bool:
        if not self.auth_token:
            return True
        if not authorization_header:
            return False
        parts = authorization_header.strip().split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return False
        return hmac.compare_digest(parts[1].strip(), self.auth_token)

    def _select_candidates(self, requested_model: str) -> List[Tuple[RouterRoute, str]]:
        target = (requested_model or self.default_model or "auto").strip()
        if target in {"", "auto", "free", "mdaar/auto", "auto-free-chain"}:
            with self._lock:
                return [(r, m) for r in self.routes for m in r.models]

        if "/" in target:
            prefix, remainder = target.split("/", 1)
            with self._lock:
                matching_routes = [r for r in self.routes if r.name.lower() == prefix.lower()]
            if matching_routes:
                route = matching_routes[0]
                if remainder in {"", "auto"}:
                    return [(route, m) for m in route.models]
                return [(route, remainder)]

        with self._lock:
            exact = [(r, target) for r in self.routes if target in r.models]
            if exact:
                return exact
            lower = target.lower()
            if lower.startswith(("gemini", "gemma")):
                gemini_routes = [r for r in self.routes if r.kind == "gemini"]
                if gemini_routes:
                    return [(r, target) for r in gemini_routes]
            if len(self.routes) == 1:
                return [(self.routes[0], target)]
        return []

    def _record_error(self, route_name: str, model_id: str, msg: str) -> None:
        safe_msg = redact_secrets(msg, self._all_known_secrets())
        with self._lock:
            errors = self._stats["errors"]
            errors.append({"route": route_name, "model": model_id, "error": safe_msg[:300]})
            if len(errors) > 25:
                del errors[: len(errors) - 25]

    def _execute_pair(
        self,
        route: RouterRoute,
        model_id: str,
        api_key: str,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int],
        temperature: Optional[float],
    ) -> Tuple[bool, str, int]:
        timeout = route.timeout or self.request_timeout
        if route.kind == "gemini":
            return post_gemini_rest_completion(
                api_key=api_key,
                model=model_id,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
        return post_openai_chat_completion(
            base_url=route.base_url,
            api_key=api_key,
            model=model_id,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_headers=route.extra_headers,
            timeout=timeout,
        )

    def handle_chat_completion(self, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        """Route a chat completion request across configured providers and keys."""
        requested_model = str(payload.get("model") or self.default_model or "auto")
        messages = payload.get("messages") or []
        max_tokens = payload.get("max_tokens")
        temperature = payload.get("temperature")

        with self._lock:
            self._stats["total_requests"] += 1

        if not isinstance(messages, list) or not messages:
            with self._lock:
                self._stats["failed_requests"] += 1
            return 400, {"error": {"message": "messages must be a non-empty list", "type": "invalid_request_error"}}, {}

        candidates = self._select_candidates(requested_model)
        if not candidates:
            with self._lock:
                self._stats["failed_requests"] += 1
            return (
                404,
                {"error": {"message": f"No route configured for model '{requested_model}'", "type": "model_not_found"}},
                {},
            )

        last_err = "All free routes failed or are currently cooling down."
        last_status = 502

        for route, model_id in candidates:
            with self._lock:
                if (route.name, model_id) in self._dead_pairs:
                    continue
                total_keys = len(route.api_keys)
                cursor = route.cursor

            for offset in range(total_keys):
                key_idx = (cursor + offset) % total_keys
                now = time.time()
                with self._lock:
                    cool_until = self._cooldown_until.get((route.name, model_id, key_idx), 0.0)
                    api_key = route.api_keys[key_idx]
                if now < cool_until:
                    continue

                ok, text_or_err, status_code = self._execute_pair(
                    route=route,
                    model_id=model_id,
                    api_key=api_key,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                if ok:
                    with self._lock:
                        route.cursor = (key_idx + 1) % total_keys
                        self._stats["success_requests"] += 1
                        by_route = self._stats["successes_by_route"]
                        by_route[route.name] = by_route.get(route.name, 0) + 1
                    resp_body = {
                        "id": f"chatcmpl-mdaar-{int(time.time() * 1000)}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": f"{route.name}/{model_id}",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": text_or_err},
                                "finish_reason": "stop",
                            }
                        ],
                    }
                    headers = {
                        "X-MDAAR-Route": route.name,
                        "X-MDAAR-Model": model_id,
                    }
                    return 200, resp_body, headers

                last_err = redact_secrets(text_or_err, self._all_known_secrets())
                last_status = status_code or 502
                self._record_error(route.name, model_id, last_err)
                kind = classify_provider_error(status_code, last_err)
                with self._lock:
                    if kind == "dead":
                        self._dead_pairs.add((route.name, model_id))
                        break
                    self._cooldown_until[(route.name, model_id, key_idx)] = time.time() + self.cooldown_seconds

        with self._lock:
            self._stats["failed_requests"] += 1

        http_status = 429 if last_status == 429 else 502
        return (
            http_status,
            {"error": {"message": last_err, "type": "upstream_error", "code": last_status}},
            {},
        )

    def get_models_payload(self) -> Dict[str, Any]:
        """Return OpenAI `/v1/models` compatible listing without leaking credentials."""
        models: List[Dict[str, Any]] = [{"id": "auto", "object": "model", "owned_by": "mdaar-free-router"}]
        seen = {"auto"}
        with self._lock:
            for route in self.routes:
                for model_id in route.models:
                    qualified = f"{route.name}/{model_id}"
                    if qualified not in seen:
                        seen.add(qualified)
                        models.append({"id": qualified, "object": "model", "owned_by": route.name})
        return {"object": "list", "data": models}

    def get_health_payload(self) -> Dict[str, Any]:
        """Return sanitized server status and per-route statistics."""
        now = time.time()
        with self._lock:
            active_cooldowns = sum(1 for ts in self._cooldown_until.values() if ts > now)
            routes_summary = [
                {
                    "name": r.name,
                    "kind": r.kind,
                    "key_count": len(r.api_keys),
                    "models": list(r.models),
                }
                for r in self.routes
            ]
            stats_copy = {
                "total_requests": self._stats["total_requests"],
                "success_requests": self._stats["success_requests"],
                "failed_requests": self._stats["failed_requests"],
                "successes_by_route": dict(self._stats["successes_by_route"]),
                "active_cooldowns": active_cooldowns,
                "dead_pairs": [f"{r}/{m}" for r, m in sorted(self._dead_pairs)],
                "recent_errors": list(self._stats["errors"][-5:]),
            }
        return {"status": "ok", "auth_enabled": bool(self.auth_token), "routes": routes_summary, "stats": stats_copy}

    def _make_handler(self):
        router = self

        class _RouterHandler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _send_json(self, status_code: int, body: Dict[str, Any], extra_headers: Optional[Dict[str, str]] = None) -> None:
                raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                if extra_headers:
                    for hk, hv in extra_headers.items():
                        self.send_header(hk, hv)
                self.end_headers()
                self.wfile.write(raw)

            def _send_sse_completion(self, completion: Dict[str, Any], extra_headers: Optional[Dict[str, str]] = None) -> None:
                choice = (completion.get("choices") or [{}])[0]
                text = ((choice.get("message") or {}).get("content")) or ""
                chunk = {
                    "id": completion.get("id"),
                    "object": "chat.completion.chunk",
                    "created": completion.get("created"),
                    "model": completion.get("model"),
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": text},
                            "finish_reason": "stop",
                        }
                    ],
                }
                payload = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(payload)))
                if extra_headers:
                    for hk, hv in extra_headers.items():
                        self.send_header(hk, hv)
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:
                path = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
                if path in {"/", "/health", "/v1/health"}:
                    self._send_json(200, router.get_health_payload())
                    return
                if path == "/v1/models":
                    if not router._verify_auth(self.headers.get("Authorization", "")):
                        self._send_json(401, {"error": {"message": "Unauthorized", "type": "auth_error"}})
                        return
                    self._send_json(200, router.get_models_payload())
                    return
                self._send_json(404, {"error": {"message": f"Unknown GET path: {path}", "type": "not_found"}})

            def do_POST(self) -> None:
                path = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
                if path != "/v1/chat/completions":
                    self._send_json(404, {"error": {"message": f"Unknown POST path: {path}", "type": "not_found"}})
                    return

                if not router._verify_auth(self.headers.get("Authorization", "")):
                    self._send_json(401, {"error": {"message": "Unauthorized", "type": "auth_error"}})
                    return

                try:
                    length = int(self.headers.get("Content-Length", "0") or 0)
                except ValueError:
                    self._send_json(400, {"error": {"message": "Invalid Content-Length", "type": "invalid_request_error"}})
                    return

                raw = self.rfile.read(max(0, length))
                try:
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                except json.JSONDecodeError as exc:
                    self._send_json(400, {"error": {"message": f"Invalid JSON body: {exc}", "type": "invalid_request_error"}})
                    return

                status_code, resp_body, extra_headers = router.handle_chat_completion(payload)
                if status_code == 200 and payload.get("stream") is True:
                    self._send_sse_completion(resp_body, extra_headers=extra_headers)
                else:
                    self._send_json(status_code, resp_body, extra_headers=extra_headers)

        return _RouterHandler

    def start_in_background(self) -> str:
        """Start the ThreadingHTTPServer in a daemon thread and return local `/v1` URL."""
        if self._httpd is not None:
            return self.base_url

        class _ReusableThreadingHTTPServer(http.server.ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._httpd = _ReusableThreadingHTTPServer((self.host, self.port), self._make_handler())
        self.bound_port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url

    def serve_with_tunnel(self, timeout: int = 30, stop_existing: bool = True) -> Optional[str]:
        """Start local HTTP server + Cloudflare Quick Tunnel and return public URL."""
        self.start_in_background()
        public_url, cf_proc = launch_cloudflared_tunnel(
            port=self.bound_port,
            timeout=timeout,
            stop_existing=stop_existing,
            target_host=self.host,
        )
        self._tunnel_process = cf_proc
        return public_url

    def stop(self) -> None:
        """Stop HTTP server and any active Cloudflare tunnel process."""
        if self._tunnel_process is not None:
            try:
                if self._tunnel_process.poll() is None:
                    self._tunnel_process.terminate()
            except Exception:
                pass
            self._tunnel_process = None

        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
            self._thread = None

