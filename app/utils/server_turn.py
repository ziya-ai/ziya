"""Server-started chat turns: a turn that begins, runs and is saved with no
browser involved (design/conversation-post.md, slice 2; the first piece of
"Turn authorship" in design/conversation-single-writer.md).

A browser-started turn gets its history, files and model from the request
body, and the browser saves the assistant message.  Here:

* the request body is built from the conversation's RECORD
  (``body_from_record``): non-muted history with scope notes and peer posts
  folded exactly as ``/api/chat`` folds them, the owning project's root, the
  record's model pin (conversation, then folder, then project);
* the turn runs through ``chat_turn_relay.start_turn`` like any other, marked
  ``server_authored`` so it is never cancelled for lack of a viewer and so a
  browser does not reattach and save a second copy of the answer;
* ``ServerTurnWriter`` wraps the frame stream, assembles the assistant
  message (``TurnAssembler``) and writes it to the record: a checkpoint at
  most every few seconds and after each tool result (``turnStatus:
  streaming``), then the final message with how the turn ended.

The assembler is deliberately small.  It reproduces the parts of the
browser's handling that decide what the message SAYS (text, tool blocks in
the four-backtick ``tool:NAME|HEADER|SYNTAX`` fence that MarkdownRenderer
and tool_history_rewrite both read, the two rewind forms).  It does not
reproduce display-only formatting (hierarchical search results, inline
rendered images, parallel-group containers, hallucination tail-stripping).

The turn runs in an empty ``contextvars`` context: it is started from inside
another conversation's tool call, and that turn's project root, conversation
id and task grants must not leak into this one.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import re
import threading
import time
import uuid
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger

CHECKPOINT_SECONDS = 5.0
TOOL_BODY_MAX_CHARS = 10_000          # what the browser's formatter keeps

_HUMAN = ("human", "user")
_ASSISTANT = ("assistant", "ai")
_REWIND_COMMENT_RE = re.compile(r"<!-- REWIND_MARKER:[\s\S]*?-->\n*")
_BACKTICK_RUN_RE = re.compile(r"^(`{4,})", re.MULTILINE)


class ServerTurnError(RuntimeError):
    """A server-started turn could not be started."""


# ── assembling the assistant message ──────────────────────────────────────

def _parse(frame: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(frame, str) or not frame.startswith("data: "):
        return None
    try:
        obj = json.loads(frame[6:])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def render_tool_block(start: Dict[str, Any], result: Dict[str, Any], content_so_far: str) -> str:
    """One tool call as the fence the transcript and the history rewrite read.

    The server twin of the default branch of renderToolPart
    (frontend/src/utils/renderToolPart.ts): same name normalisation, header
    fallback chain, shell header and ``$ command`` first line, lock mark.
    """
    name = str(result.get("tool_name") or start.get("tool_name") or "tool")
    if not name.startswith("mcp_"):
        name = f"mcp_{name}"
    name = re.sub(r"^mcp_mcp_", "mcp_", name)
    bare = name[len("mcp_"):]
    args = start.get("args") if isinstance(start.get("args"), dict) else {}

    header = start.get("display_header") or result.get("display_header") \
        or bare.replace("_", " ").title()
    body = result.get("result")
    if not isinstance(body, str):
        try:
            body = json.dumps(body, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            body = str(body)
    if bare == "run_shell_command" and args.get("command"):
        cmd = str(args["command"])
        header = f"Shell: {cmd[:47] + '...' if len(cmd) > 50 else cmd}"
        if not body.startswith("$ "):
            body = f"$ {cmd}\n{body}"
    header = re.sub(r"\s+", " ", str(header)).replace("`", "'").strip()
    if result.get("verified") is True:
        header = f"🔐 {header}"
    if len(body) > TOOL_BODY_MAX_CHARS:
        body = (body[:TOOL_BODY_MAX_CHARS]
                + f"\n… ({len(body) - TOOL_BODY_MAX_CHARS} more characters not saved)")
    syntax = str(result.get("syntax") or start.get("syntax") or "text").split("|")[0] or "text"
    # A body line that opens with four or more backticks would close a
    # four-backtick fence; use one longer than the longest such run.
    longest = max((len(m.group(1)) for m in _BACKTICK_RUN_RE.finditer(body)), default=3)
    fence = "`" * max(4, longest + 1)
    lead = "\n" if content_so_far.endswith("\n\n") else "\n\n"
    return f"{lead}{fence}tool:{name}|{header}|{syntax}\n{body}\n{fence}\n\n"


class TurnAssembler:
    """Feed it the turn's SSE frames; ``content`` is the assistant message."""

    def __init__(self) -> None:
        self.content = ""
        self.error: Optional[str] = None
        self._starts: Dict[str, Dict[str, Any]] = {}

    def _append_text(self, text: Any) -> bool:
        if not isinstance(text, str) or not text:
            return False
        text = _REWIND_COMMENT_RE.sub("", text)
        self.content += text
        return bool(text)

    def feed(self, frame: Any) -> Optional[str]:
        """Apply one frame.  Returns 'text', 'tool' or 'rewind' when the
        content changed, else None."""
        ev = _parse(frame)
        if ev is None:
            return None
        kind = ev.get("type")
        if kind == "rewind" and ev.get("to_marker"):
            marker = f"<!-- REWIND_MARKER: {ev['to_marker']}"
            lines = self.content.split("\n")
            at = next((i for i, ln in enumerate(lines) if marker in ln), None)
            if at is None:
                return None            # as the browser: marker gone, frame dropped
            self.content = "\n".join(lines[:at])
            self._append_text(ev.get("content"))
            return "rewind"
        if kind == "continuation_rewind":
            target = ev.get("rewind_line")
            lines = self.content.split("\n")
            if isinstance(target, int) and not isinstance(target, bool) and 0 <= target <= len(lines):
                self.content = "\n".join(lines[:target])
                return "rewind"
            return None
        if isinstance(ev.get("tool_start"), dict):
            st = ev["tool_start"]
            if st.get("tool_id"):
                self._starts[str(st["tool_id"])] = st
            return None
        if isinstance(ev.get("tool_result"), dict):
            res = ev["tool_result"]
            start = self._starts.get(str(res.get("tool_id") or ""), {})
            if start.get("is_internal"):
                return None
            self.content += render_tool_block(start, res, self.content)
            return "tool"
        if "error" in ev and ev.get("error"):
            self.error = str(ev["error"])[:500]
            return None
        if kind == "validation_retry":
            return "text" if self._append_text(ev.get("content")) else None
        if kind is None and "content" in ev:
            return "text" if self._append_text(ev.get("content")) else None
        return None


