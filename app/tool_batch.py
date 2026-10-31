"""Deferred, partially concurrent execution of the tool calls a single model
turn emitted.

A model turn can emit several tool calls at once.  Executing them strictly one
at a time is safe but slow; executing them all concurrently is fast but unsafe
(a write racing a read of the same file).  This module splits the difference:

* Calls whose resource footprints do not conflict (app/utils/tool_footprint.py)
  form a group that runs concurrently: reads, plus writes that are append-only
  or touch distinct paths and cannot ask for approval.
* Any other call is a **barrier** — it runs alone, after everything before it
  has finished and before anything after it starts.
* The leading reads of a message may already be running when the batch is
  dispatched (app/utils/tool_prestart.py); the batch replays their events.

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
import time
import uuid
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
    "list_run_artifacts", "validate_output",
    "shadow_list", "shadow_read", "shadow_wait",
    "render_diagram", "view_image", "recall_image",
    "analyze", "dataset_describe", "dataset_preview", "dataset_list",
    "ledger_query", "job_status", "job_read", "task_run_read",
    "handoff_read", "bead_status", "mcp_server_status",
    "nova_web_search",
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
    "tree", "du", "df", "free", "uptime", "ps", "rg", "jq", "xargs",
})

# git subcommands that never mutate the repo or working tree.
_GIT_READ_ONLY_SUBCMDS = frozenset({
    "log", "status", "show", "diff", "blame", "rev-parse", "ls-files",
    "ls-tree", "cat-file", "describe", "shortlog", "reflog", "whatchanged",
    "grep", "check-ignore",
})

# find primaries that act rather than observe.
_FIND_MUTATING = frozenset({
    "-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf",
    "-fls",
})

_ENV_ASSIGN_RE = re.compile(r"^\w+=")
# Control operators as shlex emits them with punctuation_chars=True.  Quoted
# text is never split: ``grep "a\|b"`` is one argument, not two commands.
_CONTROL_OPS = frozenset({"|", "||", "&&", ";", "&", "|&", ";;"})
_WRITE_REDIRS = frozenset({">", ">>", ">|"})
_INPUT_REDIRS = frozenset({"<", "<<", "<<<"})
_DURATION_RE = re.compile(r"^\d+(\.\d+)?[smhd]?$")
# xargs flags that consume the following token.
_XARGS_VALUE_FLAGS = frozenset({"-n", "-I", "-P", "-d", "-s", "-L", "-E", "-a", "-i"})
# rg --pre runs an arbitrary preprocessor per file.
_RG_MUTATING = frozenset({"--pre"})


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
    segments = _split_segments(cmd)
    if segments is None or not segments:
        return False
    return all(_segment_is_read_only(s) for s in segments)


def _split_segments(cmd: str) -> Optional[List[List[str]]]:
    """Tokenise ``cmd`` shell-style and split on control operators.

    Lexed in non-POSIX mode so quotes stay ON the tokens: a quoted ``"&&"``
    or ``';'`` is then a plain argument, while an unquoted one is a control
    operator (in POSIX mode both arrive as the bare string and cannot be
    told apart).  Quotes are stripped from the surviving arguments
    afterwards.  Returns None on a lexing error (unbalanced quotes): don't
    guess, treat as unsafe.  Redirections are resolved here: a write
    redirection to anything but ``/dev/null`` makes the whole command unsafe
    (signalled by returning an empty list); fd-dups (``2>&1``) and input
    redirections are dropped along with their target so they never look
    like arguments."""
    lex = shlex.shlex(cmd, posix=False, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError:
        return None

    def _dequote(tok: str) -> Optional[str]:
        if '"' not in tok and "'" not in tok:
            return tok
        try:
            parts = shlex.split(tok)
        except ValueError:
            return None
        return parts[0] if len(parts) == 1 else tok

    segments: List[List[str]] = []
    cur: List[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in _CONTROL_OPS:
            segments.append(cur)
            cur = []
        elif t in _WRITE_REDIRS or t in _INPUT_REDIRS or t.endswith(">&") or t.endswith("<&"):
            # Drop an fd number that preceded the operator (``2>``).
            if cur and cur[-1].isdigit():
                cur.pop()
            target = tokens[i + 1] if i + 1 < len(tokens) else None
            if t in _WRITE_REDIRS and target != "/dev/null":
                return []
            i += 1  # skip the target / fd
        else:
            word = _dequote(t)
            if word is None:
                return None
            cur.append(word)
        i += 1
    segments.append(cur)
    return [s for s in segments if s]


def _segment_is_read_only(tokens: List[str]) -> bool:
    i = 0
    while i < len(tokens) and _ENV_ASSIGN_RE.match(tokens[i]):
        i += 1
    if i >= len(tokens):
        return True  # bare env assignment, no command
    cmd = tokens[i]
    args = tokens[i + 1:]
    if cmd in ("timeout", "time"):
        return _wrapped_is_read_only(cmd, args)
    if cmd not in _READ_ONLY_COMMANDS:
        return False
    return _command_flags_ok(cmd, args)


def _wrapped_is_read_only(wrapper: str, args: List[str]) -> bool:
    """``timeout [flags] DURATION cmd...`` / ``time [flags] cmd...``: the
    wrapper itself is inert; classify the command it runs."""
    j = 0
    while j < len(args) and args[j].startswith("-"):
        if wrapper == "timeout" and args[j] in ("-k", "--kill-after", "-s", "--signal"):
            j += 1
        j += 1
    if wrapper == "timeout":
        if j >= len(args) or not _DURATION_RE.match(args[j]):
            return False
        j += 1
    rest = args[j:]
    return bool(rest) and _segment_is_read_only(rest)


def _command_flags_ok(cmd: str, args: List[str]) -> bool:
    if cmd in ("sed", "awk", "perl"):
        if any(_is_inplace_flag(a) for a in args):
            return False
    elif cmd == "xargs":
        return _xargs_flags_ok(args)
    elif cmd == "rg":
        if any(a in _RG_MUTATING or a.startswith("--pre=") for a in args):
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


def _xargs_flags_ok(args: List[str]) -> bool:
    """``xargs`` runs the command that follows its flags; classify that.
    Bare ``xargs`` (no command) is ``echo``."""
    j = 0
    while j < len(args) and args[j].startswith("-"):
        if args[j] in _XARGS_VALUE_FLAGS:
            j += 1
        j += 1
    rest = args[j:]
    if not rest:
        return True
    return _segment_is_read_only(rest)


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


# --------------------------------------------------------------------------
# External (mcp_*) tools
# --------------------------------------------------------------------------

# A name containing ANY of these is serial, whatever else it contains
# (``ReadRemoteTestRun`` stays serial because of ``run``; ``download_file``
# writes to disk).  Conservative by design.
_MUTATING_WORDS = frozenset({
    "set", "update", "create", "creator", "delete", "remove", "post", "write",
    "add", "edit", "upload", "send", "run", "launch", "kill", "move",
    "transition", "assign", "pin", "unpin", "share", "revoke", "restore",
    "copy", "archive", "subscribe", "unsubscribe", "download", "export",
    "sync", "publish", "merge", "unmerge", "sort", "resize", "rename",
    "reorder", "reset", "refresh", "review", "lock", "unlock", "format",
    "attach", "leave", "save", "mark", "comment", "insert", "append",
    "replace", "apply", "build", "checkout", "commit", "push", "deploy",
    "start", "stop", "execute", "exec", "invoke", "submit", "bulk",
    "duplicate", "link", "unlink", "resolve", "patch", "put", "destroy",
    "clear", "purge", "drop", "join", "open", "close", "enable", "disable",
    "toggle", "change", "modify", "spawn", "trigger", "notify", "schedule",
    "cancel", "approve", "reject", "transfer", "import", "install",
    "register", "record", "propose", "complete", "stage", "draft",
})

# With no mutating word present, any of these marks the tool as observing.
_OBSERVING_WORDS = frozenset({
    "get", "read", "list", "search", "describe", "fetch", "lookup", "find",
    "query", "view", "show", "inspect", "status", "info", "history",
    "details", "preview", "summary", "exists", "outline", "diagnostics",
})

# Argument names that name a place on disk the tool will write.  A tool
# that is read-only by name (``get_page_storage``) becomes a writer when one
# of these carries a value.
_WRITE_TARGET_ARG_RE = re.compile(
    r"(save|output|out|dest|destination|target)_?(path|file|dir|directory|folder)"
    r"|^(file_?paths?|dest_?dir)$",
    re.IGNORECASE,
)
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _strip_mcp(name: str) -> str:
    while name.startswith("mcp_"):
        name = name[4:]
    return name


def _name_words(name: str) -> frozenset:
    """``kuiper_confluence_get_page_content`` / ``ReadInternalWebsites`` ->
    lower-cased words."""
    spaced = _CAMEL_RE.sub("_", name)
    return frozenset(w.lower() for w in re.split(r"[_\-\s]+", spaced) if w)


def _parallel_config() -> Dict[str, Any]:
    """``permissions.json`` -> ``{"parallel": {"read_only_tools": [...],
    "serial_tools": [...]}}``; names with or without ``mcp_``.  Never raises."""
    try:
        from app.mcp.permissions import get_permissions_manager
        cfg = (get_permissions_manager().get_permissions() or {}).get("parallel") or {}
        return cfg if isinstance(cfg, dict) else {}
    except Exception:  # noqa: BLE001 — configuration is advisory
        return {}


def _annotations(tool: Any) -> Dict[str, Any]:
    if tool is None:
        return {}
    meta = getattr(tool, "metadata", None)
    ann = meta.get("annotations") if isinstance(meta, dict) else None
    if ann is None:
        ann = getattr(tool, "annotations", None)
    return ann if isinstance(ann, dict) else {}


def _writes_a_target_path(args: Optional[dict]) -> bool:
    for k, v in (args or {}).items():
        if isinstance(k, str) and _WRITE_TARGET_ARG_RE.search(k) and v not in (None, "", [], False):
            return True
    return False


def external_tool_is_read_only(tool_name: str, args: Optional[dict], tool: Any = None) -> bool:
    """Read-only classification for a tool served by an external MCP server.

    Ordering decision only (which calls of one turn may overlap); the write
    policy and signing are unaffected.  Precedence: permissions.json lists,
    the server's ``readOnlyHint``, a write-target argument, the name."""
    clean = _strip_mcp(tool_name)
    cfg = _parallel_config()
    serial = {_strip_mcp(n) for n in cfg.get("serial_tools") or [] if isinstance(n, str)}
    read_only = {_strip_mcp(n) for n in cfg.get("read_only_tools") or [] if isinstance(n, str)}
    if clean in serial:
        return False
    if clean in read_only:
        return True
    ann = _annotations(tool)
    if "readOnlyHint" in ann:
        return bool(ann["readOnlyHint"]) and not ann.get("destructiveHint")
    if _writes_a_target_path(args):
        return False
    words = _name_words(clean)
    if words & _MUTATING_WORDS:
        return False
    return bool(words & _OBSERVING_WORDS)


