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
from app.models.delegate import DelegateSpec
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


class SwarmReadLogInput(BaseModel):
    """Input for swarm_read_log."""
    last_n: int = Field(
        10, description="Number of recent orchestrator messages to read (default 10)."
    )


class SwarmReadLogTool(BaseMCPTool):
    name = "swarm_read_log"
    description = (
        "Read recent messages from the orchestrator conversation log. "
        "This includes notes from other delegates, orchestrator analysis "
        "of completed crystals, and cross-cutting observations. Use this "
        "to stay aware of what's happening across the plan."
    )
    InputSchema = SwarmReadLogInput

    def __init__(self, swarm_ctx: dict):
        self._ctx = swarm_ctx

    async def execute(self, last_n: int = 10, **kw) -> str:
        mgr = self._ctx["get_manager"]()
        plan_id = self._ctx["plan_id"]
        plan = mgr._plans.get(plan_id)
        if not plan or not plan.orchestrator_id:
            return "No orchestrator conversation found."

        cs = mgr._get_chat_storage()
        chat = cs.get(plan.orchestrator_id)
        if not chat or not chat.messages:
            return "Orchestrator log is empty."

        recent = chat.messages[-last_n:]
        lines = []
        for msg in recent:
            content = msg.content if hasattr(msg, 'content') else str(msg)
            if len(content) > 300:
                content = content[:300] + "..."
            lines.append(content)
        return "\n\n---\n\n".join(lines) if lines else "No messages found."


class SwarmRequestDelegateInput(BaseModel):
    """Input for swarm_request_delegate."""
    name: str = Field(..., description="Short name for the new delegate task.")
    scope: str = Field(..., description="What this delegate should do.")
    files: str = Field("", description="Comma-separated file paths for the delegate's context.")
    depends_on: str = Field("", description="Comma-separated delegate IDs this depends on.")


class SwarmRequestDelegateTool(BaseMCPTool):
    name = "swarm_request_delegate"
    description = (
        "Request that a NEW delegate be spawned for discovered work that "
        "is too large or too different for you to handle. The orchestrator "
        "will create and start the delegate. Use this for work that needs "
        "a separate context (different files, different skill) rather than "
        "swarm_add_task which just adds to the shared list."
    )
    InputSchema = SwarmRequestDelegateInput

    def __init__(self, swarm_ctx: dict):
        self._ctx = swarm_ctx

    async def execute(self, name: str = "", scope: str = "",
                      files: str = "", depends_on: str = "", **kw) -> str:
        mgr = self._ctx["get_manager"]()
        plan_id = self._ctx["plan_id"]
        delegate_id = self._ctx["delegate_id"]
        plan = mgr._plans.get(plan_id)
        if not plan:
            return "No active plan found."

        existing_ids = {s.delegate_id for s in plan.delegate_specs}
        n = len(existing_ids) + 1
        new_id = f"D{n}"
        while new_id in existing_ids:
            n += 1
            new_id = f"D{n}"

        file_list = [f.strip() for f in files.split(",") if f.strip()]
        dep_list = [d.strip() for d in depends_on.split(",") if d.strip()]

        new_spec = DelegateSpec(
            delegate_id=new_id,
            name=name,
            emoji="🆕",
            scope=scope,
            files=file_list,
            dependencies=dep_list,
        )

        with mgr._persist_lock:
            plan.delegate_specs.append(new_spec)
            mgr._statuses[plan_id][new_id] = "proposed"

            from app.models.delegate import SwarmTask
            plan.task_list.append(SwarmTask(
                task_id=f"st_{new_id}",
                title=name,
                status="open",
                added_by=delegate_id,
                created_at=time.time(),
            ))
            mgr._persist_plan(plan_id)

        if plan.orchestrator_id:
            label = (
                f"**{delegate_id} → orchestrator:** Requested new delegate "
                f"**{new_id}: {name}** — {scope}"
            )
            mgr._persist_delegate_message(plan.orchestrator_id, "assistant", label)

        import asyncio
        asyncio.create_task(
            mgr._spawn_and_start_dynamic_delegate(plan_id, new_spec),
            name=f"spawn-{new_id}",
        )

        return (
            f"🆕 Requested new delegate {new_id}: {name}. "
            f"The orchestrator will create and start it when dependencies are met."
        )


