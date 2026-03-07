"""
Delegate Manager — spawns, tracks, and orchestrates parallel delegate threads.

Layer 2 of the delegate orchestration system.  Given a TaskPlan (list of
DelegateSpecs with dependency edges), the manager:

  1. Creates the folder/conversation/context infrastructure
  2. Starts delegates whose dependencies are satisfied
  3. Listens for crystal_ready events to unblock downstream delegates
  4. Enforces a concurrency cap to stay below API rate limits
  5. Reports progress via an event callback (for WebSocket push in T25)

Lifecycle per delegate:
    proposed → ready → running → compacting → crystal (agreed)
                                    ↓
                                 failed (rejected)

Cross-references:
  - design/conversation-graph-tracker.md §Phase 2: Delegate Lifecycle
  - design/newux-context.md §Data Flow: Launching a TaskPlan
  - app/agents/compaction_engine.py — produces MemoryCrystals
  - app/streaming_tool_executor.py — runs individual delegate streams
"""

import asyncio
import time
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

from app.models.delegate import (
    DelegateBudget,
    DelegateMeta,
    DelegateSpec,
    MemoryCrystal,
    SwarmBudget,
    TaskPlan,
)
from app.models.chat import ChatCreate
from app.models.context import ContextCreate
from app.models.group import ChatGroup, ChatGroupCreate, ChatGroupUpdate
from app.utils.logging_utils import logger
from app.agents.compaction_engine import CompactionEngine
from app.agents import delegate_stream_relay

# Type alias for the optional progress callback.
# Signature: async callback(plan_id, delegate_id, new_status, extra_data)
ProgressCallback = Callable[
    [str, Optional[str], str, Dict[str, Any]],
    Coroutine[Any, Any, None],
]

DEFAULT_MAX_CONCURRENCY = 4


