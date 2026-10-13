"""Re-checking claims (design/evidence-ledger.md, ``ledger_verify``).

Two methods exist until the connector (item 3) and job runner (item 4) can
re-execute sources:

* ``rehash`` re-hashes every observation the claim was derived from.  A file
  or dataset whose bytes changed means the claim may no longer hold
  (``drifted``); a volatile source is expected to change, so rehash cannot
  judge it (``not_testable``).  Input claims that are drifted or refuted
  propagate.
* ``computed`` re-evaluates ``derivation.expression`` with the CURRENT value
  of each input claim and compares within the claim's tolerance.

Every check appends a verdict; nothing is edited.
"""
from __future__ import annotations

import ast
import math
import operator
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .store import Ledger, LedgerError, canonical_json, parse_pin, sha256_bytes, sha256_file

METHODS = ("rehash", "computed")
DEFAULT_REL_TOLERANCE = 1e-9

_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
           ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {"abs": abs, "min": min, "max": max, "round": round,
          "sqrt": math.sqrt, "log": math.log, "log10": math.log10, "exp": math.exp}


def evaluate(expression: str, names: Mapping[str, Any]) -> float:
    """Arithmetic over ``names``: + - * / // % **, unary -, and a few functions.

    No attribute access, subscripts or arbitrary calls, so a stored expression
    cannot do anything but arithmetic.
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise LedgerError(f"Expression does not parse: {e.msg}.") from None

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise LedgerError(f"Expression uses {node.id!r}, which is not an input "
                                  f"(inputs: {', '.join(sorted(names)) or 'none'}).")
            v = names[node.id]
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise LedgerError(f"Input {node.id!r} is not a number.")
            return v
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 64:
                raise LedgerError("Exponent too large.")
            return _BINOPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in _FUNCS and not node.keywords:
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise LedgerError(f"Expression element not allowed: {ast.dump(node)[:60]}.")

    try:
        return ev(tree)
    except ZeroDivisionError:
        raise LedgerError("Expression divides by zero.") from None


def within(old: Any, new: Any, tolerance: Optional[Mapping[str, float]]) -> bool:
    if isinstance(old, bool) or isinstance(new, bool) or not all(
            isinstance(x, (int, float)) for x in (old, new)):
        return old == new
    tol = dict(tolerance or {})
    if "abs" in tol and abs(new - old) <= tol["abs"]:
        return True
    rel = tol.get("rel", DEFAULT_REL_TOLERANCE if "abs" not in tol else None)
    return rel is not None and abs(new - old) <= rel * max(abs(old), abs(new), 1e-300)


def _rehash_observation(led: Ledger, obs_id: str) -> tuple:
    """(outcome, detail) for one observation."""
    obs = led.observation(obs_id)
    if obs is None:
        return "not_testable", f"{obs_id}: observation missing"
    res = obs["result"]
    ref = res.get("ref", {})
    kind = ref.get("kind")
    if kind == "file":
        p = Path(ref["path"])
        if not p.is_file():
            return "not_testable", f"{obs_id}: {p} no longer exists"
        now = sha256_file(p)
    elif kind == "dataset":
        from app.utils import dataset_store as ds
        try:
            now = ds.load_meta(ref["id"]).get("sha256")
        except ds.DatasetError:
            return "not_testable", f"{obs_id}: dataset {ref['id']} no longer exists"
    elif kind == "inline":
        now = sha256_bytes(canonical_json(res.get("inline")))
    else:
        return "not_testable", f"{obs_id}: unknown result kind {kind!r}"
    if now == res["sha256"]:
        return "agrees", f"{obs_id}: unchanged"
    if obs.get("volatile"):
        return "not_testable", f"{obs_id}: volatile source changed; rehash cannot judge it"
    return "drifted", f"{obs_id}: content changed since it was recorded"


_RANK = {"agrees": 0, "not_testable": 1, "drifted": 2, "refuted": 3}


def verify_claim(led: Ledger, ref: str, method: str,
                 provenance: Optional[Mapping[str, Any]] = None) -> dict:
    """Re-check one claim version and append the verdict; returns it."""
    if method not in METHODS:
        if method == "rerun":
            raise LedgerError("rerun needs the data-source connector and job runner, which "
                              "are not built yet; use rehash or computed.")
        raise LedgerError(f"method must be one of {', '.join(METHODS)}.")
    claim = led.claim(ref)
    if claim is None:
        raise LedgerError(f"Unknown claim: {ref}.")
    der = claim.get("derivation", {})
    old = claim["value"]
    new: Any = old

    if method == "rehash":
        results = [_rehash_observation(led, o) for o in der.get("obs_ids", [])]
        for pin in der.get("inputs", []):
            cid, _ = parse_pin(pin)
            latest = led.claim(cid)
            status, _ = led.status(latest)
            if status in ("drifted", "refuted"):
                results.append(("drifted", f"input {cid} is {status}"))
            elif latest["version"] != parse_pin(pin)[1]:
                results.append(("drifted", f"input {cid} has a newer version (v{latest['version']})"))
            else:
                results.append(("agrees", f"input {pin} current"))
        outcome = max((r[0] for r in results), key=_RANK.__getitem__, default="not_testable")
        detail = "; ".join(r[1] for r in results)
    else:
        expr = der.get("expression")
        bind: Dict[str, str] = der.get("bind") or {}
        if not expr or not bind:
            raise LedgerError("computed needs derivation.expression and input claims.")
        values, notes = {}, []
        for name, pin in bind.items():
            cid, ver = parse_pin(pin)
            latest = led.claim(cid)
            values[name] = latest["value"]
            if latest["version"] != ver:
                notes.append(f"{cid} now v{latest['version']} (recorded against v{ver})")
        new = evaluate(expr, values)
        outcome = "agrees" if within(old, new, claim.get("tolerance")) else "drifted"
        detail = f"{expr} = {new!r} (recorded {old!r})" + (f"; {'; '.join(notes)}" if notes else "")

    return led.record_verdict(f"{claim['id']}@v{claim['version']}", method=method,
                              outcome=outcome, old_value=old, new_value=new,
                              detail=detail, provenance=provenance)
