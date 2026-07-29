# CRITICAL: Set chat mode before any imports
# CLI entry point
import os
os.environ["ZIYA_MODE"] = "chat"
os.environ.setdefault("ZIYA_LOG_LEVEL", "WARNING")

# Startup optimization: block langchain_core.language_models.base from
# eagerly importing transformers (which pulls in torch, ~6s cold start).
# Langchain wraps that import in try/except and falls back to _HAS_TRANSFORMERS=False,
# which only affects BaseLanguageModel.get_num_tokens() — a path we don't use
# because tokenization goes through tiktoken (see app/utils/tiktoken_compat.py).
# Only install the stub if tiktoken is available so the fallback isn't needed.
import sys as _sys
try:
    import tiktoken as _tiktoken  # noqa: F401
    class _TransformersStub:
        def __getattr__(self, name):
            raise ImportError("transformers intentionally stubbed at CLI startup")
    _sys.modules.setdefault("transformers", _TransformersStub())
except ImportError:
    pass  # tiktoken missing — let transformers load normally as the fallback
del _sys

import logging
import unicodedata

"""
Ziya CLI - Clean command-line interface.

Usage:
    ziya chat [FILES...]           Interactive chat with optional file context
    ziya ask "question" [FILES...] Single question, get answer, exit
    ziya review [FILES...]         Review code (alias for ask with review prompt)
    ziya explain [FILES...]        Explain code (alias for ask with explain prompt)
    
Examples:
    ziya chat                      Start interactive chat
    ziya chat src/                 Chat with src/ directory in context
    ziya ask "what does this do?" main.py
    ziya explain utils.py
    git diff | ziya ask "review this"
    ziya review --staged           Review staged git changes
"""

import argparse
import hashlib
import json
from datetime import datetime
import os
import sys
from typing import Optional
import asyncio
try:
    # CRITICAL: Force reconfigure all existing loggers to respect chat mode
    # This handles case where modules were imported before ZIYA_MODE was set
    for logger_name in list(logging.Logger.manager.loggerDict.keys()):
        if logger_name.startswith('app.') or logger_name == 'app':
            try:
                existing_logger = logging.getLogger(logger_name)
                existing_logger.setLevel(logging.WARNING)
                for handler in existing_logger.handlers:
                    handler.setLevel(logging.WARNING)
            except (AttributeError, TypeError, ValueError):
                pass  # Skip loggers that can't be configured
except (AttributeError, TypeError, ValueError, RuntimeError) as e:
    print(f"Warning: Could not configure logging: {e}", file=sys.stderr)
import re
import signal
import time
import traceback
from pathlib import Path
import sys
from app.utils.logging_utils import logger
from app.config.env_registry import ziya_env
from app.utils.interruptible_input import interruptible_input
from typing import List, Tuple 
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import PathCompleter, WordCompleter, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.filters import has_selection
from prompt_toolkit.keys import Keys
from prompt_toolkit.application import Application
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window
from prompt_toolkit.layout.containers import WindowAlign
from prompt_toolkit.widgets import RadioList
from prompt_toolkit.widgets import Label, Button, TextArea
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.formatted_text import FormattedText


# ============================================================================
# Session history management
# ============================================================================

# Phrases that announce intent to act ("Let me check X before writing...").
# Mirrors the server-side StreamingToolExecutor discriminator (_INTENT_PHRASES
# in app/streaming_tool_executor.py) so the CLI's OUTER continuation loop
# applies the same rule, independently of whatever the inner executor decided.
_CLI_INTENT_PHRASES = (
    'let me check', 'let me look', 'let me examine',
    'let me search', 'let me verify', 'let me read',
    'let me review', 'let me see', 'let me inspect',
    'let me explore', 'let me first', 'let me dig',
    'let me get', 'let me run', 'let me confirm',
    'let me gather', 'let me pull', 'let me grab',
    'let me trace', 'let me find', 'let me locate', 'let me investigate',
    "i'll check", "i'll look", "i'll examine",
    "i'll search", "i'll verify", "i'll read",
    "i'll review", "i'll inspect", "i'll run", "i'll confirm",
    "i'll get", "i'll write", "i'll create", "i'll update",
    "i'll gather", "i'll pull", "i'll grab", "i'll trace",
    "i'll find", "i'll locate", "i'll investigate",
    'before writing', 'before i write',
    'before creating', 'before i create',
    'first, let me', 'first let me',
    'i need to read', 'i need to check',
)


def _strip_decorative(response: str) -> str:
    """Drop trailing decoration so the real terminal punctuation is visible.

    Emoji (So), modifier symbols such as skin tones (Sk), and format/joining
    chars such as ZWJ and variation selectors (Cf, Mn): "shall we continue?
    \U0001F604" hands the turn back to the user exactly as "shall we
    continue?" does, and must not be misread as mid-sentence truncation.
    """
    stripped = response.rstrip()
    while stripped and unicodedata.category(stripped[-1]) in ('So', 'Sk', 'Cf', 'Mn'):
        stripped = stripped[:-1].rstrip()
    return stripped


def _looks_truncated(response: str) -> bool:
    """True iff the response ends mid-thought -- the truncation trigger alone,
    independent of the unexecuted-intent trigger.

    Split out so the async call site can re-derive the truncation verdict
    after the intent judge overrules the phrase prefilter, without
    duplicating these conditions.
    """
    stripped = _strip_decorative(response)
    return (
        stripped.endswith(':') or
        stripped.endswith('...') or
        (len(stripped) > 100 and stripped[-1] not in '.!?)')
    )


def _response_looks_incomplete(response: str) -> Tuple[bool, bool]:
    """Decide whether a diff-free model response should be auto-continued.

    Returns (looks_incomplete, has_unexecuted_intent). Pure function, no I/O,
    so it is directly unit-testable without mocking the model call.

    Two independent triggers:
      - truncation: ends with ':' / '...' / a non-terminal char after 100+ chars
      - unexecuted intent: narrates an action ("Let me check X.") without a
        tool call, and does NOT end in '?' (a question is the model handing
        the turn back to the user, not narrating unfinished work -- don't
        nudge that case).

    The intent scan is restricted to the response TAIL (the same window
    app/services/intent_judge.extract_tail hands the judge), not the whole
    body.  A response that OPENS with "Let me verify X." and then actually
    verifies X with tool calls is complete; matching the phrase anywhere in
    the body fired on exactly that case and auto-continued a finished turn.
    Only a phrase still present in the closing paragraphs is evidence of an
    announcement the response never got back to.

    Trailing emoji and other decorative symbols are stripped before the
    punctuation checks: "shall we continue? \U0001F604" hands the turn back
    to the user exactly as "shall we continue?" does, and must not be
    misread as mid-sentence truncation.
    """
    stripped = _strip_decorative(response)
    yields_to_user = stripped.endswith('?')
    from app.services.intent_judge import extract_tail
    has_unexecuted_intent = (
        not yields_to_user
        and any(p in extract_tail(stripped).lower() for p in _CLI_INTENT_PHRASES)
    )
    looks_incomplete = _looks_truncated(response) or has_unexecuted_intent
    return looks_incomplete, has_unexecuted_intent


async def _adjudicate_continuation(response: str) -> Tuple[bool, bool]:
    """Full continuation verdict for a diff-free response: cheap phrase
    prefilter, then cheap-model adjudication of the intent trigger.

    Returns (should_continue, intent_confirmed) -- the same shape as
    _response_looks_incomplete, but with the judge applied.

    Extracted from ask() so the judge gate is reachable from a unit test;
    inline it could only be exercised by driving an entire turn.

    The judge runs ONLY when intent is the trigger, so a purely truncated
    response costs no model call.  A "no" verdict drops the intent trigger
    and re-derives the truncation verdict alone, so a response that was
    BOTH truncated and intent-matching still continues on truncation.
    """
    looks_incomplete, has_unexecuted_intent = _response_looks_incomplete(response)
    if looks_incomplete and has_unexecuted_intent:
        # Substring prefilter cannot tell a genuine dangling announcement
        # from an intent phrase inside quoted content, a conditional-on-the-
        # user future ("once you apply it, I'll run the suite"), or a
        # negated/past-tense disclaimer -- all of which have false-positived
        # here.  Mirror the executor's judge gate.
        # Fail-closed: transport error or "no" drops the intent trigger
        # (costing one lost auto-continue) rather than risking a wrong
        # "continue" (a full-context primary-model round trip).
        # judge_dangling_intent already returns False on transport failure,
        # but guard the CALL too: an ImportError on the inline import, or any
        # unexpected error escaping the judge, would otherwise propagate out
        # of ask() and abort the turn -- a strictly worse outcome than
        # skipping one auto-continue.
        verdict = False
        try:
            from app.services.intent_judge import judge_dangling_intent
            verdict = await judge_dangling_intent(response)
        except Exception as e:
            print(f"\033[90m[trace] intent judge unavailable ({type(e).__name__}), not continuing\033[0m", file=sys.stderr)
        if not verdict:
            print("\033[90m[trace] intent judge: response complete, not continuing\033[0m", file=sys.stderr)
            has_unexecuted_intent = False
            looks_incomplete = _looks_truncated(response)
    return looks_incomplete, has_unexecuted_intent


def get_session_dir() -> Path:
    """Get the directory for session storage."""
    session_dir = Path.home() / '.ziya' / 'sessions'
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def save_session(cli: 'CLI', name: Optional[str] = None, *, cleanup: bool = True,
                 fork: bool = False) -> str:
    """Save current session and return session ID.

    If the CLI already has a _session_id (from resume or a prior save),
    that same file is updated in place (checkpoint semantics). Otherwise
    a new timestamp-based id is generated, optionally suffixed with a
    user-supplied name.

    With fork=True a new id is always generated and the CLI's live
    _session_id/_session_name are left untouched: the write is a frozen
    snapshot that can be resumed later to fork the conversation.
    """
    session_dir = get_session_dir()
    
    # Extract opening statement from first human message
    opening_statement = ''
    for msg in cli.history:
        if msg.get('type') == 'human':
            opening_statement = msg.get('content', '')[:120]
            break

    # Preserve start_time from a previously loaded session, otherwise use now
    start_time = getattr(cli, '_session_start_time', None) or datetime.now().isoformat()

    # Determine session id / filename
    if fork:
        # Snapshots always get a fresh id, and only carry a name if one is
        # explicitly given — inheriting the live session's name would make
        # /resume <name> ambiguous between the live session and the fork.
        existing_id = None
        resolved_name = name
    else:
        existing_id = getattr(cli, '_session_id', None)
        # Resolve the friendly name: explicit arg wins, else keep prior name
        resolved_name = name if name is not None else getattr(cli, '_session_name', None)

    if existing_id:
        session_id = existing_id
    else:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        if resolved_name:
            safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in resolved_name)[:40]
            session_id = f"{ts}_{safe}" if safe else ts
        else:
            session_id = ts

    session_file = session_dir / f"{session_id}.json"

    session_data = {
        'id': session_id,
        'name': resolved_name,
        'start_time': start_time,
        'last_update_time': datetime.now().isoformat(),
        'timestamp': datetime.now().isoformat(),  # kept for backward compat
        'opening_statement': opening_statement,
        'files': cli.files,
        'history': cli.history
    }
    
    with open(session_file, 'w') as f:
        json.dump(session_data, f, indent=2)

    if not fork:
        # Remember id/name on the CLI for subsequent checkpoints
        cli._session_id = session_id
        cli._session_name = resolved_name

    if cleanup:
        # Cleanup old sessions (keep last 10) — but preserve named sessions
        cleanup_old_sessions()

    return session_id


def _autocheckpoint(cli: 'CLI') -> None:
    """Silently write a mid-session checkpoint after each completed exchange.

    Skips the cleanup scan so we don't pay the cost of globbing all session
    files on every message.  Cleanup happens on clean exit / explicit saves.
    Failures are swallowed so a disk problem never interrupts the conversation.
    """
    if getattr(cli, '_ephemeral', False):
        return
    try:
        save_session(cli, cleanup=False)
    except Exception as e:  # noqa: BLE001
        # Disk failure must not interrupt conversation
        logger.debug("Auto-save session failed: %s", e)


def load_session(session_id: str) -> dict:
    """Load a session by ID."""
    session_dir = get_session_dir()
    session_file = session_dir / f"{session_id}.json"
    
    if not session_file.exists():
        raise FileNotFoundError(f"Session {session_id} not found")
    
    with open(session_file, 'r') as f:
        return json.load(f)


