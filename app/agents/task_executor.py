"""
Task executor — run a single Task block in an isolated sandbox.

Design invariant (see design/task-cards.md):
  A task's conversation never leaves its task.  The block's
  instructions become a fresh conversation with no parent
  history.  When the block completes, only an Artifact flows
  back — not the conversation transcript.

Scope handling:
  - tools: strict allowlist — non-listed MCP tools are not exposed
    to the model for this task.  Empty/None scope means "no
    restriction" (all available tools are exposed).
  - skills: loaded from SkillStorage and prepended to the system
    prompt in the same format delegate_manager uses.  Missing
    skills are recorded in the artifact's decisions but do not
    abort the run.
  - files: text contents are preloaded into the system prompt as
    fenced blocks.  This is advisory rather than strict — the
    model can still use file_read to reach other files.  Each
    file is capped at ~128 KB and total preloaded bytes at ~512
    KB to keep the context bounded.
"""

import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from ..models.task_card import Block, Artifact

logger = logging.getLogger(__name__)


# Caps on preloaded file content.  Intentionally conservative —
# the goal is to seed the task with relevant code, not to ship
# the entire project into the prompt.
_MAX_FILE_BYTES = 128 * 1024
_MAX_TOTAL_FILE_BYTES = 512 * 1024


class TaskExecutorError(Exception):
    """Raised when a Task block cannot be executed."""


class TaskInfraError(TaskExecutorError):
    """A Task stopped because of an infrastructure fault.

    Subclasses TaskExecutorError so existing ``except`` clauses keep
    catching it unchanged.  Callers that want to treat an infra stop
    differently detect it via ``getattr(exc, "infra_kind", "")``
    rather than importing this class, so modules with no other reason
    to depend on this one do not acquire an import for it.
    """

    def __init__(self, message: str, infra_kind: str = "",
                 block_id: str = ""):
        super().__init__(message)
        self.infra_kind = infra_kind
        self.block_id = block_id


def _validate_task_block(block: Block) -> None:
    """Structural validation for a Task block dispatch."""
    if block.block_type != "task":
        raise TaskExecutorError(
            f"execute_task_block requires block_type='task'; got '{block.block_type}'"
        )
    if not block.instructions or not block.instructions.strip():
        raise TaskExecutorError("Task block requires non-empty instructions.")


# Backwards-compat alias — call sites still referencing the Slice C
# name will keep working.
validate_root_for_slice_c = _validate_task_block


def _find_skill_by_name(storage, wanted: str):
    """Find a skill by its human-readable ``name``.

    Case-insensitive.  ``list()`` returns body-less records for
    discovered skills, so the match is re-fetched by its real id to
    load the prompt body.
    """
    target = (wanted or "").strip().lower()
    if not target:
        return None
    try:
        for s in storage.list():
            if (s.name or "").strip().lower() == target:
                # list() omits the body for discovered skills.
                return storage.get(s.id) if not getattr(s, "prompt", "") else s
    except Exception:  # noqa: BLE001 — a bad storage root must not abort
        return None
    return None


def _load_skill_prompts(
    project_id: Optional[str], skill_ids: List[str],
    project_root: Optional[str] = None,
) -> tuple[List[str], List[str]]:
    """Resolve a list of skill ids to their prompt bodies.

    Returns (prompts, warnings).  Warnings capture missing skills
    so the caller can surface them in the artifact's decisions.

    Resolution accepts EITHER a stored/discovered skill id OR the
    skill's human-readable ``name``.  File-discovered skills get a
    derived id (``{prefix}-{name}-{sha256[:12]}`` — see
    skill_discovery._stable_id) that a card author cannot know, so
    requiring the id would make ``.agents/skills`` skills effectively
    unreferenceable from a card.  Name matching is case-insensitive.

    ``project_root`` is the CODE workspace root.  It must be passed for
    file-discovered skills to resolve at all.  ``SkillStorage`` takes the
    project metadata dir and the code workspace root as SEPARATE
    arguments; project discovery of ``.agents/skills`` / ``.ziya/skills``
    / ``.claude/skills`` only runs when ``workspace_path`` is supplied.
    Omitting it (as this function originally did) made every
    file-discovered skill invisible to Task Card runs while remaining
    visible in the chat skills dialog, which passes it — see
    app/api/skills.py::get_skill_storage.
    """
    prompts: List[str] = []
    warnings: List[str] = []
    if not skill_ids or not project_id:
        if skill_ids and not project_id:
            warnings.append(
                "skills in scope but no project_id on ExecutionContext; skipped"
            )
        return prompts, warnings
    try:
        from app.storage.skills import SkillStorage
        from app.services.token_service import TokenService
        from app.utils.paths import get_project_dir
        # Stored skills live under the project metadata dir; file-discovered
        # skills are found by scanning ``workspace_path``.  One storage
        # handles both when given both roots.
        storage = SkillStorage(
            get_project_dir(project_id), TokenService(),
            workspace_path=project_root or None,
        )
        for sid in skill_ids:
            try:
                skill = storage.get(sid)
                if not skill:
                    skill = _find_skill_by_name(storage, sid)
            except (OSError, ValueError) as e:
                warnings.append(f"skill {sid!r} load error: {e}")
                continue
            if not skill:
                warnings.append(
                    f"skill {sid!r} not found in project (searched stored "
                    f"skills and discovery roots by id and by name)"
                )
                continue
            prompts.append(f"[Active Skill: {skill.name}]\n{skill.prompt}")
    except (ImportError, OSError, AttributeError) as e:
        warnings.append(f"SkillStorage unavailable: {e}")
    return prompts, warnings


