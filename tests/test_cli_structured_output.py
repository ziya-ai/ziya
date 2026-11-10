"""``--output-format json|stream-json`` for one-shot CLI runs
(app/utils/cli_structured_output.py) and ``$ARGUMENTS`` substitution for
``ziya task`` (app/utils/task_arguments.py)."""
from __future__ import annotations

import argparse
import io
import json
from unittest.mock import patch

import pytest

from app.utils import cli_structured_output as so
from app.utils.task_arguments import has_placeholders, render_task_arguments, split_task_args


async def _gen(chunks):
    for c in chunks:
        yield c


CHUNKS = [
    {"type": "text", "content": "Let me look. "},
    {"type": "tool_start", "tool_id": "t1", "tool_name": "file_read", "args": {"path": "a.py"}},
    {"type": "tool_display", "tool_id": "t1", "tool_name": "file_read", "result": "x = 1\n"},
    {"type": "tool_start", "tool_id": "hidden", "tool_name": "memory_search", "is_internal": True},
    {"type": "tool_display", "tool_id": "hidden", "tool_name": "memory_search", "is_internal": True, "result": "…"},
    {"type": "text", "content": "`a.py` sets x to 1."},
]


@pytest.mark.asyncio
async def test_stream_json_emits_one_event_per_line_then_result():
    out = io.StringIO()
    sink = so.StructuredSink("stream-json", out, clock=iter([0.0, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]).__next__)
    text = await sink.consume(_gen(CHUNKS))
    with patch.object(so, "usage_for_conversation", lambda cid: {"tokens": {"input": 10, "output": 5}, "effective_cost_usd": 0.001}):
        rc = sink.finish(text, conversation_id="conv-1", model="sonnet")
    assert rc == 0
    lines = [json.loads(l) for l in out.getvalue().splitlines()]
    assert [l["type"] for l in lines] == ["text", "tool_start", "tool_result", "text", "result"]
    assert lines[1] == {"type": "tool_start", "id": "t1", "tool": "file_read", "args": {"path": "a.py"}}
    assert lines[2]["ok"] is True and lines[2]["result"] == "x = 1\n"
    assert lines[2]["duration_ms"] == 500
    res = lines[-1]
    assert res["text"] == "Let me look. `a.py` sets x to 1."
    assert res["usage"]["effective_cost_usd"] == 0.001
    assert [c["tool"] for c in res["tool_calls"]] == ["file_read"]   # internal call not reported
    assert res["conversation_id"] == "conv-1" and res["model"] == "sonnet"


@pytest.mark.asyncio
async def test_json_prints_nothing_until_the_end():
    out = io.StringIO()
    sink = so.StructuredSink("json", out)
    await sink.consume(_gen(CHUNKS[:3]))
    assert out.getvalue() == ""
    with patch.object(so, "usage_for_conversation", lambda cid: None):
        sink.finish(sink.text, conversation_id="c")
    doc = json.loads(out.getvalue())
    assert doc["type"] == "result" and doc["ok"] is True
    assert doc["usage"] is None
    assert doc["tool_calls"][0]["result"] == "x = 1\n"
    assert "started_at" not in doc["tool_calls"][0]


def test_empty_answer_or_error_is_exit_code_1():
    sink = so.StructuredSink("json", io.StringIO())
    with patch.object(so, "usage_for_conversation", lambda cid: None):
        assert sink.finish("", conversation_id=None) == 1
        sink2 = so.StructuredSink("json", io.StringIO())
        assert sink2.finish("partial", conversation_id=None, error="boom") == 1


def test_tool_error_results_are_marked_not_ok_and_long_results_previewed():
    sink = so.StructuredSink("json", io.StringIO())
    sink.handle({"type": "tool_start", "tool_id": "t", "tool_name": "run_shell_command", "args": {"command": "ls"}})
    sink.handle({"type": "tool_display", "tool_id": "t", "tool_name": "run_shell_command", "result": "ERROR: refused"})
    sink.handle({"type": "tool_start", "tool_id": "u", "tool_name": "file_read", "args": {}})
    sink.handle({"type": "tool_display", "tool_id": "u", "tool_name": "file_read", "result": "y" * 5000})
    assert sink.tool_calls[0]["ok"] is False
    assert sink.tool_calls[1]["result"].startswith("y" * 10) and "more chars" in sink.tool_calls[1]["result"]
    assert len(sink.tool_calls[1]["result"]) < 2100


