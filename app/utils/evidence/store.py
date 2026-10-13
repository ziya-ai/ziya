"""Append-only, signed ledger of observations, claims and verdicts.

design/evidence-ledger.md, Concepts and Storage.  Layout under the project:

    .ziya/ledger/observations.jsonl   one Observation per line
    .ziya/ledger/claims.jsonl         one Claim *version* per line
    .ziya/ledger/verdicts.jsonl       one Verdict per line
    .ziya/ledger/citations.jsonl      written by the exporter

Every line is a single ``O_APPEND`` write under an exclusive ``flock``, so
parallel lanes (threads or processes) can record concurrently, and every line
carries an HMAC over its canonical JSON with a per-user key in
``~/.ziya/ledger.key``.  A line whose signature does not verify (hand-edited,
or written without the key) is skipped on read and counted in ``stats()``.

Claims are never edited: recording an existing id appends version n+1, which
``supersedes`` version n.  A claim's status is derived, not stored: the
latest version's latest verdict decides it, and older versions are
``superseded``.

The read index is in memory and incremental (only appended bytes are parsed);
the spec's SQLite cache is deferred until a project is large enough to need it.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

MAX_INLINE_BYTES = 64 * 1024
SOURCES = ("sql", "file", "command", "tool", "computed")
OUTCOMES = ("agrees", "drifted", "refuted", "not_testable")
_STATUS_FOR_OUTCOME = {"agrees": "verified", "drifted": "drifted", "refuted": "refuted"}

# namespace.key, at least two dot-separated segments.
CLAIM_ID_RE = re.compile(r"^[A-Za-z0-9][\w-]*(\.[\w-]+)+$")
_PIN_RE = re.compile(r"^(?P<id>.+?)@v(?P<v>\d+)$")

_FILES = {
    "observation": "observations.jsonl",
    "claim": "claims.jsonl",
    "verdict": "verdicts.jsonl",
    "citation": "citations.jsonl",
}


class LedgerError(ValueError):
    """A request the ledger refuses.  The message is safe to show the model."""


# --------------------------------------------------------------------------- #
# Canonical bytes, hashing, signing
# --------------------------------------------------------------------------- #

def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _key_path() -> Path:
    from app.utils.paths import get_ziya_home
    return Path(get_ziya_home()) / "ledger.key"


_key_cache: Dict[str, bytes] = {}
_key_lock = threading.Lock()


def signing_key() -> bytes:
    """The per-user ledger key, created 0o600 on first use.

    Written to a temp file and linked into place, so concurrent first uses
    agree on one key instead of one process reading a half-written file.
    """
    path = _key_path()
    cached = _key_cache.get(str(path))
    if cached:
        return cached
    with _key_lock:
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".ledger.key.{os.getpid()}.{secrets.token_hex(4)}")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, secrets.token_hex(32).encode())
            finally:
                os.close(fd)
            try:
                os.link(tmp, path)
            except FileExistsError:
                pass
            finally:
                os.unlink(tmp)
        key = path.read_bytes().strip()
        if len(key) < 32:
            raise LedgerError(f"Ledger key {path} is unreadable or too short.")
        _key_cache[str(path)] = key
        return key


def _sign(record: Mapping[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k != "sig"}
    return hmac.new(signing_key(), canonical_json(body), hashlib.sha256).hexdigest()


def _sig_ok(record: Mapping[str, Any]) -> bool:
    sig = record.get("sig")
    return isinstance(sig, str) and hmac.compare_digest(sig, _sign(record))


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def parse_pin(ref: str) -> Tuple[str, Optional[int]]:
    """``a.b@v3`` -> (``a.b``, 3); ``a.b`` -> (``a.b``, None)."""
    m = _PIN_RE.match(ref or "")
    return (m.group("id"), int(m.group("v"))) if m else (ref, None)


def _json_value(v: Any) -> Any:
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        raise LedgerError("Claim values must be finite numbers.")
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    raise LedgerError(f"Claim value must be a number, string, boolean or null; got {type(v).__name__}.")


# --------------------------------------------------------------------------- #
# Ledger
# --------------------------------------------------------------------------- #

class Ledger:
    def __init__(self, root: Path):
        self.root = Path(root)
        # kind -> (inode, parsed-up-to offset, records, rejected count)
        self._cache: Dict[str, Tuple[Optional[int], int, List[dict], int]] = {}
        self._lock = threading.Lock()

    @classmethod
    def for_project(cls, project_root: str) -> "Ledger":
        return cls(Path(project_root) / ".ziya" / "ledger")

    # ---- low level -------------------------------------------------------

    def _path(self, kind: str) -> Path:
        return self.root / _FILES[kind]

    @contextlib.contextmanager
    def _locked(self, kind: str) -> Iterator[int]:
        self.root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path(kind), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield fd
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    @staticmethod
    def _write(fd: int, record: dict) -> None:
        record["sig"] = _sign(record)
        line = canonical_json(record) + b"\n"
        written = os.write(fd, line)
        if written != len(line):  # pragma: no cover - regular files write whole
            raise LedgerError("Short write to the ledger.")

    def _append(self, kind: str, record: dict) -> dict:
        with self._locked(kind) as fd:
            self._write(fd, record)
        return record

    def read(self, kind: str) -> List[dict]:
        """All verified records of ``kind``, in append order.

        Incremental: the files are append-only, so the cache keeps the byte
        offset it has parsed up to and only parses (and verifies) what was
        appended since.  A changed inode or a file shorter than the offset
        means it was replaced, and it is re-read from the start.  A trailing
        line without its newline is still being written and is left for the
        next read.
        """
        path = self._path(kind)
        try:
            st = path.stat()
        except FileNotFoundError:
            return []
        with self._lock:
            ino, offset, records, rejected = self._cache.get(kind, (None, 0, [], 0))
            if ino != st.st_ino or st.st_size < offset:
                offset, records, rejected = 0, [], 0
            if st.st_size == offset:
                self._cache[kind] = (st.st_ino, offset, records, rejected)
                return records
            records = list(records)
            with open(path, "rb") as f:
                f.seek(offset)
                chunk = f.read(st.st_size - offset)
            end = chunk.rfind(b"\n") + 1
            for raw in chunk[:end].splitlines():
                try:
                    rec = json.loads(raw)
                except ValueError:
                    rejected += 1
                    continue
                if isinstance(rec, dict) and _sig_ok(rec):
                    records.append(rec)
                else:
                    rejected += 1
            self._cache[kind] = (st.st_ino, offset + end, records, rejected)
            return records

    def stats(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for kind in _FILES:
            recs = self.read(kind)
            out[kind] = {"records": len(recs),
                         "rejected": self._cache.get(kind, (None, 0, [], 0))[3]}
        return out

    # ---- observations ----------------------------------------------------

    def record_observation(
        self,
        source: str,
        *,
        query_text: Optional[str] = None,
        path: Optional[str] = None,
        argv: Optional[Sequence[str]] = None,
        connector: Optional[str] = None,
        tool: Optional[str] = None,
        result_path: Optional[Path] = None,
        dataset_id: Optional[str] = None,
        inline: Any = None,
        has_inline: bool = False,
        volatile: bool = False,
        as_of: Optional[str] = None,
        provenance: Optional[Mapping[str, Any]] = None,
    ) -> dict:
        """Record one act of looking at data.

        Exactly one result: ``result_path`` (the server hashes the file),
        ``dataset_id`` (a registered dataset, hashed when it was stored) or
        ``inline`` (a value the caller supplies).  Only the first two are
        ``attested``: the server computed the hash from bytes it read.
        ``has_inline`` distinguishes an explicit inline ``null`` from absent.
        """
        if source not in SOURCES:
            raise LedgerError(f"source must be one of {', '.join(SOURCES)}; got {source!r}.")
        given = [n for n, v in (("result_path", result_path), ("dataset_id", dataset_id))
                 if v is not None] + (["inline"] if has_inline or inline is not None else [])
        if len(given) != 1:
            raise LedgerError("Give exactly one result: result_path, dataset_id or inline.")

        result: Dict[str, Any]
        if result_path is not None:
            p = Path(result_path)
            if not p.is_file():
                raise LedgerError(f"Result file not found: {result_path}")
            st = p.stat()
            result = {"sha256": sha256_file(p),
                      "ref": {"kind": "file", "path": str(p.resolve()), "bytes": st.st_size}}
            attested = True
        elif dataset_id is not None:
            from app.utils import dataset_store as ds
            try:
                meta = ds.load_meta(dataset_id)
            except ds.DatasetError as e:
                raise LedgerError(str(e)) from None
            if not meta.get("sha256"):
                raise LedgerError(f"Dataset {dataset_id} has no recorded hash.")
            result = {"sha256": meta["sha256"], "ref": {"kind": "dataset", "id": dataset_id},
                      "rows": meta.get("row_count"),
                      "columns": [c.get("name") for c in meta.get("columns", [])]}
            attested = True
        else:
            data = canonical_json(inline)
            if len(data) > MAX_INLINE_BYTES:
                raise LedgerError(
                    f"Inline result is {len(data):,} bytes; the limit is {MAX_INLINE_BYTES:,}. "
                    "Write it to a file (result_path) or register it as a dataset.")
            result = {"sha256": sha256_bytes(data), "ref": {"kind": "inline"}, "inline": inline}
            attested = False

        rec = {
            "kind": "observation", "id": _new_id("obs"), "at": time.time(),
            "source": source,
            **{k: v for k, v in (("query_text", query_text), ("path", path),
                                 ("argv", list(argv) if argv else None),
                                 ("connector", connector), ("tool", tool),
                                 ("as_of", as_of)) if v},
            "volatile": bool(volatile),
            "result": result,
            "attested": attested,
            "provenance": dict(provenance or {}),
        }
        return self._append("observation", rec)

    def observation(self, obs_id: str) -> Optional[dict]:
        for rec in reversed(self.read("observation")):
            if rec["id"] == obs_id:
                return rec
        return None

    # ---- claims ----------------------------------------------------------

    def claim_versions(self, claim_id: str) -> List[dict]:
        return [r for r in self.read("claim") if r["id"] == claim_id]

    def claim(self, ref: str) -> Optional[dict]:
        """A claim version: ``id`` for the latest, ``id@vN`` for a pin."""
        cid, ver = parse_pin(ref)
        versions = self.claim_versions(cid)
        if not versions:
            return None
        if ver is None:
            return versions[-1]
        return next((v for v in versions if v["version"] == ver), None)

    def record_claim(
        self,
        key: str,
        value: Any,
        *,
        namespace: Optional[str] = None,
        unit: Optional[str] = None,
        fmt: Optional[str] = None,
        population: Optional[Mapping[str, Any]] = None,
        distribution: Optional[Mapping[str, Any]] = None,
        derivation: Optional[Mapping[str, Any]] = None,
        tolerance: Optional[Mapping[str, Any]] = None,
        provenance: Optional[Mapping[str, Any]] = None,
    ) -> dict:
        """Append a claim version; returns the stored record.

        ``derivation`` must name at least one observation (``obs_ids``) or
        input claim (``inputs``), and every one of them must exist: a claim
        with no recorded source is what the ledger exists to prevent.
        """
        claim_id = self.qualify(key, namespace)
        der = dict(derivation or {})
        obs_ids = [str(o) for o in der.get("obs_ids") or []]
        raw_inputs = der.get("inputs") or []
        # inputs: a list of claim refs, or {name: ref} so an expression can
        # use its own names.  A list binds each input by its last id segment.
        if isinstance(raw_inputs, Mapping):
            named = [(str(n), str(r)) for n, r in raw_inputs.items()]
        else:
            named = [(None, str(r)) for r in raw_inputs]
        if not obs_ids and not named:
            raise LedgerError(
                "A claim needs a source: derivation.obs_ids (observations) or "
                "derivation.inputs (other claims).")
        missing = [o for o in obs_ids if self.observation(o) is None]
        if missing:
            raise LedgerError(f"Unknown observation(s): {', '.join(missing)}.")
        pins, bind = [], {}
        for name, ref in named:
            cid, ver = parse_pin(ref)
            cid = self.qualify(cid, namespace)
            c = self.claim(f"{cid}@v{ver}" if ver else cid)
            if c is None:
                raise LedgerError(f"Unknown input claim: {ref}.")
            pin = f"{c['id']}@v{c['version']}"
            pins.append(pin)
            bind_name = name or c["id"].rsplit(".", 1)[-1]
            if bind_name in bind:
                raise LedgerError(
                    f"Two inputs bind the name {bind_name!r}; give inputs as {{name: claim}}.")
            bind[bind_name] = pin
        der["obs_ids"], der["inputs"] = obs_ids, pins
        if bind:
            der["bind"] = bind
        tol = {k: float(v) for k, v in (tolerance or {}).items() if k in ("abs", "rel")}
        dist = None
        if distribution:
            dist = {str(k): _json_value(v) for k, v in distribution.items()}

        with self._locked("claim") as fd:
            # Version chosen under the file lock: two lanes recording the same
            # id get v2 and v3, never two v2s.  read() takes a fresh stat.
            prev = self.claim_versions(claim_id)
            version = prev[-1]["version"] + 1 if prev else 1
            rec = {
                "kind": "claim", "id": claim_id, "version": version, "at": time.time(),
                "value": _json_value(value),
                **{k: v for k, v in (("unit", unit), ("format", fmt)) if v},
                "population": dict(population or {}),
                **({"distribution": dist} if dist else {}),
                "derivation": der,
                "tolerance": tol,
                "supersedes": f"{claim_id}@v{version - 1}" if prev else None,
                "provenance": dict(provenance or {}),
            }
            self._write(fd, rec)
        return rec

    @staticmethod
    def qualify(key: str, namespace: Optional[str]) -> str:
        """Apply ``namespace`` to a relative key; validate the result."""
        key = (key or "").strip()
        if namespace and not key.startswith(namespace + "."):
            key = f"{namespace}.{key}"
        if not CLAIM_ID_RE.match(key):
            raise LedgerError(
                f"Invalid claim id {key!r}: use namespace.key (letters, digits, _ and -, "
                "dot-separated), or give a namespace.")
        return key

    # ---- verdicts and status ---------------------------------------------

    def record_verdict(self, claim_ref: str, *, method: str, outcome: str,
                       old_value: Any = None, new_value: Any = None,
                       detail: str = "", provenance: Optional[Mapping[str, Any]] = None) -> dict:
        if outcome not in OUTCOMES:
            raise LedgerError(f"outcome must be one of {', '.join(OUTCOMES)}.")
        c = self.claim(claim_ref)
        if c is None:
            raise LedgerError(f"Unknown claim: {claim_ref}.")
        delta = None
        if isinstance(old_value, (int, float)) and isinstance(new_value, (int, float)) \
                and not isinstance(old_value, bool) and not isinstance(new_value, bool):
            delta = new_value - old_value
        rec = {"kind": "verdict", "id": _new_id("vd"), "at": time.time(),
               "claim": f"{c['id']}@v{c['version']}", "method": method, "outcome": outcome,
               "old_value": old_value, "new_value": new_value, "delta": delta,
               "detail": detail, "provenance": dict(provenance or {})}
        return self._append("verdict", rec)

    def verdicts(self, claim_ref: str) -> List[dict]:
        c = self.claim(claim_ref)
        if c is None:
            return []
        pin = f"{c['id']}@v{c['version']}"
        return [v for v in self.read("verdict") if v["claim"] == pin]

    def status(self, claim: Mapping[str, Any]) -> Tuple[str, Optional[dict]]:
        """(status, latest verdict) for one claim version."""
        latest = self.claim_versions(claim["id"])
        verdicts = self.verdicts(f"{claim['id']}@v{claim['version']}")
        last = verdicts[-1] if verdicts else None
        if latest and latest[-1]["version"] != claim["version"]:
            return "superseded", last
        if last is not None and last["outcome"] in _STATUS_FOR_OUTCOME:
            return _STATUS_FOR_OUTCOME[last["outcome"]], last
        return "recorded", last

    def view(self, claim: Mapping[str, Any]) -> dict:
        """A claim version as tools and the exporter present it."""
        status, last = self.status(claim)
        out = {k: v for k, v in claim.items() if k not in ("sig", "kind")}
        out["status"] = status
        out["latest_verdict"] = None if last is None else {
            k: last[k] for k in ("at", "method", "outcome", "old_value", "new_value", "delta", "detail")}
        out["versions"] = len(self.claim_versions(claim["id"]))
        out["attested"] = self.attested(claim, last)
        return out

    def attested(self, claim: Mapping[str, Any], last: Optional[Mapping[str, Any]]) -> bool:
        """Decision 2: a claim is attested only once re-derived (computed or rerun)."""
        return bool(last and last["outcome"] == "agrees" and last["method"] in ("computed", "rerun"))

    def find(self, prefix: str) -> List[dict]:
        """Latest version of every claim whose id is ``prefix`` or under ``prefix.``."""
        latest: Dict[str, dict] = {}
        for rec in self.read("claim"):
            if rec["id"] == prefix or rec["id"].startswith(prefix + "."):
                latest[rec["id"]] = rec
        return [latest[k] for k in sorted(latest)]

    # ---- citations (written by the exporter) -----------------------------

    def record_citation(self, document: str, claim_ref: str, field: Optional[str],
                        rendered: str) -> dict:
        rec = {"kind": "citation", "id": _new_id("cit"), "at": time.time(),
               "document": document, "claim": claim_ref, "field": field, "rendered": rendered}
        return self._append("citation", rec)


_ledgers: Dict[str, Ledger] = {}
_ledgers_lock = threading.Lock()


def ledger_for(project_root: str) -> Ledger:
    """The process-wide Ledger for a project (shares the read cache)."""
    key = os.path.realpath(project_root)
    with _ledgers_lock:
        led = _ledgers.get(key)
        if led is None:
            led = _ledgers[key] = Ledger.for_project(key)
        return led
