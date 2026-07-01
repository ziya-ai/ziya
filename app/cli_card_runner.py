"""In-process task-card runner for the terminal (CLI/TUI).

This is the "library face" of the task-card runtime described in the
surface-unification design: a single engine (``execute_block``) driven by
three thin adapters — the web ``/launch`` endpoint, this terminal adapter,
and (eventually) the TUI ``/card`` slash command.

It deliberately does NOT depend on the FastAPI server.  ``execute_block``
is pure async that needs only a model (``ModelManager.get_state``), MCP
up (the CLI stands this up in ``_run_with_mcp``), and a project root.
``ExecutionContext.storage`` is left ``None`` — the executor tolerates
that (iteration recording early-returns, cancel checks return False).

Escalation authorization is NOT re-implemented here: ``execute_task_block``
gates each task block through ``authorize_scope(block.id, scope)`` against
the same signed ledger the GUI writes.  A card approved/edited/tested in
the GUI is therefore already authorized when run from the command line —
no second approval, no ``tasks.yaml`` round-trip.
"""
from __future__ import annotations

import re
import sys
import time
import uuid
from typing import Optional

from app.utils.logging_utils import logger


def _c(code: str, text: str) -> str:
    """Wrap text in an ANSI color when stdout is a TTY, else return plain."""
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def resolve_card(root: str, card_ref: str):
    """Resolve a card by id (or, failing that, by name) for a project root.

    Returns ``(card, project_id)`` or ``(None, project_id_or_None)``.  The
    card store is keyed by ``project_id``, which the server derives from the
    filesystem path via ``ProjectStorage.get_by_path``.  If the root has
    never been opened in the GUI there is no project record and therefore
    no cards to run — that is reported distinctly from "card not found".
    """
    from app.storage.projects import ProjectStorage
    from app.storage.task_cards import TaskCardStorage
    from app.utils.paths import get_ziya_home, get_project_dir

    project = ProjectStorage(get_ziya_home()).get_by_path(root)
    if project is None:
        return None, None

    storage = TaskCardStorage(get_project_dir(project.id))
    card = storage.get(card_ref)
    if card is not None:
        return card, project.id

    # Fall back to a case-insensitive name match so `ziya task <name>` can
    # address a card the same way the deck labels it.
    for c in storage.list():
        if c.name.lower() == card_ref.lower():
            return c, project.id
    return None, project.id


def list_cards(root: str) -> int:
    """Print the cards available for ``root``.  Returns a process exit code."""
    from app.storage.projects import ProjectStorage
    from app.storage.task_cards import TaskCardStorage
    from app.utils.paths import get_ziya_home, get_project_dir

    project = ProjectStorage(get_ziya_home()).get_by_path(root)
    if project is None:
        print("No project registered for this directory — open it in the "
              "Ziya GUI once to create task cards.", file=sys.stderr)
        return 1

    cards = TaskCardStorage(get_project_dir(project.id)).list()
    if not cards:
        print("No task cards defined for this project.")
        return 0
    width = max(len(c.name) for c in cards)
    print(_c("1", "Available task cards:") + "\n")
    for c in sorted(cards, key=lambda x: x.name.lower()):
        tmpl = _c("90", " (template)") if c.is_template else ""
        print(f"  {_c('36', c.name.ljust(width))}  {c.description}{tmpl}")
        print(f"  {' ' * width}  {_c('90', 'id: ' + c.id)}")
    print("\nRun: " + _c("36", "ziya task --card <id>"))
    return 0


# Meta markers the executor emits for the GUI to render structurally:
#   <thinking-data>…</thinking-data>  collapsible reasoning panel
#   <self_assessment .../>            parsed into artifact.self_assessment
# In the terminal they are noise — the assessment is already surfaced by
# _render_artifact, and reasoning is not worth the clutter.  We strip both
# from complete strings (summary/outputs) and, statefully, from the live
# delta stream where a tag may straddle two deltas.
_THINK_BLOCK_RE = re.compile(r"<thinking-data>.*?</thinking-data>", re.DOTALL)


def _strip_meta_tags(text: str) -> str:
    """Remove thinking-data blocks and the self_assessment tag from a
    COMPLETE string (not a delta).  Idempotent; safe on tag-free text."""
    if not text:
        return text
    from app.utils.completion_check import strip_assessment_tag
    return strip_assessment_tag(_THINK_BLOCK_RE.sub("", text)).strip()


