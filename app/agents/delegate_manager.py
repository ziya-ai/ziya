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
        # Shared concurrency semaphore across all plans
        self._semaphore = asyncio.Semaphore(max_concurrency)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def launch_plan(
        self,
        name: str,
        description: str,
        delegate_specs: List[DelegateSpec],
        *,
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

        # 4. Initialise tracking state
        self._plans[plan_id] = task_plan
        self._statuses[plan_id] = {
            s.delegate_id: "proposed" for s in delegate_specs
        }
        self._crystals[plan_id] = {}
        self._running[plan_id] = set()
        self._tasks[plan_id] = {}
        self._callbacks[plan_id] = on_progress

        self._patch_group_task_plan(group_id, task_plan)

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

        await self._emit(plan_id, delegate_id, "crystal", {
            "crystal_tokens": crystal.crystal_tokens,
            "original_tokens": crystal.original_tokens,
        })

        if self._is_plan_complete(plan_id):
            plan.status = "completed"
            plan.completed_at = time.time()
            self._persist_plan(plan_id)
            await self._emit(plan_id, None, "plan_completed", {
                "total_delegates": len(plan.delegate_specs),
                "total_crystals": len(self._crystals[plan_id]),
            })
            logger.info(f"✅ TaskPlan complete: {plan.name} ({plan_id[:8]})")
        else:
            await self._resolve_and_start(plan_id)

    async def on_delegate_failed(
        self, plan_id: str, delegate_id: str, error: str,
    ) -> None:
        """Handle a delegate that failed during execution."""
        if plan_id not in self._plans:
            return
        logger.error(f"❌ Delegate failed: {delegate_id} — {error}")
        self._statuses[plan_id][delegate_id] = "failed"
        self._running[plan_id].discard(delegate_id)
        await self._emit(plan_id, delegate_id, "failed", {"error": error})
        await self._resolve_and_start(plan_id)

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
        return SwarmBudget(
            delegates=delegates,
            total_active=total_active,
            total_freed=total_freed,
        )

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
            deps_met = all(
                statuses.get(dep) == "crystal" for dep in spec.dependencies
            )
            if deps_met:
                statuses[did] = "ready"
                newly_ready.append(spec)
                logger.info(f"📋 Delegate ready: {spec.name} ({did[:8]})")

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
        async with self._semaphore:
            try:
                messages = self._build_delegate_messages(plan_id, spec)
                logger.info(
                    f"🤖 Running delegate {spec.name} ({did[:8]}) "
                    f"with {len(messages)} message(s)"
                )

                accumulated = ""
                crystal_from_stream: Optional[MemoryCrystal] = None

                async for chunk in self._create_delegate_stream(
                    spec, messages, plan_id
                ):
                    ctype = chunk.get("type")
                    if ctype == "text":
                        accumulated += chunk.get("content", "")
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
                        break

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
                self._statuses[plan_id][did] = "failed"
                self._running[plan_id].discard(did)
                await self._emit(plan_id, did, "failed", {"error": "Cancelled"})
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

        conv_id = spec.conversation_id or f"delegate_{spec.delegate_id}"
        lc_messages = build_messages_for_streaming(
            question=messages[0]["content"] if messages else "",
            chat_history=[],
            files=spec.files,
            conversation_id=conv_id,
            use_langchain_format=True,
        )

        project_root = os.environ.get("ZIYA_USER_CODEBASE_DIR")
        async for chunk in executor.stream_with_tools(
            lc_messages, conversation_id=conv_id, project_root=project_root,
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
        cs = self._get_chat_storage()
        chat = cs.get(chat_id)
        if not chat:
            return
        d = chat.model_dump()
        dm = d.get("delegateMeta", {})
        dm["status"] = status
        d["delegateMeta"] = dm
        cs._write_json(cs._chat_file(chat_id), d)

    def _persist_plan(self, plan_id: str) -> None:
        plan = self._plans.get(plan_id)
        if not plan:
            return
        gs = self._get_group_storage()
        gf = gs._read_groups_file()
        for i, g in enumerate(gf.groups):
            d = g.model_dump()
            tp = d.get("taskPlan")
            if tp and tp.get("name") == plan.name:
                d["taskPlan"] = plan.model_dump()
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
        terminal = {"crystal", "failed"}
        return all(
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

_instance: Optional[DelegateManager] = None


def get_delegate_manager(
    project_id: str = "",
    project_dir: Optional[Path] = None,
    **kwargs,
) -> DelegateManager:
    """Get or create the DelegateManager singleton."""
    global _instance
    if _instance is None:
        if not project_dir:
            from app.utils.paths import get_project_dir
            project_dir = get_project_dir(project_id)
        _instance = DelegateManager(project_id, project_dir, **kwargs)
    return _instance


def reset_delegate_manager() -> None:
    """Reset the singleton (for testing)."""
    global _instance
    _instance = None
