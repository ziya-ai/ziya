"""
Swarm coordination tools exposed to delegates during execution.

These are BaseMCPTool implementations that get injected into delegate
streams via extra_tools.  Each tool is a thin wrapper that calls back
to the DelegateManager's shared task list for the active plan.

Tools:
  swarm_task_list   — view all tasks with status
  swarm_complete_task — mark a task done with summary
  swarm_add_task    — register a newly-discovered subtask
  swarm_claim_task  — claim an open task to prevent duplicate work
  swarm_note        — post a note visible to all delegates
"""

import time
import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.utils.logging_utils import logger


# ---------------------------------------------------------------------------
# Each tool receives a _swarm_ctx dict at construction time containing:
#   plan_id, delegate_id, get_manager (callable returning DelegateManager)
# This avoids circular imports — the manager is resolved lazily.
# ---------------------------------------------------------------------------


class SwarmTaskListInput(BaseModel):
    """Input for swarm_task_list."""
    status_filter: Optional[str] = Field(
        None, description="Filter by status: open, claimed, done, blocked. Omit for all."
    )


class SwarmTaskListTool(BaseMCPTool):
    name = "swarm_task_list"
    description = (
        "View the shared task list for this plan. Shows every task with "
        "its status, who claimed it, and completion summary. Use this "
        "before starting work to see what others are doing and avoid "
        "duplicate effort."
    )
    InputSchema = SwarmTaskListInput

    def __init__(self, swarm_ctx: dict):
        self._ctx = swarm_ctx

    async def execute(self, status_filter: Optional[str] = None, **kw) -> str:
        mgr = self._ctx["get_manager"]()
        plan_id = self._ctx["plan_id"]
        plan = mgr._plans.get(plan_id)
        if not plan:
            return "No active plan found."

        tasks = plan.task_list
        if status_filter:
            tasks = [t for t in tasks if t.status == status_filter]

        if not tasks:
            return "Task list is empty." if not status_filter else f"No tasks with status '{status_filter}'."

        lines = ["| # | Task | Status | Claimed By | Summary |",
                 "|---|------|--------|------------|---------|"]
        for i, t in enumerate(tasks, 1):
            claimed = t.claimed_by or "—"
            summary = (t.summary or "—")[:60]
            lines.append(f"| {i} | {t.title} | {t.status} | {claimed} | {summary} |")
        return "\n".join(lines)


class SwarmCompleteTaskInput(BaseModel):
    """Input for swarm_complete_task."""
    task_id: str = Field(..., description="ID of the task to mark done.")
    summary: str = Field(..., description="Brief summary of what was done (1-2 sentences).")


class SwarmCompleteTaskTool(BaseMCPTool):
    name = "swarm_complete_task"
    description = (
        "Mark a task as done on the shared task list. Provide a brief "
        "summary so other delegates know what was accomplished."
    )
    InputSchema = SwarmCompleteTaskInput

    def __init__(self, swarm_ctx: dict):
        self._ctx = swarm_ctx

    async def execute(self, task_id: str = "", summary: str = "", **kw) -> str:
        mgr = self._ctx["get_manager"]()
        plan_id = self._ctx["plan_id"]
        plan = mgr._plans.get(plan_id)
        if not plan:
            return "No active plan found."

        with mgr._persist_lock:
            for t in plan.task_list:
                if t.task_id == task_id:
                    t.status = "done"
                    t.summary = summary
                    t.completed_at = time.time()
                    mgr._persist_plan(plan_id)
                    return f"✅ Task '{t.title}' marked done."
            return f"Task '{task_id}' not found. Use swarm_task_list to see available tasks."


class SwarmAddTaskInput(BaseModel):
    """Input for swarm_add_task."""
    title: str = Field(..., description="Short title for the new task.")
    tags: str = Field("", description="Comma-separated tags (e.g. 'migration,database').")


class SwarmAddTaskTool(BaseMCPTool):
    name = "swarm_add_task"
    description = (
        "Add a newly-discovered subtask to the shared task list. Use "
        "this when you find work that needs doing but is outside your "
        "current scope — another delegate (or a future run) can pick it up."
    )
    InputSchema = SwarmAddTaskInput

    def __init__(self, swarm_ctx: dict):
        self._ctx = swarm_ctx

    async def execute(self, title: str = "", tags: str = "", **kw) -> str:
        mgr = self._ctx["get_manager"]()
        plan_id = self._ctx["plan_id"]
        delegate_id = self._ctx["delegate_id"]
        plan = mgr._plans.get(plan_id)
        if not plan:
            return "No active plan found."

        with mgr._persist_lock:
            from app.models.delegate import SwarmTask
            task = SwarmTask(
                task_id=f"st_{uuid.uuid4().hex[:8]}",
                title=title,
                added_by=delegate_id,
                created_at=time.time(),
                tags=[t.strip() for t in tags.split(",") if t.strip()],
            )
            plan.task_list.append(task)
            mgr._persist_plan(plan_id)
            return f"📋 Task '{title}' added (id: {task.task_id}). Other delegates can see it via swarm_task_list."


class SwarmClaimTaskInput(BaseModel):
    """Input for swarm_claim_task."""
    task_id: str = Field(..., description="ID of the task to claim.")


