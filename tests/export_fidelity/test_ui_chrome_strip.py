"""Exporter-level regression tests for MD-02: live-session UI chrome stripping.

The markdown export must drop the auto-added-context banner and the
checking-context spinner (live-session affordances that instruct the reader to
click UI that does not exist in a document) while preserving all real answer
content and any surrounding prose. These tests exercise
``_strip_ui_chrome`` / ``_process_content_for_export`` / ``_export_as_markdown``
directly and guard the narrow regex against over-consumption.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.utils import conversation_exporter as ce  # noqa: E402
from tests.export_fidelity import fixture  # noqa: E402
from tests.export_fidelity.checks import check_no_ui_chrome  # noqa: E402


BANNER = (
    "Auto-added 3 file(s) to context (app_super.py, util.py, main.py) — "
    "available for subsequent queries. Remove via the A button in the Files panel."
)


def test_banner_stripped_answer_kept():
    """The full banner is removed; the real answer beside it survives."""
    content = BANNER + "\n\nReal answer body here."
    out = ce._strip_ui_chrome(content)
    assert "Auto-added" not in out
    assert "Files panel" not in out
    assert "available for subsequent queries" not in out
    assert "Remove via" not in out
    assert "Real answer body here." in out


def test_end_to_end_export_passes_check():
    """The shared hygiene fixture now passes check_no_ui_chrome end-to-end."""
    conv = fixture.make_ui_chrome_conversation()
    md = ce._export_as_markdown(conv, "gist", "v", "m", "p", {})
    res = check_no_ui_chrome(md)
    assert res.passed, res.measurements
    assert res.measurements["leaked_substrings"] == []
    assert res.measurements["answer_kept"] is True


def test_spinner_stripped():
    """The transient checking-context spinner is removed, prose is not."""
    content = "🔄 Checking context...\n\nHere is the real content."
    out = ce._strip_ui_chrome(content)
    assert "Checking context" not in out
    assert "Here is the real content." in out


def test_spinner_without_emoji_stripped():
    content = "Checking context...\n\nBody."
    out = ce._strip_ui_chrome(content)
    assert "Checking context" not in out
    assert "Body." in out


# --- over-consumption guards: the regex must NOT eat legitimate content ------

def test_prose_mentioning_words_is_untouched():
    """Prose that merely uses 'auto-added', 'context', 'Files panel' is kept."""
    content = (
        "The build auto-added logic checks context. "
        "The Files panel layout is fine.\n\nReal answer here."
    )
    out = ce._strip_ui_chrome(content)
    assert out == content  # no line matched the full banner phrase sequence


def test_partial_banner_phrases_not_stripped():
    """A line with only SOME anchor phrases (not the whole banner) is kept."""
    # Missing "Remove via ... Files panel" tail -> not the banner.
    content = "Auto-added a file to context — available for subsequent queries.\n\nAnswer."
    out = ce._strip_ui_chrome(content)
    assert "Auto-added a file to context" in out
    assert "Answer." in out


def test_banner_does_not_bleed_into_adjacent_paragraphs():
    """Text before and after the banner survives intact (no cross-line eating)."""
    content = (
        "Intro paragraph that must survive.\n"
        + BANNER
        + "\nClosing paragraph that must survive."
    )
    out = ce._strip_ui_chrome(content)
    assert "Intro paragraph that must survive." in out
    assert "Closing paragraph that must survive." in out
    assert "Auto-added" not in out


def test_no_chrome_content_is_identity():
    """Content without any chrome markers is returned unchanged (fast path)."""
    content = "A normal answer with a diff and some code.\n\nMore text."
    assert ce._strip_ui_chrome(content) is content


def test_process_content_for_export_strips_chrome():
    """The wiring in _process_content_for_export applies the strip."""
    content = BANNER + "\n\nKept answer."
    out = ce._process_content_for_export(content)
    assert "Auto-added" not in out
    assert "Kept answer." in out


def test_canonical_fixture_untouched_by_chrome_strip():
    """The canonical fixture (no chrome) exports identically w.r.t. its markers."""
    conv = fixture.make_fidelity_conversation()
    md = ce._export_as_markdown(conv, "gist", "v", "m", "p", {})
    # No chrome strings should be introduced or matched.
    for s in fixture.UI_CHROME_FORBIDDEN_SUBSTRINGS:
        assert s not in md
