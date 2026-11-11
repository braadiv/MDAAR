"""
Repository Scanner and Semantic Chunker for MDAAR v2.0.0.
Reads 100% of non-ignored repository files without arbitrary count or length caps,
redacts secrets immediately, classifies languages/layers, and splits oversized
files cleanly at semantic boundaries for Map-Reduce LLM inspection.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import subprocess
from collections import Counter

from mdaar.security.sanitizer import IgnoreMatcher, SecretSanitizer


EXTENSION_LANGUAGE_MAP = {
    ".php": "php",
    ".phtml": "php",
    ".inc": "php",
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".sql": "sql",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".cs": "csharp",
    ".csproj": "csharp",
    ".sln": "csharp",
    ".go": "go",
    ".mod": "go",
    ".rb": "ruby",
    ".rake": "ruby",
    ".rs": "rust",
    ".dart": "dart",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".sh": "shell",
    ".bash": "shell",
    ".ps1": "powershell",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".xml": "xml",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "config",
    ".conf": "config",
    ".env": "config",
    ".md": "markdown",
    ".rst": "markdown",
    ".txt": "text",
    ".html": "html",
    ".htm": "html",
    ".vue": "vue",
    ".svelte": "svelte",
    ".css": "css",
    ".scss": "css",
    ".sass": "css",
    ".less": "css",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".proto": "protobuf",
}

SEMANTIC_BOUNDARY_RE = re.compile(
    r"^\s*(?:"
    r"(?:export\s+)?(?:default\s+)?(?:abstract\s+|final\s+)?(?:async\s+)?class\s+"
    r"|(?:export\s+)?(?:async\s+)?function\s+"
    r"|(?:public|protected|private|static|final|abstract)\s+(?:static\s+)?function\s+"
    r"|(?:async\s+)?def\s+"
    r"|func\s+"
    r"|(?:pub\s+)?(?:struct|enum|trait|impl|fn)\s+"
    r"|(?:public|internal|private|protected)\s+(?:partial\s+|sealed\s+|abstract\s+)?(?:class|interface|record|struct|enum)\s+"
    r"|CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW|PROCEDURE|FUNCTION|TRIGGER|INDEX)\s+"
    r"|ALTER\s+TABLE\s+"
    r"|#{1,3}\s+"
    r")"
)


@dataclass
class GitMetadata:
    """Git commit and branch metadata extracted from the target repository."""

    is_git_repo: bool = False
    commit_hash: str = "N/A"
    short_hash: str = "N/A"
    commit_subject: str = "N/A"
    commit_date: str = "N/A"
    branch: str = "N/A"
    remote_url: str = "N/A"

    @property
    def trace_stamp(self):
        return f"{self.commit_hash} {self.commit_subject} {self.commit_date}".strip()


@dataclass
class FileChunk:
    """A lossless slice of a repository file aligned to line/semantic boundaries."""

    rel_path: str
    chunk_index: int
    total_chunks: int
    start_line: int
    end_line: int
    content: str


@dataclass
class ScannedFile:
    """Complete sanitized representation of a single non-ignored source file."""

    rel_path: str
    abs_path: Path
    extension: str
    language: str
    layer: str
    size_bytes: int
    line_count: int
    content: str
    chunks: list = field(default_factory=list)


@dataclass
class ScanResult:
    """Aggregate result of a 100% full-repository scan."""

    repo_dir: Path
    git_meta: GitMetadata
    files: list = field(default_factory=list)
    total_bytes: int = 0
    total_lines: int = 0
    language_counts: dict = field(default_factory=dict)
    layer_counts: dict = field(default_factory=dict)
    skipped_binaries: list = field(default_factory=list)
    skipped_ignored_count: int = 0

    def iter_chunks(self):
        """Yield (ScannedFile, FileChunk) pairs across the entire codebase."""
        for sf in self.files:
            for ch in sf.chunks:
                yield sf, ch

    def summary_dict(self):
        return {
            "total_files": len(self.files),
            "total_bytes": self.total_bytes,
            "total_lines": self.total_lines,
            "language_counts": dict(self.language_counts),
            "layer_counts": dict(self.layer_counts),
            "skipped_binaries_count": len(self.skipped_binaries),
            "skipped_ignored_count": self.skipped_ignored_count,
            "git_commit": self.git_meta.commit_hash,
            "git_branch": self.git_meta.branch,
            "git_subject": self.git_meta.commit_subject,
            "git_date": self.git_meta.commit_date,
        }


class SemanticFileChunker:
    """Splits large files into lossless line-aligned chunks at semantic boundaries."""

    def __init__(self, max_chars=12000):
        self.max_chars = max(1000, int(max_chars))

    def chunk_file(self, rel_path, content):
        lines = content.splitlines(keepends=True)
        total_lines = len(lines) if lines else 1

        if len(content) <= self.max_chars:
            return [
                FileChunk(
                    rel_path=rel_path,
                    chunk_index=1,
                    total_chunks=1,
                    start_line=1,
                    end_line=total_lines,
                    content=content,
                )
            ]

        chunks_raw = []
        current_lines = []
        current_chars = 0
        chunk_start_line = 1
        last_semantic_idx = None

        for idx, line in enumerate(lines):
            line_num = idx + 1
            line_len = len(line)

            # Handle extreme single-line strings gracefully without losing characters
            if line_len > self.max_chars:
                if current_lines:
                    chunks_raw.append((chunk_start_line, line_num - 1, "".join(current_lines)))
                    current_lines = []
                    current_chars = 0
                    last_semantic_idx = None
                for offset in range(0, line_len, self.max_chars):
                    chunks_raw.append((line_num, line_num, line[offset : offset + self.max_chars]))
                chunk_start_line = line_num + 1
                continue

            if SEMANTIC_BOUNDARY_RE.match(line) and current_lines:
                last_semantic_idx = len(current_lines)

            if current_chars + line_len > self.max_chars and current_lines:
                # Prefer cutting at the most recent semantic boundary if it preserves at least 40% of chunk
                if last_semantic_idx and sum(len(x) for x in current_lines[:last_semantic_idx]) >= self.max_chars * 0.4:
                    head = current_lines[:last_semantic_idx]
                    tail = current_lines[last_semantic_idx:]
                    end_l = chunk_start_line + len(head) - 1
                    chunks_raw.append((chunk_start_line, end_l, "".join(head)))
                    chunk_start_line = end_l + 1
                    current_lines = tail
                    current_chars = sum(len(x) for x in current_lines)
                    last_semantic_idx = None
                else:
                    end_l = chunk_start_line + len(current_lines) - 1
                    chunks_raw.append((chunk_start_line, end_l, "".join(current_lines)))
                    chunk_start_line = end_l + 1
                    current_lines = []
                    current_chars = 0
                    last_semantic_idx = None

            current_lines.append(line)
            current_chars += line_len

        if current_lines:
            end_l = chunk_start_line + len(current_lines) - 1
            chunks_raw.append((chunk_start_line, end_l, "".join(current_lines)))

        total = len(chunks_raw)
        return [
            FileChunk(
                rel_path=rel_path,
                chunk_index=i + 1,
                total_chunks=total,
                start_line=s,
                end_line=e,
                content=txt,
            )
            for i, (s, e, txt) in enumerate(chunks_raw)
        ]


def detect_language(rel_path):
    """Identify the programming/markup language from filename or extension."""
    name_lower = Path(rel_path).name.lower()
    if name_lower == "dockerfile" or name_lower.startswith("dockerfile."):
        return "docker"
    if name_lower in ("docker-compose.yml", "docker-compose.yaml", "makefile", "rakefile", "gemfile", "procfile"):
        return "devops"
    if name_lower.startswith(".env"):
        return "config"
    if name_lower.endswith(".blade.php"):
        return "php"
    ext = Path(name_lower).suffix
    return EXTENSION_LANGUAGE_MAP.get(ext, "text")


def classify_layer(rel_path, language):
    """Classify a file into its primary architectural layer."""
    p = "/" + str(rel_path).replace("\\", "/").lower()
    name = Path(p).name

    if any(tok in p for tok in ("/test/", "/tests/", "/spec/", "__tests__", ".test.", ".spec.")):
        return "tests"
    if any(tok in p for tok in ("/.github/", "/ci/", "dockerfile", "docker-compose", "helm/", "k8s/", "terraform/", "/deploy/")):
        return "devops"
    if language == "sql" or any(tok in p for tok in ("/migrations/", "/migration/", "/schema/", "/seeds/", "/fixtures/", "alembic/")):
        return "database"
    if "postman_collection.json" in name or any(tok in name for tok in ("openapi", "swagger")) or any(
        tok in p for tok in ("/api/", "/routes/", "/router/", "/routers/", "/endpoints/", "/graphql/")
    ):
        return "api"
    if any(tok in p for tok in ("/controller", "/controllers/", "/handlers/", "/handler/")):
        return "controller"
    if any(tok in p for tok in ("/model", "/models/", "/entities/", "/entity/", "/schemas/", "/dto/")):
        return "domain_model"
    if any(tok in p for tok in ("/service", "/services/", "/repository", "/repositories/", "/usecase", "/domain/", "/helpers/", "/components/")):
        return "service"
    if any(tok in p for tok in ("/auth/", "/rbac/", "/acl/", "/policy/", "/policies/", "/middleware/", "/guards/", "/permissions/")):
        return "security_auth"
    if any(tok in p for tok in ("/views/", "/view/", "/templates/", "/pages/", "/components/", "/assets/", "/frontend/", "/widgets/")):
        return "view_ui"
    if any(tok in p for tok in ("/console/", "/commands/", "/cmd/", "/jobs/", "/workers/", "/queues/", "/cron/", "/tasks/")):
        return "jobs_cli"
    if any(tok in p for tok in ("/config/", "/configs/", "/settings/")) or name in (
        "composer.json", "package.json", "requirements.txt", "pyproject.toml", "setup.py",
        "pom.xml", "build.gradle", "go.mod", "cargo.toml", "pubspec.yaml", "params.php", "web.php",
    ) or language in ("yaml", "toml", "ini", "config"):
        return "config"
    if language == "markdown" or any(tok in p for tok in ("/docs/", "/doc/")):
        return "docs"
    return "other"


class RepoScanner:
    """Walks a repository completely (100% file coverage, 0 file caps) with safety sanitization."""

    def __init__(self, repo_dir, max_chunk_chars=12000):
        self.repo_dir = Path(repo_dir)
        self.ignore_matcher = IgnoreMatcher(self.repo_dir)
        self.chunker = SemanticFileChunker(max_chars=max_chunk_chars)

    def _run_git(self, args):
        try:
            res = subprocess.run(
                ["git"] + args,
                cwd=str(self.repo_dir),
                capture_output=True,
                timeout=15,
            )
            if res.returncode == 0:
                return res.stdout
        except Exception:
            pass
        return None

    def get_git_metadata(self):
        meta = GitMetadata()
        if not (self.repo_dir / ".git").exists():
            return meta

        log_raw = self._run_git(["log", "-1", "--format=%H\n%h\n%s\n%ai"])
        if not log_raw:
            return meta

        lines = log_raw.decode("utf-8", errors="replace").splitlines()
        meta.is_git_repo = True
        if len(lines) >= 1:
            meta.commit_hash = lines[0].strip()
        if len(lines) >= 2:
            meta.short_hash = lines[1].strip()
        if len(lines) >= 3:
            meta.commit_subject = lines[2].strip()
        if len(lines) >= 4:
            meta.commit_date = lines[3].strip()

        branch_raw = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        if branch_raw:
            meta.branch = branch_raw.decode("utf-8", errors="replace").strip()

        remote_raw = self._run_git(["config", "--get", "remote.origin.url"])
        if remote_raw:
            url = remote_raw.decode("utf-8", errors="replace").strip()
            # Never leak embedded Git PAT tokens in logs or generated docs
            meta.remote_url = re.sub(r"(https?://)[^/@]+@", r"\1[REDACTED_CREDENTIAL]@", url)

        return meta

    def list_candidate_paths(self):
        """Return sorted relative POSIX file paths via git ls-files or pruned os.walk."""
        if (self.repo_dir / ".git").exists():
            raw = self._run_git(["ls-files", "--cached", "--others", "--exclude-standard", "-z"])
            if raw is not None:
                paths = [p for p in raw.decode("utf-8", errors="replace").split("\x00") if p]
                if paths:
                    return sorted(set(p.replace("\\", "/") for p in paths))

        discovered = []
        for root, dirs, files in os.walk(self.repo_dir):
            # Prune known noisy/vendor directories in-place before descending
            dirs[:] = sorted(
                d for d in dirs if d not in IgnoreMatcher.DEFAULT_IGNORE_DIRS and d != ".git"
            )
            rel_root = Path(root).relative_to(self.repo_dir)
            for fname in sorted(files):
                rel = fname if rel_root == Path(".") else (rel_root / fname).as_posix()
                discovered.append(rel)
        return discovered

    @staticmethod
    def _read_text_safely(abs_path):
        """Read bytes safely; return (text_or_none, is_binary)."""
        try:
            raw_bytes = abs_path.read_bytes()
        except Exception:
            return None, True

        if b"\x00" in raw_bytes[:8192]:
            return None, True

        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw_bytes.decode("utf-8", errors="replace")
            except Exception:
                return None, True

        return text.replace("\r\n", "\n").replace("\r", "\n"), False

    def scan(self):
        """Scan every non-ignored file in the repository with zero file-count or char caps."""
        git_meta = self.get_git_metadata()
        candidates = self.list_candidate_paths()

        scanned_files = []
        skipped_binaries = []
        skipped_ignored = 0
        lang_counter = Counter()
        layer_counter = Counter()
        total_bytes = 0
        total_lines = 0

        for rel in candidates:
            if self.ignore_matcher.should_ignore(rel):
                skipped_ignored += 1
                continue

            abs_path = self.repo_dir / rel
            if not abs_path.is_file():
                continue

            raw_text, is_bin = self._read_text_safely(abs_path)
            if is_bin or raw_text is None:
                skipped_binaries.append(rel)
                continue

            sanitized = SecretSanitizer.sanitize(raw_text, rel_path=rel)
            ext = Path(rel).suffix.lower()
            language = detect_language(rel)
            layer = classify_layer(rel, language)
            line_count = len(sanitized.splitlines()) if sanitized else 0
            size_bytes = len(sanitized.encode("utf-8", errors="ignore"))
            chunks = self.chunker.chunk_file(rel, sanitized)

            scanned_files.append(
                ScannedFile(
                    rel_path=rel,
                    abs_path=abs_path,
                    extension=ext,
                    language=language,
                    layer=layer,
                    size_bytes=size_bytes,
                    line_count=line_count,
                    content=sanitized,
                    chunks=chunks,
                )
            )
            lang_counter[language] += 1
            layer_counter[layer] += 1
            total_bytes += size_bytes
            total_lines += line_count

        return ScanResult(
            repo_dir=self.repo_dir,
            git_meta=git_meta,
            files=scanned_files,
            total_bytes=total_bytes,
            total_lines=total_lines,
            language_counts=dict(lang_counter.most_common()),
            layer_counts=dict(layer_counter.most_common()),
            skipped_binaries=skipped_binaries,
            skipped_ignored_count=skipped_ignored,
        )
