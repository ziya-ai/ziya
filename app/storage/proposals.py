"""
Probationary proposal store — append-only JSONL persistence for memory
candidates that have not yet earned active status.

Distinct from the legacy ``proposals.json`` user-facing queue (which is
still used by the ``MemoryStorage.add_proposal`` path).  This store is
invisible to the user: candidates land here automatically from
extraction, accumulate corroboration / retrieval signals, and are
either promoted to ``memories.json`` or archived silently.

Design choices:

  * Append-only at the application level — every write either appends
    a new record or appends a status-change event.  Records are never
    rewritten in place; effective state is the projection over the
    event log.  This keeps writes cheap and the file naturally serves
    as an audit trail.

  * Encryption preserves consistency with the rest of the memory
    store: the whole file is wrapped in the standard ALE envelope
    when ALE is active.  We decrypt → mutate → re-encrypt → atomic
    rename on each write.  When/if multi-machine merge becomes a
    requirement, switching to per-line encryption is a contained
    change behind this API.

  * Hash-based IDs (``prop_<sha8>``) so concurrent extraction passes
    in different processes/sessions can write without coordination.
    Two extractions producing identical content map to the same ID,
    which is the correct corroboration signal — no dedup logic needed
    at write time.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional

from app.models.memory import MemoryProposal
from app.utils.logging_utils import logger


# Event kinds appended to the log.  ``record`` is the initial proposal
# row; subsequent ``status`` / ``corroborate`` / ``signal`` events
# carry only the deltas.
EVENT_RECORD = "record"
EVENT_STATUS = "status"
EVENT_CORROBORATE = "corroborate"
EVENT_SIGNAL = "signal"

# Statuses tracked here.  ``open`` is the default; ``promoted`` and
# ``archived`` are terminal — the projection treats either as removal
# from the live working set.
STATUS_OPEN = "open"
STATUS_PROMOTED = "promoted"
STATUS_ARCHIVED = "archived"


def _content_hash(content: str) -> str:
    """Stable 8-hex-char hash of memory content, used as proposal ID."""
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:8]


class ProposalsStore:
    """File-backed append-only proposal store.

    Path: ``<memory_dir>/probationary.jsonl`` (encrypted with ALE when
    ALE is enabled for ``session_data``).

    Thread-safe within a process via an ``RLock``.  Cross-process
    coordination is not provided; if/when needed, add an ``fcntl``
    advisory lock around the read-modify-write sequence in ``_append``.
    """

    def __init__(self, memory_dir: Optional[Path] = None) -> None:
        if memory_dir is None:
            from app.utils.paths import get_ziya_home
            memory_dir = get_ziya_home() / "memory"
        self._dir = Path(memory_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        # Cached projection of the event log — invalidated on each append.
        self._cache: Optional[Dict[str, Dict[str, Any]]] = None
        self._cache_mtime: float = 0.0

    @property
    def _path(self) -> Path:
        return self._dir / "probationary.jsonl"

    # -- Low-level read/write (envelope-aware) ------------------------------

    def _read_lines(self) -> List[Dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            raw = self._path.read_bytes()
            if not raw:
                return []
            from app.utils.encryption import is_encrypted, get_encryptor
            if is_encrypted(raw):
                raw = get_encryptor().decrypt(raw)
            text = raw.decode("utf-8")
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        except Exception as e:
            logger.error(f"ProposalsStore: error reading {self._path}: {e}")
            return []

    def _write_lines(self, events: Iterable[Dict[str, Any]]) -> None:
        plaintext = ("\n".join(json.dumps(e, ensure_ascii=False) for e in events)
                     + "\n").encode("utf-8")
        temp = self._path.with_suffix(".jsonl.tmp")
        try:
            from app.utils.encryption import get_encryptor
            enc = get_encryptor()
            payload = (enc.encrypt(plaintext, "session_data")
                       if enc.is_enabled("session_data") else plaintext)
            temp.write_bytes(payload)
            temp.rename(self._path)
        except Exception:
            if temp.exists():
                temp.unlink()
            raise

    def _append(self, event: Dict[str, Any]) -> None:
        """Read existing events, append one, rewrite.

        Read-modify-write is required because the file is encrypted as
        a single envelope.  Cost is bounded — proposal volume is small
        (single-digits per conversation, 100s lifetime) and writes are
        infrequent compared to reads.
        """
        with self._lock:
            existing = self._read_lines()
            existing.append(event)
            self._write_lines(existing)
            self._cache = None  # Invalidate

    # -- Projection ---------------------------------------------------------

    def _project(self, events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Reduce the event log into current proposal state.

        Each ``record`` event seeds a row.  Subsequent events for the
        same ``id`` mutate that row.  Terminal-status rows
        (``promoted``/``archived``) remain in the projection so callers
        can still inspect them — filter on status when querying.
        """
        state: Dict[str, Dict[str, Any]] = {}
        for ev in events:
            kind = ev.get("kind")
            pid = ev.get("id")
            if not pid:
                continue
            if kind == EVENT_RECORD:
                # If a record event re-arrives for the same hash, skip — the
                # original ``record`` is canonical, later identical extractions
                # are corroborations and must be logged via ``corroborate``.
                if pid not in state:
                    state[pid] = ev["data"]
            elif kind == EVENT_STATUS and pid in state:
                state[pid]["status"] = ev["status"]
                state[pid]["status_changed_at"] = ev.get("ts", int(time.time() * 1000))
            elif kind == EVENT_CORROBORATE and pid in state:
                state[pid]["corroborations"] = (
                    state[pid].get("corroborations", 0) + 1
                )
                # Track which conversations corroborated, capped to last 5.
                seen = state[pid].setdefault("corroborated_by", [])
                conv = ev.get("conversation_id")
                if conv and conv not in seen:
                    seen.append(conv)
                    if len(seen) > 5:
                        del seen[0]
            elif kind == EVENT_SIGNAL and pid in state:
                # Generic signal hook for future retrieval-feedback writes.
                signals = state[pid].setdefault("signals", [])
                signals.append({
                    "name": ev.get("name"),
                    "ts": ev.get("ts", int(time.time() * 1000)),
                    "value": ev.get("value"),
                })
        return state

    def _projection(self) -> Dict[str, Dict[str, Any]]:
        try:
            mtime = self._path.stat().st_mtime if self._path.exists() else 0.0
        except OSError:
            mtime = 0.0
        if self._cache is not None and mtime == self._cache_mtime:
            return self._cache
        events = self._read_lines()
        self._cache = self._project(events)
        self._cache_mtime = mtime
        return self._cache

    # -- Public API ---------------------------------------------------------

    def add(self, proposal: MemoryProposal,
            activity_count: int = 0) -> str:
        """Append a probationary proposal.  Returns the stable ID.

        If a proposal with this content already exists, appends a
        ``corroborate`` event instead and returns the existing ID.
        """
        if not proposal.content.strip():
            raise ValueError("proposal.content must be non-empty")
        pid = f"prop_{_content_hash(proposal.content)}"
        proposal.id = pid
        proposal.content_hash = pid.split("_", 1)[1]
        proposal.activity_count_at_proposal = activity_count

        existing = self._projection().get(pid)
        if existing and existing.get("status") == STATUS_OPEN:
            self._append({
                "kind": EVENT_CORROBORATE,
                "id": pid,
                "ts": int(time.time() * 1000),
                "conversation_id": proposal.conversation_id,
            })
            return pid

        self._append({
            "kind": EVENT_RECORD,
            "id": pid,
            "ts": int(time.time() * 1000),
            "data": {**proposal.model_dump(), "status": STATUS_OPEN},
        })

        # Cache an embedding for this proposal under the same key memories
        # use, so retrieval-feedback can score response windows against
        # open proposals (Diff 6c) without re-embedding on every call.
        try:
            from app.services.embedding_service import embed_and_cache
            embed_and_cache(pid, proposal.content)
        except Exception as embed_err:
            # Embedding is opportunistic — feedback degrades gracefully
            # to "no proposal-level use signal" if this fails.
            logger.debug(f"Proposal embedding skipped (non-fatal): {embed_err}")

        return pid

    def list_open(self) -> List[Dict[str, Any]]:
        """Return all proposals currently in ``open`` status."""
        return [r for r in self._projection().values()
                if r.get("status") == STATUS_OPEN]

    def get(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        """Return the projected state of a single proposal, regardless of status."""
        return self._projection().get(proposal_id)

    def list_all(self) -> List[Dict[str, Any]]:
        """Return every proposal in the projection (open + terminal).

        Useful for audit and the ``/memory reevaluate`` re-extraction path,
        which needs to see archived proposals to avoid re-proposing them.
        """
        return list(self._projection().values())

    def mark_promoted(self, proposal_id: str,
                      target_memory_id: str) -> bool:
        """Record that a proposal has graduated to the active memory store.

        ``target_memory_id`` is the ID of the active ``Memory`` record
        the proposal became, so callers can trace lineage later.

        Returns True if the transition was recorded, False if the
        proposal does not exist or was already terminal.
        """
        existing = self._projection().get(proposal_id)
        if not existing or existing.get("status") != STATUS_OPEN:
            return False
        self._append({
            "kind": EVENT_STATUS,
            "id": proposal_id,
            "ts": int(time.time() * 1000),
            "status": STATUS_PROMOTED,
            "target_memory_id": target_memory_id,
        })
        return True

    def mark_archived(self, proposal_id: str,
                      reason: str = "decay") -> bool:
        """Mark a proposal as archived (will not promote).

        ``reason`` is logged for diagnostics: ``decay`` (TTL elapsed),
        ``contradicted`` (extraction or user said no), ``redundant``
        (an active memory now covers it).
        """
        existing = self._projection().get(proposal_id)
        if not existing or existing.get("status") != STATUS_OPEN:
            return False
        self._append({
            "kind": EVENT_STATUS,
            "id": proposal_id,
            "ts": int(time.time() * 1000),
            "status": STATUS_ARCHIVED,
            "reason": reason,
        })
        return True

    def corroborate_by_id(self, proposal_id: str,
                          conversation_id: Optional[str] = None) -> bool:
        """Record a corroboration event against an existing open proposal.

        Used when extraction detects a paraphrase match via embeddings but
        cannot use the content-hash path in add() because the wording differs.
        Returns True if the event was written, False if the proposal is
        missing or already in a terminal state.
        """
        existing = self._projection().get(proposal_id)
        if not existing or existing.get("status") != STATUS_OPEN:
            return False
        self._append({
            "kind": EVENT_CORROBORATE,
            "id": proposal_id,
            "ts": int(time.time() * 1000),
            "conversation_id": conversation_id,
        })
        return True

    def record_signal(self, proposal_id: str,
                      name: str,
                      value: Optional[Any] = None) -> bool:
        """Append a generic signal event (used by retrieval-feedback in Diff 6).

        Signals don't change status on their own — the promotion engine
        (Diff 7) inspects accumulated signals and decides whether to
        promote or archive.
        """
        if proposal_id not in self._projection():
            return False
        self._append({
            "kind": EVENT_SIGNAL,
            "id": proposal_id,
            "ts": int(time.time() * 1000),
            "name": name,
            "value": value,
        })
        return True

    def find_similar_open(self,
                          content: str,
                          embedder: Optional[Any] = None,
                          threshold: float = 0.85,
                          limit: int = 5) -> List[Dict[str, Any]]:
        """Look up open proposals semantically near ``content``.

        Used by the corroboration signal: when extraction produces a
        new candidate, we check whether a near-duplicate is already
        probationary.  If so, we corroborate rather than create.

        ``embedder`` is optional — when None, falls back to a tag-and-
        keyword overlap heuristic.  The Diff 6 wiring will pass the
        embedding service so semantic match works.
        """
        opens = self.list_open()
        if not opens:
            return []

        # Embedding path (preferred, used once Diff 6 is in)
        if embedder is not None:
            try:
                target_vec = embedder.embed_text(content)
                if target_vec is not None:
                    scored: List[tuple[float, Dict[str, Any]]] = []
                    for row in opens:
                        row_vec = embedder.embed_text(row.get("content", ""))
                        if row_vec is None:
                            continue
                        # Cosine similarity (vectors assumed L2-normalized
                        # by the embedding service)
                        sim = sum(a * b for a, b in zip(target_vec, row_vec))
                        if sim >= threshold:
                            scored.append((sim, row))
                    scored.sort(key=lambda x: x[0], reverse=True)
                    return [r for _, r in scored[:limit]]
            except Exception as e:
                logger.debug(f"ProposalsStore: embedding lookup failed: {e}")

        # Fallback: token Jaccard over content + tag intersection.
        target_tokens = set(content.lower().split())
        scored = []
        for row in opens:
            row_tokens = set(row.get("content", "").lower().split())
            if not row_tokens:
                continue
            jaccard = (len(target_tokens & row_tokens)
                       / max(len(target_tokens | row_tokens), 1))
            if jaccard >= 0.5:
                scored.append((jaccard, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:limit]]

    def prune_terminal(self, max_terminal_records: int = 10_000) -> int:
        """Compact the event log when terminal records dominate the file.

        Rewrites the file containing only the events for proposals that
        are still open OR whose terminal events are within the most
        recent ``max_terminal_records`` records.  Returns the number of
        records pruned.  Idempotent.
        """
        with self._lock:
            events = self._read_lines()
            projection = self._project(events)
            terminal_ids = [pid for pid, row in projection.items()
                            if row.get("status") in (STATUS_PROMOTED,
                                                     STATUS_ARCHIVED)]
            if len(terminal_ids) <= max_terminal_records:
                return 0
            keep_terminal = set(terminal_ids[-max_terminal_records:])
            kept = [
                ev for ev in events
                if (ev.get("id") not in projection
                    or projection[ev["id"]].get("status") == STATUS_OPEN
                    or ev.get("id") in keep_terminal)
            ]
            pruned = len(events) - len(kept)
            self._write_lines(kept)
            self._cache = None
            return pruned


_instance: Optional[ProposalsStore] = None


def get_proposals_store() -> ProposalsStore:
    """Module-level singleton, mirroring ``MemoryStorage`` accessor."""
    global _instance
    if _instance is None:
        _instance = ProposalsStore()
    return _instance