def _preload_files(
    project_root: Optional[str], file_paths: List[str],
) -> tuple[str, List[str]]:
    """Read text contents of the named files and build a system-prompt
    block containing them.

    Returns (block_text, warnings).  An empty block_text means nothing
    was preloaded.  Warnings record missing/oversized/unreadable files.
    """
    warnings: List[str] = []
    if not file_paths:
        return "", warnings
    if not project_root:
        warnings.append(
            "files in scope but no project_root on ExecutionContext; skipped"
        )
        return "", warnings
    root = Path(project_root).resolve()
    parts: List[str] = ["The following files are available for this task:", ""]
    total_bytes = 0
    for rel in file_paths:
        target = (root / rel).resolve()
        # Reject paths that escape the project root via .. or symlinks.
        try:
            target.relative_to(root)
        except ValueError:
            warnings.append(f"file {rel!r} escapes project root; skipped")
            continue
        if not target.exists() or not target.is_file():
            warnings.append(f"file {rel!r} not found; skipped")
            continue
        try:
            raw = target.read_bytes()
        except OSError as e:
            warnings.append(f"file {rel!r} read error: {e}")
            continue
        if len(raw) > _MAX_FILE_BYTES:
            warnings.append(
                f"file {rel!r} exceeds {_MAX_FILE_BYTES}-byte cap; truncated"
            )
            raw = raw[:_MAX_FILE_BYTES]
        if total_bytes + len(raw) > _MAX_TOTAL_FILE_BYTES:
            warnings.append(
                f"file {rel!r} skipped; total preload cap "
                f"{_MAX_TOTAL_FILE_BYTES} bytes reached"
            )
            continue
        total_bytes += len(raw)
        try:
            text = raw.decode("utf-8", errors="replace")
        except UnicodeDecodeError:
            warnings.append(f"file {rel!r} not decodable as UTF-8; skipped")
            continue
        parts.append(f"### {rel}")
        parts.append("```")
        parts.append(text)
        parts.append("```")
        parts.append("")
    if total_bytes == 0:
        return "", warnings
    return "\n".join(parts), warnings


# Model-facing failure markers emitted by _process_result / the tool
# layer.  Kept module-level (not inlined in the loop) so the classifier
# can be unit-tested against the real strings observed in production
# logs without standing up the full streaming harness.  Extend this
# tuple when a new terminal failure prefix is introduced.
FAILURE_RESULT_MARKERS = (
    "POLICY BLOCK",
    "🚫 BLOCKED",
    "🚫 WRITE BLOCKED",
    "COMMAND FAILED",
    "Tool call refused",
    "Tool call blocked",
    "SECURITY VERIFICATION FAILED",
    "non-zero exit status",
)

# Fault kinds that mean "the infrastructure broke", not "the work failed".
# Module-level because they are matched against TWO different fields: a
# chunk whose ``type`` is one of these, and — the case that actually
# occurs — a chunk whose ``type`` is the flat string ``"error"`` with the
# kind carried in ``error_type``/``error`` instead (see
# _classify_and_handle_error and the StreamError branch in
# app/streaming_tool_executor.py, both of which emit that shape for an
# expired-credentials failure).
#
# Matching only on ``type`` left this set with no producer at all, so the
# infra-hold path was unreachable and every expired-credential stop was
# recorded as a failure of the card.  Reclassification then turned it
# into ``partial``, which reports the work as half-done rather than the
# environment as broken — and loses the resume position a hold keeps.
INFRA_ERROR_KINDS = (
    "transient_service_error",
    "throttling_error",
    "connection_error",
    "authentication_error",
)


def is_failure_result(result: object) -> bool:
    """Return True if a tool result's model-facing text denotes failure.

    Used by the run-level consecutive-failure breaker.  Non-string
    results (dicts, None) are treated as non-failures — a structured
    result that reached the model without a failure prefix is, by this
    heuristic, progress.  Matching is substring-based against
    ``FAILURE_RESULT_MARKERS`` because the underlying tools do not
    carry a machine-readable status field on the text channel.
    """
    if not isinstance(result, str):
        return False
    return any(marker in result for marker in FAILURE_RESULT_MARKERS)