class DelegateManager:
    """
    Manages the lifecycle of parallel delegate threads within a TaskPlan.

    Usage::

        manager = DelegateManager(project_id, project_dir)
        result = await manager.launch_plan("Refactor auth", "...", specs)
        # delegates run concurrently; crystal_ready fires on completion
        budget = manager.get_swarm_budget(result["plan_id"])
    """

    def __init__(
        self,
        project_id: str,
        project_dir: Path,
        *,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    ):
        self.project_id = project_id
        self.project_dir = project_dir
        self.max_concurrency = max_concurrency

        # plan_id → TaskPlan metadata
        self._plans: Dict[str, TaskPlan] = {}
        # plan_id → { delegate_id → current_status }
        self._statuses: Dict[str, Dict[str, str]] = {}
        # plan_id → { delegate_id → MemoryCrystal }
        self._crystals: Dict[str, Dict[str, MemoryCrystal]] = {}
        # plan_id → set of currently-running delegate_ids
        self._running: Dict[str, Set[str]] = {}
        # plan_id → { delegate_id → asyncio.Task }
        self._tasks: Dict[str, Dict[str, asyncio.Task]] = {}
        # plan_id → progress callback
        self._callbacks: Dict[str, Optional[ProgressCallback]] = {}

        self._semaphore = asyncio.Semaphore(max_concurrency)
        # Serializes all file mutations: _groups.json writes, individual
        # chat JSON writes, and swarm task list mutations.  RLock so
        # nested calls (e.g. swarm tool → _persist_plan) don't deadlock.
        self._persist_lock = threading.RLock()

        self._group_to_plan: Dict[str, str] = {}
        self.compaction_engine = CompactionEngine()

    def rehydrate(self) -> int:
        """Rebuild in-memory state from persisted ChatGroup/Chat data.

        Called once after construction (in get_delegate_manager) to recover
        plans that survived a server restart.  Delegates that were 'running'
        get marked 'interrupted' since their asyncio Tasks are gone.

        Returns the number of plans recovered.
        """
        gs = self._get_group_storage()
        cs = self._get_chat_storage()
        recovered = 0

        for group in gs.list():
            tp_raw = group.taskPlan
            if not tp_raw:
                continue

            try:
                plan = TaskPlan(**tp_raw) if isinstance(tp_raw, dict) else tp_raw
            except Exception as e:
                logger.warning(f"rehydrate: skipping group {group.id}, bad taskPlan: {e}")
                continue

            # Skip terminal plans — nothing to track
            if plan.status in ("completed", "cancelled"):
                continue

            # Derive a stable plan_id from the orchestrator's DelegateMeta,
            # falling back to the group id.
            plan_id = None
            chats = cs.list(group_id=group.id)
            for chat in chats:
                dm = chat.delegateMeta
                if isinstance(dm, dict) and dm.get("plan_id"):
                    plan_id = dm["plan_id"]
                    break
                elif hasattr(dm, "plan_id") and dm.plan_id:
                    plan_id = dm.plan_id
                    break

            if not plan_id:
                plan_id = group.id  # fallback — won't match old tasks but keeps it addressable

            self._plans[plan_id] = plan
            self._group_to_plan[group.id] = plan_id
            self._statuses[plan_id] = {}
            self._crystals[plan_id] = {}
            self._running[plan_id] = set()
            self._tasks[plan_id] = {}
            self._callbacks[plan_id] = None

            for chat in chats:
                dm = chat.delegateMeta
                if not dm:
                    continue
                dm_dict = dm if isinstance(dm, dict) else dm.model_dump()
                did = dm_dict.get("delegate_id")
                if not did:
                    continue

                status = dm_dict.get("status", "proposed")
                # Delegates that were mid-flight are now interrupted
                if status in ("running", "compacting"):
                    status = "interrupted"
                    self._patch_chat_status(chat.id, "interrupted")

                self._statuses[plan_id][did] = status
                crystal_raw = dm_dict.get("crystal")
                if crystal_raw and status == "crystal":
                    try:
                        crystal = MemoryCrystal(**crystal_raw) if isinstance(crystal_raw, dict) else crystal_raw
                        self._crystals[plan_id][did] = crystal
                    except Exception:
                        pass

            recovered += 1
            logger.info(
                f"♻️  Rehydrated plan {plan.name!r} ({plan_id[:8]}) — "
                f"{len(self._statuses[plan_id])} delegates, "
                f"{sum(1 for s in self._statuses[plan_id].values() if s == 'crystal')} crystals"
            )

        return recovered

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def launch_plan(
        self,
        name: str,
        description: str,
        delegate_specs: List[DelegateSpec],
        *,
        source_conversation_id: Optional[str] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        """
        Create infrastructure and start a TaskPlan.

        Returns dict with plan_id, group_id, orchestrator_id,
        conversation_ids, and context_ids.
        """
        plan_id = str(uuid.uuid4())
        now = time.time()

        # 1. Create folder (ChatGroup) with TaskPlan metadata
        group_storage = self._get_group_storage()
        group = group_storage.create(ChatGroupCreate(name=f"⚡ {name}"))
        group_id = group.id

        task_plan = TaskPlan(
            name=name,
            description=description,
            delegate_specs=delegate_specs,
            status="running",
            created_at=now,
            source_conversation_id=source_conversation_id,
        )
        self._patch_group_task_plan(group_id, task_plan)

        # 2. Create orchestrator conversation
        chat_storage = self._get_chat_storage()
        orch_chat = chat_storage.create(ChatCreate(
            groupId=group_id,
            title=f"🎯 Orchestrator — {name}",
        ))
        orchestrator_id = orch_chat.id
        self._patch_chat_delegate_meta(orchestrator_id, DelegateMeta(
            role="orchestrator",
            plan_id=plan_id,
            status="running",
        ))
        task_plan.orchestrator_id = orchestrator_id

        # Post the initial orchestrator message describing the plan
        delegate_names = ", ".join(f"{s.emoji} {s.name}" for s in delegate_specs)
        orch_intro = (
            f"**orchestrator → all:** Launching {len(delegate_specs)} delegates "
            f"for *{name}*\n\n"
            f"Delegates: {delegate_names}\n\n"
            f"Description: {description}\n\n"
            f"---"
        )
        self._persist_delegate_message(orchestrator_id, "assistant", orch_intro)

        # 3. Create per-delegate conversations and contexts
        conversation_ids: Dict[str, str] = {}
        context_ids: Dict[str, str] = {}

        for spec in delegate_specs:
            # 3a. Scoped Context for the delegate's files
            ctx_id: Optional[str] = None
            if spec.files:
                ctx = self._get_context_storage().create(ContextCreate(
                    name=f"[D] {spec.name}",
                    files=spec.files,
                ))
                ctx_id = ctx.id
                context_ids[spec.delegate_id] = ctx_id

            # 3b. Delegate Chat
            d_chat = chat_storage.create(ChatCreate(
                groupId=group_id,
                title=f"{spec.emoji} {spec.name}",
                contextIds=[ctx_id] if ctx_id else [],
                skillIds=[spec.skill_id] if spec.skill_id else [],
            ))
            conversation_ids[spec.delegate_id] = d_chat.id
            spec.conversation_id = d_chat.id

            self._patch_chat_delegate_meta(d_chat.id, DelegateMeta(
                role="delegate",
                plan_id=plan_id,
                delegate_id=spec.delegate_id,
                delegate_spec=spec,
                status="proposed",
                context_id=ctx_id,
                skill_id=spec.skill_id,
            ))

        # 3c. Post each delegate's task assignment to the orchestrator
        for spec in delegate_specs:
            deps_md = ""
            if spec.dependencies:
                dep_names = []
                for dep_id in spec.dependencies:
                    dep_spec = self._find_spec(task_plan, dep_id)
                    dep_names.append(f"{dep_spec.emoji} {dep_spec.name}" if dep_spec else dep_id)
                deps_md = f"\nWaits for: {', '.join(dep_names)}"

            files_md = ""
            if spec.files:
                files_md = f"\nFiles: {', '.join(f'`{f}`' for f in spec.files[:8])}"
                if len(spec.files) > 8:
                    files_md += f" (+{len(spec.files) - 8} more)"

            task_msg = (
                f"**orchestrator → {spec.emoji} {spec.name}:** "
                f"{spec.scope}"
                f"{files_md}"
                f"{deps_md}"
            )
            self._persist_delegate_message(orchestrator_id, "assistant", task_msg)

        # 4. Initialise tracking state
        # Seed the shared task list from delegate specs
        from app.models.delegate import SwarmTask
        task_plan.task_list = [
            SwarmTask(
                task_id=f"st_{spec.delegate_id}",
                title=spec.name,
                status="open",
                created_at=now,
            )
            for spec in delegate_specs
        ]

        self._plans[plan_id] = task_plan
        self._statuses[plan_id] = {
            s.delegate_id: "proposed" for s in delegate_specs
        }
        self._crystals[plan_id] = {}
        self._running[plan_id] = set()
        self._tasks[plan_id] = {}
        self._callbacks[plan_id] = on_progress

        self._patch_group_task_plan(group_id, task_plan)
        self._group_to_plan[group_id] = plan_id

        # 5. Start ready delegates
        await self._resolve_and_start(plan_id)

        logger.info(
            f"🚀 TaskPlan launched: {name} — "
            f"{len(delegate_specs)} delegates, plan_id={plan_id[:8]}"
        )

        return {
            "plan_id": plan_id,
            "group_id": group_id,
            "orchestrator_id": orchestrator_id,
            "conversation_ids": conversation_ids,
            "context_ids": context_ids,
        }

    async def on_crystal_ready(
        self, plan_id: str, delegate_id: str, crystal: MemoryCrystal,
    ) -> None:
        """Handle crystal_ready from CompactionEngine."""
        if plan_id not in self._plans:
            logger.warning(f"💎 Crystal for unknown plan {plan_id[:8]}")
            return

        # Auto-complete this delegate's entry on the shared task list
        plan = self._plans[plan_id]
        for t in plan.task_list:
            if t.task_id == f"st_{delegate_id}" and t.status != "done":
                t.status = "done"
                t.summary = crystal.summary[:120] if crystal.summary else "Completed"
                t.completed_at = time.time()
                break

        logger.info(
            f"💎 Crystal ready: {delegate_id} ({plan_id[:8]}) "
            f"{crystal.original_tokens:,} → {crystal.crystal_tokens:,} tokens"
        )

        self._statuses[plan_id][delegate_id] = "crystal"
        self._crystals[plan_id][delegate_id] = crystal
        self._running[plan_id].discard(delegate_id)

        # Persist crystal on the Chat
        plan = self._plans[plan_id]
        spec = self._find_spec(plan, delegate_id)
        if spec and spec.conversation_id:
            self._patch_chat_crystal(spec.conversation_id, crystal)

        plan.crystals.append(crystal)
        self._persist_plan(plan_id)

        # Post crystal arrival to orchestrator conversation (non-blocking).
        # The LLM commentary is observational and must not delay scheduling.
        if plan.orchestrator_id:
            asyncio.create_task(
                self._orchestrator_receive_crystal(plan_id, delegate_id, crystal),
                name=f"orch-recv-{delegate_id[:8]}",
            )

        # T39: Retroactive review (non-blocking background task)
        asyncio.create_task(
            self._background_retroactive_review(plan_id, delegate_id, crystal),
            name=f"retro-{delegate_id[:8]}",
        )

        await self._emit(plan_id, delegate_id, "crystal", {
            "crystal_tokens": crystal.crystal_tokens,
            "original_tokens": crystal.original_tokens,
        })

        if self._is_plan_complete(plan_id):
            plan.status = "completed"
            plan.completed_at = time.time()
            self._persist_plan(plan_id)
            await self._orchestrator_final_synthesis(plan_id)
            self._post_completion_to_source(plan_id)
            # Signal the orchestrator's WebSocket that the stream is done
            if plan.orchestrator_id:
                await delegate_stream_relay.push(
                    plan.orchestrator_id, {"type": "stream_end"}
                )
            await self._emit(plan_id, None, "plan_completed", {
                "total_delegates": len(plan.delegate_specs),
                "total_crystals": len(self._crystals[plan_id]),
            })
            logger.info(f"✅ TaskPlan complete: {plan.name} ({plan_id[:8]})")
        else:
            await self._resolve_and_start(plan_id)
            self._post_progress_to_source(plan_id, delegate_id, crystal)

    async def on_delegate_failed(
        self, plan_id: str, delegate_id: str, error: str,
    ) -> None:
        """Handle a delegate that failed during execution."""
        if plan_id not in self._plans:
            return
        logger.error(f"❌ Delegate failed: {delegate_id} — {error}")
        self._statuses[plan_id][delegate_id] = "failed"
        self._running[plan_id].discard(delegate_id)

        # Mark on shared task list so other delegates see the failure
        plan = self._plans[plan_id]
        for t in plan.task_list:
            if t.task_id == f"st_{delegate_id}" and t.status != "done":
                t.status = "blocked"
                t.summary = f"Failed: {error[:100]}"
                break

        # Persist error to Chat so user can see why it failed
        spec = self._find_spec(plan, delegate_id)
        if spec and spec.conversation_id:
            self._persist_delegate_message(
                spec.conversation_id, "assistant",
                f"❌ **Delegate failed:** {error}"
            )
            self._patch_chat_status(spec.conversation_id, "failed")

        self._persist_plan(plan_id)
        await self._emit(plan_id, delegate_id, "failed", {"error": error})
        await self._resolve_and_start(plan_id)

    async def _background_retroactive_review(
        self, plan_id: str, delegate_id: str, crystal: MemoryCrystal,
    ) -> None:
        """Check if any late-answered questions affect this crystal.

        Placeholder — full implementation requires tracking open questions
        per delegate.  See design/conversation-graph-tracker.md §Retroactive Review.
        """
        # When open_question tracking is implemented, evaluate whether
        # this crystal's work is compatible with late answers.
        # Returns: 'preserved' | 'extended' | 'discarded'
        logger.debug(
            f"🔄 Retroactive review for {delegate_id[:8]} — "
            f"no open questions tracked yet, skipping"
        )

    def get_plan_status(self, plan_id: str) -> Optional[Dict[str, Any]]:
        """Get current status of a TaskPlan and all delegates."""
        if plan_id not in self._plans:
            return None
        plan = self._plans[plan_id]
        statuses = self._statuses[plan_id]
        return {
            "plan_id": plan_id,
            "name": plan.name,
            "status": plan.status,
            "delegates": {
                did: {
                    "status": st,
                    "has_crystal": did in self._crystals.get(plan_id, {}),
                }
                for did, st in statuses.items()
            },
            "running_count": len(self._running.get(plan_id, set())),
            "crystal_count": len(self._crystals.get(plan_id, {})),
            "total_delegates": len(plan.delegate_specs),
            "task_list": [t.model_dump() for t in plan.task_list],
        }

    def get_swarm_budget(self, plan_id: str) -> Optional[SwarmBudget]:
        """Build a SwarmBudget snapshot for a TaskPlan."""
        if plan_id not in self._plans:
            return None

        plan = self._plans[plan_id]
        statuses = self._statuses[plan_id]
        crystals = self._crystals.get(plan_id, {})
        delegates: Dict[str, DelegateBudget] = {}
        total_active = 0
        total_freed = 0
        for spec in plan.delegate_specs:
            did = spec.delegate_id
            crystal = crystals.get(did)
            budget = DelegateBudget(status=statuses.get(did, "proposed"))
            if crystal:
                budget.original_tokens = crystal.original_tokens
                budget.active_tokens = crystal.crystal_tokens
                total_freed += crystal.original_tokens - crystal.crystal_tokens
            delegates[did] = budget
            total_active += budget.active_tokens
        return SwarmBudget(
            delegates=delegates,
            total_active=total_active,
            total_freed=total_freed,
        )

    async def retry_delegate(
        self, plan_id: str, delegate_id: str
    ) -> Dict[str, Any]:
        """Reset a failed/interrupted delegate and restart it.

        The delegate's status is set back to 'proposed' so that
        ``_resolve_and_start`` will pick it up if deps are met.
        Downstream delegates that were cascade-failed are also
        reset to 'proposed' so they get a second chance.
        """
        if plan_id not in self._plans:
            raise ValueError(f"Unknown plan: {plan_id}")

        statuses = self._statuses[plan_id]
        current = statuses.get(delegate_id)
        if current not in ("failed", "interrupted"):
            raise ValueError(
                f"Delegate {delegate_id} is '{current}', not retryable "
                f"(must be 'failed' or 'interrupted')"
            )

        # Reset this delegate
        statuses[delegate_id] = "proposed"
        spec = self._find_spec(self._plans[plan_id], delegate_id)
        if spec and spec.conversation_id:
            self._patch_chat_status(spec.conversation_id, "proposed")

        # Also reset any downstream delegates that cascade-failed
        plan = self._plans[plan_id]
        for s in plan.delegate_specs:
            if delegate_id in s.dependencies and statuses.get(s.delegate_id) == "failed":
                statuses[s.delegate_id] = "proposed"
                if s.conversation_id:
                    self._patch_chat_status(s.conversation_id, "proposed")

        await self._resolve_and_start(plan_id)
        self._persist_plan(plan_id)
        return {"retried": delegate_id, "status": statuses.get(delegate_id, "proposed")}

    async def promote_to_stub_crystal(
        self, plan_id: str, delegate_id: str,
    ) -> Dict[str, Any]:
        """Promote a failed delegate to a stub crystal to unblock downstream.

        Creates a minimal MemoryCrystal marking the delegate as 'completed
        via manual promotion' and triggers the normal crystal cascade so
        downstream delegates can proceed.
        """
        statuses = self._statuses.get(plan_id, {})
        current = statuses.get(delegate_id)
        if current not in ("failed", "interrupted"):
            raise ValueError(
                f"Delegate {delegate_id} has status '{current}' "
                f"(must be 'failed' or 'interrupted' to promote)"
            )

        plan = self._plans.get(plan_id)
        spec = self._find_spec(plan, delegate_id) if plan else None
        name = spec.name if spec else delegate_id

        stub = MemoryCrystal(
            delegate_id=delegate_id,
            task=name,
            summary=f"[Stub crystal] {name} was manually promoted to unblock downstream delegates.",
            files_changed=[],
            decisions=[f"Delegate '{name}' failed and was promoted to stub crystal"],
            original_tokens=0,
            crystal_tokens=0,
            created_at=time.time(),
        )

        await self.on_crystal_ready(plan_id, delegate_id, stub)
        return {"promoted": delegate_id, "status": "crystal"}

    def get_crystal(
        self, plan_id: str, delegate_id: str
    ) -> Optional[MemoryCrystal]:
        """Retrieve a stored crystal."""
        return self._crystals.get(plan_id, {}).get(delegate_id)

    def get_upstream_crystals(
        self, plan_id: str, delegate_id: str
    ) -> List[MemoryCrystal]:
        """Get crystals from this delegate's dependency chain."""
        if plan_id not in self._plans:
            return []
        spec = self._find_spec(self._plans[plan_id], delegate_id)
        if not spec:
            return []
        crystals = self._crystals.get(plan_id, {})
        return [crystals[d] for d in spec.dependencies if d in crystals]

    # ------------------------------------------------------------------
    # Internal: readiness resolution and execution
    # ------------------------------------------------------------------

    async def _resolve_and_start(self, plan_id: str) -> None:
        """Mark ready delegates and start them up to concurrency cap."""
        plan = self._plans[plan_id]
        statuses = self._statuses[plan_id]
        newly_ready: List[DelegateSpec] = []

        for spec in plan.delegate_specs:
            did = spec.delegate_id
            if statuses.get(did) != "proposed":
                continue
            # A dependency is satisfied if it produced a crystal.
            # A failed dependency means this delegate can never run.
            deps_met = all(
                statuses.get(dep) in ("crystal", "failed") for dep in spec.dependencies
            )
            if deps_met:
                # Only actually run if all deps succeeded
                all_deps_crystal = all(
                    statuses.get(dep) == "crystal" for dep in spec.dependencies
                )
                if all_deps_crystal:
                    statuses[did] = "ready"
                    newly_ready.append(spec)
                    logger.info(f"📋 Delegate ready: {spec.name} ({did[:8]})")
                else:
                    # At least one dep failed — cascade the failure
                    statuses[did] = "failed"
                    if spec.conversation_id:
                        self._patch_chat_status(spec.conversation_id, "failed")
                    await self._emit(plan_id, did, "failed", {
                        "error": "Upstream dependency failed",
                    })
                    logger.warning(
                        f"⛔ Delegate {spec.name} ({did[:8]}) failed: upstream dependency failed"
                    )

        for spec in newly_ready:
            await self._start_delegate(plan_id, spec)

    async def _start_delegate(
        self, plan_id: str, spec: DelegateSpec
    ) -> None:
        """Acquire a concurrency slot and launch a delegate Task."""
        did = spec.delegate_id
        if did in self._running.get(plan_id, set()):
            return

        self._statuses[plan_id][did] = "running"
        self._running[plan_id].add(did)
        if spec.conversation_id:
            self._patch_chat_status(spec.conversation_id, "running")
        await self._emit(plan_id, did, "running", {"name": spec.name})

        task = asyncio.create_task(
            self._run_delegate(plan_id, spec),
            name=f"delegate-{did[:8]}",
        )
        self._tasks.setdefault(plan_id, {})[did] = task
        logger.info(
            f"▶️  Delegate started: {spec.name} ({did[:8]}) "
            f"[{len(self._running[plan_id])}/{self.max_concurrency} slots]"
        )

    async def _run_delegate(
        self, plan_id: str, spec: DelegateSpec
    ) -> None:
        """Execute a delegate's stream_with_tools loop under semaphore."""
        did = spec.delegate_id
        # Build messages and persist user prompt BEFORE semaphore so
        # the conversation shows the task even while queued.
        messages = self._build_delegate_messages(plan_id, spec)
        logger.info(
            f"🤖 Running delegate {spec.name} ({did[:8]}) "
            f"with {len(messages)} message(s)"
        )

        # Persist user prompt immediately
        if spec.conversation_id and messages:
            self._persist_delegate_message(
                spec.conversation_id, "human", messages[0].get("content", "")
            )

        async with self._semaphore:
            try:
                accumulated = ""
                crystal_from_stream: Optional[MemoryCrystal] = None
                streaming_msg_id: Optional[str] = None
                _last_flush_len = 0
                _FLUSH_INTERVAL = 2000  # chars between disk flushes

                async for chunk in self._create_delegate_stream(
                    spec, messages, plan_id
                ):
                    ctype = chunk.get("type")
                    if ctype == "text":
                        accumulated += chunk.get("content", "")
                        # Push to WebSocket relay for live streaming
                        await delegate_stream_relay.push(
                            spec.conversation_id or did, chunk
                        )
                        # Periodic disk flush for non-WS viewers
                        if len(accumulated) - _last_flush_len >= _FLUSH_INTERVAL:
                            _last_flush_len = len(accumulated)
                            streaming_msg_id = self._update_delegate_assistant_message(
                                spec.conversation_id,
                                accumulated,
                                msg_id=streaming_msg_id,
                            )
                    elif ctype == "crystal_ready":
                        cd = chunk.get("crystal")
                        if cd:
                            crystal_from_stream = MemoryCrystal(**cd)
                    elif ctype == "error":
                        await self.on_delegate_failed(
                            plan_id, did, chunk.get("content", "Unknown")
                        )
                        return
                    elif ctype == "stream_end":
                        # Notify relay that stream is done
                        await delegate_stream_relay.push(
                            spec.conversation_id or did,
                            {"type": "stream_end"}
                        )
                        break
                    elif ctype in ("tool_start", "tool_display", "processing"):
                        # Forward tool events to relay so frontend can render them
                        await delegate_stream_relay.push(
                            spec.conversation_id or did, chunk
                        )
                # Final persistence of complete response
                if spec.conversation_id and accumulated.strip():
                    self._update_delegate_assistant_message(
                        spec.conversation_id,
                        accumulated,
                        msg_id=streaming_msg_id,
                    )
                    logger.info(f"💬 Final persist: {len(accumulated)} chars to delegate {did[:8]}")


                if crystal_from_stream:
                    await self.on_crystal_ready(
                        plan_id, did, crystal_from_stream
                    )
                else:
                    # No crystal produced (below threshold or failure).
                    # Create a stub so downstream delegates aren't blocked.
                    stub = MemoryCrystal(
                        delegate_id=did,
                        task=spec.name,
                        summary=f"Completed: {spec.name}. "
                                f"{accumulated[:200]}",
                        original_tokens=len(accumulated) // 4,
                        crystal_tokens=50,
                        created_at=time.time(),
                    )
                    await self.on_crystal_ready(plan_id, did, stub)

            except asyncio.CancelledError:
                logger.warning(f"🛑 Delegate cancelled: {spec.name}")
                await self.on_delegate_failed(plan_id, did, "Cancelled by user")
            except Exception as exc:
                logger.error(
                    f"❌ Delegate error: {spec.name} — {exc}", exc_info=True
                )
                await self.on_delegate_failed(plan_id, did, str(exc))

    def _build_delegate_messages(
        self, plan_id: str, spec: DelegateSpec
    ) -> List[Dict[str, Any]]:
        """Build initial messages including upstream crystal context."""
        parts: List[str] = []

        upstream = self.get_upstream_crystals(plan_id, spec.delegate_id)
        if upstream:
            crystal_ctx = "\n\n".join(
                f"## Prior work: {c.task}\n"
                f"{c.summary}\n"
                f"Files changed: {', '.join(fc.path for fc in c.files_changed)}\n"
                f"Key decisions: {'; '.join(c.decisions[:3])}"
                for c in upstream
            )
            parts.append(
                "The following delegate tasks have already been completed. "
                f"Use their results as context:\n\n{crystal_ctx}"
            )

        # Include current shared task list snapshot
        plan = self._plans.get(plan_id)
        if plan and plan.task_list:
            task_lines = []
            for t in plan.task_list:
                marker = {"open": "○", "claimed": "◉", "done": "✓", "blocked": "✗"}.get(t.status, "?")
                claimed = f" (claimed: {t.claimed_by})" if t.claimed_by else ""
                done_note = f" — {t.summary}" if t.summary else ""
                task_lines.append(f"  {marker} [{t.task_id}] {t.title}{claimed}{done_note}")
            parts.append(
                "## Shared Task List\n"
                "Other delegates can see and update this list. "
                "Use swarm_task_list to refresh, swarm_claim_task to claim work, "
                "swarm_complete_task when done, swarm_add_task for discovered work.\n\n"
                + "\n".join(task_lines)
            )

        parts.append(
            f"Your task: {spec.name}\n\n"
            f"Scope: {spec.scope}\n\n"
            "Work within the files listed in your context. "
            "Complete the task thoroughly and provide clear summaries "
            "of what you changed and why."
        )
        return [{"role": "user", "content": "\n\n---\n\n".join(parts)}]

    async def _create_delegate_stream(
        self, spec: DelegateSpec, messages: List[Dict[str, Any]], plan_id: str
    ):
        """Yield chunks from StreamingToolExecutor for this delegate."""
        import os
        from app.streaming_tool_executor import StreamingToolExecutor
        from app.agents.models import ModelManager
        from app.server import build_messages_for_streaming

        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        if endpoint != "bedrock":
            yield {"type": "error", "content": f"Delegates not yet supported for endpoint: {endpoint}"}
            return

        state = ModelManager.get_state()
        executor = StreamingToolExecutor(
            profile_name=state.get("aws_profile"),
            region=state.get("aws_region", "us-west-2"),
        )

        # Resolve skill prompt if delegate has an assigned skill
        skill_prompt = ""
        if spec.skill_id:
            try:
                from app.storage.skills import SkillStorage
                from app.services.token_service import TokenService
                skill_storage = SkillStorage(self.project_dir, TokenService())
                skill = skill_storage.get(spec.skill_id)
                if skill:
                    skill_prompt = f"[Active Skill: {skill.name}]\n{skill.prompt}"
            except Exception as e:
                logger.warning(f"Could not resolve skill {spec.skill_id}: {e}")

        conv_id = spec.conversation_id or f"delegate_{spec.delegate_id}"
        lc_messages = build_messages_for_streaming(
            question=messages[0]["content"] if messages else "",
            chat_history=[],
            files=spec.files,
            conversation_id=conv_id,
            use_langchain_format=True,
            system_prompt_addition=skill_prompt,
        )

        # Create swarm coordination tools for this delegate
        from app.agents.swarm_tools import create_swarm_tools
        swarm_tools = create_swarm_tools(
            plan_id=plan_id,
            delegate_id=spec.delegate_id,
            get_manager=lambda: self,
        )

        project_root = os.environ.get("ZIYA_USER_CODEBASE_DIR")
        async for chunk in executor.stream_with_tools(
            lc_messages, conversation_id=conv_id, project_root=project_root,
            is_delegate=True, extra_tools=swarm_tools,
        ):
            yield chunk

    # ------------------------------------------------------------------
    # Storage helpers (lazy imports to avoid circular deps)
    # ------------------------------------------------------------------

    def _get_chat_storage(self):
        from app.storage.chats import ChatStorage
        return ChatStorage(self.project_dir)

    def _get_group_storage(self):
        from app.storage.groups import ChatGroupStorage
        return ChatGroupStorage(self.project_dir)

    def _get_context_storage(self):
        from app.storage.contexts import ContextStorage
        from app.services.token_service import TokenService
        import os
        storage = ContextStorage(self.project_dir, TokenService())
        pp = os.environ.get("ZIYA_USER_CODEBASE_DIR", "")
        if pp:
            storage.set_project_path(pp)
        return storage

    def _patch_group_task_plan(
        self, group_id: str, task_plan: TaskPlan
    ) -> None:
        """Write task_plan directly onto a ChatGroup's JSON."""
        with self._persist_lock:
            gs = self._get_group_storage()
            gf = gs._read_groups_file()
            for i, g in enumerate(gf.groups):
                if g.id == group_id:
                    d = g.model_dump()
                    d["taskPlan"] = task_plan.model_dump()
                    d["systemInstructions"] = task_plan.description
                    gf.groups[i] = ChatGroup(**d)
                    break
            gs._write_groups_file(gf)

    def _patch_chat_delegate_meta(
        self, chat_id: str, meta: DelegateMeta
    ) -> None:
        with self._persist_lock:
            cs = self._get_chat_storage()
            chat = cs.get(chat_id)
            if not chat:
                return
            d = chat.model_dump()
            d["delegateMeta"] = meta.model_dump()
            cs._write_json(cs._chat_file(chat_id), d)

    def _patch_chat_crystal(
        self, chat_id: str, crystal: MemoryCrystal
    ) -> None:
        with self._persist_lock:
            cs = self._get_chat_storage()
            chat = cs.get(chat_id)
            if not chat:
                return
            d = chat.model_dump()
            dm = d.get("delegateMeta", {})
            dm["crystal"] = crystal.model_dump()
            dm["status"] = "crystal"
            d["delegateMeta"] = dm
            cs._write_json(cs._chat_file(chat_id), d)

    def _patch_chat_status(self, chat_id: str, status: str) -> None:
        with self._persist_lock:
            cs = self._get_chat_storage()
            chat = cs.get(chat_id)
            if not chat:
                return
            d = chat.model_dump()
            dm = d.get("delegateMeta", {})
            dm["status"] = status
            d["delegateMeta"] = dm
            cs._write_json(cs._chat_file(chat_id), d)

    # ------------------------------------------------------------------
    # Active Orchestrator
    # ------------------------------------------------------------------

    async def _orchestrator_receive_crystal(
        self, plan_id: str, delegate_id: str, crystal: MemoryCrystal,
    ) -> None:
        """Process a crystal arrival in the orchestrator conversation.

        Posts the crystal as a labeled message, then runs an orchestrator
        LLM turn to analyze and optionally direct follow-up work.
        """
        plan = self._plans.get(plan_id)
        if not plan or not plan.orchestrator_id:
            return

        spec = self._find_spec(plan, delegate_id)
        spec_name = spec.name if spec else delegate_id
        spec_emoji = spec.emoji if spec else "🔵"

        # 1. Post crystal arrival as incoming message
        files_md = ""
        if crystal.files_changed:
            files_md = "\n".join(
                f"  - `{fc.path}` {fc.line_delta}" for fc in crystal.files_changed[:5]
            )
            files_md = f"\nFiles changed:\n{files_md}"

        decisions_md = ""
        if crystal.decisions:
            decisions_md = "\nKey decisions: " + "; ".join(crystal.decisions[:3])

        incoming = (
            f"**{spec_emoji} {spec_name} → orchestrator:** [Crystal received]\n\n"
            f"{crystal.summary}"
            f"{files_md}"
            f"{decisions_md}\n\n"
            f"*({crystal.original_tokens:,} → {crystal.crystal_tokens:,} tokens)*"
        )
        self._persist_delegate_message(plan.orchestrator_id, "assistant", incoming)
        await delegate_stream_relay.push(plan.orchestrator_id, {
            "type": "orchestrator_message",
            "content": incoming,
        })

        # 2. Run orchestrator LLM turn to analyze
        completed = [
            did for did, st in self._statuses.get(plan_id, {}).items()
            if st == "crystal"
        ]
        total = len(plan.delegate_specs)
        progress = f"{len(completed)}/{total}"

        analysis_prompt = (
            f"You are the orchestrator for task plan '{plan.name}'.\n\n"
            f"Progress: {progress} delegates completed.\n\n"
            f"Delegate '{spec_name}' just completed with this crystal:\n"
            f"Summary: {crystal.summary}\n"
            f"Files: {', '.join(fc.path for fc in crystal.files_changed[:5])}\n"
            f"Decisions: {'; '.join(crystal.decisions[:3])}\n\n"
            f"Briefly analyze: Is this acceptable? Any concerns? "
            f"Note any cross-delegate dependencies or conflicts. "
            f"Respond in 2-4 sentences."
        )

        try:
            analysis = await self._orchestrator_llm_call(analysis_prompt)
            if analysis:
                response = (
                    f"**orchestrator → {spec_emoji} {spec_name}:** {analysis}"
                )
                self._persist_delegate_message(
                    plan.orchestrator_id, "assistant", response
                )
                await delegate_stream_relay.push(plan.orchestrator_id, {
                    "type": "orchestrator_message",
                    "content": response,
                })
        except Exception as exc:
            logger.warning(f"🎯 Orchestrator analysis failed: {exc}")

    async def _orchestrator_final_synthesis(self, plan_id: str) -> None:
        """Run the orchestrator's final synthesis when all delegates complete."""
        plan = self._plans.get(plan_id)
        if not plan or not plan.orchestrator_id:
            return

        crystals = self._crystals.get(plan_id, {})
        statuses = self._statuses.get(plan_id, {})

        crystal_summaries = []
        for spec in plan.delegate_specs:
            c = crystals.get(spec.delegate_id)
            status = statuses.get(spec.delegate_id, "unknown")
            if c:
                crystal_summaries.append(
                    f"- {spec.emoji} {spec.name} ({status}): {c.summary}"
                )
            else:
                crystal_summaries.append(
                    f"- {spec.emoji} {spec.name} ({status}): No crystal"
                )

        synthesis_prompt = (
            f"You are the orchestrator for task plan '{plan.name}'.\n"
            f"Description: {plan.description}\n\n"
            f"All {len(plan.delegate_specs)} delegates have completed. "
            f"Here are their results:\n\n"
            + "\n".join(crystal_summaries) + "\n\n"
            f"Provide a final synthesis: What was accomplished overall? "
            f"Any gaps or concerns? What should the user know? "
            f"Respond in a structured summary (5-10 sentences)."
        )

        try:
            synthesis = await self._orchestrator_llm_call(synthesis_prompt)
            if synthesis:
                msg = f"**orchestrator → source:** ✅ Final Synthesis\n\n{synthesis}"
                self._persist_delegate_message(
                    plan.orchestrator_id, "assistant", msg
                )
                await delegate_stream_relay.push(plan.orchestrator_id, {
                    "type": "orchestrator_message",
                    "content": msg,
                })
        except Exception as exc:
            logger.warning(f"🎯 Orchestrator synthesis failed: {exc}")

    async def _orchestrator_llm_call(self, prompt: str) -> str:
        """Make a lightweight LLM call for orchestrator analysis."""
        from app.agents.compaction_engine import CompactionEngine
        engine = CompactionEngine()
        # Reuse the same model path as compaction summaries
        return await engine._call_summary_model(
            task="orchestrator analysis",
            files="",
            decisions="",
            last_msg=prompt,
        )

    # ------------------------------------------------------------------
    # Incremental message persistence
    # ------------------------------------------------------------------

    def _update_delegate_assistant_message(
        self, chat_id: Optional[str], content: str, *, msg_id: Optional[str] = None
    ) -> Optional[str]:
        """Create or update a specific assistant message for incremental streaming.

        Returns the message ID so callers can track the same message across flushes.
        """
        if not chat_id or not content.strip():
            return msg_id
        from app.models.chat import Message
        import uuid as _uuid
        with self._persist_lock:
            cs = self._get_chat_storage()
            chat = cs.get(chat_id)
            if not chat:
                return msg_id

            # Find the specific message we're updating by ID, or create a new one
            target_idx = None
            if msg_id:
                for i, m in enumerate(chat.messages):
                    if m.id == msg_id:
                        target_idx = i
                        break

            if target_idx is not None:
                existing = chat.messages[target_idx]
                chat.messages[target_idx] = Message(
                    id=existing.id, role="assistant",
                    content=content, timestamp=int(time.time() * 1000),
                )
                used_id = existing.id
            else:
                new_id = str(_uuid.uuid4())
                chat.messages.append(Message(
                    id=new_id, role="assistant",
                    content=content, timestamp=int(time.time() * 1000),
                ))
                used_id = new_id
            chat.lastActiveAt = int(time.time() * 1000)
            cs._write_json(cs._chat_file(chat_id), chat.model_dump())
            return used_id

    def _post_completion_to_source(self, plan_id: str) -> None:
        """Write a summary message to the conversation that launched this plan."""
        plan = self._plans.get(plan_id)
        if not plan or not plan.source_conversation_id:
            return

        crystals = self._crystals.get(plan_id, {})
        statuses = self._statuses.get(plan_id, {})

        # Build per-delegate rows
        rows: list[str] = []
        for spec in plan.delegate_specs:
            did = spec.delegate_id
            status = statuses.get(did, "unknown")
            icon = "✅" if status == "crystal" else "❌"
            crystal = crystals.get(did)
            files_col = ""
            if crystal and crystal.files_changed:
                files_col = ", ".join(
                    f"`{fc.path}` {fc.line_delta}" for fc in crystal.files_changed[:3]
                )
            rows.append(f"| {spec.emoji} {spec.name} | {icon} {status} | {files_col} |")

        table = "\n".join(rows)

        crystal_count = len(crystals)
        total = len(plan.delegate_specs)

        # Collect key decisions across all crystals
        all_decisions: list[str] = []
        for c in crystals.values():
            all_decisions.extend(c.decisions[:2])
        decisions_md = ""
        if all_decisions:
            items = "\n".join(f"- {d}" for d in all_decisions[:6])
            decisions_md = f"\n\n**Key decisions across delegates:**\n{items}"

        # Crystal summaries in a collapsible block
        summaries: list[str] = []
        for spec in plan.delegate_specs:
            c = crystals.get(spec.delegate_id)
            if c and c.summary:
                summaries.append(f"**{spec.name}:** {c.summary}")
        summaries_md = ""
        if summaries:
            body = "\n\n".join(summaries)
            summaries_md = (
                f"\n\n<details>\n<summary>Crystal summaries</summary>\n\n"
                f"{body}\n</details>"
            )

        content = (
            f"## ✅ Task Plan Complete: {plan.name}\n\n"
            f"**{crystal_count}/{total}** delegates completed successfully.\n\n"
            f"| Delegate | Status | Files Changed |\n"
            f"|----------|--------|---------------|\n"
            f"{table}"
            f"{decisions_md}"
            f"{summaries_md}"
        )

        from app.models.chat import Message
        import uuid as _uuid
        msg = Message(
            id=str(_uuid.uuid4()),
            role="assistant",
            content=content,
            timestamp=int(time.time() * 1000),
        )
        cs = self._get_chat_storage()
        cs.add_message(plan.source_conversation_id, msg)
        logger.info(
            f"📬 Posted completion summary to source conversation "
            f"{plan.source_conversation_id[:8]}"
        )

    def _post_progress_to_source(
        self, plan_id: str, delegate_id: str, crystal: MemoryCrystal,
    ) -> None:
        """Post a brief progress update to the source conversation per crystal."""
        plan = self._plans.get(plan_id)
        if not plan or not plan.source_conversation_id:
            return

        spec = self._find_spec(plan, delegate_id)
        name = f"{spec.emoji} {spec.name}" if spec else delegate_id
        total = len(plan.delegate_specs)
        done = sum(
            1 for s in self._statuses.get(plan_id, {}).values()
            if s == "crystal"
        )

        summary_preview = crystal.summary[:120] if crystal.summary else ""
        content = f"💎 **{done}/{total}** — {name} completed: {summary_preview}"

        from app.models.chat import Message
        import uuid as _uuid
        msg = Message(
            id=str(_uuid.uuid4()),
            role="assistant",
            content=content,
            timestamp=int(time.time() * 1000),
        )
        cs = self._get_chat_storage()
        cs.add_message(plan.source_conversation_id, msg)

    def _persist_delegate_message(
        self, chat_id: str, role: str, content: str
    ) -> None:
        """Write a message (human or assistant) to a delegate's chat file."""
        from app.models.chat import Message
        import uuid
        cs = self._get_chat_storage()
        msg = Message(
            id=str(uuid.uuid4()),
            role=role,
            content=content,
            timestamp=int(time.time() * 1000),
        )
        cs.add_message(chat_id, msg)

    def _persist_plan(self, plan_id: str) -> None:
        plan = self._plans.get(plan_id)
        if not plan:
            return

        # Reverse-lookup the group_id for this plan
        group_id = None
        for gid, pid in self._group_to_plan.items():
            if pid == plan_id:
                group_id = gid
                break
        if not group_id:
            logger.warning(f"_persist_plan: no group_id found for plan {plan_id[:8]}")
            return

        with self._persist_lock:
            gs = self._get_group_storage()
            gf = gs._read_groups_file()
            for i, g in enumerate(gf.groups):
                if g.id == group_id:
                    d = g.model_dump()
                    d["taskPlan"] = plan.model_dump(mode="json")
                    gf.groups[i] = ChatGroup(**d)
                    gs._write_groups_file(gf)
                    return

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _find_spec(plan: TaskPlan, delegate_id: str) -> Optional[DelegateSpec]:
        for s in plan.delegate_specs:
            if s.delegate_id == delegate_id:
                return s
        return None

    def _is_plan_complete(self, plan_id: str) -> bool:
        # 'interrupted' is NOT terminal — it means a delegate was mid-flight
        # when the server restarted and needs retry/promote to proceed.
        terminal = {"crystal", "failed"}
        return bool(self._statuses.get(plan_id)) and all(
            s in terminal for s in self._statuses.get(plan_id, {}).values()
        )

    async def _emit(
        self,
        plan_id: str,
        delegate_id: Optional[str],
        event: str,
        data: Dict[str, Any],
    ) -> None:
        cb = self._callbacks.get(plan_id)
        if cb:
            try:
                await cb(plan_id, delegate_id, event, data)
            except Exception as exc:
                logger.warning(f"Progress callback error: {exc}")

    async def cancel_plan(self, plan_id: str) -> None:
        """Cancel all running delegates in a plan."""
        for did, task in self._tasks.get(plan_id, {}).items():
            if not task.done():
                task.cancel()
                logger.info(f"🛑 Cancelling delegate {did[:8]}")
        self._running.pop(plan_id, None)
        self._tasks.pop(plan_id, None)
        plan = self._plans.get(plan_id)
        if plan:
            plan.status = "cancelled"
            self._persist_plan(plan_id)
        await self._emit(plan_id, None, "plan_cancelled", {})

    def cleanup_plan(self, plan_id: str) -> None:
        """Remove in-memory state for a completed/cancelled plan."""
        for attr in (
            self._plans, self._statuses, self._crystals,
            self._running, self._tasks, self._callbacks,
        ):
            attr.pop(plan_id, None)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_instances: Dict[str, DelegateManager] = {}


def get_delegate_manager(
    project_id: str = "",
    project_dir: Optional[Path] = None,
    **kwargs,
) -> DelegateManager:
    """Get or create a DelegateManager for the given project."""
    if not project_id:
        raise ValueError("get_delegate_manager requires a project_id when no instances exist")
    if project_id not in _instances:
        if not project_dir:
            from app.utils.paths import get_project_dir
            project_dir = get_project_dir(project_id)
        mgr = DelegateManager(project_id, project_dir, **kwargs)
        mgr.rehydrate()
        _instances[project_id] = mgr
    return _instances[project_id]


def reset_delegate_manager() -> None:
    """Reset the singleton (for testing)."""
    _instances.clear()
