"""
Builtin tool: list_run_artifacts

The read side of ``emit_artifact``'s ``from_run``.  Copying evidence by
filename presumes the aggregating card already knows the filenames,
which for a sweep that emitted several hundred it does not — so without
a way to enumerate a run's artifacts, cross-run aggregation cannot be
authored at all.

Returns an INDEX, never payloads: no blob bytes, no inline text, no
absolute paths.  That is what makes it affordable to place in a model's
context; the blobs stay on disk and reach the browser through the run's
artifact blob route.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool

logger = logging.getLogger(__name__)


def _text(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}]}


class ListRunArtifactsInput(BaseModel):
    """Input schema for list_run_artifacts."""

    from_run: str = Field(
        default="self",
        description=(
            "Which run to index. 'self' (default) is this run, including "
            "output from earlier blocks and from cards this run called. "
            "Also accepts a card name or card id (that card's most recent "
            "finished run), or an explicit run id."
        ),
    )
    limit: int = Field(
        default=500,
        description="Maximum entries to return (default 500).",
    )


class ListRunArtifactsTool(BaseMCPTool):
    """Index a run's emitted artifacts without their payloads."""

    name: str = "list_run_artifacts"
    description: str = (
        "[DIRECT] List the artifacts a run emitted — name, group, label, "
        "seq, filename, media type, status, block and iteration — WITHOUT "
        "their contents. Use it before emit_artifact(from_run=...) to see "
        "what evidence exists instead of guessing filenames. 'from_run' "
        "accepts 'self' (this run, including cards it called), a card name "
        "or id, or a run id. Only available inside Task Card runs."
    )

    InputSchema = ListRunArtifactsInput

    async def execute(self, **kwargs) -> Any:
        kwargs.pop("_workspace_path", None)
        from app.utils.task_artifacts import (
            collection_active, list_run_artifacts,
        )

        if not collection_active():
            return _text(
                "list_run_artifacts is only available inside a Task Card "
                "run — there is no active run to resolve against."
            )

        try:
            limit = int(kwargs.get("limit") or 500)
        except (TypeError, ValueError):
            return _text("Error: 'limit' must be an integer.")
        if limit < 1:
            return _text("Error: 'limit' must be at least 1.")

        entries, err = list_run_artifacts(
            from_run=str(kwargs.get("from_run") or "self"), limit=limit,
        )
        if err:
            return _text(f"Error: {err}")
        if not entries:
            return _text(
                "That run recorded no emitted artifacts. If it is still "
                "running, its outputs are drained per task as blocks finish."
            )
        return _text(json.dumps(
            {"count": len(entries), "artifacts": entries}, indent=1,
        ))
