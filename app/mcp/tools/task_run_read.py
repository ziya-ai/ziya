"""task_run_read — inspect a task run without the browser.

``task_card_launch`` tells the model to "poll the task-run views for
status and the final artifact", but no tool exposed them: run records
are encrypted at rest and the live journal was read only by the UI.  An
operator diagnosing a failed scheduled run had to reconstruct the
verdict from text deltas in events.jsonl.

Thin wrapper over app.utils.task_run_read, which does the reading and
bounding; this file resolves the project and the reference.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.mcp.tools.task_card_tools import _resolve_card_storage


class TaskRunReadInput(BaseModel):
    """Input schema for task_run_read."""
    run_id: Optional[str] = Field(
        None, description="A run id to read in full.  Omit to list runs.")
    card: Optional[str] = Field(
        None, description="A card id or name.  With run_id omitted, lists "
                          "that card's runs newest first; with "
                          "run_id='latest', reads the card's newest run.")
    block_id: Optional[str] = Field(
        None, description="With run_id: add that block's full artifact "
                          "decisions and iteration summaries, and narrow "
                          "the events to it.")
    include_events: bool = Field(
        True, description="With run_id: include the verdict-bearing journal "
                          "events (task_finished, holds, asks).  Text and "
                          "tool traffic are counted, never returned.")
    limit: int = Field(20, description="Max runs in a listing (<= 50).")


class TaskRunReadTool(BaseMCPTool):
    """Read a task run's status, failures and outputs index."""

    name: str = "task_run_read"
    description: str = (
        "[DIRECT] Read a task run: status, the card name/version it ran "
        "(from its launch snapshot), every block's status, WHICH block "
        "failed and WHY (the executor's failure_signature and "
        "failure_reason, the model's self_assessment), an index of "
        "emitted outputs (no payloads), the task_finished events from "
        "the run's journal, and — for a scheduled card — the schedule "
        "state with fires and skips.  Call with no arguments to list "
        "recent runs, with card=<id or name> to list one card's runs, "
        "with run_id=<id> (or run_id='latest' plus card) to read one, "
        "and block_id to drill into a block.  Read-only.  Use this after "
        "task_card_launch instead of polling, and to diagnose a failed "
        "or skipped scheduled run."
    )
    InputSchema = TaskRunReadInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        kwargs.pop("_workspace_path", None)
        from app.storage.task_runs import TaskRunStorage
        from app.utils.paths import get_project_dir
        from app.utils.task_run_read import (
            list_runs, resolve_card_id, summarize_run,
        )

        res = _resolve_card_storage()
        if not res["ok"]:
            return {"error": True, "message": res["error"]}
        project_id = res["project_id"]
        card_storage = res["storage"]
        run_storage = TaskRunStorage(get_project_dir(project_id))

        card_ref = (kwargs.get("card") or "").strip() or None
        card_id = None
        if card_ref:
            card_id = resolve_card_id(card_storage, card_ref)
            if card_id is None:
                return {"error": True,
                        "message": f"No task card with id or name {card_ref!r}."}

        run_id = (kwargs.get("run_id") or "").strip() or None
        try:
            limit = int(kwargs.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20

        if run_id is None:
            rows = list_runs(run_storage, card_id=card_id, limit=limit)
            if not rows:
                return {"runs": [], "message": (
                    f"No runs recorded for card {card_ref!r}." if card_ref
                    else "No task runs recorded in this project.")}
            return {"count": len(rows), "card_id": card_id, "runs": rows}

        if run_id.lower() == "latest":
            if card_id is None:
                return {"error": True,
                        "message": "run_id='latest' needs card=<id or name>."}
            rows = list_runs(run_storage, card_id=card_id, limit=1)
            if not rows:
                return {"error": True,
                        "message": f"Card {card_ref!r} has no runs."}
            run_id = rows[0]["run_id"]

        if any(c in run_id for c in ("/", "\\", "\x00")) or ".." in run_id:
            return {"error": True, "message": "Malformed run_id."}
        run = run_storage.get(run_id)
        if run is None:
            return {"error": True, "message": f"Run not found: {run_id}"}
        if card_id and run.card_id != card_id:
            return {"error": True, "message": (
                f"Run {run_id[:8]} belongs to card {run.card_id}, not "
                f"{card_ref!r}.")}

        out = summarize_run(
            run_storage, run, project_id=project_id,
            block_id=(kwargs.get("block_id") or "").strip() or None,
            include_events=bool(kwargs.get("include_events", True)),
        )
        # Keep the result JSON-safe for the transport (datetimes, Paths).
        return json.loads(json.dumps(out, default=str))