def is_read_only_call(tool_name: str, args: Optional[dict], tool: Any = None) -> bool:
    """True if calling ``tool_name`` with ``args`` only observes state.

    ``tool`` is the advertised tool object when the caller has it; external
    tools are classified from its annotations (see
    ``external_tool_is_read_only``)."""
    if tool_name in _SHELL_TOOL_NAMES:
        return shell_command_is_read_only((args or {}).get("command"))
    if tool_name in _READ_ONLY_BUILTINS:
        return True
    is_builtin = tool is not None and getattr(tool, "tool_instance", None) is not None
    if is_builtin or tool_name.startswith(("file_", "memory_", "context_", "bead_", "job_", "ledger_", "task_card_", "shadow_", "handoff_", "dataset_")):
        return False  # a Ziya builtin not on the list stays a barrier
    return external_tool_is_read_only(tool_name, args, tool)


def _find_tool(ctx: Any) -> Any:
    tools = getattr(ctx, "all_tools", None) or ()
    wanted = {ctx.actual_tool_name, getattr(ctx, "tool_name", None), f"mcp_{ctx.actual_tool_name}"}
    for t in tools:
        if getattr(t, "name", None) in wanted:
            return t
    return None


# --------------------------------------------------------------------------
# Grouping
# --------------------------------------------------------------------------

