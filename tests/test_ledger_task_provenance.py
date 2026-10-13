"""Evidence ledger inside a Task Card (design/evidence-ledger.md, Task Card
integration; M1 step 8).

The seam: execute_block -> execute_task_block -> (patched model stream) ->
the REAL ledger_record tool -> the ledger -> the task's artifact.  The fake
stream calls the tool the way the model does mid-task, with the task's
workspace as ``_workspace_path``, so the context variables the executors set
are what the tool actually sees.

Asserted end to end:
  * a claim recorded during a task carries the run and block ids;
  * the task's artifact lists it in a ``ledger_claims`` data part, and a
    later pass of the same block lists only its own claims;
  * a State block's ``evidence_namespace`` is the default namespace;
  * a scope ``cwd`` does not move claims into the subdirectory's ledger.
"""
import asyncio
from unittest.mock import patch

import pytest

from app.agents.block_executor import ExecutionContext, execute_block
from app.models.task_card import Block, TaskScope
from app.utils.evidence import scope as sc
from app.utils.evidence import store as st


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    st._key_cache.clear()
    st._ledgers.clear()


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    (p / "sub").mkdir(parents=True)
    (p / "sub" / "r.csv").write_text("x\n1\n")
    return p


class _ToolCallingExecutor:
    """Stands in for the model: records one claim through the real tool."""
    seen = []          # (workspace, namespace, envelope payload) per stream
    claim_key = "s1.errors"

    def __init__(self, *a, **k):
        pass

    async def stream_with_tools(self, messages, tools=None, project_root=None, **_):
        from app.context import get_evidence_namespace
        from app.mcp.tools.ledger_tools import LedgerRecordTool
        env = sc.build_envelope(project_root, "conv-x")
        type(self).seen.append((project_root, get_evidence_namespace(), sc.verify(env["token"])))
        out = await LedgerRecordTool().execute(
            _workspace_path=project_root,
            observation={"source": "file", "result_path": "r.csv"},
            claim={"key": type(self).claim_key, "value": 1},
        )
        assert not out.get("error"), out
        yield {"type": "text", "content": "done <self_assessment objective_met=\"true\" rationale=\"ok\"/>"}


def _run(root_block, proj, run_id="run-1"):
    _ToolCallingExecutor.seen = []
    with patch("app.streaming_tool_executor.StreamingToolExecutor", _ToolCallingExecutor), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x", "current_model": "f"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]):
        ctx = ExecutionContext(run_id=run_id, project_root=str(proj))
        return asyncio.run(execute_block(root_block, ctx)), ctx


def _task(id_="t1"):
    return Block(block_type="task", id=id_, name="T", instructions="record it",
                 scope=TaskScope(cwd="sub"))


def _state(ns):
    return Block(block_type="state", id="s", name="S", state_variables={"evidence_namespace": ns})


def _ledger_part(artifact):
    parts = [o for o in artifact.outputs
             if (o.data or {}).get("ledger_claims") is not None]
    return parts[0].data["ledger_claims"] if parts else None


def test_claim_in_task_has_block_provenance_project_ledger_and_artifact_part(proj):
    root = Block(block_type="group", id="g", name="G", body=[_state("audit"), _task()])
    _run(root, proj)
    ws, ns, tok = _ToolCallingExecutor.seen[0]
    # The task's tools run in the scope cwd ...
    assert ws.endswith("/sub")
    # ... but its claims go to the project's ledger, not sub/.ziya/ledger.
    assert not (proj / "sub" / ".ziya" / "ledger").exists()
    led = st.ledger_for(str(proj))
    c = led.claim("audit.s1.errors")
    assert c is not None, "namespace from the State block was not applied"
    assert c["provenance"]["run_id"] == "run-1" and c["provenance"]["block_id"] == "t1"
    assert c["provenance"]["via"] == "tool"
    # The shell token a task's commands would get names the same ledger and namespace.
    assert ns == "audit"
    assert tok["project_root"] == str(proj) and tok["namespace"] == "audit"
    assert tok["run_id"] == "run-1" and tok["block_id"] == "t1"


def test_task_artifact_lists_this_passes_claims_only(proj):
    # No State block here, so the key must be qualified itself.
    _ToolCallingExecutor.claim_key = "ns.s1.errors"
    try:
        art, _ = _run(_task("t2"), proj, run_id="r-b")
        assert _ledger_part(art) == ["ns.s1.errors@v1"]
        art, _ = _run(_task("t2"), proj, run_id="r-b")
        # Second pass of the same block in the same run: only its own version.
        assert _ledger_part(art) == ["ns.s1.errors@v2"]
    finally:
        _ToolCallingExecutor.claim_key = "s1.errors"


def test_unqualified_key_without_namespace_is_refused(proj):
    from app.mcp.tools.ledger_tools import LedgerRecordTool
    out = asyncio.run(LedgerRecordTool().execute(
        _workspace_path=str(proj), observation={"source": "tool", "inline": 1},
        claim={"key": "nodot", "value": 1}))
    assert out["error"] and "Invalid claim id" in out["message"]


def test_no_claims_no_part(proj):
    class _Silent(_ToolCallingExecutor):
        async def stream_with_tools(self, messages, tools=None, project_root=None, **_):
            yield {"type": "text", "content": "nothing <self_assessment objective_met=\"true\" rationale=\"ok\"/>"}
    with patch("app.streaming_tool_executor.StreamingToolExecutor", _Silent), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x", "current_model": "f"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]):
        art = asyncio.run(execute_block(_task("t3"), ExecutionContext(run_id="r-c", project_root=str(proj))))
    assert _ledger_part(art) is None


def test_namespace_and_ledger_root_do_not_leak_past_the_task(proj):
    from app.context import get_evidence_namespace, get_ledger_root
    root = Block(block_type="group", id="g", name="G", body=[_state("audit"), _task()])
    _run(root, proj)
    assert get_evidence_namespace() is None and get_ledger_root() is None
