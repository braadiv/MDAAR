"""Tests for SecretSanitizer, IgnoreMatcher, RepoScanner (100% file coverage), and SemanticFileChunker."""

from pathlib import Path
import pytest

from mdaar.analyzers import RepoScanner, SemanticFileChunker
from mdaar.security import IgnoreMatcher, SecretSanitizer


def test_secret_sanitizer_redacts_tokens_and_service_account():
    raw_env = (
        "GEMINI_API_KEY=AIzaSyDummyKey123456789012345678901234567\n"
        "GITHUB_TOKEN=ghp_1234567890abcdefghij1234567890\n"
        "DB_DSN=mysql://root:SuperSecretPass99@127.0.0.1:3306/appdb\n"
    )
    cleaned = SecretSanitizer.sanitize(raw_env, ".env")
    assert "AIzaSy" not in cleaned
    assert "ghp_123" not in cleaned
    assert "SuperSecretPass99" not in cleaned
    assert "[REDACTED_GOOGLE_API_KEY]" in cleaned
    assert "[REDACTED_GITHUB_TOKEN]" in cleaned
    assert "[REDACTED_DSN_PASS]" in cleaned

    sa_json = (
        '{"type": "service_account", "project_id": "demo", '
        '"private_key_id": "key123", "private_key": "-----BEGIN PRIVATE KEY-----\\nABC\\n-----END PRIVATE KEY-----", '
        '"client_id": "12345"}'
    )
    cleaned_sa = SecretSanitizer.sanitize(sa_json, "config/firebase-sa.json")
    assert "key123" not in cleaned_sa
    assert "BEGIN PRIVATE KEY" not in cleaned_sa
    assert "[REDACTED_SERVICE_ACCOUNT_SECRET]" in cleaned_sa


def test_ignore_matcher_respects_gitignore_and_preserves_dotfiles(tmp_path):
    (tmp_path / ".gitignore").write_text("*.log\nsecret_folder/\n", encoding="utf-8")
    matcher = IgnoreMatcher(tmp_path)

    assert matcher.should_ignore("vendor/yiisoft/yii2/BaseYii.php") is True
    assert matcher.should_ignore("node_modules/lodash/index.js") is True
    assert matcher.should_ignore("assets/logo.png") is True
    assert matcher.should_ignore("debug.log") is True
    assert matcher.should_ignore("secret_folder/notes.txt") is True

    # Legitimate dotfiles and source files MUST NOT be ignored or mangled
    assert matcher.should_ignore(".github/workflows/ci.yml") is False
    assert matcher.should_ignore(".env.example") is False
    assert matcher.should_ignore("controllers/OrderController.php") is False


def test_repo_scanner_no_40_file_cap_and_lossless_chunking(tmp_path):
    # Create 55 real source files (> old 40-file cap) + vendor noise
    src = tmp_path / "src"
    src.mkdir()
    for i in range(55):
        (src / f"module_{i:02d}.py").write_text(f"def func_{i}():\n    return {i}\n", encoding="utf-8")

    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "ignored.php").write_text("<?php echo 'vendor';", encoding="utf-8")

    # Dotfile preservation check
    gh = tmp_path / ".github" / "workflows"
    gh.mkdir(parents=True)
    (gh / "deploy.yml").write_text("name: CI\non: [push]\n", encoding="utf-8")

    # Oversized single file (~300 lines) chunked cleanly with zero skipped lines
    big_lines = [f"def big_handler_{i}(x):\n    return x + {i}\n" for i in range(180)]
    big_text = "".join(big_lines)
    (src / "large_service.py").write_text(big_text, encoding="utf-8")

    scanner = RepoScanner(tmp_path, max_chunk_chars=1200)
    res = scanner.scan()

    scanned_paths = {sf.rel_path for sf in res.files}
    assert len(res.files) == 57  # 55 module files + large_service.py + .github/workflows/deploy.yml
    assert "vendor/ignored.php" not in scanned_paths
    assert ".github/workflows/deploy.yml" in scanned_paths

    # Verify lossless chunk reconstruction equals original content line-for-line
    large_sf = next(sf for sf in res.files if sf.rel_path == "src/large_service.py")
    assert len(large_sf.chunks) >= 4
    reconstructed = "".join(ch.content for ch in large_sf.chunks)
    assert reconstructed == big_text