class SwarmClaimTaskTool(BaseMCPTool):
    name = "swarm_claim_task"
    description = (
        "Claim an open task so other delegates know you're handling it. "
        "Check swarm_task_list first to find open tasks."
    )
    InputSchema = SwarmClaimTaskInput

    def __init__(self, swarm_ctx: dict):
        self._ctx = swarm_ctx

    async def execute(self, task_id: str = "", **kw) -> str:
        mgr = self._ctx["get_manager"]()
        plan_id = self._ctx["plan_id"]
        delegate_id = self._ctx["delegate_id"]
        plan = mgr._plans.get(plan_id)
        if not plan:
            return "No active plan found."

        with mgr._persist_lock:
            for t in plan.task_list:
                if t.task_id == task_id:
                    if t.status == "claimed" and t.claimed_by != delegate_id:
                        return f"⚠️ Task '{t.title}' already claimed by {t.claimed_by}."
                    if t.status == "done":
                        return f"Task '{t.title}' is already done."
                    t.status = "claimed"
                    t.claimed_by = delegate_id
                    mgr._persist_plan(plan_id)
                    return f"🔒 Claimed task '{t.title}'. It's yours."
            return f"Task '{task_id}' not found."


class SwarmNoteInput(BaseModel):
    """Input for swarm_note."""
    message: str = Field(..., description="Note content visible to all delegates.")


class SwarmNoteTool(BaseMCPTool):
    name = "swarm_note"
    description = (
        "Post a note to the orchestrator visible to all delegates. "
        "Use for cross-cutting observations: 'Found that the DB schema "
        "changed — downstream delegates should use v2 table names.'"
    )
    InputSchema = SwarmNoteInput

    def __init__(self, swarm_ctx: dict):
        self._ctx = swarm_ctx

    async def execute(self, message: str = "", **kw) -> str:
        mgr = self._ctx["get_manager"]()
        plan_id = self._ctx["plan_id"]
        delegate_id = self._ctx["delegate_id"]

        # Post to orchestrator conversation as a labeled message
        plan = mgr._plans.get(plan_id)
        if plan and plan.orchestrator_id:
            label = f"**{delegate_id} → all:** {message}"
            mgr._persist_delegate_message(plan.orchestrator_id, "assistant", label)
        return f"📝 Note posted to orchestrator."


class SwarmQueryCrystalInput(BaseModel):
    """Input for swarm_query_crystal."""
    delegate_id: str = Field(
        "", description=(
            "ID of the delegate whose crystal to read (e.g. 'D1'). "
            "Omit to list all available crystals."
        )
    )


class SwarmQueryCrystalTool(BaseMCPTool):
    name = "swarm_query_crystal"
    description = (
        "Query completed crystals from sibling delegates in this plan. "
        "Use without delegate_id to see which crystals are available. "
        "Use with delegate_id to read the full crystal summary, files "
        "changed, decisions, and exported symbols. This lets you "
        "coordinate with work completed by other delegates after your "
        "initial context was built."
    )
    InputSchema = SwarmQueryCrystalInput

    def __init__(self, swarm_ctx: dict):
        self._ctx = swarm_ctx

    async def execute(self, delegate_id: str = "", **kw) -> str:
        mgr = self._ctx["get_manager"]()
        plan_id = self._ctx["plan_id"]
        my_id = self._ctx["delegate_id"]
        crystals = mgr._crystals.get(plan_id, {})

        if not delegate_id:
            if not crystals:
                return "No crystals available yet. Other delegates are still running."
            lines = ["Available crystals:"]
            for did, c in crystals.items():
                tag = " (yours)" if did == my_id else ""
                lines.append(f"  💎 {did}{tag}: {c.task} — {c.summary[:80]}")
            return "\n".join(lines)

        crystal = crystals.get(delegate_id)
        if not crystal:
            available = ", ".join(crystals.keys()) if crystals else "none yet"
            return f"No crystal for '{delegate_id}'. Available: {available}"

        parts = [
            f"## Crystal: {delegate_id} — {crystal.task}",
            f"\n**Summary:** {crystal.summary}",
        ]
        if crystal.files_changed:
            parts.append("\n**Files changed:**")
            for fc in crystal.files_changed:
                parts.append(f"  - {fc.path} ({fc.action}, {fc.line_delta})")
        if crystal.decisions:
            parts.append("\n**Decisions:**")
            for d in crystal.decisions:
                parts.append(f"  - {d}")
        if crystal.exports:
            parts.append("\n**Exports:**")
            for sym, desc in crystal.exports.items():
                parts.append(f"  - `{sym}`: {desc}")
        return "\n".join(parts)


def create_swarm_tools(plan_id: str, delegate_id: str, get_manager) -> list:
    """Create the full set of swarm coordination tools for a delegate."""
    ctx = {
        "plan_id": plan_id,
        "delegate_id": delegate_id,
        "get_manager": get_manager,
    }
    return [
        SwarmTaskListTool(ctx),
        SwarmCompleteTaskTool(ctx),
        SwarmAddTaskTool(ctx),
        SwarmClaimTaskTool(ctx),
        SwarmNoteTool(ctx),
        SwarmQueryCrystalTool(ctx),
    ]
