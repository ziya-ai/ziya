"""
Tests for app.utils.error_handlers.sanitize_client_error.

Pins the client-facing error-message redaction control (ASR: Protect Against
Data Leakage). The function collapses absolute filesystem paths (3+ segments)
to their last two components — hiding machine layout / username — bounds the
message length, and coerces non-string input. Server-side logs are unaffected;
this only sanitizes what is returned to the client.

Each assertion here was verified against the real implementation before being
written (path threshold, trailing-slash handling, URL non-mangling, the
truncation boundary), so these lock in observed behavior, not assumptions.
"""

from app.utils.error_handlers import sanitize_client_error


# =============================================================================
# Absolute-path collapsing
# =============================================================================

class TestPathCollapsing:
    def test_three_segment_path_collapsed_to_last_two(self):
        out = sanitize_client_error("boom at /Users/dcohn/workplace/ziya/app.py")
        assert "/Users/dcohn" not in out
        assert ".../ziya/app.py" in out

    def test_two_segment_path_not_collapsed(self):
        # Below the {3,} threshold — nothing sensitive enough to redact.
        assert sanitize_client_error("at /a/b here") == "at /a/b here"

    def test_exactly_three_segments_collapses(self):
        assert sanitize_client_error("at /a/b/c here") == "at .../b/c here"

    def test_trailing_slash_dropped(self):
        assert sanitize_client_error("dir /x/y/z/ end") == "dir .../y/z end"

    def test_multiple_paths_each_collapsed(self):
        assert sanitize_client_error("/a/b/c and /d/e/f") == ".../b/c and .../e/f"

    def test_segments_with_dots_dashes_plus(self):
        # Real paths carry version dirs / extensions; these must survive.
        out = sanitize_client_error("/opt/ziya-1.2/app+x/mod.py")
        assert out == ".../app+x/mod.py"

    def test_username_not_disclosed(self):
        # The core threat: a home-dir path leaking the OS username.
        out = sanitize_client_error(
            "FileNotFoundError: /home/jsmith/.ziya/keys/ale_key")
        assert "jsmith" not in out


# =============================================================================
# URL non-mangling (negative lookbehind must spare scheme://host/path)
# =============================================================================

class TestUrlsPreserved:
    def test_https_url_untouched(self):
        url = "see https://code.amazon.com/packages/Ziya/blobs/x/y/z"
        assert sanitize_client_error(url) == url

    def test_http_url_untouched(self):
        url = "http://host.example.com/a/b/c/d"
        assert sanitize_client_error(url) == url


# =============================================================================
# Length bounding
# =============================================================================

class TestTruncation:
    def test_under_limit_unchanged(self):
        msg = "a" * 100
        assert sanitize_client_error(msg) == msg

    def test_at_limit_not_truncated(self):
        # len == max_len is NOT over the limit — no ellipsis.
        msg = "a" * 300
        out = sanitize_client_error(msg)
        assert out == msg
        assert "…" not in out

    def test_over_limit_truncated_with_ellipsis(self):
        out = sanitize_client_error("a" * 301)
        assert out.endswith("…")
        # 300 kept chars + 1 ellipsis char.
        assert len(out) == 301

    def test_custom_max_len(self):
        out = sanitize_client_error("a" * 250, max_len=200)
        assert out.endswith("…")
        assert len(out) == 201

    def test_custom_max_len_under_limit(self):
        assert sanitize_client_error("short", max_len=200) == "short"


# =============================================================================
# Non-string coercion
# =============================================================================

class TestNonStringInput:
    def test_none_coerced(self):
        assert sanitize_client_error(None) == "None"

    def test_exception_object_coerced_and_redacted(self):
        out = sanitize_client_error(ValueError("/a/b/c/d failed"))
        assert out == ".../c/d failed"

    def test_integer_coerced(self):
        assert sanitize_client_error(500) == "500"


# =============================================================================
# Combined — redaction and truncation together
# =============================================================================

class TestCombined:
    def test_path_collapsed_then_truncated(self):
        # A long message containing a path: path is collapsed, then the whole
        # thing is bounded. Both transforms apply, redaction first.
        msg = "error at /Users/dcohn/workplace/ziya/mod.py — " + ("x" * 400)
        out = sanitize_client_error(msg)
        assert "/Users/dcohn" not in out
        assert out.endswith("…")
        assert len(out) == 301
