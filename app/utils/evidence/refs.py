"""Resolving a citation to a value (design/evidence-ledger.md, Citations).

A reference is ``[namespace.]claim.id[@vN][.field.path]``.  Claim ids and
field paths are both dotted, so the claim is the LONGEST dotted prefix that
names a recorded claim; whatever follows is the field (default ``value``).
``s1.per_unit.distribution.median`` is the ``median`` of claim ``s1.per_unit``'s
distribution.  A relative reference is tried under ``namespace`` first.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from .store import Ledger, LedgerError

_PIN_SPLIT_RE = re.compile(r"^(?P<id>[^@]+)@v(?P<v>\d+)(?:\.(?P<field>.+))?$")


def _split(led: Ledger, ref: str) -> Optional[Tuple[str, Optional[int], Optional[str]]]:
    m = _PIN_SPLIT_RE.match(ref)
    if m:
        return m.group("id"), int(m.group("v")), m.group("field")
    parts = ref.split(".")
    for i in range(len(parts), 1, -1):
        cid = ".".join(parts[:i])
        if led.claim_versions(cid):
            return cid, None, ".".join(parts[i:]) or None
    return None


def resolve(led: Ledger, ref: str, namespace: Optional[str] = None) -> Dict[str, Any]:
    """``{claim: view, field, value}``.  Raises LedgerError for unknown refs."""
    ref = (ref or "").strip()
    candidates = [f"{namespace}.{ref}", ref] if namespace and not ref.startswith(namespace + ".") else [ref]
    for cand in candidates:
        split = _split(led, cand)
        if split is None:
            continue
        cid, ver, field = split
        claim = led.claim(f"{cid}@v{ver}" if ver else cid)
        if claim is None:
            raise LedgerError(f"{cid} has no version {ver}.")
        view = led.view(claim)
        value: Any = view
        for part in (field or "value").split("."):
            if not isinstance(value, dict) or part not in value:
                raise LedgerError(f"{cid} has no field {field!r}.")
            value = value[part]
        return {"claim": view, "field": field or "value", "value": value}
    raise LedgerError(f"Unknown claim: {ref}.")


def format_value(value: Any, fmt: Optional[str] = None, unit: Optional[str] = None) -> str:
    """Python format spec (``.2%``, ``,.0f``); numbers only, others as text."""
    if fmt and isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            text = format(value, fmt)
        except (ValueError, TypeError):
            raise LedgerError(f"Bad format {fmt!r} for {value!r}.") from None
    else:
        text = str(value)
    return f"{text} {unit}" if unit else text
