"""
Task executor — run a single Task block in an isolated sandbox.

Slice C scope: executes a single Task block (block_type == "task").
Repeat blocks are rejected with a clear error; Slice D adds loops.

Design invariant (see design/task-cards.md):
  A task's conversation never leaves its task.  The block's
  instructions become a fresh conversation with no parent
  history.  When the block completes, only an Artifact flows
  back — not the conversation transcript.

Tool/file/skill scope enforcement:
  - tools: filter available MCP tools to the scope.tools allowlist
  - files: preload into a minimal system prompt file context
  - skills: load and prepend each skill prompt to the system prompt
"""

import logging
import time
from typing import Dict, List, Optional

from ..models.task_card import Block, Artifact

logger = logging.getLogger(__name__)


class TaskExecutorError(Exception):
    """Raised when a Task block cannot be executed."""


def validate_root_for_slice_c(block: Block) -> None:
    """Slice C only supports a single Task block as the card's root."""
    if block.block_type != "task":
        raise TaskExecutorError(
            f"Slice C can only execute Task blocks; got '{block.block_type}'. "
            "Repeat / Parallel support lands in Slice D."
        )
    if not block.instructions or not block.instructions.strip():
        raise TaskExecutorError("Task block requires non-empty instructions.")


async def execute_task_block(
    block: Block,
    project_root: Optional[str] = None,
) -> Artifact:
    """Execute a single Task block in a sandboxed model invocation.

    Returns an Artifact summarizing the run.

    This function is intentionally minimal in Slice C — it validates
    the block, constructs a fresh message context, streams through
    StreamingToolExecutor with scope enforcement, and compacts the
    result into an Artifact.
    """
    validate_root_for_slice_c(block)

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
    if scope_files:
        system_parts.append(
            "You have access to these files for this task: " +
            ", ".join(scope_files)
        )
    if scope_skills:
        system_parts.append(
            "Active skills for this task: " + ", ".join(scope_skills)
        )
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
    except (ImportError, OSError, RuntimeError) as e:
        logger.warning(f"Task executor: MCP tool load failed, proceeding without: {e}")

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
            raise TaskExecutorError(
                f"Task execution failed: {chunk.get('content', 'unknown')}"
            )

    elapsed_ms = int((time.time() - start_time) * 1000)
    full_text = "".join(collected_text)

    # Slice C: the artifact summary is just the final model response.
    # Slice D will add explicit summary/decisions extraction via a
    # follow-up "compaction" LLM call, similar to delegate crystals.
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
