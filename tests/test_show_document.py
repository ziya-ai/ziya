"""show_document: the user sees the file; the model sees only a summary.

The SEAM is ``execute_single_tool`` with the real tool behind the real
signing/verification path: the ``tool_display`` event (what the frontend
persists in the assistant message) must carry the file text, and the
``tool_result_for_model`` event must carry none of it.  The replay half is
``build``-side: a persisted show_document block must reach the model as a
note, not the body.  Unit tests pin section selection, the size cap and
fence escaping.  Design: design/harness-small-items.md, item 1.
"""

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("ZIYA_TESTING", "1")

from app.mcp.enhanced_tools import DirectMCPTool  # noqa: E402
from app.tool_execution import ToolExecContext, execute_single_tool  # noqa: E402
from app.utils import show_document as sd  # noqa: E402
from app.utils.tool_history_rewrite import rewrite_assistant_text  # noqa: E402

CONV = "conv-show-doc"


def _report(n=300):
    out = ["# Report", ""]
    for i in range(1, n + 1):
        if i % 100 == 1:
            out += [f"## {i // 100 + 1}. Section {i // 100 + 1}", ""]
        out.append(f"Line {i}: the per-host median outage was {i * 7} seconds today.")
    return "\n".join(out) + "\n"


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "report.md").write_text(_report())
    return root


def _ctx(root, args):
    from app.mcp.tools.show_document import ShowDocumentTool
    manager = MagicMock()
    manager._exceeds_turn_ceiling = lambda *_: False
    manager._is_repetitive_call = lambda *_: False
    executor = SimpleNamespace(
        _format_tool_result=lambda name, text, a: text,
        _infer_syntax_hint=lambda name, a: "markdown",
        _get_tool_header=lambda name, a: f"document: {a.get('path', '')}",
    )
    return ToolExecContext(
        tool_id="tc-doc", tool_name="show_document", actual_tool_name="show_document",
        args=dict(args), all_tools=[DirectMCPTool(ShowDocumentTool())],
        internal_tool_names=set(), mcp_manager=manager, project_root=str(root),
        conversation_id=CONV, conversation=[], recent_commands=[],
        inter_tool_delay={'current': 0.0, 'min': 0.0, 'max': 1, 'decay_factor': 0.6,
                          'growth_factor': 2.5, 'last_was_throttled': False},
        iteration_start_time=0, track_yield_fn=lambda x: x,
        drain_feedback_fn=lambda: [], executor=executor,
    )


async def _run(root, args):
    events = []

    async def pull():
        async for ev in execute_single_tool(_ctx(root, args)):
            events.append(ev)
    # Generous: the first call in a process imports app.server lazily.
    await asyncio.wait_for(pull(), 60)
    display = [e for e in events if e.get("type") == "tool_display"]
    model = [e for e in events if e.get("type") == "tool_result_for_model"]
    return display, model


# ── seam ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_sees_the_file_and_the_model_does_not(proj):
    display, model = await _run(proj, {"path": "docs/report.md"})
    assert display and model
    shown = display[-1]["result"]
    assert display[-1]["verified"] is True
    body_lines = [l for l in _report().splitlines() if l.startswith("Line ")]
    assert len(body_lines) == 300
    # Positive: every line of the file reached the user's tool block.
    assert all(l in shown for l in body_lines)
    assert shown.startswith(sd.CAPTION_PREFIX)
    # And none reached the model.
    to_model = str(model[-1]["content"])
    assert not any(l in to_model for l in body_lines)
    summary = json.loads(to_model)
    assert summary["path"] == "docs/report.md"
    assert summary["lines"] == [1, summary["total_lines"]] == [1, len(_report().splitlines())]
    assert summary["sha256"] and summary["shown_to_user"] is True
    assert "## 3. Section 3" in summary["headings"]


@pytest.mark.asyncio
async def test_file_text_is_not_registered_for_parroting(proj):
    from app.hallucination.shingle_index import check_for_parroting, clear_session
    clear_session(CONV)
    await _run(proj, {"path": "docs/report.md"})
    # Nothing of the document is in the index, so the model writing about
    # it (it cannot copy it) is not mistaken for parroting.
    assert check_for_parroting(CONV, _report()) is None


