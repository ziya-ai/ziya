"""Deferred, partially concurrent execution of the tool calls a single model
turn emitted.

A model turn can emit several tool calls at once.  Executing them strictly one
at a time is safe but slow; executing them all concurrently is fast but unsafe
(a write racing a read of the same file).  This module splits the difference:

* Consecutive **read-only** calls form a group that runs concurrently.
* Any other call is a **barrier** — it runs alone, after everything before it
  has finished and before anything after it starts.

Read-only classification is deliberately conservative (``is_read_only_call`` /
``shell_command_is_read_only``): an unknown tool, or a shell command that is
not obviously side-effect-free, is treated as a barrier.

Ordering guarantees the model relies on are preserved:

* ``_tool_result`` events are emitted in the calls' ARRIVAL order (the order
  the model wrote them), never completion order, so the tool_result blocks
  line up with the tool_use blocks in the assistant message.
* The adaptive inter-tool delay is paid once per batch, not once per call.
* A stop directive raised while one call is in flight cancels its siblings and
  ends the batch with no results.
* Non-stop feedback lets the in-flight group finish, then stubs every call that
  had not started yet (mirroring the old sequential skip path).
"""
import asyncio
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.tool_execution import execute_single_tool

# Stub result handed to a call skipped because earlier feedback takes priority.
# Matches the message the old sequential skip path used.
FEEDBACK_SKIP_MESSAGE = (
    "Tool execution skipped: user provided real-time feedback that takes "
    "priority. Re-evaluate based on the feedback before continuing."
)

# --------------------------------------------------------------------------
# Read-only classification
# --------------------------------------------------------------------------

# Builtin tools that only observe state.  Anything not listed is treated as a
# barrier — conservative by design, so a new mutating tool is serial until it
# is deliberately added here.
_READ_ONLY_BUILTINS = frozenset({
    "file_read", "file_list",
    "ast_get_tree", "ast_search", "ast_references",
    "pdf_outline", "pdf_read_pages", "pdf_search",
    "memory_search", "memory_context", "memory_expand",
    "chat_search", "chat_read", "chat_list",
    "context_list_files",
    "list_architecture_shape_categories", "search_architecture_shapes",
    "get_architecture_diagram_template",
    "get_skill_details",
    "task_card_list", "task_card_read", "task_card_validate",
    "list_run_artifacts",
    "shadow_list", "shadow_read",
})

_SHELL_TOOL_NAMES = frozenset({"run_shell_command", "mcp_run_shell_command"})

# Commands that only read.  A pipeline is read-only only if EVERY segment's
# command is in here and carries no mutating flag (see _segment_is_read_only).
_READ_ONLY_COMMANDS = frozenset({
    "ls", "cat", "grep", "egrep", "fgrep", "zgrep", "zcat", "head", "tail",
    "wc", "find", "sed", "awk", "perl", "sort", "cut", "tr", "echo", "printf",
    "git", "cd", "pwd", "stat", "file", "diff", "dirname", "basename",
    "realpath", "readlink", "nl", "column", "uniq", "comm", "od", "hexdump",
    "xxd", "strings", "date", "seq", "which", "whoami", "hostname", "uname",
    "id", "true", "false", "test", "less", "fold", "expand", "paste", "join",
    "tree", "du", "df", "free", "uptime", "ps",
})

# git subcommands that never mutate the repo or working tree.
_GIT_READ_ONLY_SUBCMDS = frozenset({
    "log", "status", "show", "diff", "blame", "rev-parse", "ls-files",
    "ls-tree", "cat-file", "describe", "shortlog", "reflog", "whatchanged",
    "grep",
})

# find primaries that act rather than observe.
_FIND_MUTATING = frozenset({
    "-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf",
    "-fls",
})

