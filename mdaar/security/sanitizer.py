"""
Security and File-Filtering Utilities for MDAAR.
Handles secret redaction before sending code to LLMs or saving docs,
and enforces .gitignore + vendor/binary/lockfile exclusions.
"""

import fnmatch
import json
import re
from pathlib import Path


class IgnoreMatcher:
    """Matches repository paths against .gitignore and MDAAR safety exclusions."""

    DEFAULT_IGNORE_DIRS = {
        ".git",
        ".svn",
        ".hg",
        ".idea",
        ".vscode",
        ".agents",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "vendor",
        "bower_components",
        "dist",
        "build",
        "target",
        "out",
        "runtime",
        "logs",
        "tmp",
        "temp",
        "uploads",
        ".graphify",
        ".next",
        ".nuxt",
        "coverage",
    }

    BINARY_OR_NOISE_EXTENSIONS = {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp", ".tiff",
        ".woff", ".woff2", ".ttf", ".eot", ".otf",
        ".mp3", ".mp4", ".avi", ".mov", ".flv", ".wmv", ".wav",
        ".zip", ".tar", ".gz", ".rar", ".7z", ".deb", ".rpm", ".jar", ".war",
        ".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".pyc", ".pyo", ".class",
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".map", ".min.js", ".min.css", ".lock", ".sqlite", ".db", ".ser", ".sig",
    }

    NOISE_FILENAMES = {
        "composer.lock",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "cargo.lock",
        "gemfile.lock",
        "pubspec.lock",
        "thumbs.db",
        ".ds_store",
    }

    def __init__(self, repo_dir):
        self.repo_dir = Path(repo_dir)
        self.gitignore_patterns = self._load_gitignore()

    def _load_gitignore(self):
        gi_path = self.repo_dir / ".gitignore"
        patterns = []
        if gi_path.exists():
            try:
                for raw in gi_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#") or line.startswith("!"):
                        continue
                    patterns.append(line.lstrip("/"))
            except Exception:
                pass
        return patterns

    def should_ignore(self, rel_path):
        """Return True if the relative file path should be excluded from analysis."""
        rel_str = str(rel_path).replace("\\", "/")
        while rel_str.startswith("./"):
            rel_str = rel_str[2:]
        rel_str = rel_str.lstrip("/")
        if not rel_str:
            return True

        parts = rel_str.split("/")
        # Exclude any path traversing known noisy/vendor/cache directories
        for part in parts[:-1]:
            if part in self.DEFAULT_IGNORE_DIRS or part == ".git":
                return True

        filename_lower = parts[-1].lower()
        if filename_lower in self.NOISE_FILENAMES:
            return True

        # Exclude minified assets & binary/lock suffixes
        for suffix in (".min.js", ".min.css", ".bundle.js", ".chunk.js"):
            if filename_lower.endswith(suffix):
                return True

        ext = Path(filename_lower).suffix
        if ext in self.BINARY_OR_NOISE_EXTENSIONS:
            return True

        # Check .gitignore patterns
        for pat in self.gitignore_patterns:
            clean_pat = pat.rstrip("/")
            if pat.endswith("/"):
                if clean_pat in parts[:-1] or rel_str.startswith(clean_pat + "/"):
                    return True
            if fnmatch.fnmatch(rel_str, pat) or fnmatch.fnmatch(filename_lower, pat):
                return True
            if "/" in clean_pat and fnmatch.fnmatch(rel_str, clean_pat + "/*"):
                return True

        return False


class SecretSanitizer:
    """Redacts API keys, DB credentials, private keys, and tokens from source snippets."""

    TOKEN_PATTERNS = [
        # Google / Gemini / Firebase API Keys
        (re.compile(r"AIza[0-9A-Za-z\-_]{35}"), "[REDACTED_GOOGLE_API_KEY]"),
        # GitHub Tokens
        (re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
        (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_PAT]"),
        # OpenAI / Stripe / Slack / AWS
        (re.compile(r"sk-(?:proj-|live-|test-)?[A-Za-z0-9\-_]{20,}"), "[REDACTED_SECRET_KEY]"),
        (re.compile(r"(?:rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"), "[REDACTED_PAYMENT_KEY]"),
        (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_KEY]"),
        (re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}"), "[REDACTED_SLACK_TOKEN]"),
        # PEM Private Keys / Certificates
        (
            re.compile(
                r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
                re.MULTILINE,
            ),
            "[REDACTED_PRIVATE_KEY_BLOCK]",
        ),
        # Bearer / JWT literals
        (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[REDACTED_JWT]"),
    ]

    # Key=value / JSON / PHP array assignments containing sensitive names
    SENSITIVE_KEY_NAMES = (
        r"(?:password|passwd|pwd|db_pass|db_password|secret|secret_key|app_secret|"
        r"api_key|apikey|access_token|refresh_token|auth_token|bot_token|hf_token|"
        r"private_key|private_key_id|cookieValidationKey|encryptionKey|jwt_secret|"
        r"client_secret|smtp_pass|mail_password|fcm_server_key|firebase_key)"
    )

    ASSIGNMENT_PATTERN = re.compile(
        rf"(['\"]?{SENSITIVE_KEY_NAMES}['\"]?\s*(?:=>|=|:)\s*['\"])(?!\[REDACTED_)([^'\"]{{4,}})(['\"])",
        re.IGNORECASE,
    )

    ENV_LINE_PATTERN = re.compile(
        rf"^(\s*(?:export\s+)?{SENSITIVE_KEY_NAMES}\s*=\s*)(?!\[REDACTED_)([^\s#\r\n]+)(.*)$",
        re.IGNORECASE | re.MULTILINE,
    )

    DSN_PASSWORD_PATTERN = re.compile(
        r"((?:mysql|pgsql|postgres|postgresql|mongodb|redis|amqp|sqlsrv)://[^:/\s]+:)([^@/\s]+)(@)",
        re.IGNORECASE,
    )

    @classmethod
    def sanitize(cls, text, rel_path=""):
        """Return sanitized text with secrets replaced by [REDACTED_*] placeholders."""
        if not text:
            return text

        # Special safeguard for GCP/Firebase service account JSON files
        if str(rel_path).lower().endswith(".json") and '"private_key"' in text and '"service_account"' in text:
            try:
                data = json.loads(text)
                if isinstance(data, dict) and data.get("type") == "service_account":
                    for k in ("private_key_id", "private_key", "client_id"):
                        if k in data:
                            data[k] = "[REDACTED_SERVICE_ACCOUNT_SECRET]"
                    return json.dumps(data, indent=2, ensure_ascii=False)
            except Exception:
                pass

        cleaned = text
        for pattern, replacement in cls.TOKEN_PATTERNS:
            cleaned = pattern.sub(replacement, cleaned)

        cleaned = cls.DSN_PASSWORD_PATTERN.sub(r"\1[REDACTED_DSN_PASS]\3", cleaned)
        cleaned = cls.ASSIGNMENT_PATTERN.sub(r"\1[REDACTED_SECRET]\3", cleaned)
        cleaned = cls.ENV_LINE_PATTERN.sub(r"\1[REDACTED_ENV_SECRET]\3", cleaned)
        return cleaned