async def execute_task_block(
    block: Block,
    project_root: Optional[str] = None,
    project_id: Optional[str] = None,
    run_id: Optional[str] = None,
    *,
    pre_authorized_shell_commands: Optional[List[str]] = None,
    pre_authorized_writable: Optional[List[dict]] = None,
) -> Artifact:
    """Execute a single Task block in a sandboxed model invocation.

    Returns an Artifact summarizing the run.

    When ``run_id`` is provided, live-observation events
    (``task_started``, ``task_text_delta``, ``task_tool_call``,
    ``task_finished``) are pushed to the task-run relay's replay buffer.

    The two ``pre_authorized_*`` arguments carry grants that a CALLER has
    already authorized against the signed ledger under a key this function
    cannot check — today, a file task's ``allow`` block, keyed
    ``cli:<realpath>#<name>`` (see ``app/agents/task_call.py``).  They are
    unioned into the grants below and deliberately bypass
    ``authorize_scope``, which hashes a scope against the BLOCK's id: a
    synthetic block created for a call has no approval record and never
    could, so routing an approved file task through that gate would floor
    it.  Passing these is a privileged act; the only caller is
    ``block_executor._run_callee``, immediately after the CLI-ledger check.
    """
    logger.info(f"📋 TASK_EXEC: entering execute_task_block for {block.name!r}")
    _validate_task_block(block)

    # Lazy import — these modules have heavy deps
    from ..streaming_tool_executor import StreamingToolExecutor
    from ..agents.models import ModelManager
    from langchain_core.messages import SystemMessage, HumanMessage

    # Best-effort relay emitter.  No-op when run_id is unset (e.g. in
    # unit tests or direct calls outside a run).
    async def _emit(evt: Dict) -> None:
        if not run_id:
            return
        try:
            from . import task_run_stream_relay as _relay
            await _relay.safe_push(run_id, evt)
        except Exception as e:  # noqa: BLE001
            logger.debug("Stream relay push failed: %s", e)

    # Heartbeat sink — persists "alive right now" plus a short
    # progress note onto the run file so REST pollers (the inline
    # tile between WS events, agents inspecting the run) can
    # distinguish "slow but alive" from "hung" and see what the
    # task is currently doing.  Throttling lives inside
    # record_activity so per-token deltas don't become disk writes.
    _hb_storage = None
    if run_id and project_id:
        try:
            from ..storage.task_runs import TaskRunStorage
            from ..utils.paths import get_project_dir
            _hb_storage = TaskRunStorage(get_project_dir(project_id))
        except Exception as e:  # noqa: BLE001
            logger.debug("Heartbeat storage unavailable: %s", e)

    def _heartbeat(
        note: Optional[str] = None, source: Optional[str] = None,
    ) -> None:
        if _hb_storage is None or not run_id:
            return
        try:
            # ``source`` reaches the durable progress trail so the UI can
            # tell a model-authored phase note from a tool-derived line
            # after the fact, not only live on the WS stream.
            _hb_storage.record_activity(run_id, note=note, source=source)
        except Exception as e:  # noqa: BLE001
            logger.debug("Heartbeat write failed: %s", e)

    # Model-authored progress notes: incremental scanner for
    # <progress note="..."/> tags in the streamed text.  Feeds the
    # same task_progress / record_activity channel as the
    # tool-derived notes; model notes are semantically richer so a
    # later note simply overwrites an earlier one (last-write-wins).
    try:
        from app.utils.completion_check import ProgressTagScanner
        _progress_scanner = ProgressTagScanner()
    except Exception as e:  # noqa: BLE001
        logger.debug("Progress scanner unavailable: %s", e)
        _progress_scanner = None

    scope = block.scope or None
    # ── Task-scope authorization gate (ASR F-001, design doc §4.2) ──────────
    # Before any privilege-bearing grant (shell_commands / writable paths) is
    # activated below, route the scope through the signed approval chokepoint.
    # An escalating scope with no matching signed approval is replaced by a
    # floor-only view (shell_commands dropped, write flags stripped) so the task
    # still runs, just un-escalated. A non-escalating scope passes through
    # unchanged. This is surface-agnostic: the CLI path (apply_task_permissions)
    # consults the same store. The agent cannot mint an approval (root key).
    if scope is not None:
        from app.utils.scope_approvals import authorize_scope
        _authz = authorize_scope(block.id, scope)
        if _authz is not scope:
            logger.warning(
                f"🔒 TASK_EXEC: scope escalation for block {block.id!r} "
                f"({block.name!r}) is not authorized — running at default floor"
            )
        scope = _authz
    scope_paths = (scope.paths if scope else []) or []
    scope_tools = set((scope.tools if scope else []) or [])
    scope_skills = (scope.skills if scope else []) or []
    scope_cwd = (scope.cwd if scope else None)
    scope_model_tier = (getattr(scope, "model_tier", None) if scope else None)
    scope_model_name = (getattr(scope, "model_name", None) if scope else None)
    scope_model_id_override = (getattr(scope, "model_id_override", None) if scope else None)
    scope_model_endpoint = (getattr(scope, "model_endpoint", None) if scope else None)

    # Resolve an effective project root for this task.  ``scope.cwd``
    # is interpreted relative to the caller's project_root and must
    # stay inside it; on violation we fall back and record a warning.
    effective_root = project_root
    cwd_warning: Optional[str] = None
    if scope_cwd and project_root:
        try:
            base = Path(project_root).resolve()
            cand = (base / scope_cwd).resolve()
            cand.relative_to(base)
            if not cand.exists() or not cand.is_dir():
                cwd_warning = (
                    f"cwd {scope_cwd!r} not found or not a directory; "
                    f"falling back to project root"
                )
            else:
                effective_root = str(cand)
        except ValueError:
            cwd_warning = (
                f"cwd {scope_cwd!r} escapes project root; "
                f"falling back to project root"
            )

    # Files to preload: any path entry with ``context=True``.  File
    # entries are added directly.  Directory entries are expanded to
    # every regular file under the subtree at task-launch time so the
    # saved scope can stay compact (one entry per granted dir) while
    # the executor still preloads each file.  We skip hidden / ignored
    # paths (``.git``, ``__pycache__``, ``node_modules``) and bound
    # the expansion at 200 files per directory grant to keep runaway
    # subtrees from blowing the prompt budget — anything beyond that
    # logs a warning and is truncated.
    _DIR_CONTEXT_FILE_LIMIT = 200
    _DIR_CONTEXT_SKIP = {
        '.git', '.hg', '.svn', '__pycache__', 'node_modules',
        '.venv', 'venv', '.tox', '.pytest_cache', '.mypy_cache',
        '.ziya',
    }
    preload_files: List[str] = []
    for entry in scope_paths:
        if not getattr(entry, "context", False):
            continue
        p = getattr(entry, "path", None)
        if not p:
            continue
        if not getattr(entry, "is_dir", False):
            if p not in preload_files:
                preload_files.append(p)
            continue
        # Directory: expand to every regular file underneath.
        abs_dir = os.path.join(project_root, p) if not os.path.isabs(p) else p
        if not os.path.isdir(abs_dir):
            logger.warning(f"📋 TASK_EXEC: dir-context entry {p!r} not a directory, skipping")
            continue
        added = 0
        truncated = False
        for root, dirs, files in os.walk(abs_dir):
            dirs[:] = [d for d in dirs if d not in _DIR_CONTEXT_SKIP and not d.startswith('.')]
            for fname in files:
                if fname.startswith('.'):
                    continue
                if added >= _DIR_CONTEXT_FILE_LIMIT:
                    truncated = True
                    break
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, project_root)
                if rel not in preload_files:
                    preload_files.append(rel)
                    added += 1
            if truncated:
                break
        logger.info(
            f"📋 TASK_EXEC: dir-context {p!r} expanded to {added} files"
            + (" (truncated)" if truncated else "")
        )

    start_time = time.time()
    tokens_used = 0
    # Baseline for token accounting.  GlobalUsageTracker is keyed by
    # conversation_id, and every task in a run shares one
    # (conversation_id=run_id below), so the tracker's list for this key
    # ALREADY holds the earlier tasks' records.  Summing it outright
    # would attribute the whole run's spend to each task and grow
    # quadratically across a loop; we therefore record the current
    # length and count only records appended past it.
    #
    # Length, not a timestamp: record_usage stamps its own time, so a
    # length watermark is the only index that cannot be perturbed by
    # clock skew or two tasks landing in the same millisecond.
    _usage_baseline = 0
    try:
        if run_id:
            from app.streaming_tool_executor import get_global_usage_tracker
            _usage_baseline = len(
                get_global_usage_tracker().get_conversation_usages(run_id)
            )
    except Exception as e:  # noqa: BLE001 — metrics must never break a run
        logger.debug("Usage baseline unavailable: %s", e)
    tool_call_count = 0
    collected_text: List[str] = []
    decisions: List[str] = []
    # Run-level consecutive-failure breaker.  The per-call breakers in
    # MCPManager.call_tool (turn ceiling, repetitive-call) do NOT protect
    # the shell path: the shell server is workspace-scoped and returns
    # before those guards run, and the model can evade repetition
    # detection by varying arguments (e.g. `git add`, `git apply`,
    # `git commit …`).  Without a backstop the run loops until the model
    # happens to stop, leaving the card stuck "running" indefinitely.
    # Count consecutive failing tool results; reset on any success; abort
    # the run once the streak crosses the threshold.
    try:
        _consecutive_fail_limit = int(os.environ.get("ZIYA_TASK_MAX_CONSECUTIVE_TOOL_FAILURES", "12"))
    except (TypeError, ValueError):
        _consecutive_fail_limit = 12
    _consecutive_tool_failures = 0

    # Build the sandboxed conversation.
    # Only the task's instructions; no parent transcript, no prior chat.
    system_parts: List[str] = [
        "You are executing an isolated task. Your conversation is a "
        "sandbox: it will not be shown to the caller. Only the final "
        "artifact you return flows back. Focus on producing a clean, "
        "concise result."
    ]

    # Inject the unified Session Context + effective-permissions block.
    # Without this the agent has no way to know what its cwd is or
    # which paths it's allowed to write; without it agents fall back
    # to workaround paths instead of using their writable grant.
    try:
        from app.utils.session_context_prompt import build_session_context_section
        ctx_block = build_session_context_section(
            project_root=effective_root,
            task_scope=scope,
            cwd=effective_root,
        )
        if ctx_block:
            system_parts.append(ctx_block)
    except Exception as e:
        logger.warning(f"📋 TASK_EXEC: session_context_prompt failed (non-fatal): {e}")

    # Require a structured self-assessment at the end of the
    # response.  Cheap, conscious-evaluation step that catches
    # tasks which streamed cleanly but abandoned their stated goal
    # mid-run.
    try:
        from app.utils.completion_check import (
            SELF_ASSESSMENT_INSTRUCTION, PROGRESS_INSTRUCTION,
        )
        system_parts.append(SELF_ASSESSMENT_INSTRUCTION)
        system_parts.append(PROGRESS_INSTRUCTION)
    except Exception as e:
        logger.warning(f"📋 TASK_EXEC: self_assessment instruction inject failed (non-fatal): {e}")

    # Teach the agent about artifact emission.  Phrased conditionally
    # ("if an emit_artifact tool is available") because tool exposure
    # is still subject to the scope's tools allowlist.
    try:
        from app.utils.task_artifacts import EMIT_ARTIFACT_INSTRUCTION
        system_parts.append(EMIT_ARTIFACT_INSTRUCTION)
    except Exception as e:
        logger.warning(f"📋 TASK_EXEC: emit_artifact instruction inject failed (non-fatal): {e}")

    if cwd_warning:
        decisions.append(f"scope: {cwd_warning}")
        logger.warning(f"📋 TASK_EXEC: {block.name!r}: {cwd_warning}")

    # Skills: resolve ids to prompt bodies and prepend to system.
    # ``effective_root`` is the code workspace root — required for
    # file-discovered skills (.agents/skills, .ziya/skills, …) to resolve.
    skill_prompts, skill_warnings = _load_skill_prompts(
        project_id, scope_skills, project_root=effective_root,
    )
    for p in skill_prompts:
        system_parts.append(p)
    for w in skill_warnings:
        decisions.append(f"scope: {w}")
        logger.warning(f"📋 TASK_EXEC: {block.name!r}: {w}")

    # Files: preload contents into the system prompt (advisory —
    # file_read remains available for anything not in the list).
    file_block, file_warnings = _preload_files(effective_root, preload_files)
    if file_block:
        system_parts.append(file_block)
    for w in file_warnings:
        decisions.append(f"scope: {w}")
        logger.warning(f"📋 TASK_EXEC: {block.name!r}: {w}")

    # Advertise the run-scoped blackboard (granted further below, where
    # the path grants are assembled).  Announced here because
    # ``system_parts`` is sealed into the SystemMessage a few lines down.
    if run_id and project_root:
        _bb = os.path.join(project_root, ".ziya", "task-runs", run_id)
        system_parts.append(
            "SHARED RUN SCRATCHPAD\n"
            f"You may read and write files under: {_bb}\n\n"
            "Every task in this run shares this directory and it "
            "persists for the whole run.  It is the ONLY way to hand "
            "STRUCTURED state to a later task: your conversation is "
            "discarded when you finish, and your summary is prose a "
            "later task cannot verify.\n\n"
            "Record facts a later task would otherwise re-derive — a "
            "mutable backlog with per-item status, a verified build "
            "hash, what you have already confirmed and how.  BEFORE "
            "re-deriving anything expensive, read here first: an "
            "earlier task may have already established it."
        )

    messages = [
        SystemMessage(content="\n\n".join(system_parts)),
        HumanMessage(content=block.instructions),
    ]

    # Resolve AWS profile/region from ModelManager state
    state = ModelManager.get_state()
    region = state.get("aws_region", "us-east-1")
    profile = state.get("aws_profile", "default")

    # Per-task model override: a Task block's scope can name a portable
    # tier (preferred) or a specific model/endpoint to run on, letting a
    # decomposed task run cheaper/faster than the top-level conversation
    # (or, for scoped power users, on a specific model). model_id_override
    # wins over model_name if both are set; either wins over model_tier.
    _model_override = scope_model_id_override or scope_model_name or scope_model_tier
    if _model_override:
        decisions.append(f"scope: model override = {_model_override!r}")
    executor = StreamingToolExecutor(
        profile_name=profile, region=region,
        model_id=scope_model_id_override,
        model_override=(scope_model_name or scope_model_tier) if not scope_model_id_override else None,
        endpoint_override=scope_model_endpoint,
    )

    # Resolve which tools this task may call.  We do NOT filter here and
    # hand the result to the executor: ``stream_with_tools`` re-derives its
    # tool list from ``create_secure_mcp_tools()`` internally and ignores the
    # ``tools`` argument entirely, so a caller-side filter was dead code —
    # every task ran with the full tool set while ``_format_tools_section``
    # told the model "all other tools are filtered out of this run".  Pass
    # the NAMES via ``tool_allowlist`` so enforcement happens where the list
    # is actually built.  The always-available floor (emit_artifact,
    # render_diagram, beads) is unioned in by the shared resolver.
    tools: List = []
    try:
        from ..mcp.enhanced_tools import create_secure_mcp_tools
        from app.utils.task_tool_floor import (
            filter_tools_by_scope, unmatched_scope_tools,
        )
        all_tools = create_secure_mcp_tools()
        tools = filter_tools_by_scope(all_tools, scope_tools)
        if scope_tools:
            missing = unmatched_scope_tools(all_tools, scope_tools)
            if missing:
                decisions.append(
                    f"scope: tools requested but unavailable: {missing}"
                )
    except (ImportError, OSError, RuntimeError) as e:
        logger.warning(f"Task executor: MCP tool load failed, proceeding without: {e}")
    logger.info(
        f"📋 TASK_EXEC: {block.name!r} tools_ready ({len(tools)} tools) — "
        f"starting stream via model={state.get('current_model', '?')}"
    )

    # Activate the task-scoped writable allowlist for the duration of
    # the stream.  Tools (notably file_write) consult
    # ``get_task_writable_paths`` to decide whether to allow writes
    # that would otherwise be denied by the base WritePolicy.  The
    # list is built from any ``paths`` entry with ``write=True`` —
    # both files and directories.
    from app.context import (
        set_task_writable_paths, reset_task_writable_paths,
        set_task_readable_paths, reset_task_readable_paths,
        set_task_shell_commands, reset_task_shell_commands,
        get_task_iteration_context,
    )
    writable_grant: List[dict] = []
    readable_grant: List[dict] = []
    for entry in scope_paths:
        path = getattr(entry, "path", None)
        if not path:
            continue
        is_dir = bool(getattr(entry, "is_dir", False))
        if getattr(entry, "write", False):
            writable_grant.append({"path": path, "is_dir": is_dir})
        # ``write`` implies ``read``: a task that can overwrite a file
        # ought to be able to read it back.  We OR the two flags here.
        if getattr(entry, "read", False) or getattr(entry, "write", False):
            readable_grant.append({"path": path, "is_dir": is_dir})
    # Caller-authorized writable grants.  Appended, not merged by path:
    # these are fnmatch ``{"pattern": …}`` entries (a file task's
    # ``write_patterns``), a shape ``ScopeEntry`` cannot express, which
    # ``_task_scope_grants_write`` matches against the project-relative
    # path and its basename.
    for entry in (pre_authorized_writable or []):
        if entry:
            writable_grant.append(dict(entry))
    # Run-scoped blackboard.  Appended BEFORE the set_task_*_paths calls
    # below, since those snapshot the lists into ContextVars.
    #
    # Exists because a task's only inbound channel is its predecessor's
    # summary string, which is lossy and unverifiable.  Observed
    # consequence: in a four-iteration fix loop, iterations 2 and 3 each
    # opened by re-deriving deployment state from scratch — re-reading
    # the bundle hash, re-comparing mtimes — because the upstream report
    # carried no facts they could check.  A durable place to write
    # "deployed hash = X, backlog item 3 VERIFIED" makes that a read.
    #
    # A granted PATH rather than a new tool: file_read / file_write /
    # the shell already work, and file_write creates missing parents.
    # Nothing is automatic — a card must instruct its tasks to use it,
    # so state crosses task boundaries only when the card says so.
    blackboard_dir = None
    if run_id and project_root:
        blackboard_dir = os.path.join(
            project_root, ".ziya", "task-runs", run_id,
        )
        writable_grant.append({"path": blackboard_dir, "is_dir": True})
        readable_grant.append({"path": blackboard_dir, "is_dir": True})
    scope_token = set_task_writable_paths(writable_grant or None)
    read_token = set_task_readable_paths(readable_grant or None)
    # Per-task shell command grants (Slice B).  Stored on the scope as
    # a list of strings; bare strings are literal first-token grants,
    # ``re:`` prefix turns the rest into a regex against the full
    # command line.  Consulted by ShellWriteChecker only when the base
    # policy would otherwise block — see app/mcp_servers/write_policy.py.
    shell_commands_grant = list(getattr(scope, "shell_commands", []) or [])
    for cmd in (pre_authorized_shell_commands or []):
        if cmd and cmd not in shell_commands_grant:
            shell_commands_grant.append(cmd)
    shell_token = set_task_shell_commands(shell_commands_grant or None)
    # Shell timeout grant.  Set unconditionally (None when unspecified)
    # so the reset in the finally below always has a valid token — the
    # same discipline the path and command grants follow.
    from ..context import (
        set_task_shell_timeout, reset_task_shell_timeout,
    )
    shell_timeout_token = set_task_shell_timeout(
        getattr(scope, "shell_timeout_secs", None)
    )
    # Open the output-artifact collector for this task, alongside the
    # permission grants and reset in the same ``finally``.  The
    # emit_artifact builtin appends ArtifactPart-shaped dicts here;
    # they are drained into ``Artifact.outputs`` below.  Rendered
    # blobs persist under the run's artifacts dir when the run is
    # known (encryption-aware — see app/utils/task_artifacts.py).
    from app.utils.task_artifacts import (
        start_artifact_collection, finish_artifact_collection,
    )
    _artifacts_dir = None
    if project_id and run_id:
        try:
            from app.utils.paths import get_project_dir
            _artifacts_dir = str(
                get_project_dir(project_id) / "task_runs" / run_id / "artifacts"
            )
        except Exception as e:  # noqa: BLE001 — blob persistence is optional
            logger.warning(f"📋 TASK_EXEC: artifacts dir resolution failed: {e}")
    artifact_token = start_artifact_collection(
        block_id=block.id, artifacts_dir=_artifacts_dir, run_id=run_id,
    )
    emitted_outputs: List[dict] = []
    if writable_grant:
        logger.info(
            f"📋 TASK_EXEC: {block.name!r} writable_grant={writable_grant!r}"
        )
    if readable_grant:
        logger.info(
            f"📋 TASK_EXEC: {block.name!r} readable_grant={readable_grant!r}"
        )
    if shell_commands_grant:
        logger.info(
            f"📋 TASK_EXEC: {block.name!r} shell_commands_grant={shell_commands_grant!r}"
        )

    # When this task is the body of a Repeat/Until iteration, the
    # parent block_executor stamps an iteration context so streaming
    # deltas can be attributed to the iteration owner's block_id.
    # The frontend reducer routes events to iteration buckets by
    # block_id; emitting the inner task's own id would land every
    # iteration's output in a phantom bucket and collapse them into
    # a single "Iteration 0".  ``task_started`` / ``task_finished``
    # keep the inner block id (they describe the task itself); only
    # the per-iteration deltas are re-tagged.
    iter_ctx = get_task_iteration_context()
    delta_block_id = (
        iter_ctx["block_id"] if iter_ctx and iter_ctx.get("block_id") else block.id
    )

    await _emit({
        "type": "task_started",
        "run_id": run_id,
        "block_id": block.id,
        "block_name": block.name,
        "tools_count": len(tools),
        "cwd": effective_root,
        "ts": time.time(),
    })

    # Stream the task — accumulate the response text and metrics
    # ``try/finally`` guarantees the task-scoped ContextVars are reset
    # even when ``stream_with_tools`` raises an unstructured exception
    # (network error, cancellation, etc.) — without it the writable /
    # readable grants would leak past task boundaries on any
    # non-``error``-chunk failure path.
    try:
        async for chunk in executor.stream_with_tools(
            messages, tools=tools, project_root=effective_root,
            # The list actually enforced.  ``tools`` above is retained only
            # for the tests/callers that inspect what was resolved; the
            # executor narrows its own tool set from these names.
            tool_allowlist=(sorted(scope_tools) if scope_tools else None),
            # Without a conversation_id, app.context.set_conversation_id()
            # is never called for Task Card runs, so model-driven context
            # tools (context_add_file/context_remove_file/context_list_files)
            # fail with "no conversation_id is set in the current request
            # context". run_id is unique per run and stable for its duration.
            conversation_id=run_id,
        ):
            ctype = chunk.get("type")
            if ctype == "text":
                content = chunk.get("content", "")
                if content:
                    collected_text.append(content)
                    await _emit({
                        "type": "task_text_delta",
                        "run_id": run_id,
                        "block_id": delta_block_id,
                        "content": content,
                        # Server clock, matching every other timestamped
                        # event (task_tool_call, task_progress, run
                        # last_activity_at).  Without this the client fell
                        # back to its own clock, and any skew between the
                        # two machines corrupted the "Ns ago" age label
                        # and the note-source preference that compares
                        # against run.last_activity_at.
                        "ts": time.time(),
                    })
                    _heartbeat()
                    if _progress_scanner is not None:
                        for _pnote in _progress_scanner.feed(content):
                            await _emit({
                                "type": "task_progress",
                                "run_id": run_id,
                                "block_id": delta_block_id,
                                "note": _pnote,
                                "source": "model",
                                "ts": time.time(),
                            })
                            _heartbeat(_pnote, source="model")
            elif ctype == "tool_display":
                tool_call_count += 1
                # Close the open text run at the tool boundary.
                #
                # Text is only appended for ``ctype == "text"`` chunks, so a
                # tool call leaves a GAP in ``collected_text``: the prose
                # before the call and the prose after it become adjacent list
                # entries and ``"".join`` welds them into one line --
                # "...in parallel.The broken render confirms...".  The tell
                # that this is a seam and not a lost newline is that a
                # following "## Heading" renders as literal text: a heading
                # only parses at line start, so no newline was ever present.
                #
                # Fixing it here rather than in the frontend repairs BOTH
                # consumers -- the live inspector and ``full_text``, which
                # feeds the persisted ``Artifact.summary`` below.
                #
                # Deliberately NOT emitted as a ``task_text_delta``: the CLI
                # sink already calls ``_break_text()`` on ``task_tool_call``,
                # so a whitespace delta would add two blank lines there.  The
                # frontend reconstructs the same seam from ``task_tool_call``,
                # which the relay's delta-collapsing already breaks on.
                if collected_text and not collected_text[-1].endswith("\n"):
                    collected_text.append("\n\n")
                _result = chunk.get("result", "")
                _tool_name = chunk.get("tool_name") or "tool"
                _args = chunk.get("args")
                _arg_hint = ""
                if isinstance(_args, dict):
                    _arg_hint = str(
                        _args.get("command") or _args.get("path")
                        or _args.get("query") or ""
                    )[:120]
                _note = (f"ran {_tool_name}: {_arg_hint}" if _arg_hint
                         else f"ran {_tool_name}")
                await _emit({
                    "type": "task_tool_call",
                    "run_id": run_id,
                    "block_id": delta_block_id,
                    "tool_name": chunk.get("tool_name"),
                    "tool_id": chunk.get("tool_id"),
                    "result_preview": (_result or "")[:500] if isinstance(_result, str) else "",
                    "ts": time.time(),
                })
                await _emit({
                    "type": "task_progress",
                    "run_id": run_id,
                    "block_id": delta_block_id,
                    "note": _note,
                    "ts": time.time(),
                })
                _heartbeat(_note)
                # Consecutive-failure detection.  ``_result`` is the
                # model-facing text produced by _process_result, so failed
                # calls carry a recognizable prefix regardless of the
                # underlying tool (policy block, non-zero exit, refusal,
                # verification failure, generic error).
                _is_failure = is_failure_result(_result)
                if _is_failure and _consecutive_fail_limit > 0:
                    _consecutive_tool_failures += 1
                    if _consecutive_tool_failures >= _consecutive_fail_limit:
                        _abort_note = (
                            f"aborting: {_consecutive_tool_failures} consecutive "
                            f"tool failures (last: {_tool_name}). The task is not "
                            f"making progress — likely a blocked command or a "
                            f"missing permission grant."
                        )
                        await _emit({
                            "type": "task_progress",
                            "run_id": run_id,
                            "block_id": delta_block_id,
                            "note": _abort_note,
                            "level": "error",
                            "ts": time.time(),
                        })
                        logger.warning(
                            f"📋 TASK_EXEC: {block.name!r} aborting after "
                            f"{_consecutive_tool_failures} consecutive tool failures"
                        )
                        raise TaskExecutorError(
                            f"Task aborted after {_consecutive_tool_failures} "
                            f"consecutive tool failures without progress. "
                            f"Last tool: {_tool_name}."
                        )
                else:
                    _consecutive_tool_failures = 0
            elif ctype == "stream_end":
                break
            elif ctype == "error":
                # The kind lives in ``error_type``/``error``; ``type`` is
                # a flat "error".  Reading it here is what makes an
                # infrastructure fault distinguishable from a failure of
                # the work — see INFRA_ERROR_KINDS.
                _err_kind = str(
                    chunk.get("error_type") or chunk.get("error") or ""
                )
                # ``or``-chained, NOT ``.get(key, default)``: the default
                # only fires when the key is ABSENT, so a chunk carrying
                # ``content: ""`` (present but empty) passed straight
                # through and produced the observed
                # ``"Task execution failed: "`` — a colon with nothing
                # after it, recorded as a run's entire error field.  A user
                # arriving at that run learns only that something failed.
                #
                # The fallbacks mirror the infra branch below, because a
                # chunk's message is not reliably in one field: the
                # streaming layer uses ``content``, while
                # _classify_and_handle_error's paths carry it in ``detail``
                # or ``retry_message``.  Reading only ``content`` discards
                # the message whenever a producer chose a different key.
                _err_msg = (
                    chunk.get("content") or chunk.get("detail")
                    or chunk.get("retry_message") or chunk.get("error")
                    or "unknown"
                )
                logger.warning(
                    f"📋 TASK_EXEC: {block.name!r} received error chunk: "
                    f"{_err_msg} (kind={_err_kind or 'unclassified'})"
                )
                await _emit({
                    "type": "task_finished",
                    "run_id": run_id,
                    "block_id": block.id,
                    "ok": False,
                    "error": _err_msg,
                    "ts": time.time(),
                })
                if _err_kind in INFRA_ERROR_KINDS:
                    raise TaskInfraError(
                        f"Task execution failed ({_err_kind}): {_err_msg}",
                        infra_kind=_err_kind,
                        block_id=block.id or "",
                    )
                raise TaskExecutorError(
                    f"Task execution failed: {_err_msg}"
                )
            elif ctype in INFRA_ERROR_KINDS:
                # Terminal error chunks from _classify_and_handle_error's
                # non-retryable path (retries exhausted, or a non-retryable
                # class such as auth). These do not use ctype 'error', so
                # without this branch they matched nothing, the loop exited
                # normally, and the block was recorded as succeeding with
                # silently truncated output. Their message lives in
                # 'detail'/'retry_message' rather than 'content'.
                _detail = (
                    chunk.get("detail")
                    or chunk.get("retry_message")
                    or chunk.get("error")
                    or ctype
                )
                logger.warning(
                    f"📋 TASK_EXEC: {block.name!r} terminal {ctype}: {_detail}"
                )
                await _emit({
                    "type": "task_finished",
                    "run_id": run_id,
                    "block_id": block.id,
                    "ok": False,
                    "error": _detail,
                    "ts": time.time(),
                })
                raise TaskInfraError(
                    f"Task execution failed ({ctype}): {_detail}",
                    infra_kind=ctype,
                    block_id=block.id or "",
                )
    finally:
        reset_task_writable_paths(scope_token)
        reset_task_readable_paths(read_token)
        reset_task_shell_commands(shell_token)
        reset_task_shell_timeout(shell_timeout_token)
        # Drain declared outputs in the same finally that resets the
        # grants, so the collector can never leak across task
        # boundaries even on error paths.
        emitted_outputs = finish_artifact_collection(artifact_token)

    elapsed_ms = int((time.time() - start_time) * 1000)
    full_text = "".join(collected_text)

    # Token accounting.  ``tokens_used`` was initialised to 0 and never
    # incremented, so every artifact — and every iteration summary built
    # from one — reported tokens=0, leaving no way to see what a long
    # campaign actually cost or whether a model_tier grant had any
    # effect.  There is no usage-bearing stream chunk to count, so the
    # figure is read from GlobalUsageTracker, which
    # message_stop_handler populates per streaming iteration.
    #
    # Counts only records appended past ``_usage_baseline`` (see above).
    # cache_read is included: it is real input the model processed and
    # was billed for, if discounted, so excluding it would understate a
    # cache-heavy loop precisely where the cost question is sharpest.
    try:
        if run_id:
            from app.streaming_tool_executor import get_global_usage_tracker
            _records = get_global_usage_tracker().get_conversation_usages(run_id)
            for _u in _records[_usage_baseline:]:
                tokens_used += (
                    getattr(_u, "input_tokens", 0)
                    + getattr(_u, "output_tokens", 0)
                    + getattr(_u, "cache_read_tokens", 0)
                    + getattr(_u, "cache_write_tokens", 0)
                )
    except Exception as e:  # noqa: BLE001 — metrics must never break a run
        logger.debug("Usage read failed: %s", e)

    # Parse the model's structured self-assessment, attach it to
    # the artifact, and use it to decide ``ok``.  Falls back to the
    # old "stream cleanness" answer when the model omitted the tag —
    # better to ship missing-but-clean than to mark every legacy
    # task as failed.
    self_assessment = None
    assessment_failed = False
    assessment_signature = None
    try:
        from app.utils.completion_check import (
            parse_self_assessment, is_failure, signature_for,
            strip_assessment_tag, strip_progress_tags,
        )
        # Progress tags are live-UI metadata — never part of the
        # artifact summary, whether or not a self-assessment exists.
        full_text = strip_progress_tags(full_text)
        self_assessment = parse_self_assessment(full_text)
        if self_assessment is None:
            decisions.append(
                "self_assessment: missing — model did not emit the "
                "required <self_assessment .../> tag at end of response"
            )
        else:
            assessment_failed = is_failure(self_assessment)
            assessment_signature = signature_for(self_assessment)
            # Don't show the meta tag in the artifact summary.
            full_text = strip_assessment_tag(full_text)
    except Exception as e:
        logger.warning(f"📋 TASK_EXEC: self_assessment parse failed (non-fatal): {e}")

    # Artifact summary is the final model response; decisions capture
    # any scope warnings recorded earlier (missing skills, truncated
    # files, etc.).  Later slices may add LLM-driven compaction.
    # Cap with a soft-boundary truncation that adds an explicit
    # marker — the previous hard ``[:2000]`` slice cut mid-sentence
    # silently, leaving users unable to tell whether the model or
    # the system had stopped.
    # A task whose entire visible output was thinking and/or tool
    # traffic leaves an empty summary.  Passing that downstream as a
    # SUCCESS is what lets a silent step masquerade as a completed one:
    # the next block receives "" as its {{previous}} and cannot tell
    # "nothing to report" from "never ran", so it re-derives the whole
    # world defensively.  Fail explicitly instead, and say why.
    if not full_text.strip():
        decisions.append(
            "empty_summary: the task produced no prose output — only "
            "thinking and/or tool calls.  A downstream block would have "
            "received an empty result indistinguishable from silence, "
            "so this task is recorded as failed rather than passing an "
            "unverifiable success forward."
        )
        assessment_failed = True
        assessment_signature = assessment_signature or "empty_summary"

    from app.utils.artifact_summary import truncate_summary
    artifact = Artifact(
        summary=truncate_summary(full_text.strip()),
        decisions=decisions,
        # Model-declared outputs collected via the emit_artifact tool.
        # Dicts coerce into ArtifactPart; extra="allow" preserves
        # group/label/seq and render metadata for the artifact viewer.
        outputs=emitted_outputs,
        tokens=tokens_used,
        tool_calls=tool_call_count,
        duration_ms=elapsed_ms,
        created_at=time.time(),
        self_assessment=self_assessment,
        failed=assessment_failed,
        signature=assessment_signature,
    )
    await _emit({
        "type": "task_finished",
        "run_id": run_id,
        "block_id": block.id,
        "ok": not assessment_failed,
        "duration_ms": elapsed_ms,
        "tool_calls": tool_call_count,
        "ts": time.time(),
    })
    return artifact
