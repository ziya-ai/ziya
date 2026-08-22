"""
Refusal log — the record of launches that pre-launch validation stopped.

A refused launch deliberately creates **no** ``TaskRun``: ``record_run``
bumps ``run_count``, so minting a record for work that never executed
would corrupt the deck's "never run" vs "has history" distinction, and a
``held`` run advertises resume affordances for progress that does not
exist.  The cost of that correctness is that the entire class of
authoring defect caught BEFORE execution leaves no trace — and it is a
uniquely interesting class, because it is the one nobody paid for and
therefore the one nobody remembers.  Refusals are worth mining: which
mistakes recur, which block types attract them, whether model-authored
cards fail differently from hand-written ones.

Storage follows ``app/storage/proposals.py`` deliberately rather than
inventing a third persistence shape: append-only JSONL, wrapped in the
standard ALE envelope when encryption is active, read-modify-write on
append because the envelope covers the whole file.  The same bounded-cost
argument applies here and more weakly: refusals are rarer than memory
proposals, since each one requires a human to have authored something
broken.

Category is ``task_definition`` — the same category card definitions
use, and for the same stated reason (see
``EncryptionPolicy.never_encrypted_categories``): this is derived from
authored config, not model output about the codebase.  A record holds
finding messages and block paths, never task instructions.

Every write is best-effort.  This is an observability sink, and a refused
launch must still return its actionable 422 rather than a 500 because a
log failed.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional

from ..utils.logging_utils import logger

if TYPE_CHECKING:  # avoid importing the validator at module load
    from ..utils.task_card_validation import ValidationResult

# Encryption category.  Matches card definitions, not run artifacts.
_CATEGORY = "task_definition"

# Bound on retained records.  A refusal log that grows without limit
# eventually makes every append read and rewrite a large file, which is
# the read-modify-write pattern's one sharp edge.  Oldest are dropped.
MAX_RETAINED_REFUSALS = 5_000


def _finding_dict(f: Any) -> Dict[str, Any]:
    return {
        "message": getattr(f, "message", ""),
        "block_id": getattr(f, "block_id", ""),
        "path": getattr(f, "path", ""),
        "severity": getattr(f, "severity", ""),
    }


def defect_signature(result: "ValidationResult") -> str:
    """A stable id for the SHAPE of a refusal, for clustering.

    Derived from the error messages only — deliberately not from the card
    id, card name, block ids, or paths, all of which differ between two
    cards broken in the identical way.  Grouping by defect is the entire
    reason to keep these records; grouping by card would just re-derive
    the card list.

    Warnings are excluded: they do not cause the refusal, and including
    them would split one defect class across every incidental warning
    combination that happened to accompany it.
    """
    parts = sorted(getattr(f, "message", "") for f in (result.errors or []))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def build_refusal_record(
    *,
    card_id: str,
    card_name: str,
    result: "ValidationResult",
    is_resume: bool = False,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble one refusal record.

    Pure — no I/O — so the launch path can build a record even when the
    sink is unavailable, and so tests can assert record shape without a
    filesystem.
    """
    return {
        "at": int(time.time() * 1000),
        "project_id": project_id or "",
        "card_id": card_id,
        "card_name": card_name,
        # A refused resume means the card_snapshot was ALREADY broken when
        # it ran, which is a different finding from an author breaking a
        # card just now — worth distinguishing before the records are
        # aggregated and the difference is unrecoverable.
        "is_resume": bool(is_resume),
        "signature": defect_signature(result),
        "error_count": len(result.errors or []),
        "warning_count": len(result.warnings or []),
        "errors": [_finding_dict(f) for f in (result.errors or [])],
        "warnings": [_finding_dict(f) for f in (result.warnings or [])],
    }


class RefusalLog:
    """Append-only store of refused launches for one project."""

    def __init__(self, project_dir: Path) -> None:
        self._dir = Path(project_dir)
        self._lock = threading.Lock()

    @property
    def _path(self) -> Path:
        return self._dir / "task_card_refusals.jsonl"

    # -- envelope-aware read/write (mirrors ProposalsStore) --------------

    def _read_lines(self) -> List[Dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            raw = self._path.read_bytes()
            if not raw:
                return []
            from ..utils.encryption import get_encryptor, is_encrypted
            if is_encrypted(raw):
                raw = get_encryptor().decrypt(raw)
            out: List[Dict[str, Any]] = []
            for line in raw.decode("utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    # One garbled line must not discard the rest of the
                    # history; a partial log is strictly better than none.
                    logger.debug("RefusalLog: skipping unparseable line")
            return out
        except Exception as e:  # noqa: BLE001
            logger.error(f"RefusalLog: error reading {self._path}: {e}")
            return []

    def _write_lines(self, records: Iterable[Dict[str, Any]]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        plaintext = (
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n"
        ).encode("utf-8")
        temp = self._path.with_suffix(".jsonl.tmp")
        try:
            from ..utils.encryption import get_encryptor
            enc = get_encryptor()
            payload = (
                enc.encrypt(plaintext, _CATEGORY)
                if enc.is_enabled(_CATEGORY) else plaintext
            )
            temp.write_bytes(payload)
            temp.rename(self._path)
        except Exception:
            if temp.exists():
                temp.unlink()
            raise

    # -- public API ------------------------------------------------------

    def record(self, record: Dict[str, Any]) -> None:
        """Append one refusal.  Never raises.

        Swallowing is the point: the caller is on its way to returning a
        422 that names a real authoring defect, and turning that into a
        500 because a sink was unwritable would replace an actionable
        error with a misleading one.
        """
        try:
            with self._lock:
                rows = self._read_lines()
                rows.append(record)
                if len(rows) > MAX_RETAINED_REFUSALS:
                    rows = rows[-MAX_RETAINED_REFUSALS:]
                self._write_lines(rows)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"RefusalLog: could not record refusal: {e}")

    def list_all(self) -> List[Dict[str, Any]]:
        """Every retained record, oldest first."""
        return self._read_lines()

    def cluster_by_signature(self) -> List[Dict[str, Any]]:
        """Refusals grouped by defect shape, most frequent first.

        The read side that justifies the write side: "this defect has been
        hit 14 times across 6 cards" is the shape of an actionable finding,
        whereas a flat list of 200 refusals is not.
        """
        groups: Dict[str, Dict[str, Any]] = {}
        for row in self._read_lines():
            sig = row.get("signature", "")
            g = groups.setdefault(sig, {
                "signature": sig,
                "count": 0,
                "card_ids": set(),
                "first_at": row.get("at", 0),
                "last_at": row.get("at", 0),
                "messages": [
                    e.get("message", "") for e in (row.get("errors") or [])
                ],
            })
            g["count"] += 1
            g["card_ids"].add(row.get("card_id", ""))
            g["first_at"] = min(g["first_at"], row.get("at", 0))
            g["last_at"] = max(g["last_at"], row.get("at", 0))
        out = []
        for g in groups.values():
            g["card_ids"] = sorted(x for x in g["card_ids"] if x)
            g["distinct_cards"] = len(g["card_ids"])
            out.append(g)
        out.sort(key=lambda g: (-g["count"], -g["last_at"]))
        return out


def get_refusal_log(project_dir: Path) -> RefusalLog:
    return RefusalLog(project_dir)