# ── the request body, from the record ─────────────────────────────────────

def history_from_record(raw: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    """``(question, chat_history)`` in the shape ``stream_chunks`` takes.

    Muted messages are left out.  Scope notes and peer posts are folded the
    way ``/api/chat`` folds them.  The last surviving entry must be
    human-role: it is what the turn answers.
    """
    from app.utils.chat_post import fold_peer_posts
    from app.utils.project_change_note import fold_project_change_notes
    entries = [
        {"role": m.get("role"), "content": m.get("content") if isinstance(m.get("content"), str) else "",
         "images": m.get("images"), "timestamp": m.get("timestamp") or m.get("_timestamp")}
        for m in (raw.get("messages") or [])
        if isinstance(m, dict) and not m.get("muted")
    ]
    entries = fold_peer_posts(fold_project_change_notes(entries))
    turns: List[Dict[str, Any]] = []
    for e in entries:
        role = e.get("role")
        if role not in _HUMAN and role not in _ASSISTANT:
            continue
        if not e.get("content") and not e.get("images"):
            continue
        turns.append({"type": "human" if role in _HUMAN else "ai", "content": e.get("content") or "",
                      "images": e.get("images"), "_timestamp": e.get("timestamp")})
    if not turns or turns[-1]["type"] != "human" or not turns[-1]["content"].strip():
        raise ServerTurnError("The conversation has nothing waiting for an answer.")
    return turns[-1]["content"], turns[:-1]


def body_from_record(raw: Dict[str, Any], project_root: Optional[str],
                     model_pin: Optional[str] = None) -> Dict[str, Any]:
    """The ``stream_chunks`` body for a turn nobody's browser is sending.

    ``files`` is empty: the window's file selection is browser state.  Files
    the model pinned (``additionalFiles``) are merged by the prompt builder
    from the record, as on any turn.  ``hold_for_diff_apply`` is off: with
    nobody at the Apply buttons a diff-apply hold would never end.
    """
    cid = raw.get("id")
    question, history = history_from_record(raw)
    return {
        "question": question,
        "conversation_id": cid,
        "chat_history": history,
        "files": [],
        "config": {
            "conversation_id": cid,
            "files": [],
            "systemPromptAddition": raw.get("additionalPrompt") or "",
            "project_root": project_root,
            "modelOverrides": {},
            "preferredToolIds": [],
            "modelSelection": {"model": model_pin} if model_pin else {},
            "hold_for_diff_apply": False,
            "server_started": True,
        },
    }


def model_pin_for(raw: Dict[str, Any], project: Any, project_id: str) -> Optional[str]:
    """Conversation pin, else its folder's, else the project's."""
    pin = raw.get("modelPreference")
    if not pin and raw.get("groupId"):
        try:
            from app.storage.groups import ChatGroupStorage
            from app.utils.paths import get_project_dir
            group = ChatGroupStorage(get_project_dir(project_id)).get(raw["groupId"])
            pin = getattr(group, "modelPreference", None)
        except Exception as e:  # noqa: BLE001 -- a pin lookup must not stop the turn
            logger.debug(f"server turn: folder pin lookup failed: {e}")
    if not pin:
        pin = getattr(getattr(project, "settings", None), "modelPreference", None)
    return pin or None


def _resolve_model(pin: Optional[str]) -> Tuple[Optional[str], Dict[str, str]]:
    """Validate the pin and confirm the model can stream.  Mirrors the
    checks at the top of ``/api/chat``."""
    from app.agents.models import ModelManager
    from app.config.env_registry import ziya_env
    endpoint = ziya_env("ZIYA_ENDPOINT")
    route = endpoint
    if pin:
        from app.utils.model_override import resolve_override_endpoint, validate_model_override
        err = validate_model_override(pin, endpoint)
        if err:
            logger.warning(f"server turn: dropping model pin {pin!r}: {err}")
            pin = None
        else:
            route = resolve_override_endpoint(pin) or endpoint
    model = pin or ModelManager.get_model_alias()
    cfg = ModelManager.get_model_config(route, model) if model else {}
    from app.providers.factory import is_endpoint_supported
    if not is_endpoint_supported(route, cfg):
        raise ServerTurnError(f"No streaming handler for model {model!r}.")
    return pin, {"X-Ziya-Model": str(model or ""),
                 "X-Ziya-Model-Source": "pin" if pin else "global"}


# ── writing the assistant message ─────────────────────────────────────────

class ServerTurnWriter:
    """Tees a turn's frames into the conversation record."""

    def __init__(self, project_id: str, conversation_id: str,
                 trigger: Optional[Dict[str, Any]] = None) -> None:
        self.project_id = project_id
        self.conversation_id = conversation_id
        self.trigger = dict(trigger or {})
        self.message_id = str(uuid.uuid4())
        self.turn_id: Optional[str] = None
        self.asm = TurnAssembler()
        self._written = False
        self._seq = 0
        self._saved_seq = 0
        self._mutex = threading.Lock()
        self._inflight: Optional[asyncio.Task] = None
        self._last_checkpoint = 0.0
        self._force_pending = False
        self._closing = False

    # -- disk (sync; runs in a worker thread) ------------------------------

    def _persist(self, seq: int, content: str, status: str, error: Optional[str]) -> None:
        from app.storage.chats import ChatStorage
        from app.utils.chat_message_ops import SERVER_AUTHORED_FIELD, chat_record_lock
        from app.utils.paths import get_project_dir
        final = status != "streaming"
        try:
            with self._mutex:
                if seq <= self._saved_seq:
                    return                      # an older snapshot finishing late
                self._saved_seq = seq
                if not content.strip():
                    if not (final and error):
                        return
                    content = f"⚠️ This turn ended with an error: {error}"
                storage = ChatStorage(get_project_dir(self.project_id))
                with chat_record_lock(storage):
                    path = storage._chat_file(self.conversation_id)
                    raw = storage._read_json(path)
                    if not raw:
                        return
                    msgs = raw.setdefault("messages", [])
                    at = next((i for i, m in enumerate(msgs)
                               if isinstance(m, dict) and m.get("id") == self.message_id), None)
                    now = int(time.time() * 1000)
                    if at is None:
                        if self._written:
                            return              # removed since (delete / rewind): stay removed
                        msgs.append({
                            "id": self.message_id, "role": "assistant", "content": content,
                            "timestamp": now, SERVER_AUTHORED_FIELD: True, "turnStatus": status,
                            "serverTurn": {"turnId": self.turn_id, **self.trigger},
                        })
                    else:
                        msgs[at] = {**msgs[at], "content": content, "turnStatus": status}
                        msgs[at].setdefault("serverTurn", {})["turnId"] = self.turn_id
                    if final and error:
                        msgs[at if at is not None else -1]["turnError"] = error
                    raw["lastActiveAt"] = now
                    raw["_version"] = max(now, int(raw.get("_version") or 0) + 1)
                    if final:
                        raw["hasUnreadResponse"] = True
                    storage._write_json(path, raw)
                self._written = True
        except Exception as exc:  # noqa: BLE001 -- persistence must never break the turn
            logger.warning(f"server turn: could not save message for "
                           f"{self.conversation_id[:8]}: {exc!r}")

    # -- loop side ---------------------------------------------------------

    def _snapshot(self, status: str) -> Tuple[int, str, str, Optional[str]]:
        self._seq += 1
        return self._seq, self.asm.content, status, self.asm.error

    def _checkpoint(self, force: bool = False) -> None:
        if self._closing:
            return
        if self._inflight is not None and not self._inflight.done():
            # One write at a time.  A forced request (a tool result, after
            # which the turn may sit silent for the length of the next tool
            # call) is remembered and runs when this write ends.
            self._force_pending = self._force_pending or force
            return
        now = time.monotonic()
        if not force and now - self._last_checkpoint < CHECKPOINT_SECONDS:
            return
        self._last_checkpoint = now
        self._force_pending = False
        self._inflight = asyncio.create_task(
            asyncio.to_thread(self._persist, *self._snapshot("streaming")))
        self._inflight.add_done_callback(self._after_checkpoint)

    def _after_checkpoint(self, _task: "asyncio.Task") -> None:
        if self._force_pending and not self._closing:
            self._checkpoint(force=True)

    def _end_status(self, how: str) -> str:
        if how == "complete":
            return "error" if self.asm.error else "complete"
        if how == "error":
            return "error"
        try:
            from app.agents import chat_turn_relay
            st = chat_turn_relay.status(self.conversation_id) or {}
        except Exception:  # noqa: BLE001
            st = {}
        if self.turn_id and st.get("turn_id") and st["turn_id"] != self.turn_id:
            return "superseded"
        if st.get("end_reason") == "superseded":
            return "superseded"
        if st.get("end_reason") == "server_shutdown":
            return "interrupted"
        return "stopped"

    async def _finish(self, how: str) -> None:
        self._closing = True
        snap = self._snapshot(self._end_status(how))
        try:
            await asyncio.shield(asyncio.to_thread(self._persist, *snap))
        except asyncio.CancelledError:
            # Cancelled a second time while saving.  The shielded write is
            # still running; do not start another.
            pass

    async def wrap(self, source: AsyncIterator[str]) -> AsyncIterator[str]:
        """Yield ``source``'s frames unchanged, saving as they pass."""
        how = "complete"
        try:
            async for frame in source:
                changed = self.asm.feed(frame)
                if changed:
                    self._checkpoint(force=changed == "tool")
                yield frame
        except (asyncio.CancelledError, GeneratorExit):
            how = "cancelled"
            raise
        except Exception as exc:
            how = "error"
            self.asm.error = self.asm.error or f"{type(exc).__name__}: {exc}"[:500]
            raise
        finally:
            if hasattr(source, "aclose"):
                try:
                    await source.aclose()  # type: ignore[union-attr]
                except Exception:  # noqa: BLE001
                    pass
            await self._finish(how)


# ── starting one ──────────────────────────────────────────────────────────

def _load_record(conversation_id: str) -> Tuple[str, Dict[str, Any], Any]:
    from app.mcp.tools.chat_history_tools import _load_chat
    from app.storage.projects import ProjectStorage
    from app.utils.paths import get_ziya_home
    home = get_ziya_home()
    loaded = _load_chat(home, conversation_id)
    if loaded is None:
        raise ServerTurnError(f"No conversation found with id {conversation_id!r}.")
    project_id, raw = loaded
    return project_id, raw, ProjectStorage(home).get(project_id)


def _default_stream(body: Dict[str, Any]) -> AsyncIterator[str]:
    from app.server import stream_chunks      # lazy: app.server imports widely
    return stream_chunks(body)


async def start_server_turn(
    conversation_id: str,
    trigger: Optional[Dict[str, Any]] = None,
    *,
    stream_factory: Callable[[Dict[str, Any]], AsyncIterator[str]] = _default_stream,
    resolve_model: Callable[[Optional[str]], Tuple[Optional[str], Dict[str, str]]] = _resolve_model,
) -> str:
    """Run a turn for ``conversation_id`` from its record and save the
    answer to it.  Returns the relay turn id once the turn is running.

    ``trigger`` is recorded on the saved message (``serverTurn``), e.g.
    ``{"kind": "peer_post", "messageId": ...}``.  Raises ``ServerTurnError``
    when there is nothing to answer or no model can stream.
    """
    project_id, raw, project = await asyncio.to_thread(_load_record, conversation_id)
    pin, headers = resolve_model(model_pin_for(raw, project, project_id))
    body = body_from_record(raw, getattr(project, "path", None), pin)
    writer = ServerTurnWriter(project_id, conversation_id, trigger)

    async def _launch() -> str:
        from app.agents import chat_turn_relay
        return await chat_turn_relay.start_turn(
            conversation_id, writer.wrap(stream_factory(body)),
            headers=headers, server_authored=True)

    # An empty context: the pump task copies the context it is created in,
    # and the caller's is another conversation's turn.
    turn_id = await asyncio.get_running_loop().create_task(
        _launch(), context=contextvars.Context())
    writer.turn_id = turn_id
    logger.info(f"🛰️ SERVER_TURN: started {turn_id[:8]} for {conversation_id[:8]} "
                f"({len(body['chat_history'])} history entries, model {headers.get('X-Ziya-Model')})")
    return turn_id
