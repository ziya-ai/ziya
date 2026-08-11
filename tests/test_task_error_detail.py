"""
A failed task must say WHY it failed.

Run 20185b33 of the music-notation campaign recorded its entire error
field as ``"Task execution failed: "`` — a colon with nothing after it.
The run had streamed real work for 174 minutes; a user arriving at that
record could learn only that something had gone wrong.

Cause: ``chunk.get("content", "unknown")``.  A dict default fires only
when the key is ABSENT, so a chunk carrying ``content: ""`` (present but
empty) returned the empty string and the "unknown" fallback never ran.
The infra branch a few lines below already ``or``-chained its fields; the
plain-error branch did not, and that asymmetry is the whole defect.
"""

from unittest.mock import patch

import pytest

from app.agents import task_executor
from app.models.task_card import Block


def _task() -> Block:
    return Block(block_type="task", id="b-err", name="T",
                 instructions="do it")


class _ErrorChunkExecutor:
    """Yields one error chunk, shaped by the class attrs."""
    chunk: dict = {"type": "error", "content": ""}

    def __init__(self, *args, **kwargs):
        pass

    async def stream_with_tools(self, messages, tools=None,
                                project_root=None, **_):
        yield dict(type(self).chunk)


def _run_with(chunk: dict, tmp_path):
    """Drive execute_task_block over a single error chunk; return the exc."""
    import asyncio
    _ErrorChunkExecutor.chunk = chunk
    with patch("app.streaming_tool_executor.StreamingToolExecutor",
               _ErrorChunkExecutor), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1",
                             "aws_profile": "x", "current_model": "f"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools",
               return_value=[]):
        with pytest.raises(task_executor.TaskExecutorError) as exc:
            asyncio.run(task_executor.execute_task_block(
                _task(), project_root=str(tmp_path),
            ))
    return exc.value


class TestNeverEmptyDetail:
    def test_empty_content_does_not_produce_a_bare_colon(self, tmp_path):
        # The exact regression.  Before the fix this message ended at the
        # colon; the assertion below is what that run could not satisfy.
        exc = _run_with({"type": "error", "content": ""}, tmp_path)
        msg = str(exc)
        assert msg.rstrip() != "Task execution failed:", msg
        assert not msg.rstrip().endswith(":"), (
            f"error message ends at the colon with no detail: {msg!r}"
        )

    def test_empty_content_falls_back_to_unknown(self, tmp_path):
        exc = _run_with({"type": "error", "content": ""}, tmp_path)
        assert "unknown" in str(exc)

    @pytest.mark.parametrize("chunk", [
        {"type": "error"},                       # key absent
        {"type": "error", "content": ""},        # present, empty
        {"type": "error", "content": None},      # present, null
    ])
    def test_no_shape_yields_an_empty_detail(self, chunk, tmp_path):
        # All three arrive in practice: the streaming layer omits the key,
        # sets it empty, or nulls it depending on which path errored.
        exc = _run_with(chunk, tmp_path)
        detail = str(exc).split("Task execution failed:", 1)[-1].strip()
        assert detail, f"no detail for chunk {chunk!r}"


class TestMessageIsFoundWhereverItLives:
    """A chunk's message is not reliably in ``content``: the streaming
    layer uses that key, while _classify_and_handle_error's paths carry it
    in ``detail`` or ``retry_message``.  Reading only ``content`` silently
    discarded the message whenever a producer chose another field."""

    def test_content_is_preferred_when_present(self, tmp_path):
        exc = _run_with(
            {"type": "error", "content": "the real reason"}, tmp_path)
        assert "the real reason" in str(exc)

    def test_detail_is_used_when_content_is_empty(self, tmp_path):
        exc = _run_with(
            {"type": "error", "content": "", "detail": "from detail"},
            tmp_path)
        assert "from detail" in str(exc)

    def test_retry_message_is_used_when_earlier_fields_are_empty(self, tmp_path):
        exc = _run_with(
            {"type": "error", "content": "",
             "retry_message": "from retry_message"}, tmp_path)
        assert "from retry_message" in str(exc)

    def test_error_field_is_the_last_resort(self, tmp_path):
        # ``error`` doubles as the KIND carrier, so it is last: preferring
        # it would surface a classification string where a human-readable
        # message belongs.
        exc = _run_with(
            {"type": "error", "content": "", "error": "from error"},
            tmp_path)
        assert "from error" in str(exc)


class TestKindClassificationUnaffected:
    """The fix touches only the MESSAGE.  Which exception type is raised
    still keys off ``error_type``/``error``, so an infra fault must still
    hold the run rather than failing it."""

    def test_infra_kind_still_raises_infra_error(self, tmp_path):
        exc = _run_with(
            {"type": "error", "content": "",
             "error_type": "authentication_error"}, tmp_path)
        assert getattr(exc, "infra_kind", "") == "authentication_error"

    def test_unclassified_error_is_still_a_work_failure(self, tmp_path):
        exc = _run_with(
            {"type": "error", "content": "card is wrong"}, tmp_path)
        assert getattr(exc, "infra_kind", "") == ""

    def test_infra_message_also_never_empty(self, tmp_path):
        # The infra branch already or-chained, so this is a guard against
        # someone "simplifying" it back to a .get default.
        exc = _run_with(
            {"type": "connection_error", "detail": ""}, tmp_path)
        detail = str(exc).rsplit(":", 1)[-1].strip()
        assert detail, f"infra error has no detail: {str(exc)!r}"