class SwarmLaunchSubplanInput(BaseModel):
    """Input for swarm_launch_subplan."""
    name: str = Field(..., description="Short name for the sub-plan (e.g. 'Database Migration Suite').")
    description: str = Field(..., description="What this sub-plan should accomplish overall.")
    delegates_json: str = Field(
        ...,
        description=(
            'JSON array of delegate specs. Each entry: '
            '{"name": "...", "scope": "...", "files": "comma,separated", "depends_on": "D1,D2"}. '
            'delegate_id is auto-assigned (D1, D2, etc.).'
        ),
    )


class SwarmLaunchSubplanTool(BaseMCPTool):
    name = "swarm_launch_subplan"
    description = (
        "Launch a full sub-swarm with its own orchestrator and delegates. "
        "Use this when you discover a chunk of work that is too complex "
        "for a single delegate and needs its own decomposition. The "
        "sub-plan runs asynchronously — you can continue your own work. "
        "When the sub-plan completes, its results are posted back to "
        "this plan's orchestrator and task list."
    )
    InputSchema = SwarmLaunchSubplanInput

    def __init__(self, swarm_ctx: dict):
        self._ctx = swarm_ctx

    async def execute(self, name: str = "", description: str = "",
                      delegates_json: str = "[]", **kw) -> str:
        import json as _json

        mgr = self._ctx["get_manager"]()
        plan_id = self._ctx["plan_id"]
        delegate_id = self._ctx["delegate_id"]
        plan = mgr._plans.get(plan_id)
        if not plan:
            return "No active plan found."

        # Parse delegate specs from JSON
        try:
            raw_specs = _json.loads(delegates_json)
        except _json.JSONDecodeError as e:
            return f"Invalid JSON for delegates_json: {e}"

        if not isinstance(raw_specs, list) or len(raw_specs) == 0:
            return "delegates_json must be a non-empty JSON array."

        sub_specs = []
        for i, raw in enumerate(raw_specs, 1):
            did = f"D{i}"
            files = [f.strip() for f in raw.get("files", "").split(",") if f.strip()]
            deps = [d.strip() for d in raw.get("depends_on", "").split(",") if d.strip()]
            sub_specs.append(DelegateSpec(
                delegate_id=did,
                name=raw.get("name", f"Sub-delegate {i}"),
                emoji="🔹",
                scope=raw.get("scope", ""),
                files=files,
                dependencies=deps,
            ))

        # Find the calling delegate's conversation_id for source_conversation_id
        calling_spec = None
        for s in plan.delegate_specs:
            if s.delegate_id == delegate_id:
                calling_spec = s
                break
        source_conv_id = calling_spec.conversation_id if calling_spec else None

        # Note on orchestrator and parent plan
        if plan.orchestrator_id:
            label = (
                f"**{delegate_id} → orchestrator:** Launching sub-plan "
                f"**{name}** with {len(sub_specs)} delegates"
            )
            mgr._persist_delegate_message(plan.orchestrator_id, "assistant", label)

        # Launch asynchronously
        import asyncio
        asyncio.create_task(
            mgr.launch_subplan(
                name=name,
                description=description,
                delegate_specs=sub_specs,
                source_conversation_id=source_conv_id,
                parent_plan_id=plan_id,
                parent_delegate_id=delegate_id,
            ),
            name=f"subplan-{name[:20]}",
        )

        return (
            f"🚀 Sub-plan '{name}' launching with {len(sub_specs)} delegates. "
            f"It runs asynchronously — continue your own work. "
            f"Results will appear on this plan's task list when complete."
        )


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
        SwarmReadLogTool(ctx),
        SwarmRequestDelegateTool(ctx),
        SwarmLaunchSubplanTool(ctx),
    ]
