"""Interactive conditional Colab UI builders powered by ipywidgets.

Provides clean, dynamically filtered forms inside Google Colab cells so only
controls relevant to the user's currently selected options are displayed, while
unifying shared credentials (`GEMINI_KEYS`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`,
`QUANTIZATION`, `HF_TOKEN`) and resolving Colab Secrets / environment variables
automatically without cluttering dropdowns with synthetic `AUTO` entries.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from mdaar.gateway.free_router_server import FreeRouterServer
from mdaar.gateway.model_gateway import (
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_MODEL,
    FREE_GEMINI_FALLBACK_CHAIN,
    SHARED_GEMINI_KEY_ENVS,
    SHARED_HF_TOKEN_ENVS,
    SHARED_OPENAI_BASE_URL_ENVS,
    SHARED_OPENAI_KEY_ENVS,
    SHARED_QUANTIZATION_ENVS,
    DualModelManager,
    ModelGateway,
    diagnose_colab_secrets,
    resolve_setting,
    split_csv_values,
)
from mdaar.orchestrator import MDAARPipeline
from mdaar.security.sanitizer import SecretSanitizer

PROVIDER_CHOICES = ["Gemini", "OpenAI-Gateway", "Local-Offline"]
AUTH_CHOICES = ["HTTPS", "SSH"]
QUANT_CHOICES = ["4bit", "None"]
GEMINI_MODEL_CHOICES = ["auto-free-chain", *FREE_GEMINI_FALLBACK_CHAIN]
LOCAL_MODEL_CHOICES = [
    "Qwen/Qwen2.5-Coder-7B-Instruct",
    "meta-llama/Meta-Llama-3.1-8B-Instruct",
    "mistralai/Mistral-7B-Instruct-v0.3",
    "google/gemma-2-9b-it",
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen2.5-Coder-1.5B-Instruct",
]

_SHARED_GEMINI_ENVS = SHARED_GEMINI_KEY_ENVS
_SHARED_OAI_URL_ENVS = SHARED_OPENAI_BASE_URL_ENVS
_SHARED_OAI_KEY_ENVS = SHARED_OPENAI_KEY_ENVS
_SHARED_QUANT_ENVS = SHARED_QUANTIZATION_ENVS
_SHARED_HF_ENVS = SHARED_HF_TOKEN_ENVS


def _load_widgets_and_display(widgets_mod: Any = None, display_fn: Any = None) -> Tuple[Any, Any]:
    if widgets_mod is None:
        try:
            import ipywidgets as widgets_mod  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "ipywidgets is required for interactive Colab UI forms. Install it with `pip install ipywidgets`."
            ) from exc

    if display_fn is None:
        try:
            from IPython.display import display as display_fn  # type: ignore
        except ImportError:
            display_fn = lambda *_a, **_kw: None
    return widgets_mod, display_fn


def _is_rtl(lang: str) -> bool:
    return (lang or "ar").strip().lower().startswith("ar")


def _default_workspace() -> str:
    if os.path.exists("/content/drive/MyDrive"):
        return "/content/drive/MyDrive/AI_Documentation_Workspace"
    return "/content/AI_Documentation_Workspace"


def _infer_openai_default_model(base_url: str) -> str:
    low = (base_url or "").strip().lower()
    if "openrouter.ai" in low:
        return DEFAULT_OPENAI_MODEL
    if "groq.com" in low:
        return "llama-3.3-70b-versatile"
    if "mistral.ai" in low:
        return "mistral-small-latest"
    if "cerebras.ai" in low:
        return "llama-3.3-70b"
    return "auto"


def _secret_badge_html(
    secret_value: str,
    source: str,
    lang: str,
    is_csv_keys: bool = True,
    env_names: Tuple[str, ...] = (),
) -> str:
    ar = _is_rtl(lang)
    count = len(split_csv_values(secret_value)) if is_csv_keys else (1 if secret_value.strip() else 0)
    if not secret_value.strip():
        blocked = diagnose_colab_secrets(env_names, refresh=False) if env_names else {}
        if blocked:
            names_txt = ", ".join(blocked.keys())
            if ar:
                msg = (
                    f"🔒 السر <code>{names_txt}</code> موجود في Colab Secrets لكن زر <b>Notebook access</b> مغلق — "
                    "فعّله من أيقونة المفتاح 🔑 ثم أعد تشغيل الخلية أو أدخله هنا."
                )
            else:
                msg = (
                    f"🔒 Colab Secret(s) <code>{names_txt}</code> exist but <b>Notebook access</b> is disabled — "
                    "enable the toggle in Colab Secrets (🔑) or enter it above."
                )
            return f"<div style='font-size:12px;color:#b91c1c;margin:0 0 6px 4px'>{msg}</div>"

        hint_names = " / ".join(env_names[:2]) if env_names else "Colab Secrets"
        if ar:
            msg = f"⚠️ غير مضبوط — أدخله هنا أو أضفه في Colab Secrets (<code>{hint_names}</code>)"
        else:
            msg = f"⚠️ Not set — enter above or add to Colab Secrets (<code>{hint_names}</code>)"
        return f"<div style='font-size:12px;color:#b45309;margin:0 0 6px 4px'>{msg}</div>"

    if source.startswith(("colab-secret:", "env:")):
        if ar:
            msg = f"🔑 محمّل تلقائياً من <code>{source}</code>" + (f" ({count} مفتاح)" if is_csv_keys else "")
        else:
            msg = f"🔑 Auto-loaded from <code>{source}</code>" + (f" ({count} key(s))" if is_csv_keys else "")
    else:
        if ar:
            msg = "✅ مُدخل" + (f" ({count} مفتاح)" if is_csv_keys else "")
        else:
            msg = "✅ Configured" + (f" ({count} key(s))" if is_csv_keys else "")
    return f"<div style='font-size:12px;color:#15803d;margin:0 0 6px 4px'>{msg}</div>"


def _make_dropdown_with_secret(w: Any, description: str, choices: Iterable[str], env_names: Tuple[str, ...], default: str, style: Dict[str, str], layout: Any) -> Tuple[Any, str]:
    opts = list(choices)
    val, src = resolve_setting("", env_names, default=default, return_source=True)
    if val not in opts:
        opts.insert(0, val)
    return w.Dropdown(options=opts, value=val, description=description, style=style, layout=layout), src


def _make_combobox(w: Any, description: str, choices: Iterable[str], env_names: Tuple[str, ...], default: str, style: Dict[str, str], layout: Any) -> Tuple[Any, str]:
    opts = tuple(choices)
    val, src = resolve_setting("", env_names, default=default, return_source=True)
    if hasattr(w, "Combobox"):
        widget = w.Combobox(value=val, options=opts, description=description, ensure_option=False, style=style, layout=layout)
    else:
        widget = w.Text(value=val, description=description, style=style, layout=layout)
    return widget, src


def _set_visible(box: Any, visible: bool) -> None:
    box.layout.display = "" if visible else "none"


def _section_header(w: Any, title: str) -> Any:
    return w.HTML(f"<div style='font-weight:700;font-size:14px;padding:6px 0 4px 0;border-bottom:1px solid #d1d5db;margin-bottom:6px'>{title}</div>")


def _safe_run_into_output(out_widget: Any, fn: Callable[[], None]) -> None:
    ctx = getattr(out_widget, "__enter__", None)
    if ctx is not None:
        with out_widget:
            if hasattr(out_widget, "clear_output"):
                out_widget.clear_output(wait=True)
            fn()
    else:
        fn()


class RepoConfigUI:
    """Interactive controller for Source & External Documentation Repositories."""

    def __init__(
        self,
        lang: str = "ar",
        namespace: Optional[Dict[str, Any]] = None,
        widgets_mod: Any = None,
        test_on_start: bool = True,
    ) -> None:
        self.lang = lang
        self.ar = _is_rtl(lang)
        self.namespace = namespace if namespace is not None else {}
        w, _ = _load_widgets_and_display(widgets_mod=widgets_mod, display_fn=lambda *a, **k: None)
        self.w = w

        style = {"description_width": "230px"}
        wide = lambda: w.Layout(width="96%")
        box_layout = w.Layout(width="100%", margin="4px 0")

        proj_val = resolve_setting("", ("MDAAR_PROJECT_NAME",), default="MySystem")
        src_url_val = resolve_setting("", ("MDAAR_GIT_SOURCE_URL",), default="https://github.com/octocat/Hello-World.git")
        ws_val = resolve_setting("", ("MDAAR_BASE_WORKSPACE",), default=_default_workspace())
        en_docs_val = resolve_setting("", ("MDAAR_ENABLE_EXTERNAL_DOCS_REPO",), default=False, is_bool=True)
        docs_url_val = resolve_setting("", ("MDAAR_GIT_DOCS_URL",), default="")

        self.project_name = w.Text(
            value=proj_val,
            placeholder="MySystem",
            description="اسم المشروع:" if self.ar else "Project Name:",
            style=style,
            layout=wide(),
        )
        self.git_source_url = w.Text(
            value=src_url_val,
            placeholder="https://github.com/org/repo.git",
            description="رابط مستودع الكود:" if self.ar else "Source Git URL:",
            style=style,
            layout=wide(),
        )
        self.source_auth, _ = _make_dropdown_with_secret(
            w,
            "مصادقة مستودع الكود:" if self.ar else "Source Auth:",
            AUTH_CHOICES,
            ("MDAAR_SOURCE_AUTH",),
            "HTTPS",
            style,
            wide(),
        )
        self.base_workspace = w.Text(
            value=ws_val,
            placeholder=_default_workspace(),
            description="مجلد مساحة العمل:" if self.ar else "Workspace Path:",
            style=style,
            layout=wide(),
        )
        self.enable_external_docs = w.Checkbox(
            value=bool(en_docs_val),
            description="تفعيل مستودع توثيق خارجي منفصل (External Docs Repo)"
            if self.ar
            else "Enable External Shared Documentation Repo",
            indent=False,
            layout=wide(),
        )
        self.git_docs_url = w.Text(
            value=docs_url_val,
            placeholder="git@github.com:org/docs.git",
            description="رابط مستودع التوثيق:" if self.ar else "Docs Git URL:",
            style=style,
            layout=wide(),
        )
        self.docs_auth, _ = _make_dropdown_with_secret(
            w,
            "مصادقة مستودع التوثيق:" if self.ar else "Docs Auth:",
            AUTH_CHOICES,
            ("MDAAR_DOCS_AUTH",),
            "HTTPS",
            style,
            wide(),
        )

        self.docs_box = w.VBox([self.git_docs_url, self.docs_auth], layout=w.Layout(width="100%", margin="2px 0 6px 0"))
        self.test_button = w.Button(
            description="🔌 حفظ وفحص اتصال المستودعات الآن" if self.ar else "🔌 Save & Test Repository Connection",
            button_style="primary",
            layout=w.Layout(width="320px", margin="6px 0"),
        )
        self.status_html = w.HTML("")
        self.output = w.Output()

        header = _section_header(
            w,
            "📁 إعدادات المشروع ومستودع الكود (المتغيرات الفارغة تُسحب تلقائياً من أسرار كولاب)"
            if self.ar
            else "📁 Project & Repository Setup (Empty fields auto-resolve from Colab Secrets/Env)",
        )
        self.root = w.VBox(
            [
                header,
                self.project_name,
                self.git_source_url,
                self.source_auth,
                self.base_workspace,
                self.enable_external_docs,
                self.docs_box,
                self.test_button,
                self.status_html,
                self.output,
            ],
            layout=box_layout,
        )
        self.widgets: Dict[str, Any] = {
            "project_name": self.project_name,
            "git_source_url": self.git_source_url,
            "source_auth": self.source_auth,
            "base_workspace": self.base_workspace,
            "enable_external_docs": self.enable_external_docs,
            "git_docs_url": self.git_docs_url,
            "docs_auth": self.docs_auth,
            "docs_box": self.docs_box,
            "test_button": self.test_button,
            "status_html": self.status_html,
            "output": self.output,
        }

        for ctrl in (
            self.project_name,
            self.git_source_url,
            self.source_auth,
            self.base_workspace,
            self.enable_external_docs,
            self.git_docs_url,
            self.docs_auth,
        ):
            ctrl.observe(self._on_change, names="value")

        self.test_button.on_click(lambda _btn: self.run_check())
        self._refresh_visibility()
        self.sync_pipeline()
        if test_on_start:
            self.run_check()

    def _refresh_visibility(self) -> None:
        _set_visible(self.docs_box, bool(self.enable_external_docs.value))

    def _on_change(self, _change: Any = None) -> None:
        self._refresh_visibility()
        self.sync_pipeline()

    def sync_pipeline(self) -> MDAARPipeline:
        """Build or update `MDAARPipeline` in `self.namespace` cleanly."""
        project_name = self.project_name.value.strip() or resolve_setting("", ("MDAAR_PROJECT_NAME",), default="MySystem")
        source_url = self.git_source_url.value.strip() or resolve_setting(
            "", ("MDAAR_GIT_SOURCE_URL",), default="https://github.com/octocat/Hello-World.git"
        )
        source_auth = self.source_auth.value or "HTTPS"
        workspace_str = self.base_workspace.value.strip() or resolve_setting(
            "", ("MDAAR_BASE_WORKSPACE",), default=_default_workspace()
        )
        workspace = Path(workspace_str)

        enable_docs = bool(self.enable_external_docs.value)
        docs_url_raw = self.git_docs_url.value.strip() or resolve_setting("", ("MDAAR_GIT_DOCS_URL",), default="")
        docs_url = docs_url_raw if (enable_docs and docs_url_raw) else None
        docs_auth = self.docs_auth.value or "HTTPS"

        existing = self.namespace.get("pipeline")
        if isinstance(existing, MDAARPipeline) and existing.project_name == project_name and Path(existing.base_workspace) == workspace:
            pipeline = existing
            pipeline.git_source_url = source_url
            pipeline.source_auth = source_auth
            pipeline.git_docs_url = docs_url
            pipeline.docs_auth = docs_auth
        else:
            planner_gw = getattr(existing, "planner_gateway", None) if existing is not None else None
            executor_gw = getattr(existing, "executor_gateway", None) if existing is not None else None
            pipeline = MDAARPipeline(
                project_name=project_name,
                base_workspace=workspace,
                git_source_url=source_url,
                git_docs_url=docs_url,
                source_auth=source_auth,
                docs_auth=docs_auth,
                planner_gateway=planner_gw,
                executor_gateway=executor_gw,
            )

        self.namespace["pipeline"] = pipeline
        self.namespace["BASE_WORKSPACE"] = workspace
        safe_url = SecretSanitizer.sanitize(source_url)
        if self.ar:
            self.status_html.value = (
                f"<div style='font-size:13px;color:#1e3a8a'>✅ <b>المشروع:</b> {project_name} | "
                f"<b>المسار:</b> <code>{pipeline.project_dir}</code> | <b>الكود:</b> <code>{safe_url}</code></div>"
            )
        else:
            self.status_html.value = (
                f"<div style='font-size:13px;color:#1e3a8a'>✅ <b>Project:</b> {project_name} | "
                f"<b>Path:</b> <code>{pipeline.project_dir}</code> | <b>Source:</b> <code>{safe_url}</code></div>"
            )
        return pipeline

    def run_check(self) -> Dict[str, Any]:
        """Synchronize pipeline, provision SSH Deploy Keys if selected, and test repo connections."""
        pipeline = self.sync_pipeline()
        results: Dict[str, Any] = {}

        def _work() -> None:
            if pipeline.source_auth == "SSH":
                pub = pipeline.setup_source_ssh()
                print("=" * 70)
                print("🔑 مفتاح SSH العام لمستودع الكود (Deploy Key):" if self.ar else "🔑 Source SSH Public Key (Deploy Key):")
                print(pub)
                print("=" * 70)
            src_conn = pipeline.test_source_connection()
            results["source"] = src_conn
            print(src_conn["message"])

            if pipeline.git_docs_url:
                if pipeline.docs_auth == "SSH":
                    dpub = pipeline.setup_docs_ssh()
                    print("=" * 70)
                    print("🔑 مفتاح SSH العام لمستودع التوثيق (Write Deploy Key):" if self.ar else "🔑 Docs SSH Public Key (Write Deploy Key):")
                    print(dpub)
                    print("=" * 70)
                docs_conn = pipeline.test_docs_connection()
                results["docs"] = docs_conn
                print(docs_conn["message"])
            else:
                print(f"🔍 RAG Index Path: {pipeline.rag_index_dir}")

        _safe_run_into_output(self.output, _work)
        return results


class ModelsConfigUI:
    """Interactive controller for Planner/Executor models with shared unified credentials."""

    _SINGLE_AR = "نفس مودل الخطة (مودل واحد)"
    _CUSTOM_AR = "مودل مستقل للتنفيذ والفحص"
    _SINGLE_EN = "Same as Planner Model"
    _CUSTOM_EN = "Custom Executor Model"

    def __init__(
        self,
        pipeline: Optional[MDAARPipeline] = None,
        lang: str = "ar",
        namespace: Optional[Dict[str, Any]] = None,
        widgets_mod: Any = None,
    ) -> None:
        self.lang = lang
        self.ar = _is_rtl(lang)
        self.namespace = namespace if namespace is not None else {}
        if pipeline is not None:
            self.namespace.setdefault("pipeline", pipeline)

        w, _ = _load_widgets_and_display(widgets_mod=widgets_mod, display_fn=lambda *a, **k: None)
        self.w = w

        style = {"description_width": "230px"}
        wide = lambda: w.Layout(width="96%")
        sub_layout = lambda: w.Layout(width="100%", margin="4px 0 8px 0")

        # Resolve shared credentials ONCE
        gem_val, gem_src = resolve_setting("", _SHARED_GEMINI_ENVS, default="", return_source=True)
        oai_url_val, _ = resolve_setting("", _SHARED_OAI_URL_ENVS, default=DEFAULT_OPENAI_BASE_URL, return_source=True)
        oai_key_val, oai_key_src = resolve_setting("", _SHARED_OAI_KEY_ENVS, default="", return_source=True)
        hf_val, hf_src = resolve_setting("", _SHARED_HF_ENVS, default="", return_source=True)
        inferred_oai_model = _infer_openai_default_model(oai_url_val)

        # --- Section 1: Planner ---
        self.planner_provider, _ = _make_dropdown_with_secret(
            w,
            "مزود مودل الخطة:" if self.ar else "Planner Provider:",
            PROVIDER_CHOICES,
            ("MDAAR_PLANNER_PROVIDER",),
            "Gemini",
            style,
            wide(),
        )
        self.planner_gemini_model, _ = _make_dropdown_with_secret(
            w,
            "مودل Gemini للخطة:" if self.ar else "Planner Gemini Model:",
            GEMINI_MODEL_CHOICES,
            ("MDAAR_PLANNER_GEMINI_MODEL",),
            "auto-free-chain",
            style,
            wide(),
        )
        self.planner_openai_model, _ = _make_combobox(
            w,
            "مودل البوابة للخطة:" if self.ar else "Planner Gateway Model:",
            (inferred_oai_model, "auto", DEFAULT_OPENAI_MODEL),
            ("MDAAR_PLANNER_OPENAI_MODELS", "OPENAI_MODEL"),
            inferred_oai_model,
            style,
            wide(),
        )
        self.planner_local_model, _ = _make_combobox(
            w,
            "مودل HuggingFace للخطة:" if self.ar else "Planner Local HF Model:",
            LOCAL_MODEL_CHOICES,
            ("MDAAR_PLANNER_LOCAL_MODEL",),
            "Qwen/Qwen2.5-Coder-7B-Instruct",
            style,
            wide(),
        )
        self.planner_gemini_box = w.VBox([self.planner_gemini_model], layout=w.Layout(width="100%"))
        self.planner_openai_box = w.VBox([self.planner_openai_model], layout=w.Layout(width="100%"))
        self.planner_local_box = w.VBox([self.planner_local_model], layout=w.Layout(width="100%"))

        # --- Section 2: Executor ---
        single_lbl = self._SINGLE_AR if self.ar else self._SINGLE_EN
        custom_lbl = self._CUSTOM_AR if self.ar else self._CUSTOM_EN
        exec_mode_raw = resolve_setting("", ("MDAAR_EXECUTOR_MODE",), default="same")
        exec_is_custom = exec_mode_raw.strip().lower() in ("custom", "independent", custom_lbl.lower(), self._CUSTOM_AR, self._CUSTOM_EN.lower())
        self.executor_mode = w.Dropdown(
            options=[single_lbl, custom_lbl],
            value=custom_lbl if exec_is_custom else single_lbl,
            description="وضع مودل التنفيذ:" if self.ar else "Executor Mode:",
            style=style,
            layout=wide(),
        )
        self.executor_provider, _ = _make_dropdown_with_secret(
            w,
            "مزود مودل التنفيذ:" if self.ar else "Executor Provider:",
            ["Local-Offline", "Gemini", "OpenAI-Gateway"],
            ("MDAAR_EXECUTOR_PROVIDER",),
            "Local-Offline",
            style,
            wide(),
        )
        self.executor_gemini_model, _ = _make_dropdown_with_secret(
            w,
            "مودل Gemini للتنفيذ:" if self.ar else "Executor Gemini Model:",
            GEMINI_MODEL_CHOICES,
            ("MDAAR_EXECUTOR_GEMINI_MODEL",),
            "auto-free-chain",
            style,
            wide(),
        )
        self.executor_openai_model, _ = _make_combobox(
            w,
            "مودل البوابة للتنفيذ:" if self.ar else "Executor Gateway Model:",
            (inferred_oai_model, "auto", DEFAULT_OPENAI_MODEL),
            ("MDAAR_EXECUTOR_OPENAI_MODELS", "OPENAI_MODEL"),
            inferred_oai_model,
            style,
            wide(),
        )
        self.executor_local_model, _ = _make_combobox(
            w,
            "مودل HuggingFace للتنفيذ:" if self.ar else "Executor Local HF Model:",
            LOCAL_MODEL_CHOICES,
            ("MDAAR_EXECUTOR_LOCAL_MODEL",),
            "Qwen/Qwen2.5-Coder-7B-Instruct",
            style,
            wide(),
        )
        self.executor_gemini_box = w.VBox([self.executor_gemini_model], layout=w.Layout(width="100%"))
        self.executor_openai_box = w.VBox([self.executor_openai_model], layout=w.Layout(width="100%"))
        self.executor_local_box = w.VBox([self.executor_local_model], layout=w.Layout(width="100%"))
        self.executor_custom_box = w.VBox(
            [self.executor_provider, self.executor_gemini_box, self.executor_openai_box, self.executor_local_box],
            layout=w.Layout(width="100%"),
        )

        # --- Section 3: Shared Credentials & Settings ---
        self.shared_gemini_keys = w.Password(
            value=gem_val,
            placeholder="AIza...,AIza...",
            description="مفاتيح Gemini المشتركة:" if self.ar else "Shared Gemini Keys:",
            style=style,
            layout=wide(),
        )
        self._gem_src = gem_src
        self.gemini_badge = w.HTML(_secret_badge_html(gem_val, gem_src, lang, is_csv_keys=True, env_names=_SHARED_GEMINI_ENVS))
        self.shared_gemini_box = w.VBox([self.shared_gemini_keys, self.gemini_badge], layout=sub_layout())

        self.shared_openai_base_url = w.Text(
            value=oai_url_val,
            placeholder="https://openrouter.ai/api/v1",
            description="رابط البوابة (Base URL):" if self.ar else "Shared Gateway URL:",
            style=style,
            layout=wide(),
        )
        self.shared_openai_api_key = w.Password(
            value=oai_key_val,
            placeholder="sk-... or router token",
            description="مفتاح/توكن البوابة:" if self.ar else "Shared Gateway Key:",
            style=style,
            layout=wide(),
        )
        self._oai_key_src = oai_key_src
        self.openai_badge = w.HTML(_secret_badge_html(oai_key_val, oai_key_src, lang, is_csv_keys=True, env_names=_SHARED_OAI_KEY_ENVS))
        self.shared_openai_box = w.VBox(
            [self.shared_openai_base_url, self.shared_openai_api_key, self.openai_badge],
            layout=sub_layout(),
        )

        self.shared_quantization, _ = _make_dropdown_with_secret(
            w,
            "التكميم المشترك (T4):" if self.ar else "Shared Quantization:",
            QUANT_CHOICES,
            _SHARED_QUANT_ENVS,
            "4bit",
            style,
            wide(),
        )
        self.shared_hf_token = w.Password(
            value=hf_val,
            placeholder="hf_... (optional)",
            description="توكن HuggingFace المشترك:" if self.ar else "Shared HF Token:",
            style=style,
            layout=wide(),
        )
        self._hf_src = hf_src
        self.hf_badge = w.HTML(_secret_badge_html(hf_val, hf_src, lang, is_csv_keys=False, env_names=_SHARED_HF_ENVS))
        self.shared_local_box = w.VBox(
            [self.shared_quantization, self.shared_hf_token, self.hf_badge],
            layout=sub_layout(),
        )

        self.apply_button = w.Button(
            description="✅ تطبيق وتحديث إعدادات النماذج" if self.ar else "✅ Apply Model Configuration",
            button_style="success",
            layout=w.Layout(width="270px", margin="6px 6px 6px 0"),
        )
        self.test_button = w.Button(
            description="🧪 فحص اتصال النماذج الآن" if self.ar else "🧪 Test Model Connections",
            button_style="info",
            layout=w.Layout(width="240px", margin="6px 0"),
        )
        self.summary_html = w.HTML("")
        self.output = w.Output()
        actions_row = w.HBox([self.apply_button, self.test_button]) if hasattr(w, "HBox") else w.VBox([self.apply_button, self.test_button])

        self.root = w.VBox(
            [
                _section_header(w, "🧠 1. إعدادات مودل الخطة (Planner Model)" if self.ar else "🧠 1. Planner Model Settings"),
                self.planner_provider,
                self.planner_gemini_box,
                self.planner_openai_box,
                self.planner_local_box,
                _section_header(w, "⚙️ 2. إعدادات مودل التنفيذ والفحص (Executor Model)" if self.ar else "⚙️ 2. Executor Model Settings"),
                self.executor_mode,
                self.executor_custom_box,
                _section_header(
                    w,
                    "🔑 3. المفاتيح والإعدادات المشتركة (تظهر فقط حسب المزود المختار)"
                    if self.ar
                    else "🔑 3. Shared Credentials & Settings (Shown for active providers only)",
                ),
                self.shared_gemini_box,
                self.shared_openai_box,
                self.shared_local_box,
                actions_row,
                self.summary_html,
                self.output,
            ],
            layout=w.Layout(width="100%", margin="4px 0"),
        )

        self.widgets: Dict[str, Any] = {
            "planner_provider": self.planner_provider,
            "planner_gemini_model": self.planner_gemini_model,
            "planner_openai_model": self.planner_openai_model,
            "planner_local_model": self.planner_local_model,
            "planner_gemini_box": self.planner_gemini_box,
            "planner_openai_box": self.planner_openai_box,
            "planner_local_box": self.planner_local_box,
            "executor_mode": self.executor_mode,
            "executor_provider": self.executor_provider,
            "executor_gemini_model": self.executor_gemini_model,
            "executor_openai_model": self.executor_openai_model,
            "executor_local_model": self.executor_local_model,
            "executor_gemini_box": self.executor_gemini_box,
            "executor_openai_box": self.executor_openai_box,
            "executor_local_box": self.executor_local_box,
            "executor_custom_box": self.executor_custom_box,
            "shared_gemini_keys": self.shared_gemini_keys,
            "shared_gemini_box": self.shared_gemini_box,
            "shared_openai_base_url": self.shared_openai_base_url,
            "shared_openai_api_key": self.shared_openai_api_key,
            "shared_openai_box": self.shared_openai_box,
            "shared_quantization": self.shared_quantization,
            "shared_hf_token": self.shared_hf_token,
            "shared_local_box": self.shared_local_box,
            "apply_button": self.apply_button,
            "test_button": self.test_button,
            "summary_html": self.summary_html,
            "output": self.output,
        }

        self._last_inferred_oai_model = inferred_oai_model
        for ctrl in (
            self.planner_provider,
            self.planner_gemini_model,
            self.planner_openai_model,
            self.planner_local_model,
            self.executor_mode,
            self.executor_provider,
            self.executor_gemini_model,
            self.executor_openai_model,
            self.executor_local_model,
            self.shared_gemini_keys,
            self.shared_openai_base_url,
            self.shared_openai_api_key,
            self.shared_quantization,
            self.shared_hf_token,
        ):
            ctrl.observe(self._on_change, names="value")

        self.apply_button.on_click(lambda _b: self.apply())
        self.test_button.on_click(lambda _b: self.run_check())
        self._refresh_visibility()
        self.apply()

    def _is_custom_executor(self) -> bool:
        val = str(self.executor_mode.value or "").strip()
        return val in (self._CUSTOM_AR, self._CUSTOM_EN, "custom")

    def _active_providers(self) -> set[str]:
        providers = {self.planner_provider.value}
        if self._is_custom_executor():
            providers.add(self.executor_provider.value)
        return providers

    def _refresh_visibility(self) -> None:
        p_prov = self.planner_provider.value
        _set_visible(self.planner_gemini_box, p_prov == "Gemini")
        _set_visible(self.planner_openai_box, p_prov == "OpenAI-Gateway")
        _set_visible(self.planner_local_box, p_prov == "Local-Offline")

        custom_exec = self._is_custom_executor()
        _set_visible(self.executor_custom_box, custom_exec)
        e_prov = self.executor_provider.value
        _set_visible(self.executor_gemini_box, custom_exec and e_prov == "Gemini")
        _set_visible(self.executor_openai_box, custom_exec and e_prov == "OpenAI-Gateway")
        _set_visible(self.executor_local_box, custom_exec and e_prov == "Local-Offline")

        active = self._active_providers()
        _set_visible(self.shared_gemini_box, "Gemini" in active)
        _set_visible(self.shared_openai_box, "OpenAI-Gateway" in active)
        _set_visible(self.shared_local_box, "Local-Offline" in active)

    def _on_change(self, change: Any = None) -> None:
        if change is not None and getattr(change, "get", lambda *a: None)("owner") is self.shared_openai_base_url:
            new_inferred = _infer_openai_default_model(self.shared_openai_base_url.value)
            auto_defaults = {"", "auto", DEFAULT_OPENAI_MODEL, self._last_inferred_oai_model}
            if self.planner_openai_model.value.strip() in auto_defaults:
                self.planner_openai_model.value = new_inferred
            if self.executor_openai_model.value.strip() in auto_defaults:
                self.executor_openai_model.value = new_inferred
            self._last_inferred_oai_model = new_inferred

        self.gemini_badge.value = _secret_badge_html(
            self.shared_gemini_keys.value,
            self._gem_src if self.shared_gemini_keys.value.strip() else "form",
            self.lang,
            is_csv_keys=True,
            env_names=_SHARED_GEMINI_ENVS,
        )
        self.openai_badge.value = _secret_badge_html(
            self.shared_openai_api_key.value,
            self._oai_key_src if self.shared_openai_api_key.value.strip() else "form",
            self.lang,
            is_csv_keys=True,
            env_names=_SHARED_OAI_KEY_ENVS,
        )
        self.hf_badge.value = _secret_badge_html(
            self.shared_hf_token.value,
            self._hf_src if self.shared_hf_token.value.strip() else "form",
            self.lang,
            is_csv_keys=False,
            env_names=_SHARED_HF_ENVS,
        )
        self._refresh_visibility()
        self.apply()

    def _build_gateway(self, role: str) -> ModelGateway:
        provider = self.planner_provider.value if role == "planner" else self.executor_provider.value
        base_url = self.shared_openai_base_url.value.strip() or resolve_setting(
            "", _SHARED_OAI_URL_ENVS, default=DEFAULT_OPENAI_BASE_URL
        )
        quant = self.shared_quantization.value or "4bit"
        hf_tok = self.shared_hf_token.value.strip() or resolve_setting("", _SHARED_HF_ENVS, default="")

        if provider == "Gemini":
            gem_keys = self.shared_gemini_keys.value.strip() or resolve_setting("", _SHARED_GEMINI_ENVS, default="")
            model_name = (
                self.planner_gemini_model.value if role == "planner" else self.executor_gemini_model.value
            ) or "auto-free-chain"
            return ModelGateway(provider="Gemini", model_name=model_name, api_keys=gem_keys)

        if provider == "OpenAI-Gateway":
            oai_keys = self.shared_openai_api_key.value.strip() or resolve_setting("", _SHARED_OAI_KEY_ENVS, default="")
            model_widget = self.planner_openai_model if role == "planner" else self.executor_openai_model
            model_name = model_widget.value.strip() or _infer_openai_default_model(base_url)
            return ModelGateway(
                provider="OpenAI-Gateway",
                model_name=model_name,
                api_keys=oai_keys,
                base_url=base_url,
            )

        local_widget = self.planner_local_model if role == "planner" else self.executor_local_model
        local_model = local_widget.value.strip() or "Qwen/Qwen2.5-Coder-7B-Instruct"
        return ModelGateway(
            provider="Local-Offline",
            local_model_path=local_model,
            hf_token=hf_tok,
            quantization=quant,
        )

    def apply(self) -> Tuple[ModelGateway, ModelGateway]:
        """Build/bind shared-credential `planner_gateway` and `executor_gateway` onto `pipeline`."""
        planner_gw = self._build_gateway("planner")
        executor_gw = self._build_gateway("executor") if self._is_custom_executor() else planner_gw

        pipeline = self.namespace.get("pipeline")
        if pipeline is not None:
            pipeline.planner_gateway = planner_gw
            pipeline.executor_gateway = executor_gw
            pipeline.gateway = executor_gw
            pipeline.model_manager = DualModelManager(planner_gw, executor_gw)

        self.namespace["planner_gateway"] = planner_gw
        self.namespace["executor_gateway"] = executor_gw

        mode_note = ("نموذج واحد" if self.ar else "Single-Model") if planner_gw is executor_gw else ("مزدوج" if self.ar else "Dual-Model")
        p_ok, p_reason = planner_gw.check_configuration()
        e_ok, e_reason = (p_ok, p_reason) if executor_gw is planner_gw else executor_gw.check_configuration()
        warnings = []
        if not p_ok:
            warnings.append(f"Planner: {p_reason}")
        if not e_ok and (executor_gw is not planner_gw or not warnings):
            warnings.append(f"Executor: {e_reason}")

        if warnings:
            warn_html = "<br/>".join(f"⚠️ {w_msg}" for w_msg in warnings)
            box_bg, box_bd, box_fg = "#fef2f2", "#fca5a5", "#991b1b"
            extra = f"<div style='margin-top:4px;font-weight:600'>{warn_html}</div>"
        else:
            box_bg, box_bd, box_fg = "#f0fdf4", "#86efac", "#14532d"
            extra = ""

        if self.ar:
            self.summary_html.value = (
                f"<div style='padding:8px;border-radius:6px;background:{box_bg};border:1px solid {box_bd};color:{box_fg};font-size:13px'>"
                f"🧠 <b>الخطة:</b> <code>{planner_gw.descriptor}</code> ({len(planner_gw.api_keys)} مفتاح) &nbsp;|&nbsp; "
                f"⚙️ <b>التنفيذ والفحص:</b> <code>{executor_gw.descriptor}</code> ({len(executor_gw.api_keys)} مفتاح) &nbsp;|&nbsp; "
                f"<b>الوضع:</b> {mode_note}{extra}</div>"
            )
        else:
            self.summary_html.value = (
                f"<div style='padding:8px;border-radius:6px;background:{box_bg};border:1px solid {box_bd};color:{box_fg};font-size:13px'>"
                f"🧠 <b>Planner:</b> <code>{planner_gw.descriptor}</code> ({len(planner_gw.api_keys)} key(s)) &nbsp;|&nbsp; "
                f"⚙️ <b>Executor:</b> <code>{executor_gw.descriptor}</code> ({len(executor_gw.api_keys)} key(s)) &nbsp;|&nbsp; "
                f"<b>Mode:</b> {mode_note}{extra}</div>"
            )
        return planner_gw, executor_gw

    def run_check(self) -> Dict[str, Any]:
        """Run a lightweight live connectivity probe on active Planner & Executor gateways."""
        planner_gw, executor_gw = self.apply()
        results: Dict[str, Any] = {}

        def _work() -> None:
            print(f"🔎 جاري فحص اتصال مودل الخطة ({planner_gw.descriptor})..." if self.ar else f"🔎 Testing Planner Model ({planner_gw.descriptor})...")
            p_res = planner_gw.test_connection()
            results["planner"] = p_res
            print(p_res["message"])
            if executor_gw is not planner_gw:
                print(f"🔎 جاري فحص اتصال مودل التنفيذ ({executor_gw.descriptor})..." if self.ar else f"🔎 Testing Executor Model ({executor_gw.descriptor})...")
                e_res = executor_gw.test_connection()
                results["executor"] = e_res
                print(e_res["message"])

        _safe_run_into_output(self.output, _work)
        return results


class FreeRouterConfigUI:
    """Interactive controller for `free_router_colab.ipynb` with provider checkboxes."""

    _PROVIDER_SPECS = [
        ("gemini", "Gemini (Google AI Studio)", ("MDAAR_ROUTER_GEMINI_KEYS", "GEMINI_API_KEY"), ("MDAAR_ROUTER_GEMINI_MODELS",), "auto-free-chain"),
        ("groq", "Groq", ("MDAAR_ROUTER_GROQ_KEYS", "GROQ_API_KEY"), ("MDAAR_ROUTER_GROQ_MODELS",), "llama-3.3-70b-versatile,llama-3.1-8b-instant"),
        ("openrouter", "OpenRouter", ("MDAAR_ROUTER_OPENROUTER_KEYS", "OPENROUTER_API_KEY"), ("MDAAR_ROUTER_OPENROUTER_MODELS",), "openrouter/free"),
        ("mistral", "Mistral AI", ("MDAAR_ROUTER_MISTRAL_KEYS", "MISTRAL_API_KEY"), ("MDAAR_ROUTER_MISTRAL_MODELS",), "mistral-small-latest"),
        ("cerebras", "Cerebras", ("MDAAR_ROUTER_CEREBRAS_KEYS", "CEREBRAS_API_KEY"), ("MDAAR_ROUTER_CEREBRAS_MODELS",), "llama-3.3-70b"),
    ]

    def __init__(
        self,
        lang: str = "ar",
        namespace: Optional[Dict[str, Any]] = None,
        widgets_mod: Any = None,
    ) -> None:
        self.lang = lang
        self.ar = _is_rtl(lang)
        self.namespace = namespace if namespace is not None else {}
        w, _ = _load_widgets_and_display(widgets_mod=widgets_mod, display_fn=lambda *a, **k: None)
        self.w = w

        style = {"description_width": "220px"}
        wide = lambda: w.Layout(width="96%")
        indent_box = lambda: w.Layout(width="96%", margin="0 0 8px 18px")

        tok_val, tok_src = resolve_setting("", ("MDAAR_ROUTER_TOKEN",), default="", return_source=True)
        self._generated_token = ""
        if not tok_val:
            self._generated_token = secrets.token_urlsafe(24)
            tok_val = self._generated_token
            tok_src = "auto-generated"

        self.auth_token = w.Password(
            value=tok_val,
            description="توكن الحماية (Bearer):" if self.ar else "Router Auth Token:",
            style=style,
            layout=wide(),
        )
        self.port = w.Text(
            value=resolve_setting("", ("MDAAR_ROUTER_PORT",), default="8787"),
            description="منفذ التشغيل (Port):" if self.ar else "Port:",
            style=style,
            layout=wide(),
        )
        self.cooldown = w.Text(
            value=resolve_setting("", ("MDAAR_ROUTER_COOLDOWN",), default="45"),
            description="مهلة التهدئة 429 (ثانية):" if self.ar else "Cooldown (seconds):",
            style=style,
            layout=wide(),
        )

        self.provider_controls: Dict[str, Dict[str, Any]] = {}
        provider_widgets = []
        any_preloaded = False

        for key, label, key_envs, model_envs, default_models in self._PROVIDER_SPECS:
            k_val, k_src = resolve_setting("", key_envs, default="", return_source=True)
            m_val = resolve_setting("", model_envs, default=default_models)
            enabled = bool(k_val.strip())
            if enabled:
                any_preloaded = True

            chk = w.Checkbox(value=enabled, description=f"تفعيل {label}" if self.ar else f"Enable {label}", indent=False, layout=wide())
            keys_w = w.Password(value=k_val, placeholder="key1,key2", description=f"مفاتيح {label}:" if self.ar else f"{label} Keys:", style=style, layout=wide())
            models_w = w.Text(value=m_val, description=f"موديلات {label}:" if self.ar else f"{label} Models:", style=style, layout=wide())
            badge = w.HTML(_secret_badge_html(k_val, k_src, lang, is_csv_keys=True, env_names=key_envs))
            box = w.VBox([keys_w, badge, models_w], layout=indent_box())

            self.provider_controls[key] = {"chk": chk, "keys": keys_w, "models": models_w, "badge": badge, "box": box, "src": k_src, "key_envs": key_envs}
            provider_widgets.extend([chk, box])

        if not any_preloaded:
            self.provider_controls["gemini"]["chk"].value = True

        # Custom OpenAI route
        c_url = resolve_setting("", ("MDAAR_ROUTER_CUSTOM_BASE_URL",), default="")
        c_keys, c_src = resolve_setting("", ("MDAAR_ROUTER_CUSTOM_KEYS",), default="", return_source=True)
        c_models = resolve_setting("", ("MDAAR_ROUTER_CUSTOM_MODELS",), default="")
        c_name = resolve_setting("", ("MDAAR_ROUTER_CUSTOM_NAME",), default="custom")
        c_enabled = bool(c_url.strip() and c_models.strip())

        self.custom_chk = w.Checkbox(value=c_enabled, description="تفعيل مزود مخصص (Custom OpenAI Endpoint)" if self.ar else "Enable Custom OpenAI Endpoint", indent=False, layout=wide())
        self.custom_name = w.Text(value=c_name, description="اسم المسار:" if self.ar else "Route Name:", style=style, layout=wide())
        self.custom_base_url = w.Text(value=c_url, placeholder="https://api.example.com/v1", description="رابط البوابة:" if self.ar else "Base URL:", style=style, layout=wide())
        self.custom_keys = w.Password(value=c_keys, description="المفاتيح:" if self.ar else "API Keys:", style=style, layout=wide())
        self.custom_models = w.Text(value=c_models, placeholder="model-a,model-b", description="الموديلات:" if self.ar else "Models:", style=style, layout=wide())
        self.custom_box = w.VBox([self.custom_name, self.custom_base_url, self.custom_keys, self.custom_models], layout=indent_box())

        self.apply_button = w.Button(
            description="💾 تحديث إعدادات الراوتر" if self.ar else "💾 Update Router Configuration",
            button_style="success",
            layout=w.Layout(width="260px", margin="6px 0"),
        )
        self.summary_html = w.HTML("")

        self.root = w.VBox(
            [
                _section_header(w, "🛡️ أمان ومنفذ الراوتر" if self.ar else "🛡️ Router Auth & Port"),
                self.auth_token,
                self.port,
                self.cooldown,
                _section_header(w, "🌐 اختر المزودين المجانيين (تظهر حقول المزود المفعّل فقط)" if self.ar else "🌐 Select Free Providers (Only checked providers are shown)"),
                *provider_widgets,
                self.custom_chk,
                self.custom_box,
                self.apply_button,
                self.summary_html,
            ],
            layout=w.Layout(width="100%", margin="4px 0"),
        )

        self.widgets: Dict[str, Any] = {
            "auth_token": self.auth_token,
            "port": self.port,
            "cooldown": self.cooldown,
            "custom_chk": self.custom_chk,
            "custom_box": self.custom_box,
            "custom_name": self.custom_name,
            "custom_base_url": self.custom_base_url,
            "custom_keys": self.custom_keys,
            "custom_models": self.custom_models,
            "apply_button": self.apply_button,
            "summary_html": self.summary_html,
        }
        for key, item in self.provider_controls.items():
            self.widgets[f"{key}_chk"] = item["chk"]
            self.widgets[f"{key}_box"] = item["box"]
            self.widgets[f"{key}_keys"] = item["keys"]
            self.widgets[f"{key}_models"] = item["models"]

            for ctrl in (item["chk"], item["keys"], item["models"]):
                ctrl.observe(self._on_change, names="value")

        for ctrl in (self.auth_token, self.port, self.cooldown, self.custom_chk, self.custom_name, self.custom_base_url, self.custom_keys, self.custom_models):
            ctrl.observe(self._on_change, names="value")

        self.apply_button.on_click(lambda _b: self.apply())
        self._refresh_visibility()
        self.apply()

    def _refresh_visibility(self) -> None:
        for item in self.provider_controls.values():
            _set_visible(item["box"], bool(item["chk"].value))
        _set_visible(self.custom_box, bool(self.custom_chk.value))

    def _on_change(self, _change: Any = None) -> None:
        for item in self.provider_controls.values():
            kval = item["keys"].value
            item["badge"].value = _secret_badge_html(
                kval,
                item["src"] if kval.strip() else "form",
                self.lang,
                is_csv_keys=True,
                env_names=item.get("key_envs", ()),
            )
        self._refresh_visibility()
        self.apply()

    def apply(self) -> FreeRouterServer:
        """Build/rebuild `router_server` from enabled providers only."""
        old = self.namespace.get("router_server")
        if isinstance(old, FreeRouterServer) and getattr(old, "_httpd", None) is not None:
            old.stop()

        def _get(name: str) -> Tuple[str, str]:
            item = self.provider_controls[name]
            if not item["chk"].value:
                return "", ""
            return item["keys"].value.strip(), item["models"].value.strip()

        g_k, g_m = _get("gemini")
        gr_k, gr_m = _get("groq")
        or_k, or_m = _get("openrouter")
        mi_k, mi_m = _get("mistral")
        ce_k, ce_m = _get("cerebras")

        c_on = bool(self.custom_chk.value)
        server = FreeRouterServer.from_providers(
            gemini_keys=g_k,
            gemini_models=g_m,
            groq_keys=gr_k,
            groq_models=gr_m,
            openrouter_keys=or_k,
            openrouter_models=or_m,
            mistral_keys=mi_k,
            mistral_models=mi_m,
            cerebras_keys=ce_k,
            cerebras_models=ce_m,
            custom_name=self.custom_name.value.strip() if c_on else "",
            custom_base_url=self.custom_base_url.value.strip() if c_on else "",
            custom_keys=self.custom_keys.value.strip() if c_on else "",
            custom_models=self.custom_models.value.strip() if c_on else "",
            auth_token=self.auth_token.value.strip(),
            host="127.0.0.1",
            port=int(self.port.value.strip() or 8787),
            cooldown_seconds=int(self.cooldown.value.strip() or 45),
        )
        self.namespace["router_server"] = server
        self.namespace["ROUTER_AUTH_TOKEN"] = server.auth_token

        routes = server.get_health_payload()["routes"]
        route_desc = ", ".join(f"{r['name']} ({r['key_count']})" for r in routes) or ("لا توجد مفاتيح بعد" if self.ar else "No active keys yet")
        tok_note = (
            f"<br>🔑 <b>توكن الجلسة المولّد تلقائياً:</b> <code>{self._generated_token}</code>"
            if self._generated_token and server.auth_token == self._generated_token
            else ""
        )
        if self.ar:
            self.summary_html.value = (
                f"<div style='padding:8px;border-radius:6px;background:#eff6ff;border:1px solid #93c5fd;color:#1e3a8a;font-size:13px'>"
                f"📡 <b>المسارات المفعّلة ({len(routes)}):</b> {route_desc} | <b>المنفذ:</b> {server.port}{tok_note}</div>"
            )
        else:
            self.summary_html.value = (
                f"<div style='padding:8px;border-radius:6px;background:#eff6ff;border:1px solid #93c5fd;color:#1e3a8a;font-size:13px'>"
                f"📡 <b>Active Routes ({len(routes)}):</b> {route_desc} | <b>Port:</b> {server.port}{tok_note}</div>"
            )
        return server


def launch_repo_config_ui(
    lang: str = "ar",
    namespace: Optional[Dict[str, Any]] = None,
    widgets_mod: Any = None,
    display_fn: Any = None,
    auto_display: bool = True,
    test_on_start: bool = True,
) -> RepoConfigUI:
    """Render and return the interactive repository configuration controller."""
    _, disp = _load_widgets_and_display(widgets_mod=widgets_mod, display_fn=display_fn)
    ui = RepoConfigUI(lang=lang, namespace=namespace, widgets_mod=widgets_mod, test_on_start=test_on_start)
    if auto_display:
        disp(ui.root)
    return ui


def launch_models_config_ui(
    pipeline: Optional[MDAARPipeline] = None,
    lang: str = "ar",
    namespace: Optional[Dict[str, Any]] = None,
    widgets_mod: Any = None,
    display_fn: Any = None,
    auto_display: bool = True,
) -> ModelsConfigUI:
    """Render and return the interactive dual-model configuration controller."""
    _, disp = _load_widgets_and_display(widgets_mod=widgets_mod, display_fn=display_fn)
    ui = ModelsConfigUI(pipeline=pipeline, lang=lang, namespace=namespace, widgets_mod=widgets_mod)
    if auto_display:
        disp(ui.root)
    return ui


def launch_free_router_ui(
    lang: str = "ar",
    namespace: Optional[Dict[str, Any]] = None,
    widgets_mod: Any = None,
    display_fn: Any = None,
    auto_display: bool = True,
) -> FreeRouterConfigUI:
    """Render and return the interactive BYOK multi-provider router controller."""
    _, disp = _load_widgets_and_display(widgets_mod=widgets_mod, display_fn=display_fn)
    ui = FreeRouterConfigUI(lang=lang, namespace=namespace, widgets_mod=widgets_mod)
    if auto_display:
        disp(ui.root)
    return ui
