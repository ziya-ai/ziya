"""
Output store: validated structured outputs, kept where ``analyze`` can read
them (design/typed-findings.md, Implementation notes 44-47).

A value that passed an output schema -- a finding emitted inside a Task
Card run under a contract, or one checked in an ordinary conversation with
``store_output`` -- is appended to ``<project>/.ziya/outputs/<family>.jsonl``.
The family is the schema's lineage root (``ziya:finding/v1`` -> ``finding``;
a project schema extending it lands in the same file), so a run's findings
and a conversation's findings are one table.

Why JSONL and not a dataset: a finding is a nested document, and DuckDB
reads JSONL with nested structs natively, so ``analyze`` can query
``data.impact.per_unit.median.value`` as written, with no flattening to keep
in step with the schema.  ``analyze(sql=..., inputs={"f": ".ziya/outputs/
findings.jsonl"})`` is the whole query interface.

Each line is one record::

    {"id": "out_...", "stored_at": <epoch s>, "family": "finding",
     "schema_id": ..., "schema_sha256": ..., "schema_lineage": [...],
     "key": <data["id"] when present>, "version": N,
     "data": {...the value...},
     "resolved_claims": [...], "unverified_numbers": [...],
     "provenance": {"via": "tool"|"run", conversation_id, run_id, block_id,
                    iteration...}}

``version`` counts records sharing a ``key`` within a family, so a finding
re-stored under the same id supersedes the previous one (the latest version
per key is what ``latest()`` returns); every version stays on disk.
Appends are serialised with ``flock`` like the evidence ledger's.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

STORE_DIR = Path(".ziya") / "outputs"
_FAMILY_RE = re.compile(r"^(?:ziya|project):([A-Za-z0-9][A-Za-z0-9_.-]*?)(?:/v\d+)?$")


def family_of(lineage: Iterable[str], schema_id: str) -> str:
    """The store file a schema's values belong to: the lineage root's name.

    The last ``ziya:`` ancestor wins (``project:audit-finding`` extending
    ``ziya:finding/v1`` -> ``finding``); a project schema with no shipped
    ancestor files under its own name; an inline schema under ``inline``.
    """
    ids = list(lineage) or [schema_id]
    shipped = [i for i in ids if isinstance(i, str) and i.startswith("ziya:")]
    chosen = shipped[-1] if shipped else ids[-1]
    m = _FAMILY_RE.match(chosen or "")
    name = m.group(1) if m else "inline"
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name.replace("-", "_")).strip("_") or "inline"


def store_path(project_root: str, family: str) -> Path:
    return Path(project_root) / STORE_DIR / f"{family}.jsonl"


def _records(path: Path) -> List[dict]:
    if not path.is_file():
        return []
    out = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue              # a torn line is skipped, never fatal
    return out


def append(
    project_root: str,
    *,
    schema_id: str,
    schema_sha256: str,
    schema_lineage: Iterable[str],
    data: Any,
    resolved_claims: Optional[List[dict]] = None,
    unverified_numbers: Optional[List[str]] = None,
    provenance: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Append one validated value; returns the stored record."""
    lineage = list(schema_lineage or [])
    fam = family_of(lineage, schema_id)
    path = store_path(project_root, fam)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = data.get("id") if isinstance(data, Mapping) and isinstance(data.get("id"), str) else None
    with path.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            version = 1
            if key is not None:
                fh.seek(0)
                version = 1 + sum(1 for r in _records(path) if r.get("key") == key)
                fh.seek(0, os.SEEK_END)
            rec = {
                # ``stored_at``, not ``at``: AT is a DuckDB reserved word, and
                # ``ORDER BY at`` in an analyze query is a parser error.
                "id": f"out_{secrets.token_hex(8)}", "stored_at": time.time(), "family": fam,
                "schema_id": schema_id, "schema_sha256": schema_sha256,
                "schema_lineage": lineage, "key": key, "version": version,
                "data": data,
                "resolved_claims": list(resolved_claims or []),
                "unverified_numbers": list(unverified_numbers or []),
                "provenance": dict(provenance or {}),
            }
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            fh.flush()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return rec


def read(project_root: str, family: str) -> List[dict]:
    return _records(store_path(project_root, family))


def latest(project_root: str, family: str) -> List[dict]:
    """The newest version of every keyed record, plus every unkeyed one."""
    best: Dict[str, dict] = {}
    unkeyed: List[dict] = []
    for r in read(project_root, family):
        k = r.get("key")
        if k is None:
            unkeyed.append(r)
        elif k not in best or (r.get("version") or 0) >= (best[k].get("version") or 0):
            best[k] = r
    return [*best.values(), *unkeyed]


def families(project_root: str) -> List[str]:
    d = Path(project_root) / STORE_DIR
    return sorted(p.stem for p in d.glob("*.jsonl")) if d.is_dir() else []


def relative_path(family: str) -> str:
    """The project-relative path to hand to ``analyze`` as an input."""
    return str(STORE_DIR / f"{family}.jsonl")