def test_parroting_detector_still_fires_on_file_read_retype():
    # Positive control: file_read + retype is still caught.
    from app.hallucination.shingle_index import (
        check_for_parroting, clear_session, register_tool_result)
    cid = CONV + "-fr"
    clear_session(cid)
    register_tool_result(cid, "tc-fr", "file_read", _report())
    assert check_for_parroting(cid, _report()) is not None


@pytest.mark.asyncio
async def test_errors_reach_the_model_as_text(proj):
    _, model = await _run(proj, {"path": "docs/missing.md"})
    assert "File not found" in str(model[-1]["content"])


# ── replay ────────────────────────────────────────────────────────────

def test_replayed_history_carries_a_note_not_the_body(proj):
    body = sd.show("docs/report.md", str(proj))[sd.DISPLAY_KEY]
    persisted = ("Here it is.\n\n````tool:mcp_show_document|🔐 document: docs/report.md|markdown\n"
                 f"{body}\n````\n\nDone.")
    out = rewrite_assistant_text(persisted)
    assert "Line 150:" not in out
    assert "document shown to the user" in out and "docs/report.md" in out
    # Positive: a file_read block in the same message keeps its body.
    fr = persisted.replace("mcp_show_document|🔐 document:", "mcp_file_read|file read:")
    assert "Line 150:" in rewrite_assistant_text(fr)


# ── selection, cap, escaping ──────────────────────────────────────────

def test_heading_selects_exactly_that_section(proj):
    r = sd.show("docs/report.md", str(proj), heading="2. Section 2")
    shown = r[sd.DISPLAY_KEY]
    assert "Line 101:" in shown and "Line 200:" in shown
    assert "Line 100:" not in shown and "Line 201:" not in shown
    assert "## 3. Section 3" not in shown
    assert json.loads(r["content"])["headings"] == ["## 2. Section 2"]


def test_heading_inside_code_fence_is_ignored(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("# A\n```\n# not a heading\n```\ntext\n# B\nb\n")
    assert [h["text"] for h in sd.list_headings(p.read_text().splitlines(True))] == ["A", "B"]


def test_unknown_heading_lists_headings(proj):
    r = sd.show("docs/report.md", str(proj), heading="Nope")
    assert r["error"] and "## 2. Section 2" in r["message"]


def test_line_range(proj):
    r = sd.show("docs/report.md", str(proj), start_line=10, end_line=12)
    assert json.loads(r["content"])["lines"] == [10, 12]


def test_large_file_refused_without_selection_but_section_allowed(tmp_path):
    big = "# Big\n" + "".join(f"## Part {i}\n" + "x" * 1000 + "\n" for i in range(620))
    (tmp_path / "big.md").write_text(big)
    assert len(big.encode()) > 600 * 1024
    r = sd.show("big.md", str(tmp_path))
    assert r["error"] and "display limit" in r["message"] and "## Part 3" in r["message"]
    ok = sd.show("big.md", str(tmp_path), heading="Part 3")
    assert sd.DISPLAY_KEY in ok


def test_four_backtick_lines_cannot_close_the_tool_fence():
    text = "a\n````python\nx = 1\n````\nb\n``` ok\n"
    out = sd.escape_for_tool_fence(text)
    assert "````" not in out and "~~~~python" in out and "``` ok" in out


def test_code_render_wraps_in_a_tilde_fence(tmp_path):
    (tmp_path / "x.py").write_text("print('hi')\n")
    shown = sd.show("x.py", str(tmp_path))[sd.DISPLAY_KEY]
    assert "\n~~~python\nprint('hi')\n~~~" in shown


def test_path_outside_project_refused(proj):
    r = sd.show("../../etc/passwd", str(proj))
    assert r.get("error")


def test_tool_module_exists():
    # The new-file diff for app/mcp/tools/show_document.py is the piece most
    # easily missed when applying; without it get_fileio_tools silently
    # returns [] (ImportError is caught) and file_read disappears too.
    from app.mcp.tools.show_document import ShowDocumentTool
    assert ShowDocumentTool().name == "show_document"


def test_registered_with_fileio_tools():
    from app.mcp.builtin_tools import get_fileio_tools
    assert "ShowDocumentTool" in [t.__name__ for t in get_fileio_tools()]
