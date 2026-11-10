"""Machine-readable output for one-shot CLI runs: ``--output-format json|stream-json``.

``ziya ask``, ``ziya review``, ``ziya explain`` and ``ziya task <file task>``
print a rendered transcript for a person.  With ``--output-format`` they
print for a program instead:

``stream-json``
    one JSON object per line on stdout as the turn runs::

        {"type": "text", "content": "..."}
        {"type": "tool_start", "id": "...", "tool": "file_read", "args": {...}}
        {"type": "tool_result", "id": "...", "tool": "file_read", "ok": true, "result": "..."}
        {"type": "result", ...}

``json``
    nothing until the turn ends, then one ``result`` document.

The ``result`` document carries the final answer (``text``), the tool
calls made, the model, the conversation id, wall time, and ``usage`` --
the usage ledger's rollup for the conversation (token sums per dimension,
list and effective cost in USD, or ``null`` when the model is unpriced).
Everything a person would have seen (progress lines, tool panes) stays
off stdout; errors go to stderr as they always did.  Exit code is 0 when
the model produced a response and 1 otherwise, so a shell pipeline can
test it.
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any, Callable, Dict, List, Optional

FORMATS = ("json", "stream-json")
# Tool results can be large (a file read, a search); the event keeps a head
# of it.  The model saw the whole thing; the consumer gets the answer.
RESULT_PREVIEW_CHARS = 2000


def add_output_format_argument(parser) -> None:
    parser.add_argument(
        "--output-format", choices=list(FORMATS), default=None, dest="output_format",
        help="Print machine-readable output instead of a transcript: 'json' (one "
             "document at the end) or 'stream-json' (one JSON object per line as "
             "the turn runs). Includes the answer, tool calls, token usage and cost.",
    )


def _preview(value: Any) -> Any:
    if isinstance(value, list):
        texts = [b.get("text", "") for b in value if isinstance(b, dict) and b.get("type") == "text"]
        value = "\n".join(t for t in texts if t)
    if isinstance(value, str) and len(value) > RESULT_PREVIEW_CHARS:
        return value[:RESULT_PREVIEW_CHARS] + f"… [{len(value) - RESULT_PREVIEW_CHARS} more chars]"
    return value


def usage_for_conversation(conversation_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The ledger's totals for one conversation, trimmed to what a consumer
    reads: tokens per dimension, cost, and whether any line was estimated."""
    if not conversation_id:
        return None
    try:
        from app.storage.usage_ledger import get_ledger
        roll = get_ledger().rollup(by="model", conversation_id=conversation_id)
    except Exception:  # noqa: BLE001 -- usage is reported when known, never required
        return None
    t = roll.get("totals") or {}
    if not t.get("records"):
        return None
    return {
        "tokens": dict(t.get("dims") or {}),
        "list_cost_usd": t.get("list_cost"),
        "effective_cost_usd": t.get("effective_cost"),
        "has_estimates": bool(t.get("has_estimates")),
        "is_partial": bool(t.get("is_partial")),
        "records": t.get("records"),
        "models": [g.get("key") for g in roll.get("groups", []) if g.get("key")],
    }


class StructuredSink:
    """Consumes the CLI's stream chunks and writes JSON to ``out``."""

    def __init__(self, fmt: str, out=None, *, clock: Callable[[], float] = time.time):
        if fmt not in FORMATS:
            raise ValueError(f"unknown output format {fmt!r}")
        self.fmt = fmt
        self.out = out or sys.stdout
        self._clock = clock
        self.started = clock()
        self.text = ""
        self.tool_calls: List[Dict[str, Any]] = []
        self._open: Dict[str, Dict[str, Any]] = {}
        self.finished = False

    @classmethod
    def from_args(cls, args) -> Optional["StructuredSink"]:
        fmt = getattr(args, "output_format", None)
        return cls(fmt) if fmt else None

    # -- events -----------------------------------------------------------

    def _emit(self, event: Dict[str, Any]) -> None:
        if self.fmt == "stream-json":
            self.out.write(json.dumps(event, default=str) + "\n")
            self.out.flush()

    def handle(self, chunk: Dict[str, Any]) -> None:
        kind = chunk.get("type")
        if kind == "text":
            content = chunk.get("content", "") or ""
            if content:
                self.text += content
                self._emit({"type": "text", "content": content})
        elif kind == "tool_start":
            if chunk.get("is_internal"):
                return
            call = {
                "id": chunk.get("tool_id") or chunk.get("tool_use_id"),
                "tool": chunk.get("tool_name"),
                "args": chunk.get("args") if isinstance(chunk.get("args"), dict) else None,
                "started_at": self._clock(),
            }
            if call["id"]:
                self._open[call["id"]] = call
            self.tool_calls.append(call)
            self._emit({"type": "tool_start", "id": call["id"], "tool": call["tool"], "args": call["args"]})
        elif kind == "tool_display":
            if chunk.get("is_internal"):
                return
            tid = chunk.get("tool_id") or chunk.get("tool_use_id")
            call = self._open.pop(tid, None) if tid else None
            result = chunk.get("result")
            ok = not (isinstance(result, str) and result.startswith("ERROR:"))
            if call is None:
                call = {"id": tid, "tool": chunk.get("tool_name"), "args": chunk.get("args"), "started_at": None}
                self.tool_calls.append(call)
            if call.get("args") is None and isinstance(chunk.get("args"), dict):
                call["args"] = chunk.get("args")
            call["ok"] = ok
            call["result"] = _preview(result)
            if call.get("started_at"):
                call["duration_ms"] = int((self._clock() - call["started_at"]) * 1000)
            self._emit({"type": "tool_result", "id": tid, "tool": call["tool"], "ok": ok,
                        "result": call["result"], "duration_ms": call.get("duration_ms")})

    async def consume(self, stream, rewind: Optional[Callable[[str], Any]] = None) -> str:
        """Drain one model turn's chunk stream; returns the accumulated text.

        ``rewind`` is the CLI's rewind-marker handler: given the text so
        far it returns ``(kept, _)`` with any rewound tail removed, so a
        continuation that re-emits replaced text is not reported twice.
        """
        async for chunk in stream:
            self.handle(chunk)
            if rewind is not None and chunk.get("type") == "text" and "<!-- REWIND_MARKER:" in self.text:
                kept, _ = rewind(self.text)
                if kept != self.text:
                    self.text = kept
        return self.text

    # -- the end ----------------------------------------------------------

    def result_document(self, final_text: Optional[str], *, conversation_id: Optional[str],
                        model: Optional[str] = None, error: Optional[str] = None) -> Dict[str, Any]:
        text = final_text if final_text is not None else self.text
        calls = []
        for c in self.tool_calls:
            calls.append({k: v for k, v in c.items() if k != "started_at"})
        return {
            "type": "result",
            "ok": bool(text) and not error,
            "text": text,
            "error": error,
            "model": model,
            "conversation_id": conversation_id,
            "duration_ms": int((self._clock() - self.started) * 1000),
            "tool_calls": calls,
            "usage": usage_for_conversation(conversation_id),
        }

    def finish(self, final_text: Optional[str], *, conversation_id: Optional[str],
               model: Optional[str] = None, error: Optional[str] = None) -> int:
        """Write the result document; returns the process exit code."""
        doc = self.result_document(final_text, conversation_id=conversation_id, model=model, error=error)
        if self.fmt == "json":
            self.out.write(json.dumps(doc, indent=2, default=str) + "\n")
        else:
            self.out.write(json.dumps(doc, default=str) + "\n")
        self.out.flush()
        self.finished = True
        return 0 if doc["ok"] else 1