@pytest.mark.asyncio
async def test_rewind_marker_drops_the_rewound_tail():
    def rewind(text):
        # Stand-in for CLI._handle_rewind_marker: everything before the
        # marker's line is kept, the marked line is dropped.
        head, _, _ = text.partition("bad <!-- REWIND_MARKER:")
        return head, text
    sink = so.StructuredSink("json", io.StringIO())
    text = await sink.consume(_gen([
        {"type": "text", "content": "good "},
        {"type": "text", "content": "bad <!-- REWIND_MARKER: x -->"},
        {"type": "text", "content": "better"},
    ]), rewind=rewind)
    assert text == "good better"


def test_usage_reads_the_ledger_totals():
    class Ledger:
        def rollup(self, by, conversation_id):
            assert by == "model" and conversation_id == "c1"
            return {"totals": {"records": 2, "dims": {"input": 100, "output": 20},
                               "list_cost": 0.01, "effective_cost": 0.008,
                               "has_estimates": False, "is_partial": False},
                    "groups": [{"key": "sonnet4.6"}]}
    with patch("app.storage.usage_ledger.get_ledger", lambda: Ledger()):
        u = so.usage_for_conversation("c1")
    assert u["tokens"] == {"input": 100, "output": 20}
    assert u["effective_cost_usd"] == 0.008 and u["models"] == ["sonnet4.6"]


def test_from_args_and_argument_registration():
    p = argparse.ArgumentParser()
    so.add_output_format_argument(p)
    assert so.StructuredSink.from_args(p.parse_args([])) is None
    sink = so.StructuredSink.from_args(p.parse_args(["--output-format", "stream-json"]))
    assert sink.fmt == "stream-json"
    with pytest.raises(SystemExit):
        p.parse_args(["--output-format", "xml"])


# ------------------------------------------------------------ $ARGUMENTS


def test_arguments_placeholders():
    p = "Review $ARGUMENTS. First: $1, second: ${2}, third: $ARGUMENTS[2], all: ${ARGUMENTS}."
    assert render_task_arguments(p, ["a.py", "b.py", "c.py"]) == (
        "Review a.py b.py c.py. First: a.py, second: b.py, third: c.py, all: a.py b.py c.py.")


def test_missing_arguments_render_empty_and_dollar_literals_survive():
    assert render_task_arguments("Fix $1 (budget $5.00 at $HOME)", []) == "Fix  (budget $5.00 at $HOME)"
    assert not has_placeholders("costs $HOME and $5.00")


def test_arguments_without_placeholders_are_appended_not_dropped():
    out = render_task_arguments("Do the release.\n", ["--dry-run", "v1.2"])
    assert out.endswith("Arguments: --dry-run v1.2\n")
    assert render_task_arguments("Do the release.", []) == "Do the release."


def test_peel_task_options_recognises_known_options_after_the_task_name():
    from app.utils.task_arguments import peel_task_options
    opts, rest = peel_task_options(["--strict", "a.py", "--output-format", "json", "--dry-run"])
    assert opts == {"strict": True, "output_format": "json"}
    assert rest == ["a.py", "--dry-run"]          # unknown flags belong to the task
    opts, rest = peel_task_options(["--output-format=stream-json", "--no-stream"])
    assert opts == {"output_format": "stream-json", "no_stream": True} and rest == []


def test_peel_task_options_stops_at_double_dash():
    from app.utils.task_arguments import peel_task_options
    opts, rest = peel_task_options(["x", "--", "--strict", "--output-format", "json"])
    assert opts == {} and rest == ["x", "--strict", "--output-format", "json"]
    assert peel_task_options([]) == ({}, [])
    # A trailing value-taking option with no value is a prompt argument, not a crash.
    assert peel_task_options(["--output-format"]) == ({}, ["--output-format"])


def test_split_task_args_drops_remainder_separator():
    assert split_task_args(["--", "a", "b"]) == ["a", "b"]
    assert split_task_args(["a", "--", "b"]) == ["a", "--", "b"]
    assert split_task_args([]) == []