class _DeltaFilter:
    """Stateful filter that strips meta markers from a *streamed* sequence
    of text deltas, where an opening/closing tag may split across deltas.

    Holds back only the minimal tail that could be the start of a tag, so
    visible text streams with no perceptible delay.  Verified against
    split-tag, multi-block, trailing-whitespace, math-``<`` and
    unclosed-block cases.
    """

    _THINK_OPEN = "<thinking-data>"
    _THINK_CLOSE = "</thinking-data>"
    _SA = "<self_assessment"

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False

    @staticmethod
    def _tail_prefix_len(buf: str, cands) -> int:
        maxlen = max(len(c) for c in cands)
        for L in range(min(len(buf), maxlen), 0, -1):
            if any(c.startswith(buf[-L:]) for c in cands):
                return L
        return 0

    def feed(self, text: str) -> str:
        self._buf += text
        out = []
        while self._buf:
            if self._in_think:
                idx = self._buf.find(self._THINK_CLOSE)
                if idx != -1:
                    self._buf = self._buf[idx + len(self._THINK_CLOSE):]
                    self._in_think = False
                    continue
                keep = self._tail_prefix_len(self._buf, [self._THINK_CLOSE])
                self._buf = self._buf[len(self._buf) - keep:] if keep else ""
                break
            lt = self._buf.find("<")
            if lt == -1:
                out.append(self._buf); self._buf = ""; break
            if lt > 0:
                out.append(self._buf[:lt]); self._buf = self._buf[lt:]
            if self._buf.startswith(self._THINK_OPEN):
                self._buf = self._buf[len(self._THINK_OPEN):]
                self._in_think = True
                continue
            if self._buf.startswith(self._SA):
                gt = self._buf.find(">")
                if gt != -1:
                    self._buf = self._buf[gt + 1:]
                    continue
                break  # incomplete self_assessment tag — wait for more
            if any(c.startswith(self._buf) for c in (self._THINK_OPEN, self._SA)):
                break  # buffered '<' could still become a tag
            out.append("<"); self._buf = self._buf[1:]  # a real '<' (math/code)
        return "".join(out)

    def flush(self) -> str:
        """Emit any safe remainder at stream end.  An unclosed thinking
        block is dropped (never completed → never shown)."""
        if self._in_think:
            self._buf = ""
            return ""
        out = self._buf
        self._buf = ""
        return out


def _render_artifact(artifact) -> None:
    """Render a completed Artifact to the terminal."""
    print()
    if artifact.failed:
        print(_c("31", "✗ Task card failed"))
    else:
        print(_c("32", "✓ Task card complete"))

    if artifact.summary:
        summary = _strip_meta_tags(artifact.summary)
        if summary:
            print("\n" + _c("1", "Summary:"))
            print(summary)

    if artifact.decisions:
        print("\n" + _c("1", "Decisions:"))
        for d in artifact.decisions:
            print(f"  • {d}")

    text_parts = [p for p in artifact.outputs if p.part_type == "text" and p.text]
    if text_parts:
        cleaned = [t for t in (_strip_meta_tags(p.text) for p in text_parts) if t]
        if cleaned:
            print("\n" + _c("1", "Outputs:"))
            for t in cleaned:
                print(t)

    file_parts = [p for p in artifact.outputs if p.part_type == "file" and p.file_uri]
    if file_parts:
        print("\n" + _c("1", "Files:"))
        for p in file_parts:
            print(f"  • {p.file_uri}")

    sa = getattr(artifact, "self_assessment", None)
    if sa:
        verdict = sa.get("objective_met", "unknown")
        color = {"true": "32", "partial": "33", "false": "31"}.get(verdict, "90")
        print("\n" + _c("1", "Self-assessment: ") + _c(color, verdict))
        if sa.get("rationale"):
            print(f"  {sa['rationale']}")

    meta = (f"{artifact.tool_calls} tool calls · {artifact.tokens} tokens · "
            f"{artifact.duration_ms} ms")
    print("\n" + _c("90", meta))


class _StdoutSink:
    """Duck-typed relay client that renders live executor events to stdout.

    ``task_run_stream_relay`` fans every event out to any registered object
    exposing ``async send_json(event)`` — the same contract a GUI WebSocket
    satisfies.  Registering one turns the existing event stream into a live
    terminal view without the relay or executor knowing a web client isn't
    on the other end.  Only the events worth surfacing interactively are
    rendered; the end-of-run artifact remains the authoritative summary.

    Event shapes are emitted by app/agents/task_executor.py:
      task_started   → block_name / block_id
      task_text_delta→ content   (live push sends raw deltas, not collapsed)
      task_tool_call → tool_name
      task_finished  → ok / error
    and by block_executor.py: iteration_started → index.
    """

    def __init__(self) -> None:
        self._mid_text = False  # True while a delta stream is open on the line
        self._filter = None     # per-task _DeltaFilter, reset on task_started

    def _break_text(self) -> None:
        if self._filter is not None:
            tail = self._filter.flush()
            if tail:
                sys.stdout.write(tail)
            self._filter = None
        if self._mid_text:
            print()
            self._mid_text = False

    async def send_json(self, event: dict) -> None:
        etype = event.get("type")
        if etype == "task_started":
            self._break_text()
            label = event.get("block_name") or event.get("block_id") or "task"
            print(_c("36", f"  ▸ {label}"))
            self._filter = _DeltaFilter()
        elif etype == "task_text_delta":
            raw = event.get("content", "")
            visible = self._filter.feed(raw) if self._filter is not None else raw
            if visible:
                sys.stdout.write(visible)
                sys.stdout.flush()
                self._mid_text = True
        elif etype == "task_tool_call":
            self._break_text()
            print(_c("90", f"    ⚙ {event.get('tool_name') or 'tool'}"))
        elif etype == "task_finished":
            self._break_text()
            if not event.get("ok", True):
                print(_c("31", f"    ✗ {event.get('error', '')}"))
        elif etype == "iteration_started":
            idx = event.get("index")
            if idx is not None:
                print(_c("90", f"  ↻ iteration {idx}"))


