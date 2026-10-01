"""Fuzzy position search must not crash on its own debug log line.

THE DEFECT THIS EXISTS FOR
--------------------------
``calculate_enhanced_similarity`` (nested in ``find_best_chunk_position``)
appends a ratio only for the strategies that apply, then logged them with
f-string fields like ``{ratios[4] if len(ratios) > 4 else 'N/A':.3f}``.  An
f-string is evaluated eagerly -- whatever the log level -- and ``'N/A'`` cannot
take ``:.3f``, so any window where a whitespace-dependent strategy is skipped
(a blank line under a one-line hunk is enough) raised
``ValueError: Unknown format code 'f' for object of type 'str'``.  Its only
caller is the hybrid-forced difflib stage, which then failed the whole apply
(observed 2026-09-24 on the second apply of
MRE_unterminated_final_add_joins_next_line).
"""
import logging

from app.utils.diff_utils.application import fuzzy_match
from app.utils.diff_utils.application.fuzzy_match import find_best_chunk_position

FILE = ['[tool]', 'name = "x"', '', 'keywords = ["a"]', 'classifiers = [']


def test_blank_line_in_search_window_does_not_raise():
    pos, ratio = find_best_chunk_position(FILE, ['keywords = ["b"]'], 3)
    # the path must actually have scored candidates, not bailed out early
    assert pos == 3
    assert 0.5 < ratio < 1.0


def test_does_not_raise_with_debug_logging_enabled(caplog):
    """The debug line itself must render when debug logging is on."""
    with caplog.at_level(logging.DEBUG, logger=fuzzy_match.logger.name):
        pos, _ = find_best_chunk_position(FILE, ['keywords = ["b"]'], 3)
    assert pos == 3
    # positive: the debug path really ran and rendered
    assert any("Similarity ratios" in r.getMessage() for r in caplog.records)
