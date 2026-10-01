"""Every Task inside a PARALLEL fan-out is told what it owns.

On 2026-09-27 (memory-replay card, run 8c5deca5) iteration 2 of a 3-wide
for_each fan-out owned ``sep-current-aa`` but advanced ``sep-baseline``, a
run its sibling owned: its instructions carried only its own ``{{item}}``,
and nothing said the other items existed or were being worked on
concurrently.  The fix puts an ownership notice in the system prompt of
every task under a parallel iteration.

The anchor test runs the REAL block executor over that card's exact shape
(parallel for_each Repeat -> Until -> Task) and inspects the real
SystemMessage each iteration's model receives.  The Until matters: it
re-stamps the iteration context, and without inheritance the descriptor
was dropped for exactly this shape.
"""
import asyncio
import json
from unittest.mock import patch

import pytest

import app.agents.block_executor as be
from app.models.task_card import Block
from app.utils.fanout_notice import fanout_descriptor, fanout_ownership_notice

RUNS = ["sep-baseline", "sep-current", "sep-current-aa"]


class _CapturingExecutor:
    """StreamingToolExecutor stand-in: records each call's system prompt
    and the instructions it was given."""
    calls = []
    model_config = {"name": "fake", "token_limit": 100_000}
    model_id = "fake-id"
    endpoint = "bedrock"

    def __init__(self, *a, **kw):
        pass

    async def stream_with_tools(self, messages, tools=None, **kwargs):
        type(self).calls.append((messages[0].content, messages[1].content))
        await asyncio.sleep(0)
        yield {"type": "text", "content": "done"}
        yield {"type": "stream_end"}


@pytest.fixture
def captured():
    _CapturingExecutor.calls = []
    with patch("app.streaming_tool_executor.StreamingToolExecutor", _CapturingExecutor), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x",
                             "current_model": "fake"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]):
        yield _CapturingExecutor.calls


def _card(parallel: bool) -> Block:
    task = Block(id="t", block_type="task", name="Advance {{item}}",
                 instructions="Run id: {{item}}. Advance it by one chunk.")
    until = Block(id="u", block_type="until", name="Chunk {{item}}",
                  until_max=1, body=[task])
    return Block(id="fan", block_type="repeat", name="Advance the runs",
                 repeat_mode="for_each", repeat_for_each_source=json.dumps(RUNS),
                 repeat_parallel=parallel, repeat_propagate="none", body=[until])


def _ctx():
    return be.ExecutionContext(run_id="r-fanout", project_id="p", project_root="/tmp")


@pytest.mark.asyncio
async def test_each_parallel_iteration_is_told_its_item_and_its_peers(captured):
    await be.execute_block(_card(parallel=True), _ctx())
    assert len(captured) == 3                       # the path ran, once per item
    by_item = {}
    for system, human in captured:
        own = human.split("Run id: ")[1].split(".")[0]
        by_item[own] = system
    assert set(by_item) == set(RUNS)
    for own, system in by_item.items():
        assert "PARALLEL FAN-OUT OWNERSHIP" in system, own
        assert f"Your item: {own!r}" in system
        peers = system.split("The other items (")[1].split(")")[0]
        tokens = {t.strip() for t in peers.split(",")}   # exact quoted labels
        assert tokens == {repr(o) for o in RUNS if o != own}


@pytest.mark.asyncio
async def test_serial_loop_gets_no_notice(captured):
    """Positive control: the notice is specific to concurrent iterations."""
    await be.execute_block(_card(parallel=False), _ctx())
    assert len(captured) == 3
    assert not any("PARALLEL FAN-OUT OWNERSHIP" in s for s, _ in captured)


def test_notice_rendering():
    d = fanout_descriptor("L", 1, 3, ["a", "b", "c"])
    assert d["item"] == "b" and d["peers"] == ["a", "c"]
    text = fanout_ownership_notice(d)
    assert "iteration 2 of 3" in text and "'a', 'c'" in text
    assert fanout_ownership_notice(fanout_descriptor("L", 0, 1, ["only"])) is None
    assert fanout_ownership_notice(None) is None
    wide = fanout_ownership_notice(fanout_descriptor("W", 0, 50, [f"i{n}" for n in range(50)]))
    assert "and 29 more" in wide                   # 49 peers, 20 listed
