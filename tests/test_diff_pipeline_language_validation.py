"""
Tests for the post-patch language-validation stage in
``app.utils.diff_utils.pipeline.pipeline_manager.apply_diff_pipeline``.

The pipeline now re-reads the post-patch file and runs the matching
``LanguageHandler.verify_changes``.  When validation fails the file
must be rolled back to its original content and every previously
succeeded hunk must be demoted to ``failed`` with stage
``language_validation`` so the upstream apply prompt is suppressed.

These tests assert that contract directly against the real pipeline
(no mocks of the language handlers themselves).
"""

import os
from pathlib import Path

import pytest

from app.utils.diff_utils.pipeline import apply_diff_pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ORIGINAL_PY = "def add(a, b):\n    return a + b\n"


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


@pytest.fixture(autouse=True)
def _set_codebase_dir(tmp_path, monkeypatch):
    """The pipeline reads ZIYA_USER_CODEBASE_DIR for some lookups; mirror
    the pattern used by ``tests/test_pipeline_manager_fixes.py``."""
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    monkeypatch.delenv("ZIYA_FORCE_DRY_RUN", raising=False)


# ---------------------------------------------------------------------------
# 1. Syntax-breaking diff is caught and rolled back
# ---------------------------------------------------------------------------

def test_syntax_breaking_python_diff_is_rolled_back(tmp_path):
    """A diff that applies cleanly but produces invalid Python must be
    rejected by the language-validation stage, the file must be rolled
    back to its original bytes, and every succeeded hunk must be demoted
    to ``failed`` with stage ``language_validation``."""
    subject = _write(tmp_path, "broken.py", ORIGINAL_PY)

    # Structurally clean diff — the @@ header and context match the file
    # exactly — but the replacement line introduces an unclosed paren.
    bad_diff = (
        "diff --git a/broken.py b/broken.py\n"
        "--- a/broken.py\n"
        "+++ b/broken.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n"
        "-    return a + b\n"
        "+    return (a + b\n"
    )

    result = apply_diff_pipeline(bad_diff, str(subject))

    # File was rolled back.
    assert subject.read_text() == ORIGINAL_PY, (
        "File should be rolled back to original content after "
        "language-validation rejection"
    )

    # Top-level status reflects rejection.
    assert isinstance(result, dict)
    assert result.get("status") == "error", (
        f"Expected status='error' after language-validation rollback, "
        f"got {result.get('status')!r}"
    )
    assert result.get("changes_written") is False
    assert result.get("succeeded") == [], (
        f"Succeeded hunks must be cleared on rollback, got "
        f"{result.get('succeeded')!r}"
    )
    assert result.get("failed"), "failed hunks list must be non-empty"

    # The pipeline-level error message names the stage.
    err = (result.get("error") or "").lower()
    assert "language validation" in err, (
        f"Pipeline error should mention 'Language validation', got {err!r}"
    )

    # Per-hunk error_details record the stage for downstream consumers.
    hunk_statuses = result.get("hunk_statuses") or {}
    assert hunk_statuses, "hunk_statuses must be populated"
    assert any(
        (h.get("error_details") or {}).get("stage") == "language_validation"
        for h in hunk_statuses.values()
    ), (
        f"At least one hunk should record stage='language_validation'; "
        f"hunk_statuses={hunk_statuses!r}"
    )


# ---------------------------------------------------------------------------
# 2. Syntactically valid diff still succeeds (regression)
# ---------------------------------------------------------------------------

def test_valid_python_diff_still_succeeds(tmp_path):
    """A clean diff that produces valid Python must continue to apply
    cleanly — the new validation stage must not reject good patches."""
    subject = _write(tmp_path, "good.py", ORIGINAL_PY)

    good_diff = (
        "diff --git a/good.py b/good.py\n"
        "--- a/good.py\n"
        "+++ b/good.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n"
        "-    return a + b\n"
        "+    return a - b\n"
    )

    result = apply_diff_pipeline(good_diff, str(subject))

    assert isinstance(result, dict)
    assert result.get("status") == "success", (
        f"Expected status='success' for a clean Python patch, "
        f"got {result.get('status')!r} (error={result.get('error')!r})"
    )
    assert result.get("changes_written") is True
    assert result.get("succeeded"), "succeeded hunks must be non-empty"
    assert result.get("failed") in ([], None), (
        f"failed hunks should be empty, got {result.get('failed')!r}"
    )

    # File on disk reflects the patched content.
    assert subject.read_text() == "def add(a, b):\n    return a - b\n"


# ---------------------------------------------------------------------------
# 3. Non-language file (.txt) does not crash and applies cleanly
# ---------------------------------------------------------------------------

def test_plain_text_file_skips_language_validation(tmp_path):
    """For files without a language-specific handler (e.g. .txt) the
    generic handler must be used and the pipeline must not crash on
    the new validation block."""
    original = "alpha\nbeta\n"
    subject = _write(tmp_path, "notes.txt", original)

    txt_diff = (
        "diff --git a/notes.txt b/notes.txt\n"
        "--- a/notes.txt\n"
        "+++ b/notes.txt\n"
        "@@ -1,2 +1,2 @@\n"
        " alpha\n"
        "-beta\n"
        "+gamma\n"
    )

    result = apply_diff_pipeline(txt_diff, str(subject))

    assert isinstance(result, dict)
    assert result.get("status") == "success", (
        f"Expected status='success' for a clean text patch, "
        f"got {result.get('status')!r} (error={result.get('error')!r})"
    )
    assert result.get("changes_written") is True
    assert subject.read_text() == "alpha\ngamma\n"
