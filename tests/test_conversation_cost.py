"""Conversation cost including anchored Task Card runs
(app/utils/conversation_cost.py) and the attribution that feeds it.

Before this, a run's ledger rows carried ``conversation_id = run_id`` and
the Info tab's conversation filter never matched them.  Three seams:

* task_executor hands the run's ``source_conversation_id`` to the usage
  attribution, and the meter writes it on the row (so a plain
  conversation filter finds post-fix runs);
* the ledger groups by ``block`` (the per-block table on the tile);
* ``conversation_cost`` joins chat rows, attributed run rows AND
  binding-only runs (pre-fix rows) without double counting.

Runs against a real ledger in a tmp ZIYA_HOME (autouse conftest).
"""

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.storage.usage_ledger import UsageRecord, get_ledger, reset_ledger_for_tests
from app.utils.conversation_cost import conversation_cost, sum_groups

CHAT = "conv-chat-1"


@pytest.fixture(autouse=True)
def fresh_ledger():
    reset_ledger_for_tests()
    yield
    reset_ledger_for_tests()


def _row(**over):
    base = dict(id="", ts=time.time(), provider="bedrock", model_id="m", region=None,
                tier="standard", status="actual", dims={"input": 1000, "output": 100},
                source="chat", conversation_id=CHAT)
    base.update(over)
    return UsageRecord(**base)


# ── ledger: block grouping ────────────────────────────────────────────

def test_ledger_groups_by_block():
    led = get_ledger()
    led.append(_row(run_id="r1", block_id="b1", source="task"))
    led.append(_row(run_id="r1", block_id="b1", source="task"))
    led.append(_row(run_id="r1", block_id="b2", source="task"))
    r = led.rollup(by="block", run_id="r1")
    assert {g["key"]: g["records"] for g in r["groups"]} == {"b1": 2, "b2": 1}


# ── meter: conversation_id from attribution ───────────────────────────

def test_meter_writes_the_attributed_conversation_not_the_stream_stand_in():
    from app.context import reset_usage_attribution, set_usage_attribution
    from app.cost.meter import open_iteration
    tok = set_usage_attribution({"source": "task", "run_id": "r9", "block_id": "b9",
                                 "conversation_id": CHAT})
    try:
        rid = open_iteration(provider="bedrock", model_id="m", region=None,
                             conversation_id="r9",            # the executor's stand-in
                             project_root=None, iteration=0, estimated_input_tokens=5)
    finally:
        reset_usage_attribution(tok)
    rec = get_ledger().get(rid)
    assert rec.conversation_id == CHAT and rec.run_id == "r9" and rec.source == "task"


def test_meter_keeps_the_stream_conversation_when_attribution_names_none():
    from app.context import reset_usage_attribution, set_usage_attribution
    from app.cost.meter import open_iteration
    tok = set_usage_attribution({"source": "task", "run_id": "r9", "block_id": "b9",
                                 "conversation_id": None})
    try:
        rid = open_iteration(provider="bedrock", model_id="m", region=None,
                             conversation_id="r9", project_root=None,
                             iteration=0, estimated_input_tokens=5)
    finally:
        reset_usage_attribution(tok)
    assert get_ledger().get(rid).conversation_id == "r9"


# ── executor: the attribution carries the run's source conversation ──

class _AttrExec:
    seen = None

    def __init__(self, *a, **kw):
        pass

    async def stream_with_tools(self, messages, tools=None, **kwargs):
        from app.context import get_usage_attribution
        _AttrExec.seen = dict(get_usage_attribution() or {})
        yield {"type": "text", "content": "done"}
        yield {"type": "stream_end"}


@pytest.mark.asyncio
async def test_executor_attributes_to_the_runs_source_conversation():
    from app.agents import task_executor
    from app.models.task_card import Block
    run = SimpleNamespace(launch_context="interactive", source_conversation_id=CHAT)
    store = SimpleNamespace(get=lambda rid: run, record_activity=lambda *a, **k: None)
    block = Block(block_type="task", id="t1", name="T", instructions="do")
    with patch("app.streaming_tool_executor.StreamingToolExecutor", _AttrExec), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]), \
         patch("app.storage.task_runs.TaskRunStorage", return_value=store), \
         patch("app.utils.paths.get_project_dir", return_value="/tmp/x"):
        await task_executor.execute_task_block(block, run_id="r1", project_id="p1")
    assert _AttrExec.seen["conversation_id"] == CHAT
    assert _AttrExec.seen["run_id"] == "r1" and _AttrExec.seen["source"] == "task"


# ── the join ──────────────────────────────────────────────────────────