def _project_root(ctx: Any) -> Optional[str]:
    root = getattr(ctx, "project_root", None)
    if root:
        return root
    try:
        from app.context import get_project_root
        return get_project_root() or None
    except Exception:  # noqa: BLE001
        return None


def plan_groups(ctxs: List[Any]) -> List[List[Any]]:
    """Split contexts into execution groups that run one after another;
    the members of one group run concurrently.

    A call joins the current group unless its footprint (reads and writes,
    app/utils/tool_footprint.py) conflicts with a member's.  Reads never
    conflict with reads, so consecutive reads group exactly as before; an
    append-only or distinct-path write can now join them.  A call with no
    footprint (any other mutating tool, or a write that could ask for
    approval) is a barrier group of its own.  Conflicting calls always land
    in different groups, in issue order."""
    from app.utils.tool_footprint import call_footprint, conflicts
    groups: List[List[Any]] = []
    run: List[Any] = []
    footprints: List[Any] = []
    for ctx in ctxs:
        tool = _find_tool(ctx)
        name = ctx.actual_tool_name
        footprint = call_footprint(
            name, ctx.args, tool, _project_root(ctx),
            is_read_only_call(name, ctx.args, tool),
            call_id=str(getattr(ctx, "tool_id", "")),
        )
        if footprint is None:
            if run:
                groups.append(run)
                run, footprints = [], []
            groups.append([ctx])
            continue
        if any(conflicts(footprint, other) for other in footprints):
            groups.append(run)
            run, footprints = [], []
        run.append(ctx)
        footprints.append(footprint)
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