_ENV_ASSIGN_RE = re.compile(r"^\w+=")
_SEG_SPLIT_RE = re.compile(r"\|\||&&|[|;&]")
# A redirection and its target; ``2>&1`` fd-dups do not match (``&`` excluded).
_REDIR_RE = re.compile(r"\d*>>?\s*([^\s;|&]+)")


def shell_command_is_read_only(command: Optional[str]) -> bool:
    """True if ``command`` cannot mutate state as far as we can tell.

    Conservative: unknown commands, command substitution, file redirection,
    and mutating flags all make it False."""
    if not command or not command.strip():
        return False
    cmd = command.strip()
    # Command substitution can run anything.
    if "$(" in cmd or "`" in cmd:
        return False
    # Redirection to a real file writes; ``>/dev/null`` and fd-dups are fine.
    for m in _REDIR_RE.finditer(cmd):
        if m.group(1) != "/dev/null":
            return False
    segments = [s.strip() for s in _SEG_SPLIT_RE.split(cmd)]
    segments = [s for s in segments if s]
    if not segments:
        return False
    return all(_segment_is_read_only(s) for s in segments)


def _segment_is_read_only(segment: str) -> bool:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return False  # unbalanced quotes etc.: don't guess, treat as unsafe
    i = 0
    while i < len(tokens) and _ENV_ASSIGN_RE.match(tokens[i]):
        i += 1
    if i >= len(tokens):
        return True  # bare env assignment, no command
    cmd = tokens[i]
    args = tokens[i + 1:]
    if cmd not in _READ_ONLY_COMMANDS:
        return False
    return _command_flags_ok(cmd, args)


def _command_flags_ok(cmd: str, args: List[str]) -> bool:
    if cmd in ("sed", "awk", "perl"):
        if any(_is_inplace_flag(a) for a in args):
            return False
    elif cmd == "find":
        if any(a in _FIND_MUTATING for a in args):
            return False
    elif cmd == "sort":
        if any(a in ("-o", "--output") or a.startswith("--output=") for a in args):
            return False
    elif cmd == "git":
        return _git_flags_ok(args)
    return True


def _is_inplace_flag(arg: str) -> bool:
    """In-place edit flag for sed/perl/awk (``-i``, ``--in-place``, ``-pi``).

    Scoped to those commands: ``grep -i`` (case-insensitive) is not in-place."""
    if arg == "--in-place" or arg.startswith("--in-place"):
        return True
    if arg.startswith("-") and not arg.startswith("--") and "i" in arg[1:]:
        return True
    return False


def _git_flags_ok(args: List[str]) -> bool:
    sub = next((a for a in args if not a.startswith("-")), None)
    if sub not in _GIT_READ_ONLY_SUBCMDS:
        return False
    # ``--output`` writes a file even for an otherwise read-only subcommand.
    if any(a == "-o" or a.startswith("--output") for a in args):
        return False
    return True


def is_read_only_call(tool_name: str, args: Optional[dict]) -> bool:
    """True if calling ``tool_name`` with ``args`` only observes state."""
    if tool_name in _SHELL_TOOL_NAMES:
        return shell_command_is_read_only((args or {}).get("command"))
    return tool_name in _READ_ONLY_BUILTINS


# --------------------------------------------------------------------------
# Grouping
# --------------------------------------------------------------------------

def plan_groups(ctxs: List[Any]) -> List[List[Any]]:
    """Split contexts into execution groups: runs of consecutive read-only
    calls stay together; every other call is its own barrier group."""
    groups: List[List[Any]] = []
    run: List[Any] = []
    for ctx in ctxs:
        if is_read_only_call(ctx.actual_tool_name, ctx.args):
            run.append(ctx)
        else:
            if run:
                groups.append(run)
                run = []
            groups.append([ctx])
    if run:
        groups.append(run)
    return groups


# --------------------------------------------------------------------------
# Batch execution
# --------------------------------------------------------------------------