def test_sum_groups_null_cost_rules():
    s = sum_groups([
        {"records": 1, "dims": {"input": 5}, "effective_cost": None, "list_cost": None,
         "unknown_records": 1},
        {"records": 2, "dims": {"input": 7, "output": 1}, "effective_cost": 0.5,
         "list_cost": 0.6, "estimate_records": 1},
    ])
    assert s["records"] == 3 and s["dims"] == {"input": 12, "output": 1}
    assert s["effective_cost"] == 0.5 and s["list_cost"] == 0.6
    assert s["has_estimates"] is True and s["is_partial"] is True
    assert sum_groups([])["effective_cost"] is None


def _project_dir(tmp_path, runs, bindings):
    """A project data dir with run records and one chat's bindings."""
    import json
    pdir = tmp_path / "pdir"
    (pdir / "task_runs").mkdir(parents=True)
    (pdir / "chats").mkdir()
    for r in runs:
        (pdir / "task_runs" / f"{r['id']}.json").write_text(json.dumps(r))
    (pdir / "chats" / f"{CHAT}.bindings.json").write_text(json.dumps(bindings))
    return pdir


def test_chat_attributed_and_bound_runs_counted_once(tmp_path):
    led = get_ledger()
    # Chat's own turns.
    led.append(_row(dims={"input": 100}))
    led.append(_row(dims={"input": 100}, source="cli"))
    # Post-fix run: rows name the chat AND the run.
    for _ in range(3):
        led.append(_row(source="task", run_id="run-new", block_id="b", dims={"input": 1000}))
    # Pre-fix run: rows name the run only (conversation_id = run_id).
    for _ in range(2):
        led.append(_row(source="task", run_id="run-old", block_id="b",
                        conversation_id="run-old", dims={"input": 500}))
    # An unrelated conversation's run must not appear.
    led.append(_row(source="task", run_id="run-other", conversation_id="other",
                    dims={"input": 9}))
    now = int(time.time() * 1000)
    runs = [
        {"id": "run-new", "card_id": "c1", "status": "done", "created_at": now,
         "started_at": now, "completed_at": now + 60_000, "root_run_id": "run-new",
         "card_snapshot": {"name": "Release sweep", "root": {"block_type": "task"}}},
        {"id": "run-old", "card_id": "c1", "status": "held", "created_at": now - 5,
         "started_at": now - 5, "root_run_id": "run-old",
         "card_snapshot": {"name": "Release sweep", "root": {"block_type": "task"}}},
    ]
    pdir = _project_dir(tmp_path, runs, [
        {"id": "b1", "chat_id": CHAT, "card_id": "c1", "run_id": "run-old", "created_at": 1},
        {"id": "b2", "chat_id": CHAT, "card_id": "c1", "run_id": "run-new", "created_at": 2},
    ])
    rep = conversation_cost(CHAT, project_dir=pdir)

    assert rep["chat"]["records"] == 2 and rep["chat"]["dims"] == {"input": 200}
    by_run = {r["run_id"]: r for r in rep["runs"]}
    assert set(by_run) == {"run-new", "run-old"}
    assert by_run["run-new"]["cost"]["records"] == 3      # not 6: found twice, counted once
    assert by_run["run-old"]["cost"]["records"] == 2      # reached via the binding
    assert by_run["run-new"]["card_name"] == "Release sweep"
    assert by_run["run-new"]["status"] == "done" and by_run["run-new"]["wall_ms"] == 60_000
    # Ordinals count launches of the card in creation order.
    assert by_run["run-old"]["ordinal"] == 1 and by_run["run-new"]["ordinal"] == 2
    # Totals: chat + both runs, nothing from the other conversation.
    assert rep["totals"]["records"] == 7
    assert rep["totals"]["dims"] == {"input": 200 + 3000 + 1000}


def test_run_only_conversation_is_not_empty(tmp_path):
    """The complaint: a chat whose only spend is a run read 'no usage'."""
    get_ledger().append(_row(source="task", run_id="r-only", block_id="b",
                             conversation_id="r-only", dims={"input": 50}))
    pdir = _project_dir(tmp_path, [], [
        {"id": "b1", "chat_id": CHAT, "card_id": "c1", "run_id": "r-only", "created_at": 1},
    ])
    rep = conversation_cost(CHAT, project_dir=pdir)
    assert rep["chat"]["records"] == 0
    assert rep["totals"]["records"] == 1
    assert rep["runs"][0]["missing"] is True       # record gone, rows still counted


def test_no_project_dir_still_reports_attributed_rows():
    get_ledger().append(_row(source="task", run_id="r1", block_id="b"))
    rep = conversation_cost(CHAT, project_dir=None)
    assert [r["run_id"] for r in rep["runs"]] == ["r1"]
    assert rep["totals"]["records"] == 1