def _iter_task_blocks(block):
    """Yield every ``task`` block in the tree, depth-first."""
    if getattr(block, "block_type", None) == "task":
        yield block
    for child in (getattr(block, "body", None) or []):
        yield from _iter_task_blocks(child)


def _warn_unauthorized_scopes(root_block) -> None:
    """Notice (not enforcement) for task blocks whose escalating scope is
    not signed-authorized.  Those will run at the default floor — shell
    grants dropped, write flags stripped — exactly as the GUI does.

    Uses the SAME key (``block.id``) and predicate the executor enforces
    with: task_executor calls ``authorize_scope(block.id, scope)``, which
    is ``scope if is_scope_authorized(block.id, scope) else floor``.  So a
    block flagged here is exactly one the executor will down-scope — the
    notice cannot disagree with what actually runs.
    """
    from app.utils.scope_approvals import is_scope_authorized
    unauth = []
    for blk in _iter_task_blocks(root_block):
        if blk.scope is None:
            continue
        try:
            if not is_scope_authorized(blk.id, blk.scope):
                unauth.append(blk)
        except Exception as e:  # noqa: BLE001 — a check failure must not block the run
            logger.debug("scope auth check failed for block %s: %s", blk.id, e)
    if not unauth:
        return
    print(_c("33", "⚠ Un-approved escalation — these task blocks will run at "
                   "the default floor (no extra shell/write grants):"))
    for blk in unauth:
        print(_c("33", f"    • {blk.name or blk.id}"))
    print(_c("90", "  Approve the card in the GUI deck (or via ziya-approve) "
                   "to grant its full scope here."))


async def run_card(root: str, card_ref: str, stream: bool = True) -> int:
    """Resolve and execute a task card in-process.  Returns an exit code.

    Must be awaited inside ``_run_with_mcp`` so MCP servers are live for the
    duration of the run.  Environment/auth setup is the caller's job.
    """
    from app.agents.block_executor import (
        execute_block, ExecutionContext, BlockExecutionCancelled,
    )

    card, project_id = resolve_card(root, card_ref)
    if card is None:
        if project_id is None:
            print("No project registered for this directory — open it in the "
                  "Ziya GUI once to create task cards.", file=sys.stderr)
        else:
            print(_c("31", f"No task card matches '{card_ref}'."), file=sys.stderr)
            print("Run " + _c("36", "ziya task --list-cards") +
                  " to see available cards.", file=sys.stderr)
        return 1

    run_id = f"cli-{uuid.uuid4().hex[:12]}"
    ctx = ExecutionContext(
        run_id=run_id,
        project_root=root,
        project_id=project_id,
        storage=None,  # no run persistence for a one-shot CLI run (v1)
    )

    print(_c("36", f"▶ Running card '{card.name}' ({card.root.block_type})") +
          _c("90", f"  [{run_id}]"))

    # (B) Surface un-approved escalations BEFORE running, so the operator
    # knows the card is about to run at the floor rather than discovering it
    # afterward.  Reporting only — the executor independently enforces.
    _warn_unauthorized_scopes(card.root)

    # (A) Live streaming: register a stdout sink with the same relay the GUI
    # WebSocket uses.  task_executor already pushes events because ctx.run_id
    # is set; the relay simply had no listener until now.  Skip on a non-TTY
    # (piped output) where the clean end-of-run artifact reads better than
    # interleaved deltas.
    relay = None
    sink = None
    if stream and sys.stdout.isatty():
        from app.agents import task_run_stream_relay as relay
        sink = _StdoutSink()
        await relay.connect(run_id, sink)

    started = time.time()
    try:
        artifact = await execute_block(card.root, ctx)
    except BlockExecutionCancelled:
        print("\n" + _c("33", "Cancelled."), file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001 — surface any executor failure
        logger.exception("CLI card run failed")
        print("\n" + _c("31", f"Card run error: {e}"), file=sys.stderr)
        return 1
    finally:
        if sink is not None and relay is not None:
            await relay.disconnect(run_id, sink)

    _render_artifact(artifact)
    logger.debug("CLI card run %s finished in %.1fs", run_id, time.time() - started)
    return 1 if artifact.failed else 0
