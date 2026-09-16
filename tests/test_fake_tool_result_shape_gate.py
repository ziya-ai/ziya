"""
(2c) A fabricated non-shell tool fence whose BODY is a tool-result shape
must route to the hallucination path, not be laundered into an innocent
code block by FAKE_TOOL_PASSTHROUGH.

Layer C (detect_fake_tool_result) already recognises the result shape, but
it skips fenced content — and the fake tool fence *is* a fence — so the one
place a fabricated success report is most likely to appear was exactly
where the detector was blind.  Fake fences with prose / placeholder bodies
still pass through as plain code so format discussion is not punished.
"""

import logging

import pytest

from app.text_delta_processor import process_text_delta
from tests.test_text_delta_processor import _make_executor, _make_state

F4 = "`" * 4


def _run(chunks):
    logging.disable(logging.CRITICAL)
    try:
        ex, st = _make_executor(), _make_state()
        events = []
        for c in chunks:
            events.extend(process_text_delta(ex, c, st))
        return events, st
    finally:
        logging.disable(logging.NOTSET)


def _fake(tool, header, syntax, body):
    return [f"{F4}tool:{tool}|{header}|{syntax}\n", body + "\n", f"{F4}\n\n"]


RESULT_BODY = ("{'success': True, 'message': 'Patched Docs/design/shadow-sessions.md "
               "successfully (replaced 1 of 1 occurrence(s))', "
               "'path': 'Docs/design/shadow-sessions.md', 'bytes_written': 46520}")


class TestResultShapeEscalates:

    def test_file_write_success_dict_hits_hallucination_path(self):
        events, st = _run(_fake("mcp_file_write", "🔐 file write: Docs/x.md", "markdown", RESULT_BODY))
        assert st.hallucination_detected is True
        kinds = [e.get("type") for e in events]
        assert "hallucination_recovery" in kinds
        assert not any(e.get("type") == "text" and "success" in str(e.get("content"))
                       for e in events), "fabricated result was passed through as text"

    def test_recovery_event_names_the_reason(self):
        events, _ = _run(_fake("mcp_memory_propose", "🔐 Memory Propose", "text",
                               "{'success': True, 'message': 'Memory proposed for review (17 pending).', "
                               "'proposal_id': 'prop_5dc1b430'}"))
        rec = next(e for e in events if e.get("type") == "hallucination_recovery")
        assert rec["reason"] == "fabricated_tool_result"
        assert "mcp_memory_propose" in rec["pattern"]

    def test_json_shaped_result_also_caught(self):
        body = '{"success": true, "message": "Created a.py (842 bytes)", "path": "a.py", "bytes_written": 842}'
        _, st = _run(_fake("mcp_file_write", "file write: a.py", "json", body))
        assert st.hallucination_detected is True


class TestNonResultBodiesStillPassThrough:

    def test_prose_body_passes_through_as_code_block(self):
        events, st = _run(_fake("mcp_file_read", "file read: a.py", "python",
                                "def f():\n    return 1"))
        assert st.hallucination_detected is False
        texts = [e["content"] for e in events if e.get("type") == "text"]
        assert any(t.startswith(f"{F4}python\n") for t in texts)
        assert not any("tool:" in t for t in texts)

    def test_running_placeholder_passes_through(self):
        events, st = _run(_fake("mcp_file_read", "file read: a.py", "python", "⏳ Running..."))
        assert st.hallucination_detected is False
        assert any(e.get("type") == "text" for e in events)

    def test_real_shell_dispatch_unaffected(self):
        # The shell heuristic runs first and still dispatches execution.
        events, st = _run(_fake("mcp_run_shell_command", "Shell: ls", "bash", "$ ls\nREADME.md"))
        assert st.hallucination_detected is False
        assert any(e.get("type") == "fake_tool_detected" and e.get("command") == "ls" for e in events)
