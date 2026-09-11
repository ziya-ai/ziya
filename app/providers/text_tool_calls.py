"""
Recover tool calls a model wrote into its text channel.

Small local models served through OpenAI-compatible endpoints (Ollama,
llama.cpp, LM Studio) sometimes emit a tool call as plain assistant text
instead of a ``tool_calls`` delta::

    {"name": "mcp_sequentialthinking", "arguments": {"thought": "...", ...}}

Observed live with qwen2.5-coder:7b on Ollama: the model omitted the
``<tool_call>`` wrapper its chat template expects, so Ollama's parser did
not recognise the call and forwarded the JSON as ``delta.content``. Ziya
then streamed the JSON to the user verbatim, executed nothing, and ended
the turn with stop_reason ``stop``.

``TextToolCallSniffer`` sits between the provider's ``delta.content`` and
the ``TextDelta`` events it yields. It holds back the first characters of a
response only while they are still consistent with the header of such an
object -- an optional fence / ``<tool_call>`` lead, then
``{"name": "<a tool that was offered>", "arguments":`` -- and releases them
the instant they diverge, so ordinary prose is delayed by one delta at
most. Once the header names an offered tool the remainder is held and
parsed when the stream ends: a clean parse becomes ToolUseStart / Input /
End events (and the turn's stop reason becomes ``tool_calls``); anything
else is released as the text it was.

Only the leading position is handled. Prose followed by a JSON call is
left alone, since by then the prose has already been streamed.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.providers.base import (
    StreamEvent,
    TextDelta,
    ToolUseEnd,
    ToolUseInput,
    ToolUseStart,
)

# Keys under which the arguments object has been seen. Qwen/Ollama use
# "arguments"; some templates say "parameters" or "input".
_ARG_KEYS = ("arguments", "parameters", "input")

# Wrappers a template may put around the object. Longer forms first so a
# ```json fence is not consumed as a bare ``` fence leaving "json" behind.
_LEADS = ("<tool_call>", "```json", "```")
_MARKERS = ("<tool_call>", "</tool_call>", "```json", "```")

# Longest header held while undecided: lead + {"name": "<name>", "arguments":
# fits in well under this. Past it the response is not a tool call.
MAX_HEADER_HOLD = 256

_WS = re.compile(r"\s+")


class TextToolCallSniffer:
    """Incremental classifier for the head of one assistant response."""

    def __init__(self, tool_names: Iterable[str]):
        self._names = {n for n in tool_names if isinstance(n, str) and n}
        self._buf = ""
        # undecided -> committed | passthrough
        self._state = "undecided" if self._names else "passthrough"

    @property
    def state(self) -> str:
        return self._state

    @property
    def held(self) -> str:
        """Text currently held back (for logging / tests)."""
        return self._buf

    # ------------------------------------------------------------------

    def feed(self, text: str) -> List[StreamEvent]:
        """Offer one content delta; returns the TextDelta events to yield now."""
        if not text:
            return []
        if self._state == "passthrough":
            return [TextDelta(content=text)]
        self._buf += text
        if self._state == "committed":
            return []
        verdict = self._judge()
        if verdict == "reject":
            return self._release()
        if verdict == "commit":
            self._state = "committed"
        return []

    def finish(self, stop_reason: str, first_index: int = 0) -> Tuple[List[StreamEvent], str]:
        """Stream ended. Returns (events to yield, stop reason to report)."""
        if self._state == "passthrough" or not self._buf:
            self._state = "passthrough"
            return [], stop_reason
        if self._state != "committed":
            # Header never completed (e.g. cut off mid-name): it is text.
            return self._release(), stop_reason
        calls = self._parse_calls(self._buf)
        if not calls:
            return self._release(), stop_reason
        events: List[StreamEvent] = []
        for i, (name, args) in enumerate(calls):
            idx = first_index + i
            call_id = f"call_text_{uuid.uuid4().hex[:12]}"
            events.append(ToolUseStart(id=call_id, name=name, index=idx))
            events.append(ToolUseInput(partial_json=json.dumps(args), index=idx))
            events.append(ToolUseEnd(id=call_id, name=name, input=args, index=idx))
        self._buf = ""
        self._state = "passthrough"
        return events, "tool_calls"

    # ------------------------------------------------------------------

    def _release(self) -> List[StreamEvent]:
        out = [TextDelta(content=self._buf)] if self._buf else []
        self._buf = ""
        self._state = "passthrough"
        return out

    def _judge(self) -> str:
        """'wait' | 'commit' | 'reject' for the buffer as it stands."""
        body = self._buf.lstrip()
        if not body:
            return "wait"
        if len(body) > MAX_HEADER_HOLD:
            return "reject"
        # Strip complete lead markers; wait on a partial one.
        while True:
            stripped = False
            for lead in _LEADS:
                if body.startswith(lead):
                    body = body[len(lead):].lstrip()
                    stripped = True
                    break
                if lead.startswith(body):
                    return "wait"
            if not stripped:
                break
        c = _WS.sub("", body)
        if not c:
            return "wait"
        if c[0] != "{":
            return "reject"
        head = '{"name":"'
        if len(c) < len(head):
            return "wait" if head.startswith(c) else "reject"
        if not c.startswith(head):
            return "reject"
        end = c.find('"', len(head))
        if end == -1:
            return "wait"
        name = c[len(head):end]
        if name not in self._names:
            return "reject"
        rest = c[end + 1:]
        if rest.startswith("}"):
            return "commit"  # {"name": "x"} -- a call with no arguments
        for key in _ARG_KEYS:
            sep = f',"{key}":'
            if rest.startswith(sep):
                return "commit"
            if sep.startswith(rest):
                return "wait"
        return "reject"

    def _parse_calls(self, raw: str) -> List[Tuple[str, Dict[str, Any]]]:
        """Every call object in ``raw``, or [] if anything else is present."""
        decoder = json.JSONDecoder()
        s = raw
        pos = 0
        calls: List[Tuple[str, Dict[str, Any]]] = []
        while True:
            changed = True
            while changed:
                changed = False
                rest = s[pos:].lstrip()
                pos = len(s) - len(rest)
                for m in _MARKERS:
                    if s.startswith(m, pos):
                        pos += len(m)
                        changed = True
                        break
            if pos >= len(s):
                break
            if s[pos] != "{":
                return []
            try:
                obj, end = decoder.raw_decode(s, pos)
            except json.JSONDecodeError:
                return []
            call = self._as_call(obj)
            if call is None:
                return []
            calls.append(call)
            pos = end
        return calls

    def _as_call(self, obj: Any) -> Optional[Tuple[str, Dict[str, Any]]]:
        if not isinstance(obj, dict):
            return None
        name = obj.get("name")
        if not isinstance(name, str) or name not in self._names:
            return None
        args: Any = {}
        for key in _ARG_KEYS:
            if key in obj:
                args = obj[key]
                break
        if isinstance(args, str):
            # Some templates double-encode the arguments object.
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return None
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return None
        return name, args
