"""
Consent ledger: one mailbox for "a human must answer before this proceeds".

Two consumers share it (design/consent-runtime.md):

- Task-run Ask blocks.  The record is projected onto the run's existing
  ``pending_ask`` / ``ask_answers`` fields via ``TaskRunConsentStore``, so
  the restart reconciler, the ``/ask/{block}`` endpoint and the run-record
  tests see exactly what they saw before this module existed.
- Interactive chat tool consents (``conv:{conversation_id}:{tool_call_id}``).
  A chat turn has no run record to ride on, so ``FileConsentStore`` keeps
  one JSON file per open request under ``<project_dir>/consents/``.  A
  reconnecting tab asks ``open_for(conversation_id)`` and can re-render a
  pending consent even after the relay's frame buffer has rolled over.

Semantics the two stores must agree on, because the executor relies on them:

- **First answer wins.**  A double click, a retried request, or two windows
  answering at once must not change what the waiter was told.  A later
  ``record_answer`` returns the settled record untouched.
- **Answer survives close.**  ``close`` clears the *open question*; the
  answer stays on record so a resumed run (or a replayed chat turn) finds it
  settled rather than asking twice.
- **Waiting costs nothing.**  ``await_answer`` polls the store — storage is
  the mailbox — so a process restart between open and answer loses nothing
  that was written.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)

DECISIONS = ("approve", "reject")
SCOPES = ("once", "conversation", "session", "always")

# How often the wait loop re-reads the record.  A human answer arrives on a
# scale of minutes; polling faster only rewrites nothing more often.
DEFAULT_POLL_SECONDS = 0.5

# Reason recorded when a request expires unanswered.  Expiry is a *reject*
# with this reason so the held caller ends deterministically instead of
# hanging on a question nobody will answer.
TIMED_OUT_REASON = "timed out"


def _now_ms() -> int:
    return int(time.time() * 1000)


class ConsentCancelled(Exception):
    """The waiter was cancelled before an answer arrived."""


@dataclass
class ConsentRecord:
    request_id: str
    owner_id: str                       # run_id or conversation_id
    payload: Dict[str, Any]             # {"kind": "question"|"tool_call", ...}
    opened_at: int
    ttl_ms: Optional[int] = None
    answer: Optional[Dict[str, Any]] = None
    closed: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return str(self.payload.get("kind") or "question")

    @property
    def settled(self) -> bool:
        return self.answer is not None

    def expired(self, now_ms: Optional[int] = None) -> bool:
        if self.ttl_ms is None:
            return False
        return (now_ms if now_ms is not None else _now_ms()) >= self.opened_at + self.ttl_ms

    def public(self) -> Dict[str, Any]:
        """The wire shape carried on ``consent_opened`` / reconnect replies."""
        return {
            "request_id": self.request_id,
            "owner_id": self.owner_id,
            "payload": dict(self.payload),
            "opened_at": self.opened_at,
            "ttl_ms": self.ttl_ms,
            "answer": dict(self.answer) if self.answer else None,
            "closed": self.closed,
        }


def make_answer(
    decision: str, *, scope: str = "once", answer: str = "", answered_by: str = "",
) -> Dict[str, Any]:
    decision = (decision or "").strip().lower()
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, got {decision!r}")
    scope = (scope or "once").strip().lower()
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}, got {scope!r}")
    return {
        "decision": decision,
        "scope": scope if decision == "approve" else "once",
        "answer": answer or "",
        "answered_by": answered_by or "",
        "answered_at": _now_ms(),
    }


# ── store protocol ────────────────────────────────────────────────────────

class ConsentStore(Protocol):
    def open(self, record: ConsentRecord) -> ConsentRecord: ...
    def get(self, request_id: str) -> Optional[ConsentRecord]: ...
    def record_answer(self, request_id: str, answer: Dict[str, Any]) -> Optional[ConsentRecord]: ...
    def close(self, request_id: str) -> Optional[ConsentRecord]: ...
    def open_for(self, owner_id: str) -> List[ConsentRecord]: ...


# ── task-run store: projection onto TaskRun.pending_ask / ask_answers ─────

_RUN_ID_RE = re.compile(r"^run:(?P<run_id>[^:]+):(?P<block_id>.+)$")


def run_request_id(run_id: str, block_id: str) -> str:
    return f"run:{run_id}:{block_id}"


def conversation_request_id(conversation_id: str, tool_call_id: str) -> str:
    return f"conv:{conversation_id}:{tool_call_id}"


def _split_run_request(request_id: str) -> tuple[str, str]:
    m = _RUN_ID_RE.match(request_id)
    if not m:
        raise ValueError(f"not a task-run consent id: {request_id!r}")
    return m.group("run_id"), m.group("block_id")


class TaskRunConsentStore:
    """Adapts a ``TaskRunStorage`` (duck-typed: ``get``, ``open_ask``,
    ``close_ask``, ``record_ask_answer``) to the store protocol.

    Deliberately keeps the run record's shape byte-for-byte: ``pending_ask``
    still carries ``block_id/question/choices/opened_at`` and ``ask_answers``
    still carries ``decision/answer/answered_by/answered_at``, because the
    reconciler (``held_at_block_id`` from ``pending_ask``), the answer
    endpoint's 409 checks, and the frontend tile all read those fields.
    ``scope`` is added to the answer entry; older readers ignore it.
    """

    def __init__(self, storage: Any):
        self._storage = storage

    def open(self, record: ConsentRecord) -> ConsentRecord:
        run_id, block_id = _split_run_request(record.request_id)
        p = record.payload
        run = self._storage.open_ask(
            run_id, block_id,
            str(p.get("text") or p.get("question") or ""),
            [str(c) for c in (p.get("choices") or [])],
        )
        if run is None:
            raise KeyError(f"task run {run_id!r} not found")
        pending = run.pending_ask or {}
        record.opened_at = int(pending.get("opened_at") or record.opened_at)
        return record

    def get(self, request_id: str) -> Optional[ConsentRecord]:
        run_id, block_id = _split_run_request(request_id)
        run = self._storage.get(run_id)
        if run is None:
            return None
        pending = run.pending_ask or {}
        answer = (run.ask_answers or {}).get(block_id)
        is_open = pending.get("block_id") == block_id
        if not is_open and answer is None:
            return None
        payload: Dict[str, Any] = {"kind": "question"}
        if is_open:
            payload["text"] = pending.get("question", "")
            payload["choices"] = list(pending.get("choices") or [])
        return ConsentRecord(
            request_id=request_id,
            owner_id=run_id,
            payload=payload,
            opened_at=int(pending.get("opened_at") or 0),
            answer=dict(answer) if answer else None,
            closed=not is_open,
        )

    def record_answer(self, request_id: str, answer: Dict[str, Any]) -> Optional[ConsentRecord]:
        run_id, block_id = _split_run_request(request_id)
        before = self._storage.get(run_id)
        if before is None:
            return None
        already_settled = block_id in (before.ask_answers or {})
        if not already_settled and (before.pending_ask or {}).get("block_id") != block_id:
            # Never opened: same rule the /ask endpoint enforces with a 409.
            # A checkpoint cannot be answered before the run reaches it.
            return None
        run = self._storage.record_ask_answer(
            run_id, block_id,
            answer.get("decision", "approve"),
            answer.get("answer", ""),
            answer.get("answered_by", ""),
        )
        if run is None:
            return None
        # ``record_ask_answer`` is first-answer-wins and knows nothing about
        # scope.  Stamp scope onto the entry only when THIS call is the one
        # that landed — a later caller must not rewrite the settled answer.
        entry = (run.ask_answers or {}).get(block_id)
        if entry is not None and not already_settled and "scope" not in entry:
            entry["scope"] = answer.get("scope", "once")
            self._storage._write_json(self._storage._run_file(run_id), run.model_dump())
        return self.get(request_id)

    def close(self, request_id: str) -> Optional[ConsentRecord]:
        run_id, _ = _split_run_request(request_id)
        self._storage.close_ask(run_id)
        return self.get(request_id)

    def open_for(self, owner_id: str) -> List[ConsentRecord]:
        run = self._storage.get(owner_id)
        if run is None:
            return []
        pending = run.pending_ask or {}
        block_id = pending.get("block_id")
        if not block_id:
            return []
        rec = self.get(run_request_id(owner_id, block_id))
        return [rec] if rec and not rec.settled else []


# ── file store: chat tool consents ────────────────────────────────────────

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


class FileConsentStore:
    """One JSON file per request under ``<base_dir>/consents/``.

    Small by construction: a conversation has at most a handful of open
    consents at a time, and closed-and-answered records are pruned once
    they age past ``retain_ms`` (the answer only needs to outlive the
    turn that asked).
    """

    def __init__(self, base_dir: Path | str, retain_ms: int = 24 * 3600 * 1000):
        self.dir = Path(base_dir) / "consents"
        self.retain_ms = retain_ms

    def _path(self, request_id: str) -> Path:
        return self.dir / (_SAFE_NAME_RE.sub("_", request_id) + ".json")

    def _write(self, rec: ConsentRecord) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        data = {
            "request_id": rec.request_id, "owner_id": rec.owner_id,
            "payload": rec.payload, "opened_at": rec.opened_at,
            "ttl_ms": rec.ttl_ms, "answer": rec.answer, "closed": rec.closed,
            "extra": rec.extra,
        }
        tmp = self._path(rec.request_id).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1))
        os.replace(tmp, self._path(rec.request_id))

    def _read(self, path: Path) -> Optional[ConsentRecord]:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        return ConsentRecord(
            request_id=data["request_id"], owner_id=data.get("owner_id", ""),
            payload=data.get("payload") or {}, opened_at=int(data.get("opened_at") or 0),
            ttl_ms=data.get("ttl_ms"), answer=data.get("answer"),
            closed=bool(data.get("closed")), extra=data.get("extra") or {},
        )

    def open(self, record: ConsentRecord) -> ConsentRecord:
        existing = self.get(record.request_id)
        if existing is not None:
            return existing            # idempotent re-open (resume path)
        self._write(record)
        self._prune()
        return record

    def get(self, request_id: str) -> Optional[ConsentRecord]:
        p = self._path(request_id)
        return self._read(p) if p.exists() else None

    def record_answer(self, request_id: str, answer: Dict[str, Any]) -> Optional[ConsentRecord]:
        rec = self.get(request_id)
        if rec is None:
            return None
        if rec.settled:
            return rec                 # first answer wins
        rec.answer = dict(answer)
        self._write(rec)
        return rec

    def close(self, request_id: str) -> Optional[ConsentRecord]:
        rec = self.get(request_id)
        if rec is None:
            return None
        rec.closed = True
        self._write(rec)
        return rec

    def open_for(self, owner_id: str) -> List[ConsentRecord]:
        if not self.dir.exists():
            return []
        out = []
        for p in sorted(self.dir.glob("*.json")):
            rec = self._read(p)
            if rec and rec.owner_id == owner_id and not rec.closed and not rec.settled:
                out.append(rec)
        return out

    def _prune(self) -> None:
        cutoff = _now_ms() - self.retain_ms
        for p in self.dir.glob("*.json"):
            rec = self._read(p)
            if rec and rec.closed and rec.settled and rec.opened_at < cutoff:
                try:
                    p.unlink()
                except OSError:
                    pass


# ── the ledger ────────────────────────────────────────────────────────────

class ConsentLedger:
    """Open / wait / answer / close over a ``ConsentStore``."""

    def __init__(self, store: ConsentStore, poll_seconds: float = DEFAULT_POLL_SECONDS):
        self.store = store
        self.poll_seconds = poll_seconds

    def open(
        self, request_id: str, payload: Dict[str, Any], *,
        owner_id: str, ttl_ms: Optional[int] = None,
    ) -> ConsentRecord:
        payload = dict(payload)
        payload.setdefault("kind", "question")
        rec = ConsentRecord(
            request_id=request_id, owner_id=owner_id, payload=payload,
            opened_at=_now_ms(), ttl_ms=ttl_ms,
        )
        return self.store.open(rec)

    def get(self, request_id: str) -> Optional[ConsentRecord]:
        return self.store.get(request_id)

    def record_answer(
        self, request_id: str, decision: str, *, scope: str = "once",
        answer: str = "", answered_by: str = "",
    ) -> Optional[ConsentRecord]:
        return self.store.record_answer(
            request_id,
            make_answer(decision, scope=scope, answer=answer, answered_by=answered_by),
        )

    def close(self, request_id: str) -> Optional[ConsentRecord]:
        return self.store.close(request_id)

    def open_for(self, owner_id: str) -> List[ConsentRecord]:
        return self.store.open_for(owner_id)

    async def await_answer(
        self, request_id: str,
        cancel_requested: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Block until the request is answered.

        Raises ``ConsentCancelled`` if ``cancel_requested()`` turns true.
        An expired request (``ttl_ms`` elapsed) is settled as a reject with
        reason ``TIMED_OUT_REASON`` and that answer is returned.
        """
        while True:
            rec = self.store.get(request_id)
            if rec is None:
                raise KeyError(f"consent request {request_id!r} disappeared while waiting")
            if rec.settled:
                return dict(rec.answer or {})
            if rec.expired():
                settled = self.store.record_answer(
                    request_id, make_answer("reject", answer=TIMED_OUT_REASON, answered_by="system"),
                )
                return dict((settled.answer if settled else None) or {})
            if cancel_requested is not None and cancel_requested():
                raise ConsentCancelled(request_id)
            await asyncio.sleep(self.poll_seconds)
