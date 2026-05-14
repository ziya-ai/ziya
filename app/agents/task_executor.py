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
) -> Artifact:
    """Execute a single Task block in a sandboxed model invocation.

    Returns an Artifact summarizing the run.
    """
    logger.info(f"📋 TASK_EXEC: entering execute_task_block for {block.name!r}")
    _validate_task_block(block)

    # Lazy import — these modules have heavy deps
    from ..streaming_tool_executor import StreamingToolExecutor
    from ..agents.models import ModelManager
    from langchain_core.messages import SystemMessage, HumanMessage

    scope = block.scope or None
    scope_files = (scope.files if scope else []) or []
    scope_tools = set((scope.tools if scope else []) or [])
    scope_skills = (scope.skills if scope else []) or []

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

    # Skills: resolve ids to prompt bodies and prepend to system.
    skill_prompts, skill_warnings = _load_skill_prompts(project_id, scope_skills)
    for p in skill_prompts:
        system_parts.append(p)
    for w in skill_warnings:
        decisions.append(f"scope: {w}")
        logger.warning(f"📋 TASK_EXEC: {block.name!r}: {w}")

    # Files: preload contents into the system prompt (advisory —
    # file_read remains available for anything not in the list).
    file_block, file_warnings = _preload_files(project_root, scope_files)
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

    # Stream the task — accumulate the response text and metrics
    async for chunk in executor.stream_with_tools(
        messages, tools=tools, project_root=project_root,
    ):
        ctype = chunk.get("type")
        if ctype == "text":
            content = chunk.get("content", "")
            if content:
                collected_text.append(content)
        elif ctype == "tool_display":
            tool_call_count += 1
        elif ctype == "stream_end":
            break
        elif ctype == "error":
            logger.warning(
                f"📋 TASK_EXEC: {block.name!r} received error chunk: "
                f"{chunk.get('content', 'unknown')}"
            )
            raise TaskExecutorError(
                f"Task execution failed: {chunk.get('content', 'unknown')}"
            )

    elapsed_ms = int((time.time() - start_time) * 1000)
    full_text = "".join(collected_text)

    # Artifact summary is the final model response; decisions capture
    # any scope warnings recorded earlier (missing skills, truncated
    # files, etc.).  Later slices may add LLM-driven compaction.
    artifact = Artifact(
        summary=full_text.strip()[:2000],
        decisions=decisions,
        outputs=[],
        tokens=tokens_used,
        tool_calls=tool_call_count,
        duration_ms=elapsed_ms,
        created_at=time.time(),
    )
    return artifact