def find_session_by_name(name: str) -> Optional[str]:
    """Find a session id by friendly name or id match.

    Preference order: exact name match, exact id match, name prefix,
    id prefix, name substring. Returns the most recently updated match.
    """
    session_dir = get_session_dir()
    candidates = []  # (priority, mtime, id)
    for p in sorted(session_dir.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(p) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        sid = data.get('id') or p.stem
        sname = data.get('name') or ''
        mt = p.stat().st_mtime
        if sname == name:
            candidates.append((0, mt, sid))
        elif sid == name:
            candidates.append((1, mt, sid))
        elif sname and sname.startswith(name):
            candidates.append((2, mt, sid))
        elif sid.startswith(name):
            candidates.append((3, mt, sid))
        elif sname and name.lower() in sname.lower():
            candidates.append((4, mt, sid))
    if not candidates:
        return None
    candidates.sort(key=lambda t: (t[0], -t[1]))
    return candidates[0][2]


def list_sessions_for_completion(limit: int = 50) -> List[Tuple[str, str]]:
    """Return (completion_text, meta) pairs for session-name completion.

    Sessions are listed most-recently-updated first.  The completion text is
    the friendly name when present, otherwise the session id.  The meta is a
    short human hint (name/id + message count) shown in the menu.
    """
    session_dir = get_session_dir()
    results: List[Tuple[str, str]] = []
    seen = set()
    for p in sorted(session_dir.glob('*.json'),
                    key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            with open(p) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        sid = data.get('id') or p.stem
        sname = data.get('name') or ''
        text = sname or sid
        if text in seen:
            continue
        seen.add(text)
        msg_count = len(data.get('history', []))
        meta = f"id {sid[:8]} · {msg_count} msgs" if sname else f"{msg_count} msgs"
        results.append((text, meta))
    return results


# Once-per-process guard for the orphaned-bead TTL sweep.
_bead_sweep_done = False


def _remove_session_beads(session_path) -> None:
    """Remove the fallback bead file tied to a CLI session file.

    CLI conversations are keyed cli_<session_id>, and the session file is
    named <session_id>.json — so the stem maps directly.
    """
    try:
        from app.storage.beads import remove_fallback_beads
        remove_fallback_beads(f"cli_{session_path.stem}")
    except Exception:
        pass


def _print_resumed_beads(conversation_id: str) -> None:
    """Print open (active + parked) bead labels when resuming a session.

    Surfaces the task threads still in flight so a resumed session shows
    what was being worked on — the CLI analogue of the bead chip on GUI
    conversation-list rows.  Completed/abandoned beads are omitted; if no
    open threads remain, nothing is printed.  Failure is non-fatal — a
    bead-store hiccup must never block session resumption.
    """
    try:
        from app.storage.beads import load_bead_tree
        tree = load_bead_tree(conversation_id=conversation_id)
    except Exception:
        return
    beads = getattr(tree, 'beads', None) or []
    open_beads = [b for b in beads if b.status in ('active', 'parked')]
    if not open_beads:
        return
    # Match the /beads command's status glyphs/colors for consistency.
    glyphs = {
        'active': ('\033[32m', '●'),  # green
        'parked': ('\033[33m', '◐'),  # yellow
    }
    n = len(open_beads)
    print(f"\033[1mOpen threads ({n}):\033[0m")
    # Active first, then parked — most-relevant thread on top.
    for b in sorted(open_beads, key=lambda x: (x.status != 'active', x.created_at)):
        color, glyph = glyphs.get(b.status, ('\033[90m', '·'))
        hint = f"  \033[90m({b.context_hint})\033[0m" if b.context_hint else ''
        print(f"  {color}{glyph}\033[0m {b.content}{hint}")
    print()


def cleanup_old_sessions(keep_count: int = 10):
    """Keep only the most recent sessions. Named sessions and sessions with open beads are preserved."""
    global _bead_sweep_done
    session_dir = get_session_dir()
    sessions = sorted(session_dir.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)

    # Protect named sessions and sessions with active/parked beads from auto-cleanup
    unnamed = []
    for p in sessions:
        try:
            with open(p) as f:
                data = json.load(f)
                if data.get('name'):
                    continue
                sid = data.get('id') or p.stem
        except (OSError, json.JSONDecodeError, ValueError):
            sid = p.stem
        try:
            from app.storage.beads import load_bead_tree
            tree = load_bead_tree(conversation_id=f"cli_{sid}")
            if any(b.status in ('active', 'parked') for b in (getattr(tree, 'beads', None) or [])):
                continue
        except Exception:
            pass
        unnamed.append(p)
    for old_session in unnamed[keep_count:]:
        _remove_session_beads(old_session)
        old_session.unlink()

    # Sweep fallback bead files orphaned by other paths (crashed sessions,
    # ephemeral web chats that never synced).  Cheap, but once per process
    # is plenty since this runs on every session checkpoint.
    if not _bead_sweep_done:
        _bead_sweep_done = True
        try:
            from app.storage.beads import cleanup_orphaned_fallbacks
            cleanup_orphaned_fallbacks()
        except Exception:
            pass


async def select_session() -> Optional[str]:
    """Interactive session selector."""
    session_dir = get_session_dir()
    sessions = sorted(session_dir.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    
    if not sessions:
        print("No previous sessions found.")
        return None
    
    # Load session metadata
    session_list = []
    for session_file in sessions[:50]:  # newest 50; trimmed to valid below
        try:
            with open(session_file, 'r') as f:
                data = json.load(f)
                session_list.append({
                    'id': data['id'],
                    'name': data.get('name'),
                    'start_time': data.get('start_time', data.get('timestamp', '')),
                    'last_update_time': data.get('last_update_time', data.get('timestamp', '')),
                    'opening_statement': data.get('opening_statement', ''),
                    'file_count': len(data.get('files', [])),
                    'message_count': len(data.get('history', []))
                })
        except (json.JSONDecodeError, OSError, KeyError, ValueError):
            continue
    
    if not session_list:
        print("No valid sessions found.")
        return None

    # Exclude empty sessions (nothing typed, no files attached)
    session_list = [s for s in session_list if s['message_count'] > 0 or s['file_count'] > 0 or s.get('opening_statement')]

    # Cap the menu *after* dropping empties so blank sessions don't steal slots.
    session_list = session_list[:25]

    if not session_list:
        print("No valid sessions found.")
        return None
    
    # Build radio list values with formatted labels
    radio_values = []
    for session in session_list:
        try:
            started = datetime.fromisoformat(session['start_time']).strftime('%b %d %H:%M')
        except (ValueError, TypeError):
            started = '?'
        try:
            updated = datetime.fromisoformat(session['last_update_time']).strftime('%b %d %H:%M')
        except (ValueError, TypeError):
            updated = started

        opener = session.get('opening_statement', '') or ''
        # Truncate and clean for display
        opener = opener.replace('\n', ' ').strip()
        if len(opener) > 80:
            opener = opener[:77] + '...'

        from prompt_toolkit.formatted_text import HTML

        from html import escape as html_escape
        opener = html_escape(opener)
        name = session.get('name')
        meta = f"{session['message_count']} msgs, {session['file_count']} files"
        if started == updated:
            time_info = f"  <style fg='ansibrightblack'>{started}</style>"
        else:
            time_info = f"  <style fg='ansibrightblack'>started {started} · updated {updated}</style>"

        name_tag = f"<style fg='ansicyan'>[{html_escape(name)}]</style> " if name else ""
        if opener:
            label = HTML(f"{name_tag}<b>{opener}</b>\n    {meta}{time_info}")
        else:
            label = HTML(f"{name_tag}<b>(no opening message)</b>  {meta}{time_info}")

        radio_values.append((session['id'], label))

    radio_list = RadioList(values=radio_values, default=session_list[0]['id'])

    # Key bindings
    kb = KeyBindings()

    @kb.add('enter')
    def _(event):
        event.app.exit(result=radio_list.current_value)

    @kb.add('escape')
    def _(event):
        event.app.exit(result=None)

    custom_kb = KeyBindings()

    @custom_kb.add('up')
    def _(event):
        radio_list._selected_index = max(0, radio_list._selected_index - 1)
        radio_list.current_value = radio_list.values[radio_list._selected_index][0]

    @custom_kb.add('down')
    def _(event):
        radio_list._selected_index = min(len(radio_list.values) - 1, radio_list._selected_index + 1)
        radio_list.current_value = radio_list.values[radio_list._selected_index][0]

    @custom_kb.add('enter')
    def _(event):
        highlighted = radio_list.values[radio_list._selected_index][0]
        radio_list.current_value = highlighted
        event.app.exit(result=highlighted)

    from prompt_toolkit.key_binding import merge_key_bindings
    radio_list.control.key_bindings = merge_key_bindings([radio_list.control.key_bindings, custom_kb])

    layout = Layout(HSplit([
        Window(
            content=FormattedTextControl(text='Resume Session — ↑/↓ navigate, Enter select, Esc cancel\n'),
            height=2
        ),
        radio_list,
    ]))

    app = Application(layout=layout, key_bindings=kb, full_screen=False, mouse_support=False)
    import sys, termios
    _saved_tc = None
    try:
        if sys.stdin.isatty():
            _saved_tc = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    finally:
        if _saved_tc is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _saved_tc)
            except Exception:
                pass
        # Flush stdout so any buffered escape sequences are cleared.
        try:
            sys.stdout.flush()
        except Exception:
            pass

    try:
        return await app.run_async()
    except (EOFError, KeyboardInterrupt):
        return None


async def select_joinable_chat(summaries) -> Optional[str]:
    """Interactive picker for a GUI conversation to /join.

    ``summaries`` are ChatSummary objects (already sorted most-recent-first
    by the storage layer).  Mirrors select_session's RadioList UX.  Returns
    the chosen chat id, or None on cancel.
    """
    from app.utils.cli_chat_bridge import chat_display_label
    from prompt_toolkit.formatted_text import HTML
    from html import escape as html_escape

    if not summaries:
        print("No conversations to join.")
        return None

    radio_values = []
    for s in summaries[:25]:
        label_txt = html_escape(chat_display_label(s))
        try:
            updated = datetime.fromtimestamp(s.lastActiveAt / 1000).strftime('%b %d %H:%M')
        except (ValueError, TypeError, OverflowError, AttributeError):
            updated = '?'
        meta = f"{getattr(s, 'messageCount', 0)} msgs"
        open_beads = getattr(s, 'openBeadCount', 0) or 0
        bead_tag = f" · {open_beads} open" if open_beads else ""
        label = HTML(f"<b>{label_txt}</b>\n    {meta}{bead_tag}"
                     f"  <style fg='ansibrightblack'>{updated}</style>")
        radio_values.append((s.id, label))

    radio_list = RadioList(values=radio_values, default=radio_values[0][0])

    kb = KeyBindings()

    @kb.add('enter')
    def _join_enter(event):
        event.app.exit(result=radio_list.current_value)

    @kb.add('escape')
    def _join_escape(event):
        event.app.exit(result=None)

    custom_kb = KeyBindings()

    @custom_kb.add('up')
    def _join_up(event):
        radio_list._selected_index = max(0, radio_list._selected_index - 1)
        radio_list.current_value = radio_list.values[radio_list._selected_index][0]

    @custom_kb.add('down')
    def _join_down(event):
        radio_list._selected_index = min(len(radio_list.values) - 1, radio_list._selected_index + 1)
        radio_list.current_value = radio_list.values[radio_list._selected_index][0]

    @custom_kb.add('enter')
    def _join_sel_enter(event):
        highlighted = radio_list.values[radio_list._selected_index][0]
        radio_list.current_value = highlighted
        event.app.exit(result=highlighted)

    from prompt_toolkit.key_binding import merge_key_bindings
    radio_list.control.key_bindings = merge_key_bindings([radio_list.control.key_bindings, custom_kb])

    layout = Layout(HSplit([
        Window(content=FormattedTextControl(
            text='Join GUI Conversation — ↑/↓ navigate, Enter select, Esc cancel\n'), height=2),
        radio_list,
    ]))
    app = Application(layout=layout, key_bindings=kb, full_screen=False, mouse_support=False)
    try:
        return await app.run_async()
    except (EOFError, KeyboardInterrupt):
        return None


def print_chat_startup_info(args):
    """Pretty print essential startup information for chat mode."""
    root = getattr(args, 'root', None) or os.getcwd()
    profile = getattr(args, 'profile', None) or os.environ.get('AWS_PROFILE', 'default')
    model = getattr(args, 'model', None) or ziya_env('ZIYA_MODEL') or ''
    
    # Only show essential info
    print(f"Ziya CLI • profile: {profile} • model: {model}")
    print(f"Root: {root}")
    
    # Show MCP server count if available
    try:
        from app.mcp.manager import get_mcp_manager
        mcp_mgr = get_mcp_manager()
        if mcp_mgr and mcp_mgr.is_initialized:
            tool_count = len(mcp_mgr._tool_cache) if hasattr(mcp_mgr, '_tool_cache') else 0
            server_count = len(mcp_mgr.clients) if hasattr(mcp_mgr, 'clients') else 0
            print(f"MCP: {server_count} servers, {tool_count} tools")
    except (ImportError, AttributeError, OSError, RuntimeError):
        pass  # Silently skip if MCP not available
    
    print()  # Blank line before prompt


def setup_env(args):
    """CLI entry-point environment setup.

    Handles CLI-only concerns (debug logging, logger reconfiguration),
    then delegates common settings to the shared setup_environment().
    """
    # Handle debug flag first, before any other setup (must precede shared setup
    # so that logger calls inside it respect the level).
    if getattr(args, 'debug', False):
        os.environ["ZIYA_LOG_LEVEL"] = "DEBUG"
        os.environ["ZIYA_MODE"] = "debug"
        print("🐛 Debug logging enabled", file=sys.stderr)

    # Reconfigure existing loggers so modules imported before ZIYA_MODE was set
    # pick up the correct level (WARNING for chat, DEBUG when --debug).
    try:
        target_level = getattr(logging, (ziya_env('ZIYA_LOG_LEVEL') or 'WARNING').upper())
        for logger_name in list(logging.Logger.manager.loggerDict.keys()):
            if logger_name.startswith('app.') or logger_name == 'app':
                try:
                    existing_logger = logging.getLogger(logger_name)
                    existing_logger.setLevel(target_level)
                    for handler in existing_logger.handlers:
                        handler.setLevel(target_level)
                except (AttributeError, TypeError, ValueError):
                    pass
    except (AttributeError, TypeError, ValueError, RuntimeError) as e:
        print(f"Warning: Could not reconfigure logging in setup_env: {e}", file=sys.stderr)

    # Shared setup (root dir, AWS, endpoint/model validation, model params, …)
    from app.config.environment import setup_environment as _shared_setup_environment
    _shared_setup_environment(args)

    # -- CLI-only: enable MCP by default for CLI sessions -------------------
    os.environ.setdefault("ZIYA_ENABLE_MCP", "true")

def resolve_files(paths: List[str], root: str) -> List[str]:
    """Resolve file/directory paths to list of files."""
    import glob
    
    files = []
    for path in paths:
        full_path = path if os.path.isabs(path) else os.path.join(root, path)
        
        if os.path.isfile(full_path):
            files.append(os.path.relpath(full_path, root))
        elif os.path.isdir(full_path):
            # Add all supported files in directory
            for ext in ['py', 'js', 'ts', 'tsx', 'jsx', 'java', 'go', 'rs', 'rb', 'c', 'cpp', 'h']:
                pattern = os.path.join(full_path, '**', f'*.{ext}')
                for f in glob.glob(pattern, recursive=True):
                    rel = os.path.relpath(f, root)
                    # Skip common excludes
                    if not any(x in rel for x in ['node_modules', '__pycache__', '.git', 'venv', '.venv']):
                        files.append(rel)
        elif '*' in path or '?' in path:
            # Glob pattern
            for f in glob.glob(full_path, recursive=True):
                if os.path.isfile(f):
                    files.append(os.path.relpath(f, root))
    
    return sorted(set(files))


def add_auto_included_docs(files: List[str], root: str) -> List[str]:
    """Union AGENTS.md / README.md keys into ``files`` so CLI sessions get the
    same auto-included project guidance the GUI seeds via
    /api/default-included-folders (see folder_service.collect_documentation_file_keys
    and frontend/src/context/FolderContext.tsx). AGENTS.md is collected
    recursively; README.md only at the project root, mirroring that endpoint.
    """
    try:
        from app.services.folder_service import collect_documentation_file_keys
        doc_keys = collect_documentation_file_keys(
            root, is_inside_workspace=True, user_codebase_dir=root, readme_root_only=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("Could not collect auto-included docs for CLI session: %s", e)
        return files
    merged = list(files)
    for dk in doc_keys:
        if dk not in merged:
            merged.append(dk)
    return sorted(set(merged))


def read_stdin_if_available() -> Optional[str]:
    """Read from stdin if data is being piped in."""
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return None


def get_git_staged_diff() -> Optional[str]:
    """Get diff of staged changes."""
    import subprocess
    try:
        result = subprocess.run(['git', 'diff', '--cached'], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def get_git_diff() -> Optional[str]:
    """Get diff of unstaged changes."""
    import subprocess
    try:
        result = subprocess.run(['git', 'diff'], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    return None


# ---------------------------------------------------------------------------
# Declarative command spec — the single source of truth for the CLI's
# slash-command surface.  Tab completion (SmartCompleter), the "?" inline
# help pattern, /help text, and dispatch are derived from this structure.
# To add a command, subcommand, or option, edit ONLY this spec.
#
# Entry keys:
#   name        canonical command name, e.g. '/shell'
#   aliases     optional list of alternate names, e.g. ['/a']
#   help        one-line description (completion menu + /help)
#   usage       optional usage string for /help, e.g. '/add <path>'
#   completion  argument completion style: 'path' | 'model' (default: none)
#   subcommands optional {name: {'help': str, 'options': {name: help}}}
# ---------------------------------------------------------------------------
COMMAND_SPEC = [
    {
        'name': '/add',
        'aliases': ['/a'],
        'help': 'Add file or directory to context',
        'usage': '/add <path>',
        'completion': 'path',
        'handler': 'cmd_add',
    },
    {
        'name': '/rm',
        'aliases': ['/remove'],
        'help': 'Remove from context',
        'usage': '/rm <path>',
        'completion': 'path',
        'handler': 'cmd_rm',
    },
    {
        'name': '/files',
        'aliases': ['/ls', '/f'],
        'help': 'List context files',
        'handler': 'cmd_files',
    },
    {
        'name': '/root',
        'aliases': ['/cd'],
        'help': 'Show or change the project base directory',
        'usage': '/root [path]',
        'completion': 'path',
        'handler': 'cmd_root',
    },
    {
        'name': '/shell',
        'help': 'Manage shell commands (session-local by default)',
        'handler': 'cmd_shell',
        'subcommands': {
            'list': {'help': 'Show allowed shell commands'},
            'add': {'help': "Add command(s) to allowlist (append 'save' to persist)"},
            'rm': {'help': 'Remove command(s) from allowlist'},
            'yolo': {'help': 'Allow ANY shell command (session only)',
                     'options': {'off': 'Disable YOLO mode'}},
            'git': {'help': 'Allow git operations (add, commit, push, all, safe)',
                    'options': {
                        'all': 'Allow ALL git operations',
                        'add': "Allow 'git add'",
                        'commit': "Allow 'git commit'",
                        'push': "Allow 'git push'",
                        'safe': 'Reset to safe (read-only) git ops',
                    }},
            'timeout': {'help': 'Set command timeout in seconds (0 = no limit)'},
            'reset': {'help': 'Reset shell config to defaults'},
        },
    },
    {
        'name': '/goal',
        'help': 'Set an autonomous goal (runs as task card)',
        'usage': '/goal <text>',
        'handler': 'cmd_goal',
    },
    {
        'name': '/card',
        'help': 'Run a GUI task card in-process (same engine as the deck)',
        'usage': '/card [list | <id-or-name>]',
        'handler': 'cmd_card',
        'subcommands': {
            'list': {'help': 'List available task cards'},
            'run': {'help': 'Run a card by id or name'},
        },
    },
    {
        'name': '/tune',
        'help': 'Adjust session settings',
        'usage': '/tune <key> <value>',
        'handler': 'cmd_tune',
        'subcommands': {
            'iterations': {'help': 'Max tool iterations per response'},
        },
    },
    {
        'name': '/model',
        'aliases': ['/m'],
        'help': 'Switch model',
        'usage': '/model [name]',
        'completion': 'model',
        'handler': 'cmd_model',
    },
    {
        'name': '/clear',
        'help': 'Clear conversation history',
        'handler': 'cmd_clear',
    },
    {
        'name': '/reset',
        'help': 'Clear history, files, and all session state',
        'handler': 'cmd_reset',
    },
    {
        'name': '/suspend',
        'help': 'Save session and exit',
        'usage': '/suspend [name]',
        'completion': 'session',
        'arg_hint': 'session name',
        'handler': 'cmd_suspend',
    },
    {
        'name': '/save',
        'help': 'Save a forkable snapshot of the session (always a new id)',
        'usage': '/save [name]',
        'completion': 'session',
        'arg_hint': 'session name',
        'handler': 'cmd_save',
    },
    {
        'name': '/resume',
        'help': "Restore a previous session's files and history",
        'usage': '/resume [name]',
        'completion': 'session',
        'arg_hint': 'session name',
        'handler': 'cmd_resume',
    },
    {
        'name': '/join',
        'help': 'Attach to a live GUI conversation (shared, synced)',
        'usage': '/join [id-or-title]',
        'arg_hint': 'conversation',
        'handler': 'cmd_join',
    },
    {
        'name': '/tangent',
        'help': 'Pursue a temporary side-idea (branches from here; discarded on /quit)',
        'usage': '/tangent <topic>',
        'handler': 'cmd_tangent',
    },
    {
        'name': '/context',
        'aliases': ['/ctx'],
        'help': 'Break down token/context utilization for this session',
        'usage': '/context [files | tools | history | all]',
        'handler': 'cmd_context',
        'subcommands': {
            'files': {'help': 'Per-file token estimates'},
            'tools': {'help': 'Tool-definition tokens grouped by server'},
            'history': {'help': 'Per-message token estimates'},
            'all': {'help': 'Show every detail section'},
        },
    },
    {
        'name': '/beads',
        'help': "Show the conversation's task tree (beads)",
        'handler': 'cmd_beads',
    },
    {
        'name': '/help',
        'aliases': ['/h'],
        'help': 'Show help',
        'handler': 'cmd_help',
    },
    {
        'name': '/quit',
        'aliases': ['/q', '/exit'],
        'help': 'Exit (or return from an active tangent)',
        'usage': '/quit [summary|verbatim|discard]',
        'handler': 'cmd_quit',
        'subcommands': {
            'summary': {'help': 'Return, splicing an AI summary into the parent context'},
            'verbatim': {'help': 'Return, appending the full tangent transcript'},
            'discard': {'help': 'Return with nothing carried over (default)'},
        },
    },
]


def _derive_command_tables(spec):
    """Build completion/help lookup tables from COMMAND_SPEC.

    Returns (commands, subcommands, third_level):
      commands:    {name_or_alias: help}            top-level completion meta
      subcommands: {command: {sub: help}}            second-level completion + "?"
      third_level: {(command, sub): {option: help}}  third-level completion + "?"
    Subcommand tables are registered under the canonical name AND aliases so
    lookups work however the command was typed.
    """
    commands = {}
    subcommands = {}
    third_level = {}
    for entry in spec:
        names = [entry['name']] + entry.get('aliases', [])
        for n in names:
            commands[n] = entry['help']
        subs = entry.get('subcommands')
        if subs:
            sub_help = {s: d['help'] for s, d in subs.items()}
            for n in names:
                subcommands[n] = sub_help
                for s, d in subs.items():
                    if 'options' in d:
                        third_level[(n, s)] = d['options']
    return commands, subcommands, third_level


CLI_COMMANDS, CLI_SUBCOMMANDS, CLI_THIRD_LEVEL = _derive_command_tables(COMMAND_SPEC)

# Commands whose argument completes as a model name (vs. the path default),
# derived from the spec's 'completion' field.
CLI_MODEL_ARG_COMMANDS = {
    n for e in COMMAND_SPEC if e.get('completion') == 'model'
    for n in [e['name']] + e.get('aliases', [])
}

# Commands whose argument completes as a filesystem path.
CLI_PATH_ARG_COMMANDS = {
    n for e in COMMAND_SPEC if e.get('completion') == 'path'
    for n in [e['name']] + e.get('aliases', [])
}

# Commands whose argument completes as a saved-session name (e.g.
# /suspend, /save, /resume), derived from the spec's 'completion' field.
CLI_SESSION_ARG_COMMANDS = {
    n for e in COMMAND_SPEC if e.get('completion') == 'session'
    for n in [e['name']] + e.get('aliases', [])
}

# Free-text argument hints, shown in the completion menu for commands that
# take an argument but have no path/model completion (e.g. a session name).
CLI_ARG_HINTS = {
    n: e['arg_hint']
    for e in COMMAND_SPEC if e.get('arg_hint')
    for n in [e['name']] + e.get('aliases', [])
}

# Dispatch map: {name_or_alias: handler method name on CLI}.
CLI_DISPATCH = {
    n: e['handler']
    for e in COMMAND_SPEC if 'handler' in e
    for n in [e['name']] + e.get('aliases', [])
}


class CLI:
    """Lightweight CLI client."""
    
    def __init__(self, files: List[str] = None):
        self.files = files or []
        self.history = []
        # Eagerly assign the session id (normally generated lazily at first
        # save) so conversation-scoped state — bead trees in particular — is
        # keyed consistently from the first exchange and survives
        # suspend/resume.  save_session reuses an existing _session_id, and
        # the resume path overwrites it after construction.  The pid suffix
        # guards against two CLIs starting in the same second.
        self._session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
        self._model = None
        self._init_error = None
        self._active_task = None  # Track active streaming task for cancellation
        self._ask_task = None  # Outermost ask() task — ^C target outside streaming
        self._cancellation_requested = False
        self._diff_applicator = None  # Lazy-load diff applicator
        self._background_init_task = None  # Background MCP + plugins init task
        self._last_ctrl_c_time = 0   # Track last Ctrl+C press for double-tap exit
        self._partial_response = ""  # Accumulates streaming content for crash recovery
        self._last_keypress_time = 0  # Track last keypress for paste detection
        self._last_input_time = 0  # Track last input time for paste detection
        self._session_shell_commands = None  # Session-local shell command overrides
        self._session_yolo = False  # Session-local yolo mode (never persisted)
        self._session_timeout = None  # Session-local command timeout override
        # True while this CLI is serving a human at a prompt, which selects the
        # tighter interactive iteration budget. `ziya task` reuses this same
        # class for unattended batch work and clears it, so batch runs keep the
        # full ZIYA_MAX_TOOL_ITERATIONS budget.
        self._interactive = True
        # Remember the project root as it stood at startup so /reset can undo
        # any mid-session /root (/cd) change instead of letting it persist.
        self._initial_root = os.environ.get("ZIYA_USER_CODEBASE_DIR") or os.getcwd()
        self._initial_explicit_root = os.environ.get("ZIYA_EXPLICIT_ROOT")
        # Stack of saved (history/files) frames for /tangent — one level
        # today; a list so nesting can be enabled later without a redesign.
        self._tangent_stack = []
        # Live-attach ("/join") state — None when running a normal local
        # session.  When attached, conversation_id returns the bare GUI
        # chat id so beads/task-injection/sidebar share the conversation,
        # and each completed turn is written back into the GUI chat.
        self._attached_project_id = None
        self._attached_chat_id = None
        self._attach_baseline_sig = None
        # Pending join request from `ziya chat --join`, applied in chat().
        self._pending_join = None
        self._setup_prompt_session()
    
    @property
    def conversation_id(self) -> str:
        """Stable conversation id for this CLI session.

        Derived from the session id so per-conversation state (bead trees,
        goal attribution) follows the session through suspend/resume — the
        resume path overwrites _session_id after construction, and this
        property picks that up automatically.
        """
        if self._attached_chat_id:
            return self._attached_chat_id
        return f"cli_{self._session_id}"

    @property
    def _attached(self) -> bool:
        """True while joined to a live GUI conversation (see /join)."""
        return self._attached_chat_id is not None

    def _bead_prompt_segments(self):
        """Prompt-prefix segments for the per-conversation bead indicator.

        Mirrors the GUI chip (frontend BeadTree.tsx): hidden when no beads
        exist, amber with the parked-thread count when threads are parked,
        green with the open-thread count (active + parked, excluding
        completed/abandoned) otherwise.  Returns a list of (style, text)
        tuples to prepend to the prompt, or [] when there is nothing to
        surface.  Failure is non-fatal — the prompt must never break because
        the bead store hiccuped.
        """
        try:
            from app.storage.beads import load_bead_tree
            tree = load_bead_tree(conversation_id=self.conversation_id)
        except Exception:
            return []
        beads = getattr(tree, 'beads', None) or []
        if not beads:
            return []
        parked = sum(1 for b in beads if b.status == 'parked')
        open_count = sum(1 for b in beads if b.status in ('active', 'parked'))
        if open_count == 0:
            return []
        # Branch glyph + count wrapped in brackets so "[⑃2]" reads as one
        # self-contained status badge — N open threads — then a trailing
        # gap sets it apart from the wordmark. Amber when threads are parked
        # (waiting on attention), green when only an active thread is live.
        if parked > 0:
            return [('bold yellow', f'[⑃{parked}]'), ('', '  ')]
        return [('bold green', f'[⑃{open_count}]'), ('', '  ')]

    def _tangent_prompt_segment(self):
        """Prompt-prefix badge while a /tangent is active.

        Shown ahead of the bead indicator so it's unmissable which context
        the next message will land in — the tangent's own history frame,
        not the parent conversation's.
        """
        if not self._tangent_stack:
            return []
        topic = self._tangent_stack[-1]['topic']
        label = topic if len(topic) <= 24 else topic[:21] + '...'
        return [('bold magenta', f'[tangent:{label}]'), ('', ' ')]

    def _attach_prompt_segment(self):
        """Prompt-prefix badge while joined to a GUI conversation.

        Shown alongside the tangent/bead badges so it's unmissable that the
        next turn lands in — and syncs to — the shared GUI conversation."""
        if not self._attached:
            return []
        cid = self._attached_chat_id or ''
        return [('bold cyan', f'[⇄{cid[:6]}]'), ('', ' ')]

    @property
    def model(self):
        """Lazy-load model on first use."""
        if self._model is None:
            self._model = self._initialize_model()
        return self._model
    
    @property
    def diff_applicator(self):
        """Lazy-load diff applicator on first use."""
        if self._diff_applicator is None:
            from app.utils.cli_diff_applicator import CLIDiffApplicator
            self._diff_applicator = CLIDiffApplicator()
        return self._diff_applicator
    
    def _initialize_model(self):
        """Initialize the model with proper error handling."""
        try:
            # Check credentials first for Bedrock
            endpoint = ziya_env("ZIYA_ENDPOINT")
            if endpoint == "bedrock":
                from app.utils.aws_utils import check_aws_credentials
                _profile = (ziya_env("ZIYA_AWS_PROFILE")
                            or os.environ.get("AWS_PROFILE"))
                valid, message = check_aws_credentials(profile_name=_profile)
                if not valid:
                    self._init_error = message
                    return None
            
            from app.agents.models import ModelManager
            model_instance = ModelManager.initialize_model()
            
            if model_instance is None:
                self._init_error = "Model initialization failed. Check your credentials."
                return None
            
            return model_instance
            
        except Exception as e:  # Intentionally broad: model init can raise credential/import/config/API errors
            self._init_error = str(e)
            return None
    
    def _setup_prompt_session(self):
        """Set up prompt_toolkit session with history and completions."""
        # Custom completer for commands and file paths
        class SmartCompleter(Completer):
            # All tables derived from COMMAND_SPEC (single source of truth)
            COMMANDS = CLI_COMMANDS
            SUBCOMMANDS = CLI_SUBCOMMANDS
            THIRD_LEVEL = CLI_THIRD_LEVEL

            def __init__(self):
                self.command_completer = WordCompleter(
                    list(self.COMMANDS.keys()),
                    meta_dict=self.COMMANDS,
                    ignore_case=True, sentence=True, match_middle=True
                )
                self.subcommand_completers = {
                    cmd: WordCompleter(list(subs.keys()), meta_dict=subs, ignore_case=True)
                    for cmd, subs in self.SUBCOMMANDS.items()
                }
                self.third_level_completers = {
                    key: WordCompleter(list(subs.keys()), meta_dict=subs, ignore_case=True)
                    for key, subs in self.THIRD_LEVEL.items()
                }
                
                self.path_completer = PathCompleter(
                    only_directories=False,
                    expanduser=True
                )
                
                # Model name completer
                try:
                    from app.config.models_config import MODEL_CONFIGS
                    endpoint = ziya_env("ZIYA_ENDPOINT")
                    model_names = list(MODEL_CONFIGS.get(endpoint, {}).keys())
                    self.model_completer = WordCompleter(model_names, ignore_case=True)
                except (ImportError, KeyError, AttributeError):
                    self.model_completer = None
            
            def get_completions(self, document: Document, complete_event):
                text = document.text_before_cursor
                stripped = text.lstrip()
                
                # Show command completions when typing a command
                if stripped.startswith('/'):
                    if ' ' in stripped:
                        # We're past the command, show path completions for the argument part
                        # Check if this is a /model command
                        command = stripped.split()[0].lower()
                        if command in CLI_MODEL_ARG_COMMANDS and self.model_completer:
                            # Show model name completions
                            space_idx = stripped.index(' ')
                            model_part = stripped[space_idx + 1:]
                            
                            model_doc = Document(
                                text=model_part,
                                cursor_position=len(model_part)
                            )
                            yield from self.model_completer.get_completions(model_doc, complete_event)
                            return
                        
                        # Second/third-level completion for multipart commands
                        if command in self.subcommand_completers:
                            space_idx = stripped.index(' ')
                            rest = stripped[space_idx + 1:]
                            tokens = rest.split(' ')
                            if len(tokens) == 1:
                                # Completing the subcommand itself
                                sub_doc = Document(text=tokens[0], cursor_position=len(tokens[0]))
                                yield from self.subcommand_completers[command].get_completions(sub_doc, complete_event)
                                return
                            third = self.third_level_completers.get((command, tokens[0].lower()))
                            if third and len(tokens) == 2:
                                third_doc = Document(text=tokens[1], cursor_position=len(tokens[1]))
                                yield from third.get_completions(third_doc, complete_event)
                                return
                            # No deeper completions for these commands
                            return

                        # Path completion only for commands that explicitly
                        # accept a path argument.  Other commands (e.g.
                        # /suspend, /save, /resume) take free-text or no
                        # argument — don't expand filenames in that position.
                        if command in CLI_PATH_ARG_COMMANDS:
                            space_idx = stripped.index(' ')
                            path_part = stripped[space_idx + 1:]
                            path_doc = Document(
                                text=path_part,
                                cursor_position=len(path_part)
                            )
                            yield from self.path_completer.get_completions(
                                path_doc, complete_event)
                            return

                        # Session-name completion: list saved sessions,
                        # filtered by the text typed after the command.
                        if command in CLI_SESSION_ARG_COMMANDS:
                            space_idx = stripped.index(' ')
                            typed = stripped[space_idx + 1:]
                            sessions = list_sessions_for_completion()
                            # Before anything is typed, surface the arg hint so
                            # it's clear a (new or existing) name can be entered
                            # — not just the existing-session list.  Shown first
                            # so it's visible even when prior sessions exist.
                            if not typed:
                                hint = CLI_ARG_HINTS.get(command)
                                if hint:
                                    yield Completion(
                                        text='', start_position=0,
                                        display=f"<{hint}>",
                                        display_meta='type a name',
                                    )
                            for name, meta in sessions:
                                if name.lower().startswith(typed.lower()):
                                    yield Completion(
                                        text=name,
                                        start_position=-len(typed),
                                        display=name, display_meta=meta,
                                    )
                            return

                        # Command takes a free-text argument (or none): show an
                        # interactive hint instead of expanding filenames.
                        hint = CLI_ARG_HINTS.get(command)
                        if hint:
                            yield Completion(
                                text='', start_position=0,
                                display=f"<{hint}>", display_meta='optional',
                            )
                        return
                    else:
                        # Still typing the command - show command completions
                        command_part = stripped
                        cmd_doc = Document(text=command_part, cursor_position=len(command_part))
                        yield from self.command_completer.get_completions(cmd_doc, complete_event)
                        return
                
                # Otherwise show path completions
                yield from self.path_completer.get_completions(document, complete_event)
        
        # History file in user's home directory
        history_file = Path.home() / '.ziya' / 'history'
        history_file.parent.mkdir(parents=True, exist_ok=True)

        # Paste detection threshold: keypresses arriving faster than this are likely pasted
        PASTE_THRESHOLD_SEC = 0.05  # 50ms

        # Key bindings for ^C handling  
        bindings = KeyBindings()
        
        @bindings.add('c-c')
        def _(event):
            """Handle Ctrl+C - double tap to exit, or cancel/clear."""
            current_time = time.time()
            time_since_last = current_time - self._last_ctrl_c_time
            
            if self._active_task:
                # Cancel the active streaming task
                self._cancellation_requested = True
                if not self._active_task.done():
                    self._active_task.cancel()
                print("\n\033[33m^C - Cancelling operation...\033[0m")
                # Reset double-tap timer so this doesn't count toward exit
                self._last_ctrl_c_time = 0
                event.app.exit(result='')
            elif event.app.current_buffer.text:
                # Clear current input
                event.app.current_buffer.reset()
                # Reset double-tap timer so clearing text doesn't count toward exit
                self._last_ctrl_c_time = 0
            else:
                # Empty input - double tap within 1 second = exit
                if time_since_last < 1.0:
                    event.app.exit(result='__exit__')
                    return
                # First tap on empty prompt - show exit hint and start timer
                print("\033[90m(Press ^C again to exit, or ^D / /quit)\033[0m")
                self._last_ctrl_c_time = current_time
        
        @bindings.add(Keys.Any)
        def _track_keypress(event):
            """Track keypress timing for paste detection."""
            self._last_keypress_time = time.time()
            event.current_buffer.insert_text(event.data)

        @bindings.add(Keys.BracketedPaste)
        def _bracketed_paste(event):
            """Handle bracketed paste: insert full pasted text without submitting."""
            data = event.data
            data = data.replace("\r\n", "\n")
            data = data.replace("\r", "\n")
            event.current_buffer.insert_text(data)

        @bindings.add('enter', filter=~has_selection)
        def _(event):
            """Handle Enter - submit input, or insert newline if mid-paste."""
            buffer = event.current_buffer
            now = time.time()
            time_since_last_key = now - self._last_keypress_time
            if time_since_last_key < PASTE_THRESHOLD_SEC:
                # Rapid input detected (paste) — insert newline instead of submitting
                buffer.insert_text("\n")
                return
            buffer.validate_and_handle()
        
        self.session = PromptSession(
            history=FileHistory(str(history_file)),
            completer=SmartCompleter(),
            complete_while_typing=True,
            key_bindings=bindings,
            multiline=True,
            prompt_continuation=lambda width, line_number, is_soft_wrap: '  '
        )
    
    def _build_messages(self, question: str):
        """Build messages for the model."""
        from app.server import build_messages_for_streaming
        return build_messages_for_streaming(
            question=question,
            chat_history=self.history,
            files=self.files,
            conversation_id=self.conversation_id,
            use_langchain_format=True
        )
    
    async def ask(self, question: str, stream: bool = True) -> str:
        """Send a question and get response."""
        # Wait for background initialization (MCP + plugins) to complete before
        # processing the first message. This runs concurrently with the user
        # typing their first prompt so it's usually already done by send time.
        if self._background_init_task is not None and not self._background_init_task.done():
            print("\033[90m⟳ Finishing setup...\033[0m", file=sys.stderr, end="\r")
            try:
                await self._background_init_task
            except Exception:  # noqa: BLE001
                pass  # Errors are reported inside the task itself
            finally:
                # Clear the "finishing setup" line
                print("\033[2K", file=sys.stderr, end="\r")
        self._background_init_task = None

        if self.model is None:
            error_msg = self._init_error or "Model not available"
            print(f"\n\033[31mError: {error_msg}\033[0m", file=sys.stderr)
            self._print_auth_help()
            return ""
        
        # Reset cancellation flag
        self._cancellation_requested = False
        self._partial_response = ""  # Reset per-request accumulator

        # Track partial response for cancellation scenarios
        partial_response = ""
        
        try:
            response = await self._run_with_tools_and_validate(question, stream)
            partial_response = response
        except asyncio.CancelledError:
            # In Python 3.9+, CancelledError inherits from BaseException,
            # not Exception — it must be caught explicitly before the
            # generic Exception handler.
            print("\n\033[33mOperation cancelled.\033[0m")
            response = self._partial_response or partial_response
            self.history.append({'type': 'human', 'content': question})
            if response:
                self.history.append({'type': 'ai', 'content': response})
            return response
        except Exception as e:  # Intentionally broad: must preserve partial response on any failure
            error_str = str(e)
            
            # Preserve partial response that was already streamed to the user
            partial = self._partial_response or partial_response
            if partial:
                print(f"\n\033[33m⚠ Response was truncated due to an error ({len(partial)} chars preserved).\033[0m", file=sys.stderr)
                self.history.append({'type': 'human', 'content': question})
                self.history.append({'type': 'ai', 'content': partial})
            
            # Extract traceback info for better error reporting
            tb = traceback.extract_tb(sys.exc_info()[2])
            if tb:
                last_frame = tb[-1]
                location = f"{last_frame.filename}:{last_frame.lineno}"
                print(f"\n\033[31mError in {location}: {error_str}\033[0m", file=sys.stderr)
                if ziya_env('ZIYA_LOG_LEVEL') == 'DEBUG':
                    traceback.print_exc(file=sys.stderr)
            else:
                print(f"\033[31mError: {error_str}\033[0m", file=sys.stderr)
            
            # Check for specific error types
            if 'ThrottlingException' in error_str or 'Too many tokens' in error_str:
                print("\033[33mRate limit hit. Please wait a moment before trying again.\033[0m", file=sys.stderr)
            elif 'ExpiredToken' in error_str:
                print("\033[33mCredentials expired. Please refresh: aws sso login --profile <profile>\033[0m", file=sys.stderr)
            
            # Return whatever partial content was accumulated
            return partial if partial else ""
        
        # Update history
        self.history.append({'type': 'human', 'content': question})
        if response:  # Empty string is falsy, so this works correctly
            # Diff processing now happens in _run_with_tools_and_validate
            pass
            
            self.history.append({'type': 'ai', 'content': response})
        
        return response
    
    async def _run_with_tools_and_validate(self, question: str, stream: bool = True) -> str:
        """Run model with tool execution loop."""
        """Run model with tools and validation-feedback loop for diffs."""
        from app.utils.diff_validation_hook import DiffValidationHook
        from langchain_core.messages import HumanMessage
        from langchain_core.messages import AIMessage
        from app.agents.agent import file_state_manager
        
        # Use consistent conversation ID for this CLI session
        conversation_id = self.conversation_id

        validation_hook = DiffValidationHook(
            file_state_manager=file_state_manager,
            conversation_id=conversation_id,
            current_context=self.files,
            auto_regenerate=True
        )
        
        # Build initial messages
        messages = self._build_messages(question)
        
        # Validation loop - give model chance to fix bad diffs
        max_attempts = 3
        for attempt in range(max_attempts):
            # Reset cancellation flag for each attempt
            self._cancellation_requested = False
            print(f"\033[90m[trace] model call attempt={attempt}\033[0m", file=sys.stderr)
            
            response = await self._run_with_tools_from_messages(messages, stream)
            # Compute has_diff BEFORE the f-string: a backslash inside an
            # f-string expression part is a SyntaxError on Python 3.11 (allowed
            # only in 3.12+), and the toolbox venv is 3.11.
            _diff_re = re.search(r'^`{3,}diff\s*$', response, re.MULTILINE)
            print(f"\033[90m[trace] model returned, len={len(response)}, has_diff={bool(_diff_re)}\033[0m", file=sys.stderr)
            
            # If no diffs, we're done
            if '```diff' not in response:
                # Check if response looks incomplete (truncated mid-thought),
                # OR narrates an intended action without taking it. Pure,
                # unit-tested discriminator -- see _response_looks_incomplete
                # docstring for rationale. The inner StreamingToolExecutor has
                # its own bounded intent-continuation logic, but this OUTER
                # loop has no visibility into whether that budget was
                # exhausted, judged the turn complete, or never engaged.
                looks_incomplete, _has_unexecuted_intent = await _adjudicate_continuation(response)
                if looks_incomplete:
                    if _has_unexecuted_intent:
                        print("\033[90m[trace] response narrates unexecuted intent, auto-continuing\033[0m", file=sys.stderr)
                    else:
                        print("\033[90m[trace] response looks incomplete, auto-continuing\033[0m", file=sys.stderr)
                    messages.append(AIMessage(content=response))
                    messages.append(HumanMessage(content="[System: Your response appears incomplete. Please continue where you left off.]"))
                    continuation = await self._run_with_tools_from_messages(messages, stream)
                    # Merge and re-evaluate: the continuation may contain diffs
                    # that must flow through validation + process_response.
                    # Without re-looping, a truncated-first + diff-bearing
                    # continuation silently bypasses the applicator.
                    response = response + continuation
                    if '```diff' not in response:
                        validation_hook = None
                        return response
                    # fall through to diff validation below with combined response
                else:
                    # Clean up validation hook
                    validation_hook = None
                    return response
            
            # Validate diffs using FULL apply pipeline (dry-run)
            print(f"\033[90m[trace] starting validate_and_enhance\033[0m", file=sys.stderr)
            try:
                validation_feedback = await asyncio.wait_for(
                    asyncio.to_thread(
                        validation_hook.validate_and_enhance,
                        content=response,
                        model_messages=messages,
                    ),
                    timeout=30,
                )
            except (asyncio.TimeoutError, Exception) as e:
                print(f"\033[33m⚠ Diff validation timed out or failed ({e}), skipping validation\033[0m", file=sys.stderr)
                validation_feedback = None
            print(f"\033[90m[trace] validate_and_enhance done, has_feedback={bool(validation_feedback)}\033[0m", file=sys.stderr)
            
            # If validation passed, process diffs interactively
            if not validation_feedback:
                if attempt > 0:
                    print("\n\033[32m✓ Diff validation passed\033[0m")
                
                # Sync auto-added context files back to CLI's file list
                for f in validation_hook.added_files:
                    if f not in self.files:
                        self.files.append(f)

                # Process diffs in a loop to handle continuations
                full_response = response
                prior_turns = []  # accumulate completed turns across continuation iterations
                while True:
                    # Show diffs to user for interactive application
                    try:
                        print("\033[90m[trace] entering process_response\033[0m", file=sys.stderr)
                        completed_normally = self.diff_applicator.process_response(full_response)
                        print(f"\033[90m[trace] process_response done, completed_normally={completed_normally}\033[0m", file=sys.stderr)
                    except (OSError, ValueError, RuntimeError, KeyError, IndexError) as e:
                        import traceback
                        print(f"\n\033[33mNote: Could not process diffs: {e}\033[0m", file=sys.stderr)
                        if ziya_env('ZIYA_LOG_LEVEL') == 'DEBUG':
                            traceback.print_exc(file=sys.stderr)
                        break
                    
                    if completed_normally:
                        # If any diffs failed at apply time, tell the model what
                        # failed so it can correct — don't silently continue.
                        if self.diff_applicator.failed_count > 0:
                            failed_results = [
                                f"  ✗ {fp}: {msg}"
                                for fp, status, msg in self.diff_applicator.diff_results
                                if status == "failed"
                            ]
                            failure_feedback = (
                                f"The following diffs failed to apply:\n"
                                + "\n".join(failed_results)
                                + "\n\nPlease re-read the current file content and regenerate "
                                "only the failed diffs with corrected line numbers and context."
                            )
                            messages.append(AIMessage(content=full_response))
                            messages.append(HumanMessage(content=failure_feedback))
                            # Inject current file content for each failed file
                            for fp, status, _ in self.diff_applicator.diff_results:
                                if status == "failed":
                                    if not fp:
                                        continue
                                    file_content = validation_hook.read_file_for_context(fp)
                                    if file_content:
                                        lang = validation_hook._detect_language(fp)
                                        messages.append(HumanMessage(content=(
                                            f"[SYSTEM: Current content of {fp}]\n\n"
                                            f"```{lang}\n{file_content}\n```"
                                        )))
                            continue_response = await self._run_with_tools_from_messages(messages, stream)
                            if '```diff' in continue_response:
                                prior_turns.append(full_response)
                                full_response = continue_response
                                continue
                            return "\n\n".join(prior_turns + [full_response, continue_response])

                        # After processing diffs, check if model wants to continue
                        # Add the assistant's response to messages so the model
                        # retains context of what it already said
                        messages.append(AIMessage(content=full_response))
                        summary = self._build_diff_summary()

                        # Determine the actual outcome to frame the continuation correctly
                        applicator = self.diff_applicator
                        failed = [r for r in getattr(applicator, 'diff_results', []) if r[1] == "failed"]
                        skipped = [r for r in getattr(applicator, 'diff_results', []) if r[1] == "skipped"]
                        applied = [r for r in getattr(applicator, 'diff_results', []) if r[1] == "applied"]

                        if failed and not applied:
                            continuation_message = (
                                f"{summary}\n\n"
                                "The diffs above failed to apply. Do NOT assume they were applied. "
                                "Please re-read the current file content and regenerate corrected diffs."
                            )
                        elif failed and applied:
                            continuation_message = (
                                f"{summary}\n\n"
                                "Some diffs applied successfully but others failed. "
                                "Do NOT assume the failed diffs were applied. "
                                "Please re-read the current file content and regenerate only the failed diffs."
                            )
                        elif skipped and not applied and not failed:
                            continuation_message = (
                                f"{summary}\n\n"
                                "The user skipped all diffs. No changes were made to the files."
                            )
                        else:
                            continuation_message = (
                                f"{summary}\n\n"
                                "If there are more changes needed or additional steps to complete, "
                                "please continue. Otherwise, confirm that all necessary changes have been provided.\n\n"
                                "Important: if your previous response described an action you intended to take "
                                "(such as running tests or verifying results), please take that action now rather "
                                "than just describing it again."
                            )
                        # Continue conversation with the model
                        print("\033[90m[trace] sending continuation to model\033[0m", file=sys.stderr)
                        continue_response = await self._continue_conversation(continuation_message, messages)
                        print(f"\033[90m[trace] continuation returned, len={len(continue_response)}\033[0m", file=sys.stderr)
                        
                        # Persist the diff results and continuation to history so the
                        # model knows what was applied on subsequent turns.
                        self.history.append({'type': 'human', 'content': continuation_message})
                        self.history.append({'type': 'ai', 'content': continue_response})

                        # If continuation contains more diffs, process those too
                        if '```diff' in continue_response:
                            prior_turns.append(full_response)
                            full_response = continue_response
                            continue
                        
                        # Return combined response
                        return "\n\n".join(prior_turns + [full_response, continue_response])
                    break
                
                # Clean up validation hook
                validation_hook = None
                return response
            
            # Validation failed - regenerate if we have attempts left
            if attempt < max_attempts - 1:
                # Explain what's happening and why
                print(f"\n\033[33m⚠ Diff couldn't be applied cleanly (hunks don't match current file content)\033[0m")
                failed_files = [d['file_path'] for d in validation_hook.failed_diff_details]
                passed_files = validation_hook.successful_diffs
                if passed_files:
                    print(f"\033[32m  ✓ Passed: {', '.join(passed_files)}\033[0m")
                print(f"\033[31m  ✗ Failed: {', '.join(failed_files)}\033[0m")
                print(f"\033[90mRegenerating with file context... (attempt {attempt + 2}/{max_attempts})\033[0m\n")
                
                # After second failure, suggest breaking up the diff
                if attempt == 1:  # Second attempt - strong guidance to break it down
                    validation_feedback += (
                        "\n\n⚠️ CRITICAL: Diff validation failed again.\n\n"
                        "REQUIRED STRATEGY for next attempt:\n"
                        "1. Break this change into a SERIES of smaller, independent diffs\n"
                        "2. Each diff should:\n"
                        "   - Target 10-20 lines max\n"
                        "   - Include UNIQUE context (function names, class declarations, distinctive comments)\n"
                        "   - Be independently applicable (no dependencies between diffs)\n"
                        "3. Present ONE diff at a time, wait for it to be applied, then continue\n"
                        "4. The file content is now in your context - verify line numbers and context match exactly\n"
                    )
                elif attempt == 2:  # Third attempt - last chance with tool verification
                    validation_feedback += (
                        "\n\n🛑 FINAL VALIDATION ATTEMPT\n\n"
                        "Multiple diffs have failed. Before generating another diff:\n\n"
                        "1. **VERIFY FILE STATE** - Use tools to check:\n"
                        "   - Use grep/search tools to find the exact lines you want to modify\n"
                        "   - Verify the function/class structure matches your understanding\n"
                        "   - Check line numbers and surrounding context\n"
                        "2. **IF** verification shows discrepancies, explain what you found\n"
                        "3. **ONLY THEN** generate ONE minimal diff with:\n"
                        "   - Complete function/class signature as context\n"
                        "   - Exact indentation and whitespace from verified content\n"
                        "   - Unique identifiers (function names, variable names) as anchors\n\n"
                        "DO NOT guess or rely solely on context - actively verify with tools first.\n"
                    )
                
                # Append feedback and rebuild messages for retry
                # DON'T rebuild from scratch - just append feedback to existing messages
                messages.append(HumanMessage(content=validation_feedback))
            else:
                # Final attempt failed
                print(f"\n\033[31m✗ Diff validation failed after {max_attempts} attempts\033[0m")
                # Hard-suppress: validator already concluded these diffs won't
                # apply.  Don't surface the apply prompt.  Log a one-line
                # acknowledgement and return without calling process_response.
                _diffs = self.diff_applicator.extract_diffs(response)
                if _diffs:
                    _paths = list(dict.fromkeys(d.file_path or "(no path)" for d in _diffs))
                    _shown = ", ".join(_paths[:3])
                    if len(_paths) > 3:
                        _shown += " (+" + str(len(_paths) - 3) + " more)"
                    print("\033[2;33m⚠ Suppressed " + str(len(_diffs)) + " diff(s) that failed validation: " + _shown + "\033[0m", file=sys.stderr)
                    print("\033[2mDiffs didn't validate against current file content. Nothing to apply.\033[0m", file=sys.stderr)
                return response
        
        # Clean up validation hook after all attempts
        # Sync any auto-added files even on failure path
        if validation_hook and validation_hook.added_files:
            for f in validation_hook.added_files:
                if f not in self.files:
                    self.files.append(f)
        validation_hook = None
        
        return response
    
    def _build_diff_summary(self) -> str:
        """Build a summary message of diff processing results."""
        applicator = self.diff_applicator
        # CLIDiffApplicator tracks counts internally during process_response
        # Check if it has these attributes, otherwise return generic message
        if hasattr(applicator, 'diff_results') and applicator.diff_results:
            lines = ["Diff application results:"]
            for file_path, status, message in applicator.diff_results:
                if status == "applied":
                    lines.append(f"  ✓ {file_path}: {message}")
                elif status == "partial":
                    lines.append(f"  ⚠ {file_path}: {message}")
                elif status == "failed":
                    lines.append(f"  ✗ {file_path}: FAILED - {message}")
                elif status == "skipped":
                    lines.append(f"  ⊘ {file_path}: skipped by user")
            
            # Add actionable context for failures
            failed = [(fp, msg) for fp, st, msg in applicator.diff_results if st == "failed"]
            if failed:
                lines.append("")
                lines.append("Failed diffs need to be regenerated. For each failure:")
                for fp, msg in failed:
                    lines.append(f"  - {fp}: {msg}")
                lines.append("Please re-read the current file content and regenerate only the failed diffs.")
            
            return "\n".join(lines)
        elif hasattr(applicator, 'applied_count'):
            total = getattr(applicator, 'applied_count', 0) + getattr(applicator, 'partial_count', 0) + getattr(applicator, 'skipped_count', 0) + getattr(applicator, 'failed_count', 0)
            if total > 0:
                parts = []
                if applicator.applied_count > 0:
                    parts.append(f"{applicator.applied_count} applied")
                if getattr(applicator, 'partial_count', 0) > 0:
                    parts.append(f"{applicator.partial_count} partial")
                if applicator.skipped_count > 0:
                    parts.append(f"{applicator.skipped_count} skipped")
                if applicator.failed_count > 0:
                    parts.append(f"{applicator.failed_count} failed")
                return f"Diff processing complete: {', '.join(parts)}."
        return "Diff processing complete."
    
    async def _continue_conversation(self, message: str, messages: list) -> str:
        """Send a continuation message and get model's response."""
        from langchain_core.messages import HumanMessage
        
        # Add continuation message to history
        messages.append(HumanMessage(content=message))
        
        # Get model response using existing method
        return await self._run_with_tools_from_messages(messages, stream=True)
    
    async def _run_with_tools_from_messages(self, messages, stream: bool = True) -> str:
        """Run model with tools from existing message list (for retries)."""
        from app.streaming_tool_executor import StreamingToolExecutor
        from app.mcp.manager import get_mcp_manager
        from app.agents.models import ModelManager
        from app.mcp.enhanced_tools import create_secure_mcp_tools
        
        mcp_manager = get_mcp_manager()
        if not mcp_manager or not mcp_manager.is_initialized:
            return await self._simple_invoke(messages, stream)
        
        tools = create_secure_mcp_tools()
        if not tools:
            return await self._simple_invoke(messages, stream)
        
        state = ModelManager.get_state()
        executor = StreamingToolExecutor(
            profile_name=state.get('aws_profile'),
            region=state.get('aws_region', 'us-west-2')
        )
        
        openai_messages = self._convert_to_openai_format(messages)
        cancel_event = asyncio.Event()
        self._cancel_event = cancel_event
        
        async def stream_task():
            # Interactive prompts use the tighter iteration budget; `ziya task`
            # reaches this same method with _interactive cleared and keeps the
            # full batch budget. Task Cards and /goal never arrive here at all
            # (they run via app/agents/task_executor.py).
            async for chunk in executor.stream_with_tools(openai_messages, tools, conversation_id=self.conversation_id, cancel_event=cancel_event, interactive=self._interactive):
                if self._cancellation_requested:
                    raise asyncio.CancelledError("User cancelled operation")
                yield chunk
        
        task = asyncio.create_task(self._stream_handler(stream_task(), stream))
        self._active_task = task
        
        try:
            full_response = await task
            return full_response
        except asyncio.CancelledError:
            task.cancel()
            raise  # Re-raise to be handled by ask()
        finally:
            if not task.done():
                task.cancel()
            self._cancel_event = None
            self._active_task = None
    
    def _parse_markdown_state(self, content: str) -> dict:
        """Parse markdown to detect unclosed code blocks."""
        lines = content.split('\n')
        code_block_stack = []
        
        for line in lines:
            trimmed = line.lstrip()
            fence_match = re.match(r'^(`{3,}|~{3,})(\w*)', trimmed)
            
            if fence_match:
                fence_chars = fence_match.group(1)
                language = fence_match.group(2) or ''
                fence_type = fence_chars[0]
                
                # Check if closing
                if (code_block_stack and 
                    code_block_stack[-1]['type'] == fence_type and
                    len(fence_chars) >= 3):
                    code_block_stack.pop()
                elif len(fence_chars) >= 3:
                    code_block_stack.append({'type': fence_type, 'language': language})
        
        return {
            'in_code_block': len(code_block_stack) > 0,
            'fence_type': code_block_stack[-1]['type'] if code_block_stack else None,
            'fence_language': code_block_stack[-1]['language'] if code_block_stack else None
        }
    
    def _handle_rewind_marker(self, content: str) -> tuple[str, str]:
        """
        Handle rewind markers in streamed content.
        Truncates content to everything before the last marker.
        Returns (rewound_content, marker_stripped_chunk).
        """
        if '<!-- REWIND_MARKER:' not in content:
            return content, content
        
        # Find the last rewind marker in the content
        lines = content.split('\n')
        marker_line_idx = None
        for i in range(len(lines) - 1, -1, -1):
            if '<!-- REWIND_MARKER:' in lines[i]:
                marker_line_idx = i
                break
        
        if marker_line_idx is not None:
            before_rewind = '\n'.join(lines[:marker_line_idx])
            
            # Check if we're in a code block and close it properly
            markdown_state = self._parse_markdown_state(before_rewind)
            if markdown_state['in_code_block']:
                fence_to_use = markdown_state['fence_type'] or '`'
                before_rewind += '\n' + fence_to_use * 3 + '\n'
            
            # Strip all markers from content for display
            stripped = re.sub(r'<!-- REWIND_MARKER: [^\s]+(?: -->|(?:\|FENCE:[`~]\w*)? -->)(?:</span>)?', '', content)
            return before_rewind, stripped
        
        return content, content
    
    async def _stream_handler(self, stream_generator, stream: bool) -> str:
        """Handle streaming with cancellation support."""
        full_response = ""
        md_renderer = None
        if stream:
            from app.utils.terminal_markdown import StreamingMarkdownRenderer
            md_renderer = StreamingMarkdownRenderer()
        
        try:
            _debug_chunks = ziya_env('ZIYA_DEBUG_CHUNKS')
            async for chunk in stream_generator:
                chunk_type = chunk.get('type')

                if _debug_chunks:
                    # One-line trace per chunk — lets us see in real time what
                    # the executor is yielding during apparent "silent" gaps,
                    # including chunk types this handler intentionally drops.
                    _summary = ''
                    if chunk_type == 'text':
                        _summary = repr(chunk.get('content', '')[:60])
                    elif chunk_type in ('tool_start', 'tool_display', 'tool_execution', 'tool_use', 'tool_result_for_model'):
                        _summary = f"tool={chunk.get('tool_name')!r} id={chunk.get('tool_id') or chunk.get('tool_use_id')!r}"
                    print(f"\033[90m[chunk] {chunk_type} {_summary}\033[0m", file=sys.stderr, flush=True)

                if chunk_type == 'text':
                    content = chunk.get('content', '')
                
                    # Add original content (with markers) to full_response for proper rewind processing
                    full_response += content
                
                    # Update instance-level accumulator so ask() can recover
                    # partial content if the stream is cancelled mid-flight.
                    self._partial_response = full_response

                    # For display: filter out rewind markers and continuation messages
                    display_content = re.sub(r'<!-- REWIND_MARKER: [^\s]+(?: -->|(?:\|FENCE:[`~]\w*)? -->)', '', content)
                    display_content = display_content.replace('', '')
                
                    # Render through markdown renderer or print raw
                    if display_content:  # Only skip completely empty strings
                        if md_renderer:
                            md_renderer.feed(display_content)
                        elif stream:
                            print(display_content, end='', flush=True)
                
                    # Handle rewind markers
                    # This processes markers in the accumulated full_response and rewinds if needed
                    rewound, _ = self._handle_rewind_marker(full_response)
                    if rewound != full_response:
                        full_response = rewound
                        # Reset markdown renderer since we truncated content
                        if md_renderer:
                            from app.utils.terminal_markdown import StreamingMarkdownRenderer
                            md_renderer = StreamingMarkdownRenderer()
            
                elif chunk_type == 'tool_execution':
                    tool_name = chunk.get('tool_name', 'unknown')
                    if chunk.get('is_internal'):
                        continue
                    if md_renderer:
                        md_renderer.flush()
                    print(f"\n\033[90m⚡ {tool_name}\033[0m", flush=True)
            
                elif chunk_type == 'tool_start':
                    tool_name = chunk.get('tool_name', 'unknown')
                    display_header = chunk.get('display_header', tool_name)
                    if chunk.get('is_internal'):
                        # Flush pending text so the hidden tool boundary doesn't
                        # merge the pre-tool and post-tool text into one line.
                        if md_renderer:
                            md_renderer.flush()
                        print(f"\033[2;90m  ↩ {tool_name}\033[0m", flush=True)
                        continue
                    if md_renderer:
                        md_renderer.flush()
                    print(f"\n\033[36m⚙ Executing {display_header}...\033[0m", flush=True)
            
                elif chunk_type == 'tool_display':
                    try:
                        if md_renderer:
                            md_renderer.flush()
                        if chunk.get('is_internal'):
                            continue
                        # Show tool result with formatting
                        tool_name = chunk.get('tool_name', 'unknown')
                        result = chunk.get('result', '') or ''
                        args = chunk.get('args') or {}
                        if not isinstance(args, dict):
                            args = {}
                    
                        # Build header with any available metadata
                        display_header = chunk.get('display_header')
                        if not display_header:
                            # Derive header from args for file tools
                            normalized = tool_name.split('_', 1)[-1] if 'mcp_' in tool_name else tool_name
                            if normalized in ('file_read', 'file_write', 'file_list'):
                                path = args.get('path', '')
                                label = normalized.replace('_', ' ')
                                display_header = f"{label}: {path}" if path else label
                            else:
                                display_header = tool_name
                        header_parts = [display_header]
                        metadata = []
                    
                        # Extract common metadata patterns from args
                        if 'thoughtNumber' in args and 'totalThoughts' in args:
                            metadata.append(f"{args['thoughtNumber']}/{args['totalThoughts']}")
                    
                        if args.get('isRevision') and 'revisesThought' in args:
                            metadata.append(f"revises #{args['revisesThought']}")
                        elif 'branchId' in args:
                            metadata.append(f"branch: {args['branchId']}")
                    
                        if 'branchFromThought' in args:
                            metadata.append(f"from #{args['branchFromThought']}")
                    
                        # Add command if available (for shell tools)
                        if 'command' in args and isinstance(result, str) and not result.startswith('$ '):
                            metadata.append(f"$ {args['command']}")
                    
                        # For search tools, show the search query
                        if 'WorkspaceSearch' in tool_name or 'CodeSearch' in tool_name:
                            search_args = args.get('tool_input', args)
                            if isinstance(search_args, str):
                                try:
                                    search_args = json.loads(search_args)
                                except (json.JSONDecodeError, TypeError, ValueError):
                                    search_args = {}
                            if isinstance(search_args, dict):
                                query = search_args.get('searchQuery') or search_args.get('query', '')
                                if query:
                                    metadata.append(f'query: "{query}"')
                    
                        # Build final header
                        if metadata:
                            header = f"{header_parts[0]} ({', '.join(metadata)})"
                        else:
                            header = header_parts[0]
                    
                        # Print header
                        print(f"\n\033[36m┌─ {header}\033[0m", flush=True)
                    
                        # Print the thought content if it's in args (for sequential thinking)
                        if 'thought' in args:
                            thought_text = args['thought']
                            if thought_text and isinstance(thought_text, str):
                                from app.utils.terminal_markdown import render_prefixed_markdown
                                render_prefixed_markdown(thought_text)
                    
                        # Print result (tool output/response)
                        if result and isinstance(result, str):
                            is_json_metadata = result.strip().startswith('{') and 'thoughtNumber' in result
                    
                            if not is_json_metadata and result != args.get('thought', ''):
                                from app.utils.terminal_markdown import render_prefixed_markdown
                                # Shell tools format their result as "$ <command>\n<output>".
                                # Rendering the whole thing through the markdown renderer
                                # collapses the leading newline and flattens the command
                                # into the output. Split the command line off so it gets
                                # distinct styling (bold green $, bold white command) and
                                # a blank prefixed separator line before the output body.
                                stripped_result = result.rstrip('\n')
                                if stripped_result.startswith('$ '):
                                    nl = stripped_result.find('\n')
                                    if nl == -1:
                                        cmd_line = stripped_result[2:]
                                        body = ''
                                    else:
                                        cmd_line = stripped_result[2:nl]
                                        body = stripped_result[nl + 1:]
                                    # Prefix bar in grey (matches render_prefixed_markdown),
                                    # dollar sign in bold green, command in bold white.
                                    print(
                                        f"\033[90m│\033[0m "
                                        f"\033[1;32m$\033[0m \033[1;37m{cmd_line}\033[0m",
                                        flush=True,
                                    )
                                    print("\033[90m│\033[0m", flush=True)
                                    if body:
                                        render_prefixed_markdown(body)
                                else:
                                    render_prefixed_markdown(stripped_result)
                    
                        print(f"\033[36m└─\033[0m", flush=True)
                    except Exception as e:  # Intentionally broad: display errors must not crash the stream
                        # Log and continue — don't crash the stream for a display issue
                        logger.warning(f"Error rendering tool_display chunk: {e}")
                        tool_name = chunk.get('tool_name', 'unknown') if isinstance(chunk, dict) else 'unknown'
                        print(f"\n\033[33m⚠ Tool result from {tool_name} (display error)\033[0m", flush=True)
            
                elif chunk_type == 'stream_end':
                    break
            
                elif chunk_type == 'rewind':
                    # Targeted rewind from streaming_tool_executor
                    to_marker = chunk.get('to_marker')
                    if to_marker:
                        marker_str = f'<!-- REWIND_MARKER: {to_marker}'
                        marker_pos = full_response.find(marker_str)
                        if marker_pos >= 0:
                            full_response = full_response[:marker_pos]
                            if md_renderer:
                                from app.utils.terminal_markdown import StreamingMarkdownRenderer
                                md_renderer = StreamingMarkdownRenderer()
            
                elif chunk_type == 'throttling_error':
                    # Handle throttling gracefully
                    wait_time = chunk.get('suggested_wait', 60)
                    print(f"\n\033[33m⚠ Rate limit hit. Waiting {wait_time}s...\033[0m\n", file=sys.stderr)
                    # Don't break - let the executor handle the retry
            
                elif chunk_type == 'error':
                    error_msg = chunk.get('content', 'Unknown error')
                    print(f"\n\033[31mError: {error_msg}\033[0m", file=sys.stderr)
                    break
        except asyncio.CancelledError:
            # Streaming was cancelled - return whatever we accumulated so far
            print(f"\n\033[90m(Partial response collected: {len(full_response)} chars)\033[0m")
            if md_renderer:
                md_renderer.flush()
            # Preserve partial content and re-raise so ask() handles it uniformly
            self._partial_response = full_response
            raise
        
        if stream:
            if md_renderer:
                md_renderer.flush()
            else:
                print()  # Final newline after streaming
        
        return full_response
    
    def _convert_to_openai_format(self, messages):
        """Convert LangChain messages to OpenAI format."""
        openai_msgs = []
        for msg in messages:
            if isinstance(msg, dict):
                openai_msgs.append(msg)
            elif hasattr(msg, 'type'):
                if msg.type == 'system':
                    openai_msgs.append({"role": "system", "content": msg.content})
                elif msg.type == 'human':
                    openai_msgs.append({"role": "user", "content": msg.content})
                elif msg.type == 'ai':
                    openai_msgs.append({"role": "assistant", "content": msg.content})
        return openai_msgs
    
    async def _simple_invoke(self, messages, stream: bool) -> str:
        """Simple invocation without tools, with cancellation support."""
        if stream:
            response = ""
            try:
                async for chunk in self.model.astream(messages):
                    if self._cancellation_requested:
                        print("\n\033[33m^C - Cancelled.\033[0m")
                        break
                    if isinstance(chunk, dict):
                        content = chunk.get('content', '')
                    else:
                        content = getattr(chunk, 'content', '')
                    
                    if isinstance(content, str):
                        print(content, end='', flush=True)
                        response += content
            except asyncio.CancelledError:
                # Preserve partial content and re-raise for consistent handling
                self._partial_response = response
                raise
            print()
            return response
        else:
            # Wrap the blocking ainvoke in a task so Ctrl+C can cancel it
            task = asyncio.create_task(self.model.ainvoke(messages))
            try:
                while not task.done():
                    if self._cancellation_requested:
                        task.cancel()
                        print("\n\033[33m^C - Cancelled.\033[0m")
                        raise asyncio.CancelledError("User cancelled operation")
                    # Poll every 200ms to stay responsive to cancellation
                    await asyncio.sleep(0.2)
                result = task.result()
                if isinstance(result, dict):
                    content = result.get('content', '')
                else:
                    content = getattr(result, 'content', str(result))
                print(content)
                return content
            except asyncio.CancelledError:
                task.cancel()
                # Preserve empty partial and re-raise for consistent handling
                self._partial_response = ""
                raise

    def _print_auth_help(self):
        """Print authentication help based on endpoint."""
        endpoint = ziya_env("ZIYA_ENDPOINT")
        
        print(file=sys.stderr)
        if endpoint == "bedrock":
            print("\033[33mTo fix AWS credentials:\033[0m", file=sys.stderr)
            print("  • Run: aws configure", file=sys.stderr)
            print("  • Or set AWS_PROFILE: export AWS_PROFILE=your-profile", file=sys.stderr)
            print("  • Or refresh SSO: aws sso login --profile your-profile", file=sys.stderr)
        elif endpoint == "google":
            print("\033[33mTo fix Google credentials:\033[0m", file=sys.stderr)
            print("  • Set GOOGLE_API_KEY environment variable", file=sys.stderr)
        elif endpoint == "openai":
            print("\033[33mTo fix OpenAI credentials:\033[0m", file=sys.stderr)
            print("  • Set OPENAI_API_KEY: export OPENAI_API_KEY=sk-...", file=sys.stderr)
            print("  • Or set OPENAI_BASE_URL for a compatible local server", file=sys.stderr)
        elif endpoint == "anthropic":
            print("\033[33mTo fix Anthropic credentials:\033[0m", file=sys.stderr)
            print("  • Set ANTHROPIC_API_KEY: export ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
        print(file=sys.stderr)
    
    async def chat(self):
        """Interactive chat loop."""
        if self.model is None:
            print(f"\n\033[31mError: {self._init_error or 'Model not available'}\033[0m", file=sys.stderr)
            self._print_auth_help()
            return

        # Apply a launch-time --join before entering the loop.  Storage-only,
        # so it's safe to run before background MCP/plugin init completes.
        pending = getattr(self, '_pending_join', None)
        if pending:
            self._pending_join = None
            join_arg = pending if isinstance(pending, str) else ''
            await self.cmd_join(join_arg)
        
        while True:
            try:
                # Use prompt_toolkit for rich input
                # OSC 133;D (prior command finished), 133;A (new prompt
                # starting), then 133;B (prompt finished / awaiting input)
                # tells the terminal we are genuinely idle. Without the
                # trailing 133;B, terminals that track the FinalTerm/iTerm2
                # shell-integration state machine never see the transition
                # into "awaiting input" and keep showing the busy/spinner
                # indicator on the tab even though nothing is running.
                sys.stdout.write("\033]133;D\007\033]133;A\007\033]133;B\007"); sys.stdout.flush()
                # Pull any turns added to the joined GUI conversation since
                # our last write, before showing the prompt (no-op if local).
                self._pull_external_updates()
                try:
                    prompt_segments = (
                        self._tangent_prompt_segment()
                        + self._attach_prompt_segment()
                        + self._bead_prompt_segments()
                        + [('bold magenta', 'ℤ'), ('cyan', 'iya'), ('', ' '), ('bold cyan', '› ')]
                    )
                    user_input = await asyncio.to_thread(
                        self.session.prompt,
                        FormattedText(prompt_segments),
                        # Note: no refresh_interval here. prompt_segments is a
                        # static list computed once above, not a callable, so
                        # periodic redraws would repaint identical content on
                        # a timer. That constant stdout activity is enough to
                        # keep re-triggering iTerm2's inactive-tab "activity"
                        # spinner even while genuinely idle at the prompt.
                    )
                    user_input = user_input.strip()
                except KeyboardInterrupt:
                    # Ctrl+C on empty prompt - continue
                    print()
                    continue
                
                # Check for double Ctrl+C exit signal
                if user_input == '__exit__':
                    print("\033[90mGoodbye\033[0m")
                    break
                
                if not user_input:
                    continue
                
                # Commands
                if user_input.startswith('/') and not user_input.startswith('//'):
                    if not await self._handle_command(user_input):
                        break
                    continue
                
                # Regular message
                print()
                print("\033[90m⏳ Sending to model...\033[0m", file=sys.stderr)
                sys.stdout.write("\033]133;C\007"); sys.stdout.flush()
                # Run ask() as a task so the SIGINT handler can cancel the
                # whole operation — including non-streaming phases like diff
                # validation — and return control to the prompt.
                self._ask_task = asyncio.create_task(self.ask(user_input))
                try:
                    await self._ask_task
                except asyncio.CancelledError:
                    # Operation was cancelled, continue the loop
                    pass
                finally:
                    self._ask_task = None
                _autocheckpoint(self)
                # Write this turn back into the joined GUI conversation so the
                # GUI (and any other attached CLI) sees it (no-op if local).
                self._sync_after_turn()
                sys.stdout.write("\033]133;D\007"); sys.stdout.flush()
                print("\033[90m[trace] ask() returned, looping to prompt\033[0m", file=sys.stderr)
                print()
                
            except KeyboardInterrupt:
                # Ctrl+C during input - just continue
                print()
                continue
            except EOFError:
                break
        
        print("\033[90mGoodbye\033[0m")
    
    def _print_inline_help(self, command: str, path: list) -> bool:
        """Print next-level option descriptions for a "?" query.

        path is the tokens between the command and the trailing "?",
        e.g. "/shell git ?" -> command='/shell', path=['git'].
        Returns True if a help table was found and printed.
        """
        if not path:
            table = CLI_SUBCOMMANDS.get(command)
            label = command
        elif len(path) == 1:
            table = CLI_THIRD_LEVEL.get((command, path[0].lower()))
            label = f"{command} {path[0]}"
        else:
            table = None
            label = command
        if not table:
            return False
        width = max(len(name) for name in table)
        print(f"\033[1m{label}\033[0m options:")
        for name, desc in table.items():
            print(f"  \033[36m{name:<{width}}\033[0m  {desc}")
        return True

    def add_files(self, paths: List[str]) -> None:
        """Resolve file/dir/glob paths and add them to the context."""
        root = os.environ.get("ZIYA_USER_CODEBASE_DIR") or os.getcwd()
        resolved = resolve_files(paths, root)
        if not resolved:
            print(f"\033[33mNo files matched: {', '.join(paths)}\033[0m")
            return
        added = [f for f in resolved if f not in self.files]
        for f in added:
            self.files.append(f)
            print(f"\033[32m+ {f}\033[0m")
        if not added:
            print("\033[90mAlready in context\033[0m")
        print(f"\033[90m{len(self.files)} file(s) in context\033[0m")

    def remove_files(self, paths: List[str]) -> None:
        """Remove paths from the context (exact or resolved match)."""
        root = os.environ.get("ZIYA_USER_CODEBASE_DIR") or os.getcwd()
        targets = set(paths) | set(resolve_files(paths, root))
        removed = [f for f in self.files if f in targets]
        if not removed:
            print(f"\033[33mNot in context: {', '.join(paths)}\033[0m")
            return
        self.files = [f for f in self.files if f not in targets]
        for f in removed:
            print(f"\033[31m- {f}\033[0m")
        print(f"\033[90m{len(self.files)} file(s) in context\033[0m")

    def list_files(self) -> None:
        """Print the current context file list."""
        if not self.files:
            print("\033[90mNo files in context. Use /add <path>.\033[0m")
            return
        print(f"\033[1mContext files\033[0m \033[90m({len(self.files)})\033[0m")
        for f in self.files:
            print(f"  {f}")

    async def cmd_add(self, arg: str) -> bool:
        """/add <path> — add file or directory to context."""
        if arg:
            self.add_files([arg])
        else:
            print("\033[31mUsage: /add <path>\033[0m")
        return True

    async def cmd_rm(self, arg: str) -> bool:
        """/rm <path> — remove from context."""
        if arg:
            self.remove_files([arg])
        else:
            print("\033[31mUsage: /rm <path>\033[0m")
        return True

    async def cmd_files(self, arg: str) -> bool:
        """/files — list context files."""
        self.list_files()
        return True

    async def cmd_root(self, arg: str) -> bool:
        """/root [path] — show or change the project base directory."""
        current = os.environ.get("ZIYA_USER_CODEBASE_DIR") or os.getcwd()
        arg = arg.strip()
        if not arg:
            print(f"\033[1mRoot:\033[0m {current}")
            return True

        new_root = os.path.abspath(os.path.expanduser(arg))
        if not os.path.isdir(new_root):
            print(f"\033[31mNot a directory: {new_root}\033[0m")
            return True

        if new_root == current:
            print(f"\033[90mAlready at {new_root}\033[0m")
            return True

        if self.files:
            print(f"\033[33m⚠ Clearing {len(self.files)} context file(s) "
                  f"relative to the old root.\033[0m")
            self.files = []

        await self._switch_root(current, new_root, explicit="true")
        print(f"\033[32m✓ Root: {new_root}\033[0m")
        return True

    async def _switch_root(self, old_root: str, new_root: str, explicit) -> None:
        """Point the session at ``new_root``: update env, cwd, and MCP state.

        Shared by /root (change root) and /reset (restore the startup root).
        ``explicit`` is the value for ZIYA_EXPLICIT_ROOT — pass "true" for an
        explicit /root change, or the captured startup value on reset so the
        flag returns to exactly how it started (None removes the var).
        """
        os.environ["ZIYA_USER_CODEBASE_DIR"] = new_root
        if explicit is None:
            os.environ.pop("ZIYA_EXPLICIT_ROOT", None)
        else:
            os.environ["ZIYA_EXPLICIT_ROOT"] = explicit
        try:
            os.chdir(new_root)
        except OSError as e:
            print(f"\033[31mCould not chdir to {new_root}: {e}\033[0m")

        # Workspace-scoped MCP clients (e.g. the shell server) are keyed by
        # project root and cache a live subprocess per root. Tear down any
        # instance bound to the OLD root so the next tool call spawns a
        # fresh subprocess rooted at the new directory instead of silently
        # continuing to operate on the old one.
        try:
            from app.mcp.manager import get_mcp_manager
            mcp_manager = get_mcp_manager()
            if mcp_manager and mcp_manager.is_initialized:
                for server_name, instances in list(mcp_manager.workspace_scoped_clients.items()):
                    for instance_key, client in list(instances.items()):
                        if instance_key.startswith(old_root):
                            await client.disconnect()
                            del mcp_manager.workspace_scoped_clients[server_name][instance_key]
                            mcp_manager._workspace_instance_last_used.get(server_name, {}).pop(instance_key, None)
                mcp_manager.invalidate_tools_cache()
        except (ImportError, OSError, RuntimeError, asyncio.TimeoutError) as e:
            print(f"\033[2m(MCP workspace refresh skipped: {e})\033[0m")

    async def cmd_clear(self, arg: str) -> bool:
        """/clear — clear conversation history."""
        # Detach from any joined GUI conversation FIRST: clearing local
        # history while attached would, on the next turn's write-back,
        # truncate the shared GUI chat to the cleared contents (data loss).
        if self._attached:
            self._detach("Detached from the GUI conversation before clearing.")
        # A full /clear inside an active tangent would otherwise leave a
        # dangling saved frame that /quit later "pops" into a history that
        # was never the tangent's actual parent — drop the stack outright.
        if self._tangent_stack:
            print(f"\033[90m  (discarding {len(self._tangent_stack)} active tangent)\033[0m")
            self._tangent_stack = []
        count = len(self.history)
        self.history = []
        # Fresh session identity: the next save writes a new session file,
        # and conversation-scoped state (bead tree) starts over since
        # conversation_id derives from _session_id.
        self._session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
        self._session_name = None
        self._session_start_time = None
        print(f"\033[32m✓ Cleared {count} messages from history\033[0m")
        return True

    async def cmd_reset(self, arg: str) -> bool:
        """/reset — clear history, files, and all session state."""
        # Detach from any joined GUI conversation first (same data-loss
        # reasoning as /clear — a post-reset write-back would truncate it).
        if self._attached:
            self._detach("Detached from the GUI conversation before reset.")
        # Same reasoning as /clear: a /reset wipes session identity, so any
        # in-flight tangent frame is now orphaned — drop it rather than let
        # a future /quit resurrect a pre-reset history.
        if self._tangent_stack:
            print(f"\033[90m  (discarding {len(self._tangent_stack)} active tangent)\033[0m")
            self._tangent_stack = []
        hist_count = len(self.history)
        file_count = len(self.files)
        self.history = []
        self.files = []
        # Undo any mid-session /root (/cd) change: a changed project root is
        # session state and must not survive a reset. Restore the root as it
        # stood at startup (env vars + cwd + MCP workspace clients).
        current_root = os.environ.get("ZIYA_USER_CODEBASE_DIR") or os.getcwd()
        if current_root != self._initial_root:
            await self._switch_root(current_root, self._initial_root,
                                    explicit=self._initial_explicit_root)
            print(f"\033[90m  Restored root: {self._initial_root}\033[0m")
        self._session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
        self._session_name = None
        self._session_start_time = None
        self._session_shell_commands = None
        self._session_yolo = False
        self._session_timeout = None
        self._partial_response = ""
        print(f"\033[32m✓ Session reset\033[0m")
        print(f"\033[90m  Cleared {hist_count} messages, {file_count} files\033[0m")
        return True

    async def cmd_suspend(self, arg: str) -> bool:
        """/suspend [name] — save session and exit."""
        try:
            name = arg.strip() or None
            session_id = save_session(self, name)
        except Exception as e:
            print(f"\033[31mSave failed: {e}\033[0m")
            return True  # don't exit on a failed save
        # save_session resolves the effective name (explicit arg wins, else
        # the session's prior name) and stores it on self._session_name —
        # use that so a bare /suspend of a named session shows its name.
        label = self._session_name or session_id
        print(f"\033[32m✓ Session saved: {label}\033[0m")
        resume_arg = self._session_name or session_id
        print(f"\033[90mResume with: ziya chat --resume {resume_arg}\033[0m")
        return False

    async def cmd_save(self, arg: str) -> bool:
        """/save [name] — save a forkable snapshot (new id) without exiting."""
        try:
            name = arg.strip() or None
            session_id = save_session(self, name, fork=True)
            label = name or session_id
            print(f"\033[32m✓ Snapshot saved: {label}\033[0m")
            print(f"\033[90mFork it later with: /resume {label}\033[0m")
            if self._attached:
                self._detach("Detached from the GUI conversation — this "
                             "fork is a private local session from here on.")
        except Exception as e:
            print(f"\033[31mSave failed: {e}\033[0m")
        return True

    async def cmd_resume(self, arg: str) -> bool:
        """/resume [name] — restore a previous session."""
        arg = arg.strip()
        if arg:
            session_id = find_session_by_name(arg)
            if not session_id:
                print(f"\033[33mNo session matching '{arg}'\033[0m")
                return True
        else:
            session_id = await select_session()
            if not session_id:
                return True
        try:
            data = load_session(session_id)
        except FileNotFoundError as e:
            print(f"\033[33m{e}\033[0m")
            return True
        # Visibility: show exactly which file was loaded and how fresh it is,
        # so a stale twin (same logical session saved under two filenames,
        # e.g. an id-named file and a name-named file from different builds)
        # is detectable at resume time instead of surfacing later as a
        # mysterious gap in the restored conversation.
        session_file = get_session_dir() / f"{session_id}.json"
        loaded_name = data.get('name')
        last_update = data.get('last_update_time', data.get('timestamp', 'unknown'))
        print(f"\033[90m  Loaded: {session_file.name}"
              f" (name: {loaded_name or '—'}, last update: {last_update},"
              f" {len(data.get('history', []))} messages)\033[0m")
        # Twin detection: another session file claiming the same name or id.
        try:
            for p in get_session_dir().glob('*.json'):
                if p.stem == session_id:
                    continue
                try:
                    with open(p) as f:
                        other = json.load(f)
                except (OSError, json.JSONDecodeError, ValueError):
                    continue
                if (loaded_name and (other.get('name') == loaded_name
                                     or other.get('id') == loaded_name)) \
                        or other.get('id') == session_id:
                    print(f"\033[33m  ⚠ Twin session file: {p.name} "
                          f"(last update: {other.get('last_update_time', '?')}, "
                          f"{len(other.get('history', []))} messages) — "
                          f"if the resumed session looks stale, try that one.\033[0m")
        except Exception:
            pass  # visibility only — never block a resume
        # Checkpoint the current session before switching away so its
        # history isn't lost (auto-checkpoint may not have run yet).
        if self.history and not getattr(self, '_ephemeral', False):
            try:
                save_session(self, cleanup=False)
            except Exception:
                pass
        self.files = data.get('files', [])
        self.history = data.get('history', [])
        self._session_id = data.get('id', session_id)
        self._session_name = data.get('name')
        self._session_start_time = data.get('start_time', data.get('timestamp'))
        print(f"\033[32m✓ Resumed session {self._session_id}\033[0m")
        print(f"  Files: {len(self.files)}, Messages: {len(self.history)}\n")
        _print_resumed_beads(self.conversation_id)
        return True

    async def cmd_join(self, arg: str) -> bool:
        """/join [id-or-title] — attach to a live GUI conversation.

        Adopts the GUI chat's id as this session's conversation_id so beads,
        task-result injection, and the GUI sidebar all operate on the shared
        conversation.  Completed turns are written back into the GUI chat,
        and external updates (from the GUI or another CLI) are pulled in at
        the next prompt.  /save forks to a private local session and detaches.
        """
        from app.utils.cli_chat_bridge import (
            list_joinable_chats, load_chat_as_history, chat_signature,
            chat_display_label,
        )
        root = os.environ.get("ZIYA_USER_CODEBASE_DIR") or os.getcwd()
        project_id, summaries = list_joinable_chats(root)
        if project_id is None:
            print("\033[33mNo GUI project registered for this directory — "
                  "open it in the Ziya GUI first.\033[0m")
            return True
        if not summaries:
            print("\033[90mNo conversations to join in this project.\033[0m")
            return True

        arg = arg.strip()
        chat_id = None
        if arg:
            for s in summaries:
                if s.id == arg:
                    chat_id = s.id
                    break
            if not chat_id:
                for s in summaries:
                    if s.id.startswith(arg) or (s.title or '').lower() == arg.lower():
                        chat_id = s.id
                        break
            if not chat_id:
                print(f"\033[33mNo conversation matching '{arg}'\033[0m")
                return True
        else:
            chat_id = await select_joinable_chat(summaries)
            if not chat_id:
                return True

        chat, history = load_chat_as_history(project_id, chat_id)
        if chat is None:
            print(f"\033[31mConversation {chat_id} could not be loaded.\033[0m")
            return True

        # Checkpoint the current local session before switching away (unless
        # already attached — then the prior chat is its own store).
        if self.history and not getattr(self, '_ephemeral', False) and not self._attached:
            try:
                save_session(self, cleanup=False)
            except Exception:
                pass

        self._attached_project_id = project_id
        self._attached_chat_id = chat_id
        self.history = history
        self._attach_baseline_sig = chat_signature(project_id, chat_id)
        label = chat_display_label(chat)
        print(f"\033[32m✓ Joined GUI conversation: {label}\033[0m")
        print(f"\033[90m  {len(history)} messages · shared id {chat_id[:8]} · "
              f"/save to fork & detach\033[0m\n")
        _print_resumed_beads(self.conversation_id)
        return True

    def _detach(self, reason: str = "") -> None:
        """Detach from a joined GUI conversation (stop write-back).

        conversation_id reverts to cli_<session_id> afterward.  Safe to call
        when not attached (no-op)."""
        if not self._attached:
            return
        self._attached_project_id = None
        self._attached_chat_id = None
        self._attach_baseline_sig = None
        if reason:
            print(f"\033[90m  {reason}\033[0m")

    def _sync_after_turn(self) -> None:
        """Write the current history back into the joined GUI chat.

        Reuses ids for the unchanged prefix and adopts the resulting
        signature as the new baseline so our own write isn't mistaken for
        an external edit on the next prompt.  Non-fatal on failure."""
        if not self._attached:
            return
        try:
            from app.utils.cli_chat_bridge import write_back
            sig = write_back(self._attached_project_id, self._attached_chat_id, self.history)
            if sig is None:
                self._detach("Joined conversation disappeared — detached.")
            else:
                self._attach_baseline_sig = sig
        except Exception as e:  # noqa: BLE001 — a sync hiccup must not break the loop
            logger.debug("write_back failed (non-fatal): %s", e)

    def _pull_external_updates(self) -> None:
        """If the joined chat advanced elsewhere, pull new turns into history.

        Runs at each prompt boundary.  Compares the live chat signature to
        our baseline; on a change, reloads the full history and prints a
        short preview of the new turns.  The input buffer is untouched
        (this runs before the prompt is shown)."""
        if not self._attached:
            return
        try:
            from app.utils.cli_chat_bridge import chat_signature, load_chat_as_history
            sig = chat_signature(self._attached_project_id, self._attached_chat_id)
        except Exception:  # noqa: BLE001
            return
        if sig is None:
            self._detach("Joined conversation disappeared — detached.")
            return
        if sig == self._attach_baseline_sig:
            return
        prev_len = len(self.history)
        chat, history = load_chat_as_history(
            self._attached_project_id, self._attached_chat_id)
        if chat is None:
            self._detach("Joined conversation disappeared — detached.")
            return
        self.history = history
        self._attach_baseline_sig = sig
        new_msgs = history[prev_len:] if len(history) >= prev_len else []
        if new_msgs:
            print(f"\033[36m↻ {len(new_msgs)} new message(s) from the GUI:\033[0m")
            for m in new_msgs:
                who = 'you' if m.get('type') == 'human' else 'ai'
                preview = (m.get('content') or '').strip().replace('\n', ' ')
                if len(preview) > 100:
                    preview = preview[:97] + '...'
                color = '\033[32m' if who == 'you' else '\033[35m'
                print(f"  {color}{who}:\033[0m {preview}")
            print()

    async def cmd_shell(self, arg: str) -> bool:
        """/shell — manage shell command allowlist."""
        await self._handle_shell_command(arg)
        return True

    async def cmd_goal(self, arg: str) -> bool:
        """/goal <text> — run an autonomous goal."""
        await self._handle_goal_command(arg)
        return True

    async def cmd_card(self, arg: str) -> bool:
        """/card [list | <id-or-name>] — run a GUI task card in-process.

        Third terminal face of the shared task-card engine (alongside the
        web /launch endpoint and `ziya task --card`).  Runs the card via
        the same ``run_card`` library used by the CLI subcommand, so live
        streaming (A) and the un-approved-scope notice (B) come along.

        MCP is already initialized in this event loop by _run_async_cli, so
        we await run_card directly — wrapping it in _run_with_mcp here would
        re-init then tear MCP down on return, killing the chat session.
        """
        root = ziya_env("ZIYA_USER_CODEBASE_DIR") or os.getcwd()
        tokens = arg.split(maxsplit=1) if arg else []
        sub = tokens[0].lower() if tokens else ""

        if not sub or sub == "list":
            from app.cli_card_runner import list_cards
            list_cards(root)
            return True

        # `/card run <id>` and `/card <id>` are both accepted; `run` is an
        # optional verb so the command reads naturally either way.
        if sub == "run":
            card_ref = tokens[1].strip() if len(tokens) > 1 else ""
            if not card_ref:
                print("\033[90mUsage: /card run <id-or-name>\033[0m")
                return True
        else:
            card_ref = arg.strip()

        from app.cli_card_runner import run_card
        await run_card(root, card_ref, stream=True)
        return True

    async def cmd_tune(self, arg: str) -> bool:
        """/tune <key> <value> — adjust session settings."""
        self._handle_tune(arg)
        return True

    async def cmd_model(self, arg: str) -> bool:
        """/model [name] — switch model."""
        await self._handle_model_selection_async(arg)
        return True

    async def cmd_tangent(self, arg: str) -> bool:
        """/tangent <topic> — branch into a temporary side-conversation.

        Saves the current history/files as a frame on ``_tangent_stack``
        and continues with the SAME history underneath (so the model still
        has the prior context to reason from) but any files added while the
        tangent is active are confined to it — the snapshot restores the
        pre-tangent file list on /quit, per the "context additions disappear
        with tangent" requirement. Beads are NOT snapshotted: conversation_id
        is unchanged for the duration of the tangent, so bead_create/
        bead_complete calls made while tangential land in the exact same
        tree as the parent conversation automatically — no merge step needed.

        Single-level today (a list, not a fixed slot) so a future nested
        /tangent is a matter of relaxing the guard below, not a redesign.
        """
        topic = arg.strip()
        if not topic:
            print("\033[90mUsage: /tangent <topic>\033[0m")
            return True
        if self._tangent_stack:
            print("\033[33mAlready in a tangent — nesting isn't supported yet."
                  " /quit first.\033[0m")
            return True

        bead_id = None
        try:
            from app.models.bead import Bead
            from app.storage.beads import load_bead_tree, save_bead_tree
            tree = load_bead_tree(conversation_id=self.conversation_id)
            active = tree.active_bead
            if active:
                active.status = "parked"
            new_bead = Bead(parent_id=active.id if active else None,
                             content=f"[tangent] {topic}"[:200], status="active")
            tree.beads.append(new_bead)
            save_bead_tree(tree, conversation_id=self.conversation_id)
            bead_id = new_bead.id
        except Exception as e:
            logger.debug(f"tangent bead_create skipped (non-fatal): {e}")

        self._tangent_stack.append({
            'topic': topic,
            'history_len': len(self.history),
            'files': list(self.files),
            'bead_id': bead_id,
        })
        print(f"\033[35m↝ Tangent: {topic}\033[0m")
        print("\033[90m  /quit [summary|verbatim|discard] to return"
              " (default: discard)\033[0m")
        return True

    async def _summarize_tangent(self, tangent_messages: list) -> str:
        """One/two-line AI summary of the tangent's Q&A, for /quit summary."""
        if not tangent_messages:
            return ""
        transcript = "\n".join(
            f"{m.get('type', '?')}: {m.get('content', '')}"[:500]
            for m in tangent_messages
        )[:4000]
        try:
            from langchain_core.messages import HumanMessage
            prompt = (
                "Summarize the following side-conversation in 1-2 sentences, "
                "focused on any conclusion or decision reached:\n\n" + transcript
            )
            result = await self.model.ainvoke([HumanMessage(content=prompt)])
            content = result.content if hasattr(result, 'content') else str(result)
            return content.strip()
        except Exception as e:
            logger.debug(f"tangent summarize failed (non-fatal): {e}")
            return "[tangent summary unavailable]"

    async def _pop_tangent(self, mode: str) -> None:
        """Return from the active tangent per ``mode`` (summary/verbatim/discard)."""
        frame = self._tangent_stack.pop()
        tangent_messages = self.history[frame['history_len']:]

        if mode == 'discard' or not tangent_messages:
            self.history = self.history[:frame['history_len']]
        elif mode == 'verbatim':
            pass  # tangent messages already sit at the tail of history — keep them
        elif mode == 'summary':
            summary = await self._summarize_tangent(tangent_messages)
            self.history = self.history[:frame['history_len']]
            if summary:
                self.history.append({
                    'type': 'ai',
                    'content': f"[Tangent \"{frame['topic']}\" summary] {summary}",
                })

        # File additions made during the tangent are confined to it —
        # restore the pre-tangent set regardless of mode.
        self.files = frame['files']

        bead_id = frame.get('bead_id')
        if bead_id:
            try:
                from app.storage.beads import load_bead_tree, save_bead_tree
                tree = load_bead_tree(conversation_id=self.conversation_id)
                target = next((b for b in tree.beads if b.id == bead_id), None)
                if target and target.status not in ("completed", "abandoned"):
                    target.status = "completed" if mode != 'discard' else "abandoned"
                    if target.parent_id:
                        parent = next((b for b in tree.beads if b.id == target.parent_id), None)
                        if parent and parent.status == "parked":
                            parent.status = "active"
                    save_bead_tree(tree, conversation_id=self.conversation_id)
            except Exception as e:
                logger.debug(f"tangent bead resolve skipped (non-fatal): {e}")

        label = {'summary': 'with summary', 'verbatim': 'verbatim', 'discard': 'discarded'}[mode]
        print(f"\033[35m↜ Back from tangent \"{frame['topic']}\" ({label})\033[0m")

    async def cmd_quit(self, arg: str) -> bool:
        """/quit [summary|verbatim|discard] — exit, or pop an active tangent.

        Context-sensitive overload: with a tangent active, returns to the
        parent context per the given mode (default 'discard', silent —
        the completer surfaces the other options rather than a prompt).
        With no tangent active, behaves exactly as before (exit the CLI).
        """
        if self._tangent_stack:
            mode = arg.strip().lower() or 'discard'
            if mode not in ('summary', 'verbatim', 'discard'):
                print(f"\033[33mUnknown /quit option '{mode}' — use summary,"
                      " verbatim, or discard.\033[0m")
                return True
            await self._pop_tangent(mode)
            return True
        return False

    async def cmd_context(self, arg: str) -> bool:
        """/context — break down token/context utilization for this session."""
        from app.agents.agent import estimate_token_count

        mode = (arg or "").strip().lower()
        show_files = mode in ("files", "all")
        show_tools = mode in ("tools", "all")
        show_history = mode in ("history", "all")

        # Build the exact messages the model receives (empty question = current
        # standing context: system prompt + codebase + chat history).
        try:
            messages = self._build_messages("")
        except Exception as e:  # noqa: BLE001
            print(f"\033[31mCould not build context: {e}\033[0m")
            return True

        def _text(content) -> str:
            # LangChain message content is either a str or a list of blocks.
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        parts.append(block.get("text", "") if block.get("type") == "text" else "")
                    elif isinstance(block, str):
                        parts.append(block)
                return "".join(parts)
            return str(content)

        # Separate the system message (prompt + embedded codebase) from the
        # conversation turns.
        system_text = ""
        turn_msgs = []
        for msg in messages:
            cls = type(msg).__name__
            if cls == "SystemMessage":
                system_text += _text(msg.content)
            else:
                turn_msgs.append(msg)

        # Split the system prompt at the real codebase markers (see prompts.py).
        BEGIN = "Below is the current codebase of the user:"
        END = "Codebase ends here."
        base_prompt_text = system_text
        codebase_text = ""
        b = system_text.find(BEGIN)
        if b != -1:
            e = system_text.find(END, b)
            if e != -1:
                codebase_text = system_text[b + len(BEGIN):e]
                base_prompt_text = system_text[:b] + system_text[e + len(END):]

        base_tokens = estimate_token_count(base_prompt_text)
        codebase_tokens = estimate_token_count(codebase_text) if codebase_text else 0

        # Chat history / turn breakdown.
        history_rows = []
        history_tokens = 0
        for msg in turn_msgs:
            role = {"HumanMessage": "user", "AIMessage": "assistant"}.get(
                type(msg).__name__, "other")
            t = estimate_token_count(_text(msg.content))
            history_tokens += t
            history_rows.append((role, t, _text(msg.content)))

        # MCP tool definitions — count them the way they're actually serialized
        # into the request payload (same converter the executor uses).
        tool_tokens = 0
        tools_by_server = {}
        tool_count = 0
        try:
            from app.mcp.manager import get_mcp_manager
            mcp_manager = get_mcp_manager()
            if mcp_manager and getattr(mcp_manager, "is_initialized", False):
                from app.mcp.enhanced_tools import create_secure_mcp_tools
                from app.streaming_tool_executor import StreamingToolExecutor
                tools = create_secure_mcp_tools() or []
                executor = StreamingToolExecutor.__new__(StreamingToolExecutor)
                for tool in tools:
                    try:
                        schema = executor._convert_tool_schema(tool)
                        t = estimate_token_count(json.dumps(schema))
                    except Exception:  # noqa: BLE001
                        t = 0
                    server = (getattr(tool, "_server_name", None)
                              or (getattr(tool, "metadata", {}) or {}).get("server_name")
                              or "builtin")
                    agg = tools_by_server.setdefault(server, [0, 0])
                    agg[0] += 1
                    agg[1] += t
                    tool_tokens += t
                    tool_count += 1
        except Exception:  # noqa: BLE001
            pass

        total = base_tokens + codebase_tokens + history_tokens + tool_tokens

        # Model context window for the utilization bar.
        token_limit = None
        model_name = ziya_env("ZIYA_MODEL") or ""
        try:
            from app.config.models_config import MODEL_CONFIGS, DEFAULT_MODELS
            endpoint = ziya_env("ZIYA_ENDPOINT")
            available = MODEL_CONFIGS.get(endpoint, {})
            if not model_name:
                model_name = DEFAULT_MODELS.get(endpoint, "")
            cfg = available.get(model_name, {})
            token_limit = cfg.get("token_limit")
            if cfg.get("supports_extended_context") and cfg.get("extended_context_limit"):
                token_limit = cfg.get("extended_context_limit")
        except Exception:  # noqa: BLE001
            pass

        def bar(frac, width=28):
            frac = max(0.0, min(1.0, frac))
            filled = int(round(frac * width))
            return "█" * filled + "░" * (width - filled)

        def fmt(n):
            return f"{n:,}"

        def pct(n):
            return f"{(100.0 * n / total):.1f}%" if total else "0.0%"

        print(f"\n\033[1mContext utilization\033[0m"
              f" \033[90m({model_name or 'unknown model'})\033[0m")
        rows = [
            ("System prompt", base_tokens),
            (f"Codebase / files ({len(self.files)})", codebase_tokens),
            (f"Tool definitions ({tool_count})", tool_tokens),
            (f"Chat history ({len(turn_msgs)} msgs)", history_tokens),
        ]
        label_w = max(len(r[0]) for r in rows) + 2
        for label, n in rows:
            print(f"  \033[36m{label:<{label_w}}\033[0m "
                  f"{fmt(n):>9}  \033[90m{pct(n):>6}\033[0m")
        print(f"  {'':<{label_w}} {'':>9}  {'':>6}")
        print(f"  \033[1m{'Total input':<{label_w}}\033[0m "
              f"\033[1m{fmt(total):>9}\033[0m")

        if token_limit:
            frac = total / token_limit
            color = "\033[32m" if frac < 0.7 else ("\033[33m" if frac < 0.9 else "\033[31m")
            print(f"\n  {color}{bar(frac)}\033[0m "
                  f"{100.0 * frac:.1f}% of {fmt(token_limit)} ctx")

        # Detail sections (opt-in via subcommand).
        if show_files and self.files:
            print(f"\n\033[1mPer-file estimates\033[0m \033[90m({len(self.files)})\033[0m")
            root = os.environ.get("ZIYA_USER_CODEBASE_DIR") or os.getcwd()
            file_rows = []
            for f in self.files:
                path = f if os.path.isabs(f) else os.path.join(root, f)
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        file_rows.append((f, estimate_token_count(fh.read())))
                except OSError:
                    file_rows.append((f, 0))
            for name, t in sorted(file_rows, key=lambda r: -r[1]):
                print(f"  {fmt(t):>9}  \033[90m{name}\033[0m")

        if show_tools and tools_by_server:
            print(f"\n\033[1mTool definitions by server\033[0m")
            for server, (cnt, t) in sorted(tools_by_server.items(), key=lambda r: -r[1][1]):
                print(f"  {fmt(t):>9}  \033[36m{server}\033[0m \033[90m({cnt} tools)\033[0m")

        if show_history and history_rows:
            print(f"\n\033[1mPer-message estimates\033[0m \033[90m({len(history_rows)})\033[0m")
            for i, (role, t, text) in enumerate(history_rows, 1):
                snippet = " ".join(text.split())[:60]
                print(f"  {i:>3}. \033[36m{role:<9}\033[0m {fmt(t):>8}  "
                      f"\033[90m{snippet}\033[0m")

        if mode not in ("", "files", "tools", "history", "all"):
            print(f"\n\033[33mUnknown /context option '{mode}' — "
                  "use files, tools, history, or all.\033[0m")
        elif not mode:
            print("\n\033[90mDetail: /context files | tools | history | all\033[0m")
        print()
        return True

    async def cmd_beads(self, arg: str) -> bool:
        """/beads — render the conversation's task tree."""
        try:
            from app.storage.beads import load_bead_tree
            tree = load_bead_tree(conversation_id=self.conversation_id)
        except Exception as e:
            print(f"\033[31mCould not load bead tree: {e}\033[0m")
            return True

        if not tree.beads:
            print("\033[90mNo beads tracked in this session yet.\033[0m")
            return True

        icons = {
            'active': ('\033[32m', '●'),     # green
            'parked': ('\033[33m', '◐'),     # yellow
            'completed': ('\033[90m', '✓'),  # grey
            'abandoned': ('\033[90m', '✗'),  # grey
        }
        children_of = {}
        ids = {b.id for b in tree.beads}
        roots = []
        for b in tree.beads:
            # Treat beads with a missing parent as roots (orphan-safe).
            if b.parent_id and b.parent_id in ids:
                children_of.setdefault(b.parent_id, []).append(b)
            else:
                roots.append(b)

        def render(bead, depth):
            color, icon = icons.get(bead.status, ('', '?'))
            hint = f" \033[90m— {bead.context_hint}\033[0m" if bead.context_hint else ""
            print(f"  {'  ' * depth}{color}{icon}\033[0m {bead.content}"
                  f" \033[90m[{bead.status}]\033[0m{hint}")
            for child in sorted(children_of.get(bead.id, []), key=lambda b: b.created_at):
                render(child, depth + 1)

        counts = {}
        for b in tree.beads:
            counts[b.status] = counts.get(b.status, 0) + 1
        summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        print(f"\n\033[1mTask tree\033[0m \033[90m({summary})\033[0m")
        for root in sorted(roots, key=lambda b: b.created_at):
            render(root, 0)
        print()
        return True

    async def cmd_help(self, arg: str) -> bool:
        """/help — show help, generated from COMMAND_SPEC."""
        print("\n\033[1mCommands:\033[0m")
        usages = [e.get('usage', e['name']) for e in COMMAND_SPEC]
        width = max(len(u) for u in usages) + 2
        for entry, usage in zip(COMMAND_SPEC, usages):
            aliases = entry.get('aliases')
            alias_note = f" \033[90m(also {', '.join(aliases)})\033[0m" if aliases else ""
            print(f"  \033[36m{usage:<{width}}\033[0m{entry['help']}{alias_note}")
            subs = entry.get('subcommands')
            if subs:
                for sub, d in subs.items():
                    sub_usage = f"{entry['name']} {sub}"
                    print(f"    \033[90m{sub_usage:<{width}}{d['help']}\033[0m")
        print("\n\033[90mTip: append ? for options, e.g. /shell ? or /shell git ?\033[0m")
        print("""
\033[1mDiff Application:\033[0m
  When the AI provides code diffs, you'll be prompted to:
  [a]pply - Apply the diff to your files
  [A]pply all - Apply this and all remaining diffs
  [s]kip - Skip this diff and continue
  [v]iew - View the full diff content
  [q]uit - Stop processing remaining diffs
""")
        return True

    async def _handle_command(self, cmd: str) -> bool:
        """Handle slash commands. Returns False to exit."""
        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        # "?" help pattern: "/shell ?" or "/shell git ?" prints descriptions
        # of the next-level subcommands/options.
        if arg:
            tokens = arg.split()
            if tokens[-1] == '?' and self._print_inline_help(command, tokens[:-1]):
                return True

        # Spec-driven dispatch (see COMMAND_SPEC / CLI_DISPATCH)
        handler_name = CLI_DISPATCH.get(command)
        if handler_name:
            return await getattr(self, handler_name)(arg)

        print(f"\033[90mUnknown command: {command}\033[0m")
        
        return True

    async def _handle_goal_command(self, arg: str):
        """Handle /goal command — synthesize and launch an autonomous goal.

        Subcommands:
          /goal <text>     Set a goal and start working on it
          /goal status     Show current goal state
          /goal pause      Pause the active goal
          /goal resume     Resume a paused goal
          /goal clear      Cancel and remove the goal
        """
        if not arg.strip():
            print("\033[1mUsage:\033[0m")
            print("  /goal <objective>   Set an autonomous goal")
            print("  /goal status        Show current goal state")
            print("  /goal pause         Pause the active goal")
            print("  /goal resume        Resume a paused goal")
            print("  /goal clear         Cancel and remove the goal")
            return

        import aiohttp

        # Determine the server URL
        port = str(ziya_env("ZIYA_PORT"))
        base_url = f"http://localhost:{port}"

        # Build the command request
        first_word = arg.split(maxsplit=1)[0].lower()
        payload = {
            "command": "goal",
            "args": arg,
            "conversation_id": getattr(self, '_session_id', None),
        }

        # Add context summary for new goals (not subcommands)
        if first_word not in ("status", "pause", "resume", "clear"):
            # Summarize recent history for context
            if self.history:
                recent = self.history[-4:]  # last 2 exchanges
                context_parts = []
                for msg in recent:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        context_parts.append(f"{role}: {content[:200]}")
                payload["context_summary"] = "\n".join(context_parts)

        try:
            headers = {"Content-Type": "application/json"}
            project_root = ziya_env("ZIYA_USER_CODEBASE_DIR") or os.getcwd()
            headers["X-Project-Root"] = project_root

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{base_url}/api/v1/commands",
                    json=payload,
                    headers=headers,
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        print(f"\033[31mGoal command failed: {error_text}\033[0m")
                        return

                    result = await resp.json()

            # Display result
            msg = result.get("message", "")
            resp_type = result.get("type", "")

            if resp_type == "goal_launched":
                print(f"\033[32m{msg}\033[0m")
                data = result.get("data", {})
                print(f"\033[90m  Strategy: Until(condition met, max 15 iterations)\033[0m")
                print(f"\033[90m  Run: {data.get('run_id', 'unknown')[:8]}...\033[0m")
                print(f"\033[90m  Use /goal status to check progress\033[0m")
            elif resp_type == "error":
                print(f"\033[33m{msg}\033[0m")
            else:
                print(msg)

        except aiohttp.ClientError as e:
            print(f"\033[31mFailed to connect to server: {e}\033[0m")
            print("\033[90mIs the Ziya server running?\033[0m")
        except Exception as e:
            print(f"\033[31mGoal command error: {e}\033[0m")

    # Default is read through ziya_env (which falls back to the env registry)
    # rather than duplicated as a literal here. A hardcoded literal silently
    # misreports the real default in /tune output once the registry changes.
    #
    # /tune is only reachable from the interactive REPL, so `iterations` targets
    # the interactive budget -- the one that actually governs the session the
    # user is typing in. The batch budget is set via the environment.
    _TUNABLES = {
        'iterations': ('ZIYA_MAX_TOOL_ITERATIONS_INTERACTIVE',
                       str(ziya_env('ZIYA_MAX_TOOL_ITERATIONS_INTERACTIVE')),
                       'Max tool iterations per interactive response'),
    }

    def _handle_tune(self, arg: str):
        """Handle /tune subcommands for session settings."""
        parts = arg.split() if arg else []
        if not parts:
            print("\033[1mTunable settings:\033[0m")
            for key, (env, default, desc) in self._TUNABLES.items():
                current = os.environ.get(env, default)
                print(f"  {key} = {current}  \033[90m({desc})\033[0m")
            print(f"\n\033[90mUsage: /tune <key> <value>\033[0m")
            return

        key = parts[0]
        if key not in self._TUNABLES:
            print(f"\033[31mUnknown tunable: {key}\033[0m")
            print(f"\033[90mAvailable: {', '.join(self._TUNABLES)}\033[0m")
            return

        env_var, default, desc = self._TUNABLES[key]
        if len(parts) < 2:
            current = os.environ.get(env_var, default)
            print(f"\033[90m{key} = {current}  ({desc})\033[0m")
            return

        try:
            n = int(parts[1])
            if n < 1:
                raise ValueError
            os.environ[env_var] = str(n)
            print(f"\033[32m✓ {key} = {n}\033[0m")
        except ValueError:
            print(f"\033[31m{key} requires a positive integer\033[0m")

    async def _handle_shell_command(self, arg: str):
        """Handle /shell subcommands for managing allowed shell commands."""
        from app.config.shell_config import (
            DEFAULT_SHELL_CONFIG,
            set_persisted_allowed_commands,
            reset_shell_config,
            get_default_shell_config,
        )

        parts = arg.split() if arg else []
        sub = parts[0] if parts else ''
        rest = arg[len(sub):].strip() if sub else ''

        # Split rest by commas if present, otherwise treat as space-separated tokens
        # But for add/rm: if no commas, treat entire rest as ONE command (e.g. "git add")
        has_commas = ',' in rest

        # Check for trailing 'save' keyword
        persist = rest.endswith(' save') or rest == 'save'
        if persist:
            rest = rest.rsplit(' save', 1)[0].strip() if rest.endswith(' save') else ''

        # Parse command list: commas = multiple commands, no commas = single command
        if has_commas:
            sub_args = [c.strip() for c in rest.split(',') if c.strip()]
        else:
            sub_args = [rest] if rest else []

        # /shell  or  /shell list
        if sub in ('', 'list', 'ls'):
            commands = self._get_session_commands()
            commands.sort()
            yolo = self._session_yolo

            if yolo:
                print("\033[1;33m⚠️  YOLO MODE — all commands allowed "
                      "(except sudo/vim/nano/emacs/systemctl)\033[0m\n")

            print(f"\033[1mAllowed shell commands ({len(commands)}):\033[0m")
            col_width = max(len(c) for c in commands) + 2 if commands else 20
            cols = max(1, 80 // col_width)
            for i in range(0, len(commands), cols):
                row = commands[i:i + cols]
                print("  " + "".join(c.ljust(col_width) for c in row))

            merged_config = get_default_shell_config()
            defaults = set(merged_config["allowedCommands"])
            base_defaults = set(DEFAULT_SHELL_CONFIG["allowedCommands"])
            plugin_commands = sorted(defaults - base_defaults)
            added = sorted(set(commands) - defaults)
            removed = sorted(base_defaults - set(commands))
            if plugin_commands:
                print(f"\n\033[34m🔌 From plugins: {', '.join(plugin_commands)}\033[0m")
            if added:
                print(f"\n\033[32m+ Custom: {', '.join(added)}\033[0m")
            if removed:
                print(f"\033[31m- Removed from defaults: {', '.join(removed)}\033[0m")
            return

        # /shell add <cmd> [cmd...] [save]
        if sub == 'add':
            if not sub_args:
                print("Usage: /shell add <command> [command...] [save]")
                return
            commands = self._get_session_commands()
            added = []
            for cmd in sub_args:
                if cmd not in commands:
                    commands.append(cmd)
                    added.append(cmd)
            if added:
                self._session_shell_commands = commands
                if persist:
                    set_persisted_allowed_commands(commands)
                print(f"\033[32m✓ Added: {', '.join(added)}\033[0m")
                if persist:
                    print("\033[90m  (saved permanently)\033[0m")
                else:
                    print("\033[90m  (session only — add 'save' to persist)\033[0m")
                await self._restart_shell_server()
            else:
                print("Already in allowlist.")
            return

        # /shell rm|remove <cmd> [cmd...] [save]
        if sub in ('rm', 'remove'):
            if not sub_args:
                print("Usage: /shell rm <command> [command...] [save]")
                return
            commands = self._get_session_commands()
            removed = []
            for cmd in sub_args:
                if cmd in commands:
                    commands.remove(cmd)
                    removed.append(cmd)
            if removed:
                self._session_shell_commands = commands
                if persist:
                    set_persisted_allowed_commands(commands)
                print(f"\033[31m✓ Removed: {', '.join(removed)}\033[0m")
                if persist:
                    print("\033[90m  (saved permanently)\033[0m")
                else:
                    print("\033[90m  (session only — add 'save' to persist)\033[0m")
                await self._restart_shell_server()
            else:
                print("Not in allowlist.")
            return

        # /shell yolo [off]  — always session-only
        if sub == 'yolo':
            if sub_args and sub_args[0] == 'off':
                self._session_yolo = False
                os.environ["ZIYA_YOLO_MODE"] = "false"
                print("\033[32m✓ YOLO mode disabled.\033[0m")
                await self._restart_shell_server()
                return

            if self._session_yolo:
                print("\033[33mYOLO mode is already enabled.\033[0m")
                print("Disable with: /shell yolo off")
                return

            print("\033[1;33m" + "=" * 50)
            print("  ⚠️   YOLO MODE — LIVING DANGEROUSLY")
            print("=" * 50 + "\033[0m")
            print("\nAllows the AI to run \033[1many\033[0m shell command.")
            print("Still blocked: sudo, vim, nano, emacs, systemctl\n")

            try:
                confirm = await asyncio.to_thread(
                    input, "\033[1mType 'yolo' to confirm: \033[0m"
                )
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                return
            if confirm.strip().lower() != 'yolo':
                print("Aborted.")
                return

            self._session_yolo = True
            os.environ["ZIYA_YOLO_MODE"] = "true"
            print("\n\033[1;33m🔥 YOLO mode enabled (this session only).\033[0m")
            print("Disable with: /shell yolo off")
            await self._restart_shell_server()
            return

        # /shell git <operation|all> [save]
        if sub == 'git':
            if not sub_args:
                print("\033[1mGit access control:\033[0m")
                print("  /shell git all          Allow ALL git operations")
                print("  /shell git add          Allow 'git add'")
                print("  /shell git commit       Allow 'git commit'")
                print("  /shell git push         Allow 'git push'")
                print("  /shell git safe         Reset to safe (read-only) git ops")
                print("  Append 'save' to persist across sessions.")
                return
            op = sub_args[0]
            commands = self._get_session_commands()
            if op == 'all':
                # Add bare 'git' which the shell server treats as "allow all"
                if 'git' not in commands:
                    commands.append('git')
                self._session_shell_commands = commands
                if persist:
                    set_persisted_allowed_commands(commands)
                print("\033[33m✓ All git operations enabled.\033[0m")
            elif op == 'safe':
                # Remove bare 'git' and any explicit git subcommands
                commands = [c for c in commands if c != 'git' and not c.startswith('git ')]
                self._session_shell_commands = commands
                if persist:
                    set_persisted_allowed_commands(commands)
                print("\033[32m✓ Git reset to safe (read-only) operations.\033[0m")
            else:
                # Add specific git subcommand
                entry = f"git {op}"
                if entry not in commands:
                    commands.append(entry)
                self._session_shell_commands = commands
                if persist:
                    set_persisted_allowed_commands(commands)
                print(f"\033[32m✓ 'git {op}' enabled.\033[0m")
            if persist:
                print("\033[90m  (saved permanently)\033[0m")
            else:
                print("\033[90m  (session only — add 'save' to persist)\033[0m")
            await self._restart_shell_server()
            return

        # /shell reset [save]
        if sub == 'reset':
            merged_config = get_default_shell_config()
            self._session_shell_commands = merged_config["allowedCommands"].copy()
            self._session_yolo = False
            os.environ["ZIYA_YOLO_MODE"] = "false"
            n_cmds = len(self._session_shell_commands)
            print(f"\033[32m✓ Shell config reset to defaults ({n_cmds} commands, YOLO off)\033[0m")
            if persist:
                print("\033[90m  (saved permanently)\033[0m")
            else:
                print("\033[90m  (session only — add 'save' to persist)\033[0m")
            await self._restart_shell_server()
            return

        # /shell timeout <seconds> [save]
        if sub == 'timeout':
            if not sub_args:
                current = self._session_timeout
                if current is None:
                    from app.config.shell_config import _read_mcp_config
                    cfg = _read_mcp_config()
                    current = int(cfg.get("mcpServers", {}).get("shell", {}).get("env", {}).get("COMMAND_TIMEOUT", "30"))
                if current == 0:
                    print(f"\033[1mCommand timeout: disabled (no limit)\033[0m")
                else:
                    print(f"\033[1mCommand timeout: {current}s\033[0m")
                return
            try:
                val = int(sub_args[0])
                if val < 0:
                    raise ValueError
            except ValueError:
                print("Usage: /shell timeout <seconds>  (0 to disable)")
                return
            self._session_timeout = val
            if persist:
                from app.config.shell_config import _read_mcp_config, _ensure_shell_env, _write_mcp_config
                cfg = _read_mcp_config()
                env = _ensure_shell_env(cfg)
                env["COMMAND_TIMEOUT"] = str(val)
                _write_mcp_config(cfg)
            if val == 0:
                print("\033[33m✓ Command timeout disabled (no limit)\033[0m")
            else:
                print(f"\033[32m✓ Command timeout: {val}s\033[0m")
            if persist:
                print("\033[90m  (saved permanently)\033[0m")
            else:
                print("\033[90m  (session only — add 'save' to persist)\033[0m")
            await self._restart_shell_server()
            return

        print(f"\033[90mUnknown: /shell {sub}\033[0m")
        print("Usage: /shell [list|add|rm|yolo|git|timeout|reset]")
        print("  Append 'save' to persist changes across sessions.")

    def _get_session_commands(self) -> list:
        """Get the effective command list (session override or persisted)."""
        if self._session_shell_commands is not None:
            return self._session_shell_commands.copy()
        from app.config.shell_config import get_persisted_allowed_commands
        return get_persisted_allowed_commands()

    async def _restart_shell_server(self):
        """Restart the shell MCP server with current session state."""
        try:
            from app.mcp.manager import get_mcp_manager
            from app.config.shell_config import _read_mcp_config
            mcp_manager = get_mcp_manager()
            if mcp_manager and mcp_manager.is_initialized:
                # Start from the manager's builtin config (correct absolute
                # command/args paths).  Never take command/args from
                # mcp_config.json — those may contain stale relative paths
                # that don't resolve from the current working directory.
                shell_cfg = dict(mcp_manager.server_configs.get("shell", {}))
                # Layer persisted env customizations on top
                cfg = _read_mcp_config()
                persisted_env = cfg.get("mcpServers", {}).get("shell", {}).get("env", {})
                env = {**persisted_env, **shell_cfg.get("env", {})}
                # Apply session-local overrides
                env["ALLOW_COMMANDS"] = ",".join(self._get_session_commands())
                env["YOLO_MODE"] = "true" if self._session_yolo else "false"
                if self._session_timeout is not None:
                    env["COMMAND_TIMEOUT"] = str(self._session_timeout)
                shell_cfg["env"] = env
                # Mint an in-process ephemeral grant authorizing whatever in
                # `env` exceeds the built-in floor. This call is reachable ONLY
                # from the TTY-stdin /shell handler, so the human keystroke is
                # the trust anchor — no root key, no signing ceremony. Without
                # it the subprocess's scope gate would silently clamp beyond-
                # floor commands back to the floor (the npm/npx/craco symptom).
                granted = mcp_manager.mint_shell_session_grant("shell", env)
                ok = await mcp_manager.restart_server("shell", shell_cfg)
                if ok:
                    if granted:
                        n = sum(len(v) for v in granted.values())
                        print("\033[32m✓ Shell server restarted — changes are live "
                              f"(authorized {n} command(s) beyond the default floor).\033[0m")
                    else:
                        print("\033[32m✓ Shell server restarted — changes are live.\033[0m")
                    return
            print("\033[2mRestart Ziya session for changes to take effect.\033[0m")
        except (ImportError, OSError, RuntimeError, asyncio.TimeoutError) as e:
            print(f"\033[2mRestart Ziya session for changes to take effect. ({e})\033[0m")

    async def _show_model_settings_dialog(self, model_name: str, model_config: dict) -> Optional[dict]:
        """Show simple text-based settings configuration."""
        from app.agents.models import ModelManager
        from app.config.models_config import get_supported_parameters
        
        current_settings = ModelManager.get_model_settings()
        settings = {}
        
        print(f"\n\033[1;36m{'─' * 60}\033[0m")
        print(f"\033[1;36mConfigure {model_name}\033[0m")
        print(f"\033[1;36m{'─' * 60}\033[0m")
        print("\033[90mPress Enter to keep current value, or type new value\033[0m\n")
        
        # Consult model capabilities (honors family-level unsupported_parameters,
        # e.g. Opus 4.7 rejects temperature/top_k/top_p). Same source the web modal uses.
        endpoint = ziya_env("ZIYA_ENDPOINT")
        supported_params = get_supported_parameters(endpoint, model_name)

        # Temperature
        if 'temperature' in supported_params:
            temp_range = supported_params.get('temperature') or model_config.get('parameter_ranges', {}).get('temperature', {'min': 0, 'max': 1, 'default': 0.3})
            current_temp = current_settings.get('temperature', temp_range.get('default', 0.3))
            temp_input = interruptible_input(f"Temperature [{temp_range.get('min', 0)}-{temp_range.get('max', 1)}] (current: {current_temp}): ").strip()
            if temp_input:
                try:
                    val = float(temp_input)
                    if temp_range.get('min', 0) <= val <= temp_range.get('max', 1):
                        settings['temperature'] = val
                    else:
                        print(f"\033[33mOut of range, using {current_temp}\033[0m")
                        settings['temperature'] = current_temp
                except ValueError:
                    print(f"\033[33mInvalid, using {current_temp}\033[0m")
                    settings['temperature'] = current_temp
            else:
                settings['temperature'] = current_temp
        
        # Max output tokens
        max_output = model_config.get('max_output_tokens', 4096)
        current_max = current_settings.get('max_output_tokens', max_output)
        max_input = interruptible_input(f"Max Output Tokens [1-{max_output}] (current: {current_max}): ").strip()
        if max_input:
            try:
                val = int(max_input)
                if 1 <= val <= max_output:
                    settings['max_output_tokens'] = val
                else:
                    print(f"\033[33mOut of range, using {current_max}\033[0m")
                    settings['max_output_tokens'] = current_max
            except ValueError:
                print(f"\033[33mInvalid, using {current_max}\033[0m")
                settings['max_output_tokens'] = current_max
        else:
            settings['max_output_tokens'] = current_max
        
        # Top-K if supported
        if 'top_k' in supported_params:
            top_k_range = supported_params.get('top_k') or {'min': 0, 'max': 500, 'default': 15}
            current_top_k = current_settings.get('top_k', top_k_range.get('default', 15))
            top_k_input = interruptible_input(f"Top-K [{top_k_range.get('min', 0)}-{top_k_range.get('max', 500)}] (current: {current_top_k}): ").strip()
            if top_k_input:
                try:
                    val = int(top_k_input)
                    if top_k_range.get('min', 0) <= val <= top_k_range.get('max', 500):
                        settings['top_k'] = val
                    else:
                        print(f"\033[33mOut of range, using {current_top_k}\033[0m")
                        settings['top_k'] = current_top_k
                except ValueError:
                    print(f"\033[33mInvalid, using {current_top_k}\033[0m")
                    settings['top_k'] = current_top_k
            else:
                    settings['top_k'] = current_top_k

        # Top-P if supported
        if 'top_p' in supported_params:
            top_p_range = supported_params.get('top_p') or {'min': 0.0, 'max': 1.0, 'default': 1.0}
            current_top_p = current_settings.get('top_p', top_p_range.get('default', 1.0))
            top_p_input = interruptible_input(f"Top-P [{top_p_range.get('min', 0.0)}-{top_p_range.get('max', 1.0)}] (current: {current_top_p}): ").strip()
            if top_p_input:
                try:
                    val = float(top_p_input)
                    if top_p_range.get('min', 0.0) <= val <= top_p_range.get('max', 1.0):
                        settings['top_p'] = val
                    else:
                        print(f"\033[33mOut of range, using {current_top_p}\033[0m")
                        settings['top_p'] = current_top_p
                except ValueError:
                    print(f"\033[33mInvalid, using {current_top_p}\033[0m")
                    settings['top_p'] = current_top_p
            else:
                settings['top_p'] = current_top_p
        
        # Thinking effort if model supports adaptive thinking
        if model_config.get('supports_adaptive_thinking'):
            valid_efforts = model_config.get('supported_efforts', ['low', 'medium', 'high', 'max'])
            default_effort = model_config.get('thinking_effort_default', 'medium')
            current_effort = current_settings.get('thinking_effort') or ziya_env('ZIYA_THINKING_EFFORT') or default_effort
            effort_input = interruptible_input(f"Thinking Effort [{'/'.join(valid_efforts)}] (current: {current_effort}): ").strip().lower()
            if effort_input:
                if effort_input in valid_efforts:
                    settings['thinking_effort'] = effort_input
                else:
                    print(f"\033[33mInvalid choice, using {current_effort}\033[0m")
                    settings['thinking_effort'] = current_effort
            else:
                settings['thinking_effort'] = current_effort

        return settings
    
    async def _handle_model_selection_async(self, arg: str):
        """Handle /model command with async interactive selection."""
        from app.config.models_config import MODEL_CONFIGS, DEFAULT_MODELS, MODEL_ALIASES
        
        endpoint = ziya_env("ZIYA_ENDPOINT")
        available_models = MODEL_CONFIGS.get(endpoint, {})
        current_model = ziya_env("ZIYA_MODEL") or DEFAULT_MODELS.get(endpoint, "")
        
        # If a model name is provided directly, use it
        if arg:
            # Resolve aliases — allows "/model fable" → "fable5", etc.
            endpoint_aliases = MODEL_ALIASES.get(endpoint, {})
            if arg not in available_models and arg in endpoint_aliases:
                resolved = endpoint_aliases[arg]
                print(f"\033[90m{arg} → {resolved}\033[0m")
                arg = resolved
            if arg in available_models:
                os.environ["ZIYA_MODEL"] = arg
                self._model = None  # Force reload
                print(f"\033[32m✓ Switched to {arg}\033[0m")
            else:
                print(f"\033[31mUnknown model: {arg}\033[0m")
                print(f"\033[90mAvailable: {', '.join(sorted(available_models.keys()))}\033[0m")
            return
        
        # No argument - show interactive selection
        if not available_models:
            print(f"\033[31mNo models available for endpoint: {endpoint}\033[0m")
            return
        
        # Sort models: group by family (claude, nova, etc.) then by version descending
        def sort_key(model_name):
            # Extract family prefix
            for prefix in ['sonnet', 'opus', 'haiku', 'nova', 'gemini', 'deepseek', 'openai', 'qwen']:
                if model_name.lower().startswith(prefix):
                    rest = model_name[len(prefix):].lstrip('-')
                    try:
                        version = float(rest.split('-')[0]) if rest and rest[0].isdigit() else 0
                    except ValueError:
                        version = 0
                    return (prefix, -version, model_name)
            return ('zzz', 0, model_name)
        
        sorted_models = sorted(available_models.keys(), key=sort_key)
        
        # Build radio list values with formatted labels
        radio_values = []
        for model_name in sorted_models:
            config = available_models[model_name]
            indicators = []
            if model_name == current_model:
                indicators.append("✓ current")
            if model_name == DEFAULT_MODELS.get(endpoint):
                indicators.append("default")
            
            # Show context window with auto-scale info
            token_limit = config.get('token_limit')
            extended_limit = config.get('extended_context_limit')
            supports_extended = config.get('supports_extended_context', False)
            
            if supports_extended and extended_limit:
                # Show base→extended format
                base_display = f"{token_limit // 1000000}M" if token_limit >= 1000000 else f"{token_limit // 1000}K"
                extended_display = f"{extended_limit // 1000000}M" if extended_limit >= 1000000 else f"{extended_limit // 1000}K"
                indicators.append(f"{base_display}→{extended_display} ctx")
            elif token_limit:
                # Show just the token limit
                if token_limit >= 1000000:
                    indicators.append(f"{token_limit // 1000000}M ctx")
                else:
                    indicators.append(f"{token_limit // 1000}K ctx")
            
            label_text = model_name
            if indicators:
                label_text += f"  ({', '.join(indicators)})"
            
            radio_values.append((model_name, label_text))
        
        # Create radio list
        radio_list = RadioList(values=radio_values, default=current_model if current_model in available_models else sorted_models[0])
        
        # Create key bindings
        kb = KeyBindings()
        
        configure_requested = {'value': False}
        
        @kb.add('enter')
        def _(event):
            event.app.exit(result=radio_list.current_value)
        
        @kb.add('right')
        def _(event):
            # Mark that user wants to configure settings
            configure_requested['value'] = True
            event.app.exit(result=radio_list.current_value)
        
        @kb.add('escape')
        def _(event):
            event.app.exit(result=None)
        
        # Override Enter to make it directly select the highlighted item
        # RadioList normally requires: space to mark, then enter to confirm
        # We want: enter to immediately select highlighted item
        custom_kb = KeyBindings()
        
        # Override navigation keys to auto-select as we move
        @custom_kb.add('up')
        def _(event):
            # Move up and auto-select
            radio_list._selected_index = max(0, radio_list._selected_index - 1)
            radio_list.current_value = radio_list.values[radio_list._selected_index][0]
        
        @custom_kb.add('down')
        def _(event):
            # Move down and auto-select
            radio_list._selected_index = min(len(radio_list.values) - 1, radio_list._selected_index + 1)
            radio_list.current_value = radio_list.values[radio_list._selected_index][0]
        
        @custom_kb.add('enter')
        def _(event):
            # Get the currently highlighted value (not the space-marked one)
            # RadioList stores this in _selected_index
            highlighted_value = radio_list.values[radio_list._selected_index][0]
            radio_list.current_value = highlighted_value
            event.app.exit(result=highlighted_value)
        
        from prompt_toolkit.key_binding import merge_key_bindings
        # Put custom_kb LAST so our Enter handler overrides RadioList's default
        radio_list.control.key_bindings = merge_key_bindings([radio_list.control.key_bindings, custom_kb])
        
        # Create application layout
        layout = Layout(HSplit([
            Window(
                content=FormattedTextControl(text=f'Select Model ({endpoint}) - ↑/↓ to navigate, Enter to select, → to configure, Esc to cancel\n'),
                height=2
            ),
            radio_list,
        ]))
        
        # Create and run application
        app = Application(
            layout=layout,
            key_bindings=kb,
            full_screen=False,
            mouse_support=True
        )
        
        try:
            result = await app.run_async()
            
            if result:
                selected_model = result
                selected_config = available_models[selected_model]
                
                # If user pressed right arrow, show settings dialog
                if configure_requested['value']:
                    try:
                        settings = await self._show_model_settings_dialog(selected_model, selected_config)
                    except KeyboardInterrupt:
                        print()
                        settings = None
                    if settings is None:
                        # User cancelled settings, go back to model selection
                        print(f"\n\033[90mSettings cancelled, keeping model: {current_model}\033[0m")
                        return
                    
                    # Apply settings
                    # Check if anything actually changed
                    model_changed = selected_model != current_model
                    settings_changed = False
                    for key, value in settings.items():
                        env_key = f"ZIYA_{key.upper()}"
                        old_value = os.environ.get(env_key)
                        new_value = str(value)
                        if old_value != new_value:
                            settings_changed = True
                            break

                    if not model_changed and not settings_changed:
                        print(f"\n\033[90mSettings unchanged for {selected_model}\033[0m")
                        return

                    for key, value in settings.items():
                        env_key = f"ZIYA_{key.upper()}"
                        os.environ[env_key] = str(value)

                    print(f"\n\033[32m✓ Switched to {selected_model} with custom settings\033[0m")
                    for key, value in settings.items():
                        print(f"  {key}: {value}")
                else:
                    if selected_model == current_model:
                        print(f"\n\033[90mModel unchanged: {selected_model}\033[0m")
                        return
                    print(f"\n\033[32m✓ Switched to {selected_model}\033[0m")
                
                os.environ["ZIYA_MODEL"] = selected_model
                self._model = None  # Force reload
            else:
                print(f"\n\033[90mCancelled\033[0m")
        except (EOFError, KeyboardInterrupt, OSError, RuntimeError, ValueError) as e:
            print(f"\n\033[33mInteractive selection failed: {e}\033[0m")
            print(f"\033[90mCurrent: {current_model}\033[0m")
            print(f"\033[90mUse: /model <name>\033[0m")


async def _initialize_mcp():
    """Initialize MCP servers for CLI mode."""
    try:
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        await mcp_manager.initialize()
        
        if mcp_manager.is_initialized:
            status = mcp_manager.get_server_status()
            connected = sum(1 for s in status.values() if s.get("connected"))
            tools = sum(s.get("tools", 0) for s in status.values())
            print(f"\033[90mMCP: {connected} servers, {tools} tools\033[0m", file=sys.stderr)
    except (ImportError, OSError, RuntimeError, asyncio.TimeoutError) as e:
        print(f"\033[90mMCP initialization skipped: {e}\033[0m", file=sys.stderr)


async def _run_with_mcp(coro):
    """Initialize MCP servers then run the given coroutine in the same event loop.

    Avoids the double-asyncio.run() bug where the first run tears down MCP connections.
    """
    await _initialize_mcp()
    try:
        return await coro
    finally:
        try:
            from app.mcp.manager import get_mcp_manager
            mcp_manager = get_mcp_manager()
            if mcp_manager and mcp_manager.is_initialized:
                await mcp_manager.shutdown()
        except Exception as e:  # noqa: BLE001 — best-effort during process exit
            logger.debug("MCP shutdown error during exit: %s", e)


async def _run_async_cli(cli):
    """Run CLI in async context with MCP initialized."""
    # Start MCP and any pending plugin policy enforcement as a background task
    # so the prompt appears immediately rather than waiting ~10s for servers to
    # connect and the registry to respond.
    async def _background_init(plugins_future):
        # Wait for plugin initialization thread to finish, then enforce policy.
        if plugins_future is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, plugins_future.result)
            _enforce_endpoint_policy()
        await _initialize_mcp()

    plugins_future = getattr(cli, '_plugins_future', None)
    cli._background_init_task = asyncio.create_task(
        _background_init(plugins_future)
    )

    # Install a custom SIGINT handler on the event loop so that ^C during
    # streaming cancels the active task gracefully instead of tearing down
    # the entire event loop (which is what the default KeyboardInterrupt
    # propagation through asyncio.run() does).
    loop = asyncio.get_running_loop()

    def _sigint_handler():
        if cli._active_task and not cli._active_task.done():
            # Mid-stream: request cancellation and cancel the task
            cli._cancellation_requested = True
            if getattr(cli, '_cancel_event', None) is not None:
                cli._cancel_event.set()
            cli._active_task.cancel()
            # Immediate user feedback — task.cancel() may take time to
            # propagate through shielded reads in the provider, and the
            # prompt_toolkit ^C binding only runs at the prompt, not
            # during streaming.  Without this print the user sees no
            # reaction until cancellation fully unwinds.
            try:
                sys.stdout.write("\n\033[33m^C - Cancelling...\033[0m\n")
                sys.stdout.flush()
            except Exception:  # noqa: BLE001 — stdout may be closed during signal handling
                pass  # Nothing can be done if stdout is broken
        elif getattr(cli, '_ask_task', None) and not cli._ask_task.done():
            # Non-streaming phase of an ask() (e.g. diff validation in a
            # worker thread, between model attempts).  _active_task is unset
            # here, but the operation is still running — cancel the whole
            # ask() so control returns to the prompt.
            cli._cancellation_requested = True
            cli._ask_task.cancel()
            try:
                sys.stdout.write("\n\033[33m^C - Cancelling...\033[0m\n")
                sys.stdout.flush()
            except Exception:  # noqa: BLE001 — stdout may be closed during signal handling
                pass  # Nothing can be done if stdout is broken
        else:
            # At the prompt the terminal is in raw mode, so SIGINT
            # won't fire — prompt_toolkit handles ^C as a character.
            # Nothing to do here; keep the handler installed to
            # prevent the default asyncio SIGINT teardown.
            pass

    try:
        loop.add_signal_handler(signal.SIGINT, _sigint_handler)
    except NotImplementedError:
        # Windows doesn't support add_signal_handler; fall back to
        # default behavior (KeyboardInterrupt).
        pass

    await cli.chat()


# ============================================================================
# Session factory — single source of truth for init → auth → CLI creation
# ============================================================================

def _enforce_endpoint_policy():
    """Enforce enterprise endpoint policy for CLI invocations."""
    if not ziya_env("ZIYA_ALLOW_ALL_ENDPOINTS"):
        try:
            from app.plugins import get_allowed_endpoints
            allowed = get_allowed_endpoints()
            endpoint = ziya_env("ZIYA_ENDPOINT")
            if allowed is not None and endpoint not in allowed:
                print(f"\n\033[31m✗ Policy Violation: Endpoint '{endpoint}' is not permitted.\033[0m", file=sys.stderr)
                print(f"\033[33mAllowed endpoints: {', '.join(allowed)}\033[0m\n", file=sys.stderr)
                sys.exit(1)
        except Exception as e:  # noqa: BLE001
            logger.debug("Plugin policy check failed (non-fatal): %s", e)

def _init_and_authenticate(args, *, skip_setup_env: bool = False):
    """Common initialisation: environment setup, plugin loading, and auth check.

    Exits the process with a clear error message if authentication fails.

    Args:
        args: Parsed CLI arguments.
        skip_setup_env: Set ``True`` if ``setup_env(args)`` was already called
            (e.g. when the handler needs env set up before an early-exit path).
    """
    if not skip_setup_env:
        setup_env(args)

    from app.plugins import initialize as initialize_plugins
    initialize_plugins()

    _enforce_endpoint_policy()

    profile = getattr(args, 'profile', None)
    _auth_ok, _auth_msg = _check_auth_quick(profile)
    if not _auth_ok:
        _print_auth_error(_auth_msg)
        sys.exit(1)


def _create_cli_session(args, files=None) -> 'CLI':
    """Perform full init, authenticate, resolve files, and return a CLI instance.

    This is the canonical entry point for command handlers that follow the
    standard setup_env → plugins → auth → resolve_files → CLI() sequence.
    """
    _init_and_authenticate(args)
    if files is None:
        root = ziya_env("ZIYA_USER_CODEBASE_DIR") or os.getcwd()
        files = resolve_files(args.files, root) if getattr(args, 'files', None) else []
    return CLI(files=files)


# ============================================================================
# Command handlers
# ============================================================================

def cmd_chat(args):
    """Handle: ziya chat [FILES...]"""
    # Environment setup must happen before anything else.
    setup_env(args)

    # Propagate ephemeral mode to the environment so the bead gates
    # (bead_tools._is_ephemeral_context, bead_prompt) and any other
    # env-based ephemeral checks see it — cli._ephemeral alone is
    # invisible to those code paths.
    if getattr(args, 'ephemeral', False):
        os.environ['ZIYA_EPHEMERAL_MODE'] = 'true'

    # Kick off plugin initialization in a background thread immediately so the
    # network calls inside internal_plugins.register() overlap with auth and
    # CLI setup rather than blocking them.
    import concurrent.futures
    _plugins_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="ziya-plugins")
    from app.plugins import initialize as initialize_plugins
    _plugins_future = _plugins_executor.submit(initialize_plugins)
    
    # Handle session resume (needs env + plugins but also needs auth)
    if getattr(args, 'resume', False):
        # Resume path: must wait for plugins before enforcing policy.
        _plugins_future.result()
        _enforce_endpoint_policy()
        # Authenticate on resume path.
        profile = getattr(args, 'profile', None)
        _auth_ok, _auth_msg = _check_auth_quick(profile)
        if not _auth_ok:
            _print_auth_error(_auth_msg)
            sys.exit(1)

        resume_arg = args.resume if isinstance(args.resume, str) else None
        if resume_arg:
            session_id = find_session_by_name(resume_arg)
            if not session_id:
                print(f"\033[33mNo session matching '{resume_arg}'\033[0m")
                sys.exit(1)
        else:
            session_id = asyncio.run(select_session())
        if session_id:
            try:
                session_data = load_session(session_id)
                files = session_data.get('files', [])
                history = session_data.get('history', [])
                
                cli = CLI(files=files)
                cli.history = history
                cli._session_start_time = session_data.get('start_time', session_data.get('timestamp'))
                cli._session_id = session_data.get('id', session_id)
                cli._session_name = session_data.get('name')
                cli._ephemeral = getattr(args, 'ephemeral', False)
                
                print(f"\033[32m✓ Resumed session from {session_data.get('timestamp', 'unknown')}\033[0m")
                print(f"  Files: {len(files)}, Messages: {len(history)}\n")
                
                _print_resumed_beads(cli.conversation_id)
                asyncio.run(_run_async_cli(cli))
                
                # Save session on exit (unless ephemeral)
                if not getattr(args, 'ephemeral', False):
                    save_session(cli)
                    print(f"\n\033[90mSession saved\033[0m")
                
                return
            except FileNotFoundError as e:
                print(f"\033[33m{e}\033[0m")
                print("\033[90mStarting new session instead\033[0m\n")
    
    # Normal (non-resume) path — skip setup_env/plugins (already ran above)
    # Auth check doesn't need plugins; policy enforcement is deferred to the
    # background init task inside _run_async_cli.
    _auth_ok, _auth_msg = _check_auth_quick(getattr(args, 'profile', None))
    if not _auth_ok:
        _print_auth_error(_auth_msg)
        # _plugins_executor's worker thread is non-daemon, so a bare
        # sys.exit() here would block on it during interpreter shutdown —
        # letting initialize_plugins()'s app.server import (and the AST
        # indexing side effect it triggers) run to completion anyway.
        _plugins_executor.shutdown(wait=False, cancel_futures=True)
        os._exit(1)
    
    root = ziya_env("ZIYA_USER_CODEBASE_DIR") or os.getcwd()
    files = resolve_files(args.files, root) if args.files else []
    cli = CLI(files=files)
    cli._plugins_future = _plugins_future
    cli._ephemeral = getattr(args, 'ephemeral', False)
    cli._pending_join = getattr(args, 'join', False)
    asyncio.run(_run_async_cli(cli))
    
    if not getattr(args, 'ephemeral', False):
        save_session(cli)
        print(f"\n\033[90mSession saved\033[0m")


def cmd_ask(args):
    """Handle: ziya ask "question" [FILES...]"""
    cli = _create_cli_session(args)
    
    # Build question from args and stdin
    question = args.question
    
    # Check for piped input
    stdin_content = read_stdin_if_available()
    if stdin_content:
        question = f"{question}\n\n```\n{stdin_content}\n```" if question else stdin_content
    
    if not question:
        print("Error: No question provided", file=sys.stderr)
        sys.exit(1)
    
    asyncio.run(_run_with_mcp(cli.ask(question, stream=not args.no_stream)))


def cmd_review(args):
    """Handle: ziya review [FILES...] [--staged]"""
    cli = _create_cli_session(args)
    print_chat_startup_info(args)
    
    root = ziya_env("ZIYA_USER_CODEBASE_DIR") or os.getcwd()
    
    # Get content to review
    content = None
    
    if args.staged:
        content = get_git_staged_diff()
        if not content:
            print("No staged changes to review", file=sys.stderr)
            sys.exit(1)
    elif args.diff:
        content = get_git_diff()
        if not content:
            print("No changes to review", file=sys.stderr)
            sys.exit(1)
    else:
        # Check stdin first
        content = read_stdin_if_available()
    
    prompt = args.prompt or "Review this code. Focus on bugs, security issues, and improvements."
    
    if content:
        question = f"{prompt}\n\n```\n{content}\n```"
    else:
        question = prompt
    
    asyncio.run(_run_with_mcp(cli.ask(question, stream=not args.no_stream)))


def cmd_explain(args):
    """Handle: ziya explain [FILES...]"""
    cli = _create_cli_session(args)
    
    content = read_stdin_if_available()
    prompt = args.prompt or "Explain this code clearly and concisely."
    
    if content:
        question = f"{prompt}\n\n```\n{content}\n```"
    else:
        question = prompt
    
    asyncio.run(_run_with_mcp(cli.ask(question, stream=not args.no_stream)))


def _task_escalation_badge(signed) -> str:
    """Return the colored escalation/approval badge for a ``ziya task --list``
    row. ``signed`` is None for a floor-only task (no escalation → no badge),
    True for an approved escalation (``[⚡ signed]``), False for an unapproved
    one (``[🔒 unsigned]``, which runs at the floor). Pure/UI-only; the
    signed flag itself comes from the shared scope_audit walk so this view
    cannot disagree with the runtime gate."""
    if signed is None:
        return ""
    if signed:
        return "  \033[32m[⚡ signed]\033[0m"
    return "  \033[33m[🔒 unsigned]\033[0m"


def cmd_task(args):
    """Handle: ziya task <name> [--list] [--show TASK]"""
    from app.task_runner import (
        load_tasks, validate_task_allow,
        apply_task_permissions, restore_permissions, resolve_task_source_file,
    )
    setup_env(args)  # needed for root dir before --list/--show early exits
    setup_env(args)
    root = ziya_env("ZIYA_USER_CODEBASE_DIR") or os.getcwd()

    # ── Task-card surface (library face) ──────────────────────────────
    # A card authored/edited/tested in the GUI deck runs in-process here
    # via the shared block_executor engine — no web server required.
    # Escalation is gated inside execute_task_block via authorize_scope
    # (block.id) against the SAME signed ledger the GUI writes, so a
    # GUI-approved card is already authorized from the command line.
    if getattr(args, 'list_cards', False):
        # Plugins must load first: the encryption provider they register is
        # what derives the KEK to decrypt the GUI-written project.json /
        # card files.  Without it, reads of encrypted stores fail with
        # "'NoneType' object has no attribute 'get_dek'".  Listing needs no
        # auth (no model call), so initialize_plugins() alone suffices.
        from app.plugins import initialize as initialize_plugins
        initialize_plugins()
        from app.cli_card_runner import list_cards
        sys.exit(list_cards(root))

    if getattr(args, 'card', None):
        from app.cli_card_runner import run_card
        # Full init for actual execution (setup_env already called above).
        _init_and_authenticate(args, skip_setup_env=True)
        rc = asyncio.run(_run_with_mcp(
            run_card(root, args.card, stream=not args.no_stream)))
        sys.exit(rc)

    tasks = load_tasks(root)

    # --list: print available tasks and exit
    if getattr(args, 'list_tasks', False):
        if not tasks:
            print("No tasks defined.")
            print("Create ~/.ziya/tasks.yaml or .ziya/tasks.yaml")
            return
        # Escalation/approval status per task (ASR F-001, surface A). Reuse the
        # SAME audit walk ziya-approve --list uses, keyed by task name, so the
        # --list view can't disagree with what actually runs or with the audit.
        # collect_cli_entries returns ONLY escalating tasks (floor-only tasks
        # carry no allow block and are absent), so a name's presence here means
        # "escalates"; its .signed flag means "approved".
        try:
            from app.utils.scope_audit import collect_cli_entries
            audit_by_name = {e.label: e.signed for e in collect_cli_entries(root)}
        except Exception:  # noqa: BLE001 — status is advisory; never block --list
            audit_by_name = {}
        max_name = max(len(n) for n in tasks)
        print(f"\033[1mAvailable tasks:\033[0m\n")
        for name in sorted(tasks):
            desc = tasks[name].get("description", "")
            badge = _task_escalation_badge(audit_by_name.get(name))
            print(f"  \033[36m{name:<{max_name}}\033[0m  {desc}{badge}")
        if any(signed is False for signed in audit_by_name.values()):
            print(f"\n\033[33m🔒 Unsigned tasks request escalated permissions "
                  f"but run at the default floor until approved.\033[0m")
            print(f"   Approve: \033[36msudo ziya-approve --cli-task <name> "
                  f"--root {root}\033[0m   "
                  f"Audit all: \033[36mziya-approve --list\033[0m")
        print(f"\nRun: ziya task <name>")
        return

    # --show: print task prompt and exit
    if getattr(args, 'show', None):
        task_name = args.show
        if task_name not in tasks:
            print(f"\033[31mUnknown task: {task_name}\033[0m", file=sys.stderr)
            sys.exit(1)
        task = tasks[task_name]
        print(f"\033[1m{task_name}\033[0m: {task.get('description', '')}\n")
        print(task.get("prompt", "(no prompt)"))
        return

    # Running a task requires a name
    task_name = getattr(args, 'task_name', None)
    if not task_name:
        args.list_tasks = True
        cmd_task(args)
        return

    if task_name not in tasks:
        print(f"\033[31mUnknown task: {task_name}\033[0m", file=sys.stderr)
        print(f"Run \033[36mziya task --list\033[0m to see available tasks.", file=sys.stderr)
        sys.exit(1)

    task_def = tasks[task_name]

    # Validate and apply escalated permissions BEFORE MCP init
    errors = validate_task_allow(task_def)
    if errors:
        print(f"\033[31mTask '{task_name}' has invalid allow block:\033[0m", file=sys.stderr)
        for err in errors:
            print(f"  • {err}", file=sys.stderr)
        sys.exit(1)

    # Task-scope authorization gate (ASR F-001, design §4.2/§6). A CLI task's
    # ``allow`` escalation takes effect only if a signed approval record matches
    # its current hash. Unauthorized -> strip the allow block so
    # apply_task_permissions writes NO escalation env (the task still runs, at
    # the floor). This routes the CLI path through the SAME signed ledger as
    # cards — the agent cannot mint an approval (root key). Gating here (intent)
    # rather than relying on the env-signature gate avoids the merged-delta
    # hazard: apply_task_permissions merges the task's commands with ambient
    # ALLOW_COMMANDS, so a signature over the task's own delta would fail to
    # verify whenever the task runs alongside any other escalation.
    if task_def.get("allow"):
        from app.utils.scope_approvals import cli_task_key, is_cli_task_authorized
        src = resolve_task_source_file(task_name, root)
        task_key = cli_task_key(str(src), task_name) if src else f"cli:?#{task_name}"
        if not is_cli_task_authorized(task_key, task_def.get("allow")):
            print(
                f"\033[33m🔒 Task '{task_name}' requests escalated permissions "
                f"that are not approved — running at the default floor.\033[0m\n"
                f"   To approve: \033[36msudo ziya-approve --cli-task {task_name} "
                f"--root {root}\033[0m",
                file=sys.stderr,
            )
            task_def = {**task_def, "allow": None}

    saved_env = apply_task_permissions(task_def)
    if saved_env:
        allow = task_def.get("allow", {})
        parts = []
        if allow.get("commands"):
            parts.append(f"commands: {', '.join(allow['commands'])}")
        if allow.get("git_operations"):
            parts.append(f"git: {', '.join(allow['git_operations'])}")
        if allow.get("write_patterns"):
            parts.append(f"write: {', '.join(allow['write_patterns'])}")
        print(f"\033[33m⚡ Escalated permissions: {'; '.join(parts)}\033[0m", file=sys.stderr)

    # Mechanism B — route the AUTHORIZED escalation through the _task_scope
    # contextvars. apply_task_permissions (above) writes the escalation into
    # the env, but the shell subprocess's F-004 signature gate clamps any
    # unsigned env escalation back to the floor — and a CLI-task approval is a
    # scope_approvals/ record, not a ZIYA_SCOPE_SIG env signature. The
    # _task_scope envelope (built from these contextvars in tool_execution and
    # consulted additively by the shell server AFTER the clamp) is the path
    # that actually delivers the signed grant. task_def['allow'] is already
    # None here when unauthorized (stripped by the gate above), so an
    # unapproved task sets no grants and runs at the floor.
    from app.task_runner import allow_to_task_scope
    from app.context import (
        set_task_shell_commands, reset_task_shell_commands,
        set_task_writable_paths, reset_task_writable_paths,
    )
    _scope_cmds, _scope_writable = allow_to_task_scope(task_def.get("allow"))
    _sc_token = set_task_shell_commands(_scope_cmds or None)
    _wp_token = set_task_writable_paths(_scope_writable or None)

    try:
        # Full init for actual task execution (setup_env already called above)
        _init_and_authenticate(args, skip_setup_env=True)

        cli = CLI(files=[])  # Tasks don't use file context
        # Unattended batch work: no human is waiting at a prompt, so use the
        # full ZIYA_MAX_TOOL_ITERATIONS budget rather than the interactive one.
        cli._interactive = False
        asyncio.run(_run_with_mcp(cli.ask(task_def["prompt"], stream=not args.no_stream)))
    finally:
        reset_task_shell_commands(_sc_token)
        reset_task_writable_paths(_wp_token)
        restore_permissions(saved_env)

# ============================================================================
# Auth helpers
# ============================================================================

def _check_auth_quick(profile: str = None) -> tuple:
    """Quick check if authentication is likely to work.

    Returns (valid, message) — message is the diagnostic detail from
    check_aws_credentials() (distinguishing expired creds from network/
    outage failures) when available, else None.
    """
    endpoint = ziya_env("ZIYA_ENDPOINT")
    
    if endpoint == "bedrock":
        try:
            from app.utils.aws_utils import check_aws_credentials
            # Pass profile explicitly to ensure it's used
            valid, message = check_aws_credentials(profile_name=profile)
            return valid, message
        except (ImportError, OSError, RuntimeError, ValueError):
            return False, None
    elif endpoint == "google":
        return bool(os.environ.get("GOOGLE_API_KEY")), None
    elif endpoint == "openai":
        return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_BASE_URL")), None
    elif endpoint == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY")), None
    
    return True, None


def _print_auth_error(message: str = None):
    """Print authentication error with helpful instructions.

    Args:
        message: Optional diagnostic detail from check_aws_credentials().
            When it indicates a network/connectivity failure (as opposed to
            missing/expired credentials), a distinct message is shown so
            users aren't told to re-authenticate during an AWS-side outage.
    """
    endpoint = ziya_env("ZIYA_ENDPOINT")
    is_network_error = bool(message) and "NETWORK ERROR" in message
    
    print("\n\033[31m✗ Authentication failed\033[0m\n", file=sys.stderr)
    
    if endpoint == "bedrock" and is_network_error:
        print("Could not reach AWS to verify credentials — this looks like a network", file=sys.stderr)
        print("or service issue, not expired/missing credentials.\n", file=sys.stderr)
        print(f"\033[33mDetail:\033[0m {message}\n", file=sys.stderr)
        print("If this persists, check AWS service health before re-authenticating.", file=sys.stderr)
    elif endpoint == "bedrock":
        print("Your AWS credentials are missing or expired.\n", file=sys.stderr)
        print("\033[33mTo fix:\033[0m", file=sys.stderr)
        print("  aws sso login --profile <your-profile>", file=sys.stderr)
        print("  # or", file=sys.stderr)
        print("  export AWS_PROFILE=<your-profile>", file=sys.stderr)
        print("  # or", file=sys.stderr)
        print("  aws configure", file=sys.stderr)
    elif endpoint == "google":
        print("GOOGLE_API_KEY environment variable is not set.\n", file=sys.stderr)
        print("\033[33mTo fix:\033[0m", file=sys.stderr)
        print("  export GOOGLE_API_KEY=<your-api-key>", file=sys.stderr)
    elif endpoint == "openai":
        print("OPENAI_API_KEY environment variable is not set.\n", file=sys.stderr)
        print("\033[33mTo fix:\033[0m", file=sys.stderr)
        print("  export OPENAI_API_KEY=sk-...", file=sys.stderr)
    elif endpoint == "anthropic":
        print("ANTHROPIC_API_KEY environment variable is not set.\n", file=sys.stderr)
        print("\033[33mTo fix:\033[0m", file=sys.stderr)
        print("  export ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
        print("  # or for a compatible local server:", file=sys.stderr)
        print("  export OPENAI_BASE_URL=http://localhost:8080/v1", file=sys.stderr)
    
    print(file=sys.stderr)


# ============================================================================
# Argument parsing
# ============================================================================

def create_parser():
    """Create the CLI argument parser."""
    from app.config.common_args import add_common_arguments
    
    parser = argparse.ArgumentParser(
        prog='ziya',
        description='Ziya AI coding assistant',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ziya chat                        Interactive chat
  ziya chat src/                   Chat with src/ in context  
  ziya ask "what does this do?" main.py
  ziya explain utils.py
  ziya review --staged             Review staged git changes
  git diff | ziya review           Review piped diff
  cat error.log | ziya ask "what's wrong?"
        """
    )
    
    # Create a parent parser with common arguments (add_help=False prevents conflict)
    common_parent = argparse.ArgumentParser(add_help=False)
    add_common_arguments(common_parent)
    
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # chat
    chat_parser = subparsers.add_parser('chat', parents=[common_parent], help='Interactive chat')
    chat_parser.add_argument('files', nargs='*', help='Files/directories for context')
    chat_parser.add_argument('--resume', nargs='?', const=True, default=False, metavar='NAME',
                             help='Resume a session; optional NAME/id to skip the picker')
    chat_parser.add_argument('--join', nargs='?', const=True, default=False, metavar='NAME',
                             help='Join a live GUI conversation; optional NAME/id to skip the picker')
    chat_parser.add_argument('--ephemeral', action='store_true', help='Do not save session history')
    chat_parser.set_defaults(func=cmd_chat)
    
    # ask
    ask_parser = subparsers.add_parser('ask', parents=[common_parent], help='Ask a question')
    ask_parser.add_argument('question', nargs='?', help='Question to ask')
    ask_parser.add_argument('files', nargs='*', help='Files for context')
    ask_parser.set_defaults(func=cmd_ask)
    review_parser = subparsers.add_parser('review', parents=[common_parent], help='Review code')
    review_parser.add_argument('files', nargs='*', help='Files to review')
    review_parser.add_argument('--staged', '-s', action='store_true', help='Review staged git changes')
    review_parser.add_argument('--diff', '-d', action='store_true', help='Review unstaged git changes')
    review_parser.add_argument('--prompt', '-p', help='Custom review prompt')
    review_parser.set_defaults(func=cmd_review)
    
    # explain
    explain_parser = subparsers.add_parser('explain', parents=[common_parent], help='Explain code')
    explain_parser.add_argument('files', nargs='*', help='Files to explain')
    explain_parser.add_argument('--prompt', '-p', help='Custom prompt')
    explain_parser.set_defaults(func=cmd_explain)
    
    # task
    task_parser = subparsers.add_parser('task', parents=[common_parent],
                                        help='Run a named task prompt')
    task_parser.add_argument('task_name', nargs='?', help='Task to run')
    task_parser.add_argument('--list', '-l', action='store_true',
                             dest='list_tasks', help='List available tasks')
    task_parser.add_argument('--card', metavar='ID',
                             help='Run a GUI task card by id or name (in-process)')
    task_parser.add_argument('--list-cards', action='store_true',
                             dest='list_cards', help='List available task cards')
    task_parser.add_argument('--show', metavar='TASK',
                             help='Show the prompt for a task')
    task_parser.set_defaults(func=cmd_task)

    return parser
    
    


def main():
    """CLI entry point."""
    parser = create_parser()
    
    # Save current terminal title and set ours (xterm title stack push/pop)
    sys.stdout.write("\033[22;0t")
    sys.stdout.write("\033]0;Ziya Chat\007")
    sys.stdout.flush()
    
    # Pre-process argv to support flags both before and after subcommand
    # e.g., "ziya --profile x chat" -> "ziya chat --profile x"
    argv = sys.argv[1:]
    commands = {'chat', 'ask', 'review', 'explain', 'task'}
    global_flags = {'--model', '-m', '--profile', '--region', '--root', '--no-stream', '--debug'}
    
    # Find command position
    cmd_idx = None
    for i, arg in enumerate(argv):
        if arg in commands:
            cmd_idx = i
            break
    
    # If command found and there are flags before it, move them after
    if cmd_idx is not None and cmd_idx > 0:
        pre_cmd = argv[:cmd_idx]
        cmd = argv[cmd_idx]
        post_cmd = argv[cmd_idx + 1:]
        
        # Separate flags from non-flags in pre_cmd section
        flags_to_move = []
        i = 0
        while i < len(pre_cmd):
            arg = pre_cmd[i]
            if arg in global_flags or arg.startswith('--'):
                flags_to_move.append(arg)
                # Check if next arg is a value (not a flag)
                if i + 1 < len(pre_cmd) and not pre_cmd[i + 1].startswith('-'):
                    flags_to_move.append(pre_cmd[i + 1])
                    i += 1
            i += 1
        
        # Reconstruct argv with flags after command
        argv = [cmd] + post_cmd + flags_to_move
    
    args = parser.parse_args(argv)
    
    if args.command is None:
        # No command - show help
        parser.print_help()
        sys.exit(0)
    
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.stdout.write("\033[23;0t")
        sys.stdout.flush()
        print()
        sys.exit(0)
    except Exception as e:  # Intentionally broad: top-level CLI error handler
        # Extract traceback info for better error reporting
        tb = traceback.extract_tb(sys.exc_info()[2])
        sys.stdout.write("\033[23;0t")
        sys.stdout.flush()
        if tb:
            last_frame = tb[-1]
            location = f"{last_frame.filename}:{last_frame.lineno}"
            print(f"\033[31mError in {location}: {e}\033[0m", file=sys.stderr)
            if ziya_env('ZIYA_LOG_LEVEL') == 'DEBUG':
                print("\nFull traceback:", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
        else:
            print(f"\033[31mError: {e}\033[0m", file=sys.stderr)
        sys.stdout.write("\033[23;0t")
        sys.stdout.flush()
        sys.exit(1)


    # Restore terminal title on clean exit
    sys.stdout.write("\033[23;0t")
    sys.stdout.flush()

if __name__ == '__main__':
    main()