@dataclass
class BatchOutcome:
    """Side-effect summary the caller inspects after the batch drains."""
    executed: bool = False
    stop_requested: bool = False
    feedback_received: bool = False
    cancelled: bool = False
    deferred_feedback: List[str] = field(default_factory=list)


def _absorb(ctx: Any, outcome: BatchOutcome) -> None:
    if getattr(ctx, "should_stop_stream", False):
        outcome.stop_requested = True
    if getattr(ctx, "feedback_received", False):
        outcome.feedback_received = True
    fb = getattr(ctx, "deferred_feedback", None)
    if fb:
        outcome.deferred_feedback.extend(fb)


async def _run_group(group: List[Any], outcome: BatchOutcome) -> AsyncGenerator[Dict[str, Any], None]:
    """Run one group concurrently.  Live events (tool_start, tool_display,
    stream_end, ...) are yielded as they arrive; ``_tool_result`` events are
    buffered and re-emitted in group order once every call finishes.  A stop
    directive cancels the siblings still in flight and yields no results."""
    queue: asyncio.Queue = asyncio.Queue()
    tasks: Dict[str, asyncio.Task] = {}
    results: Dict[str, Dict[str, Any]] = {}

    async def _drive(ctx: Any) -> None:
        try:
            async for evt in execute_single_tool(ctx):
                await queue.put((ctx, evt))
        finally:
            await queue.put((ctx, None))  # completion sentinel

    for ctx in group:
        tasks[ctx.tool_id] = asyncio.create_task(_drive(ctx))

    done = 0
    stop = False
    while done < len(group):
        ctx, evt = await queue.get()
        if evt is None:
            done += 1
            _absorb(ctx, outcome)
            if getattr(ctx, "should_stop_stream", False):
                stop = True
                break
            continue
        if evt.get("type") == "_tool_result":
            results[ctx.tool_id] = evt
        else:
            yield evt

    if stop:
        for task in tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        return  # a stopped batch emits no tool results

    await asyncio.gather(*tasks.values(), return_exceptions=True)
    for ctx in group:  # arrival order, not completion order
        if ctx.tool_id in results:
            yield results[ctx.tool_id]


async def run_tool_batch(
    ctxs: List[Any],
    *,
    inter_tool_delay: dict,
    outcome: BatchOutcome,
    cancel_event: Optional[asyncio.Event] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Execute the batch of tool calls ``ctxs`` and stream their events.

    Reads in a group overlap; a non-read-only call is a barrier.  Results are
    emitted in arrival order.  Populates ``outcome`` with what happened."""
    if not ctxs:
        return  # empty batch: outcome.executed stays False

    # The adaptive inter-tool delay is a per-turn cost, paid once here rather
    # than once per call, then decayed a single step on this successful batch.
    wait = inter_tool_delay.get("current", 0.0)
    if wait and wait > 0:
        await asyncio.sleep(wait)
    inter_tool_delay["current"] = max(
        inter_tool_delay.get("min", 0.0),
        wait * inter_tool_delay.get("decay_factor", 1.0),
    )

    groups = plan_groups(ctxs)
    for gi, group in enumerate(groups):
        async for evt in _run_group(group, outcome):
            yield evt
        outcome.executed = True

        if outcome.stop_requested:
            return  # siblings cancelled inside _run_group; no stubbing
        if outcome.feedback_received:
            # Everything not yet started is stubbed, and each stub tells the
            # model why — exactly as the old sequential skip path did.
            for later in groups[gi + 1:]:
                for ctx in later:
                    yield {
                        "type": "_tool_result", "tool_id": ctx.tool_id,
                        "tool_name": ctx.tool_name, "result": FEEDBACK_SKIP_MESSAGE,
                    }
                    yield {
                        "type": "tool_result_for_model", "tool_use_id": ctx.tool_id,
                        "content": FEEDBACK_SKIP_MESSAGE,
                    }
            return
        if cancel_event is not None and cancel_event.is_set():
            outcome.cancelled = True
            return
