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


def _load_skill_prompts(
    project_id: Optional[str], skill_ids: List[str],
) -> tuple[List[str], List[str]]:
    """Resolve a list of skill ids to their prompt bodies.

    Returns (prompts, warnings).  Warnings capture missing skills
    so the caller can surface them in the artifact's decisions.
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
        storage = SkillStorage(get_project_dir(project_id), TokenService())
        for sid in skill_ids:
            try:
                skill = storage.get(sid)
            except (OSError, ValueError) as e:
                warnings.append(f"skill {sid!r} load error: {e}")
                continue
            if not skill:
                warnings.append(f"skill {sid!r} not found in project")
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


async def execute_task_block(
    block: Block,
    project_root: Optional[str] = None,
    project_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Artifact:
    """Execute a single Task block in a sandboxed model invocation.

    Returns an Artifact summarizing the run.

    When ``run_id`` is provided, live-observation events
    (``task_started``, ``task_text_delta``, ``task_tool_call``,
    ``task_finished``) are pushed to the task-run relay's replay buffer.
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

    scope = block.scope or None
    scope_paths = (scope.paths if scope else []) or []
    scope_tools = set((scope.tools if scope else []) or [])
    scope_skills = (scope.skills if scope else []) or []
    scope_cwd = (scope.cwd if scope else None)

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
    tool_call_count = 0
    collected_text: List[str] = []
    decisions: List[str] = []

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
        from app.utils.completion_check import SELF_ASSESSMENT_INSTRUCTION
        system_parts.append(SELF_ASSESSMENT_INSTRUCTION)
    except Exception as e:
        logger.warning(f"📋 TASK_EXEC: self_assessment instruction inject failed (non-fatal): {e}")

    if cwd_warning:
        decisions.append(f"scope: {cwd_warning}")
        logger.warning(f"📋 TASK_EXEC: {block.name!r}: {cwd_warning}")

    # Skills: resolve ids to prompt bodies and prepend to system.
    skill_prompts, skill_warnings = _load_skill_prompts(project_id, scope_skills)
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

    messages = [
        SystemMessage(content="\n\n".join(system_parts)),
        HumanMessage(content=block.instructions),
    ]

    # Resolve AWS profile/region from ModelManager state
    state = ModelManager.get_state()
    region = state.get("aws_region", "us-east-1")
    profile = state.get("aws_profile", "default")

    executor = StreamingToolExecutor(profile_name=profile, region=region)

    # Load MCP tools and filter by scope.tools allowlist
    tools: List = []
    try:
        from ..mcp.enhanced_tools import create_secure_mcp_tools
        all_tools = create_secure_mcp_tools()
        tools = [t for t in all_tools if not scope_tools or t.name in scope_tools]
        if scope_tools:
            exposed_names = {t.name for t in tools}
            missing = [n for n in scope_tools if n not in exposed_names]
            if missing:
                decisions.append(
                    f"scope: tools requested but unavailable: {sorted(missing)}"
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
    scope_token = set_task_writable_paths(writable_grant or None)
    read_token = set_task_readable_paths(readable_grant or None)
    # Per-task shell command grants (Slice B).  Stored on the scope as
    # a list of strings; bare strings are literal first-token grants,
    # ``re:`` prefix turns the rest into a regex against the full
    # command line.  Consulted by ShellWriteChecker only when the base
    # policy would otherwise block — see app/mcp_servers/write_policy.py.
    shell_commands_grant = list(getattr(scope, "shell_commands", []) or [])
    shell_token = set_task_shell_commands(shell_commands_grant or None)
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
                    })
            elif ctype == "tool_display":
                tool_call_count += 1
                _result = chunk.get("result", "")
                await _emit({
                    "type": "task_tool_call",
                    "run_id": run_id,
                    "block_id": delta_block_id,
                    "tool_name": chunk.get("tool_name"),
                    "tool_id": chunk.get("tool_id"),
                    "result_preview": (_result or "")[:500] if isinstance(_result, str) else "",
                    "ts": time.time(),
                })
            elif ctype == "stream_end":
                break
            elif ctype == "error":
                logger.warning(
                    f"📋 TASK_EXEC: {block.name!r} received error chunk: "
                    f"{chunk.get('content', 'unknown')}"
                )
                await _emit({
                    "type": "task_finished",
                    "run_id": run_id,
                    "block_id": block.id,
                    "ok": False,
                    "error": chunk.get("content", "unknown"),
                    "ts": time.time(),
                })
                raise TaskExecutorError(
                    f"Task execution failed: {chunk.get('content', 'unknown')}"
                )
    finally:
        reset_task_writable_paths(scope_token)
        reset_task_readable_paths(read_token)
        reset_task_shell_commands(shell_token)

    elapsed_ms = int((time.time() - start_time) * 1000)
    full_text = "".join(collected_text)

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
            strip_assessment_tag,
        )
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
    from app.utils.artifact_summary import truncate_summary
    artifact = Artifact(
        summary=truncate_summary(full_text.strip()),
        decisions=decisions,
        outputs=[],
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
