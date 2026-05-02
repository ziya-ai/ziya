"""
Regression test: malformed-hunk warnings are emitted at most once per unique
hunk even when the same diff is re-parsed by multiple apply strategies
(strict -> shifted -> fuzzy).

Before the fix: each strategy re-ran `parse_unified_diff_exact_plus` and each
call re-logged the same "Malformed hunk #N: header declares ..." warning,
producing 3x duplicate noise per apply.

After the fix: `parse_unified_diff_exact_plus` keeps a module-level set of
already-warned hunk keys on the logger and skips repeat warnings.

The project uses a custom ModeAwareLogger with propagate=False, so pytest's
caplog can't observe emissions directly. We capture by wrapping the logger's
warning() method with a counter instead.
"""

import pytest

from app.utils.diff_utils.parsing.diff_parser import parse_unified_diff_exact_plus
from app.utils.diff_utils.parsing import diff_parser as diff_parser_module


# A diff whose hunk header (-10,+12) disagrees with its body (-12,+14)
# — exactly the shape that triggered the original 3x warning.
MALFORMED_DIFF = """diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -1,3 +1,3 @@
 line_a
 line_b
 line_c
-drop1
-drop2
-drop3
+add1
+add2
+add3
+add4
+add5
"""
# declared: old=3, new=3
# body:     old=3 context + 3 removals = 6, new=3 context + 5 additions = 8
# deltas of 3 and 5 both exceed the tolerance of 1, so the malformed
# warning path fires.


class _WarnCapture:
    """Wraps the parser's logger.warning to count 'Malformed hunk' emissions
    while preserving original behavior."""

    def __init__(self, logger):
        self._logger = logger
        self._orig = logger.warning
        self.malformed_count = 0

    def __enter__(self):
        def _wrapped(msg, *args, **kwargs):
            try:
                formatted = msg % args if args else msg
            except Exception:
                formatted = str(msg)
            if "Malformed hunk" in str(formatted):
                self.malformed_count += 1
            return self._orig(msg, *args, **kwargs)
        self._logger.warning = _wrapped
        return self

    def __exit__(self, *exc):
        self._logger.warning = self._orig


@pytest.fixture(autouse=True)
def _reset_warn_cache():
    """The dedupe cache is stamped on the parser's module logger. Clear it
    between tests so each test sees a clean slate."""
    logger = diff_parser_module.logger
    if hasattr(logger, "_malformed_hunk_warned"):
        delattr(logger, "_malformed_hunk_warned")
    yield
    if hasattr(logger, "_malformed_hunk_warned"):
        delattr(logger, "_malformed_hunk_warned")


def test_malformed_warning_emitted_once_on_single_parse():
    logger = diff_parser_module.logger
    with _WarnCapture(logger) as cap:
        hunks = parse_unified_diff_exact_plus(MALFORMED_DIFF, "x.py")
    assert hunks, "parser should still yield the hunk even if header is malformed"
    assert cap.malformed_count == 1, (
        f"expected exactly 1 malformed warning, got {cap.malformed_count}"
    )


def test_malformed_warning_deduped_across_repeated_parses():
    """Simulate the strict -> shifted -> fuzzy re-parse path. The warning
    must only appear once across all three parses of the same diff."""
    logger = diff_parser_module.logger
    with _WarnCapture(logger) as cap:
        for _ in range(3):
            parse_unified_diff_exact_plus(MALFORMED_DIFF, "x.py")
    assert cap.malformed_count == 1, (
        f"expected exactly 1 warning across 3 parses (dedupe); got {cap.malformed_count}"
    )


def test_hunk_malformed_header_flag_still_set():
    """Dedupe must not suppress the per-hunk malformed_header metadata —
    downstream consumers still rely on that flag to reject the hunk."""
    # Parse twice to exercise dedupe
    parse_unified_diff_exact_plus(MALFORMED_DIFF, "x.py")
    hunks = parse_unified_diff_exact_plus(MALFORMED_DIFF, "x.py")
    assert hunks[0].get("malformed_header"), (
        "malformed_header flag must still be attached to the hunk even when "
        "the warning log was deduped"
    )