async def _run_group(group: List[Any], outcome: BatchOutcome,
                     prestarted: Any = None) -> AsyncGenerator[Dict[str, Any], None]:
    """Run one group concurrently.  Live events (tool_start, tool_display,
    stream_end, ...) are yielded as they arrive; ``_tool_result`` events are
    buffered and re-emitted in group order once every call finishes.  A stop
    directive cancels the siblings still in flight and yields no results.

    Group chrome for the frontend: when the group has two or more members,
    each member's ``tool_start`` carries ``batch_group = {id, size, index}``
    (index = issue order) and one ``tool_group_end`` follows the results with
    the group's wall-clock and serial time, so the chat can draw the overlap
    (utils/toolGroupDisplay.ts).  A lone call is emitted exactly as before.

    ``prestarted`` (app/utils/tool_prestart.ToolPrestarter): a member it
    already started is not executed again; its buffered events are replayed
    and its duration counts from when it really started."""
    queue: asyncio.Queue = asyncio.Queue()
    tasks: Dict[str, asyncio.Task] = {}
    results: Dict[str, Dict[str, Any]] = {}
    tagged = len(group) >= 2
    group_id = uuid.uuid4().hex[:8]
    index_of = {ctx.tool_id: i for i, ctx in enumerate(group)}
    started: Dict[str, float] = {}
    durations: Dict[str, float] = {}
    t0 = time.monotonic()
    pre_of: Dict[str, Any] = {}
    if prestarted is not None:
        for ctx in group:
            pre = prestarted.take(ctx.tool_id)
            if pre is not None:
                pre_of[ctx.tool_id] = pre

    async def _drive(ctx: Any) -> None:
        pre = pre_of.get(ctx.tool_id)
        started[ctx.tool_id] = pre.started if pre is not None else time.monotonic()
        try:
            if pre is not None:
                from app.utils.tool_prestart import replay
                source = replay(pre)
            else:
                source = execute_single_tool(ctx)
            async for evt in source:
                await queue.put((ctx, evt))
        finally:
            durations[ctx.tool_id] = (pre.duration if pre is not None
                                      else time.monotonic() - started[ctx.tool_id])
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
            if tagged and evt.get("type") == "tool_start":
                evt = {**evt, "batch_group": {
                    "id": group_id, "size": len(group), "index": index_of[ctx.tool_id],
                }}
            yield evt

    if stop:
        for task in tasks.values():
            if not task.done():
                task.cancel()
        for pre in pre_of.values():  # a replayed call's real task
            if not pre.task.done():
                pre.task.cancel()
        await asyncio.gather(*tasks.values(), *(p.task for p in pre_of.values()),
                             return_exceptions=True)
        return  # a stopped batch emits no tool results

    await asyncio.gather(*tasks.values(), return_exceptions=True)
    for ctx in group:  # arrival order, not completion order
        if ctx.tool_id in results:
            yield results[ctx.tool_id]
    if tagged:
        yield {
            "type": "tool_group_end",
            "group_id": group_id,
            "size": len(group),
            "tool_ids": [ctx.tool_id for ctx in group],
            "wall_ms": int((time.monotonic() - t0) * 1000),
            "serial_ms": int(sum(durations.values()) * 1000),
        }


async def run_tool_batch(
    ctxs: List[Any],
    *,
    inter_tool_delay: dict,
    outcome: BatchOutcome,
    cancel_event: Optional[asyncio.Event] = None,
    prestarted: Any = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Execute the batch of tool calls ``ctxs`` and stream their events.

    Reads in a group overlap; a non-read-only call is a barrier.  Results are
    emitted in arrival order.  Populates ``outcome`` with what happened.
    ``prestarted``: calls already running (app/utils/tool_prestart.py)."""
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
        async for evt in _run_group(group, outcome, prestarted):
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
