"""The ledger tools' logic (``ledger_record`` / ``ledger_query`` / ``ledger_verify``).

Kept out of ``app/mcp/tools/ledger_tools.py`` so the tool classes are thin
and this is testable without the MCP layer.  Every function returns a
JSON-safe dict; refusals come back as ``{"error": True, "message": ...}``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from .refs import resolve
from .store import LedgerError, ledger_for
from .verify import verify_claim

MAX_QUERY_RESULTS = 200


def _err(message: str) -> Dict[str, Any]:
    return {"error": True, "message": message}


def _compact(view: Mapping[str, Any]) -> Dict[str, Any]:
    keep = ("id", "version", "value", "unit", "status", "attested", "population",
            "distribution", "derivation", "tolerance", "supersedes", "latest_verdict",
            "versions", "at")
    return {k: view[k] for k in keep if view.get(k) not in (None, {}, [])}


def record(
    *,
    project_root: str,
    resolve_path: Callable[[str], Path],
    observation: Optional[Mapping[str, Any]] = None,
    claim: Optional[Mapping[str, Any]] = None,
    provenance: Optional[Mapping[str, Any]] = None,
    namespace: Optional[str] = None,
) -> Dict[str, Any]:
    """Record an observation, a claim, or both (the claim then cites the observation).

    ``namespace`` is the inherited default (a State block's
    ``evidence_namespace``); a claim that gives its own ``namespace`` wins.
    """
    if not observation and not claim:
        return _err("Give an observation, a claim, or both.")
    led = ledger_for(project_root)
    out: Dict[str, Any] = {}
    try:
        if observation:
            o = dict(observation)
            result_path = o.pop("result_path", None)
            if result_path:
                try:
                    result_path = resolve_path(str(result_path))
                except ValueError:
                    return _err(f"result_path {result_path!r} is outside the allowed directories.")
            has_inline = "inline" in o
            rec = led.record_observation(
                o.pop("source", ""), result_path=result_path, has_inline=has_inline,
                provenance=provenance,
                **{k: o.get(k) for k in ("query_text", "path", "argv", "connector", "tool",
                                         "dataset_id", "inline", "volatile", "as_of")
                   if k in o},
            )
            out["observation"] = {"id": rec["id"], "sha256": rec["result"]["sha256"],
                                  "attested": rec["attested"]}
        if claim:
            c = dict(claim)
            der = dict(c.get("derivation") or {})
            if "observation" in out and not der.get("obs_ids"):
                der["obs_ids"] = [out["observation"]["id"]]
            if "key" not in c or "value" not in c:
                return _err("A claim needs key and value.")
            rec = led.record_claim(
                c["key"], c["value"], namespace=c.get("namespace") or namespace, unit=c.get("unit"),
                fmt=c.get("format"), population=c.get("population"),
                distribution=c.get("distribution"), derivation=der,
                tolerance=c.get("tolerance"), provenance=provenance,
            )
            out["claim"] = _compact(led.view(rec))
    except LedgerError as e:
        # An observation recorded before a claim refusal stays recorded; say so.
        return {**_err(str(e)), **out}
    return out


def query(
    *,
    project_root: str,
    ref: Optional[str] = None,
    prefix: Optional[str] = None,
    namespace: Optional[str] = None,
    history: bool = False,
) -> Dict[str, Any]:
    """One reference (claim or claim.field), or every claim under a prefix."""
    led = ledger_for(project_root)
    try:
        if ref:
            r = resolve(led, ref, namespace)
            out: Dict[str, Any] = {"claim": _compact(r["claim"]), "field": r["field"],
                                   "value": r["value"]}
            if history:
                out["history"] = [_compact(led.view(v))
                                  for v in led.claim_versions(r["claim"]["id"])]
            return out
        p = prefix or namespace
        if not p:
            return _err("Give ref (a claim or claim.field) or prefix (a namespace).")
        found = led.find(p)
        rows = [{k: v for k, v in (("id", c["id"]), ("version", c["version"]),
                                   ("value", c["value"]), ("unit", c.get("unit")),
                                   ("status", led.view(c)["status"])) if v is not None}
                for c in found[:MAX_QUERY_RESULTS]]
        return {"prefix": p, "count": len(found), "claims": rows,
                **({"truncated": True} if len(found) > MAX_QUERY_RESULTS else {})}
    except LedgerError as e:
        return _err(str(e))


def verify(
    *,
    project_root: str,
    method: str,
    refs: Optional[List[str]] = None,
    prefix: Optional[str] = None,
    provenance: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Re-check claims and append verdicts.  Per-claim failures do not stop the rest."""
    led = ledger_for(project_root)
    targets = list(refs or [])
    if prefix:
        targets += [c["id"] for c in led.find(prefix)]
    if not targets:
        return _err("Give refs (claim ids) or prefix (a namespace).")
    results, counts = [], {}
    for ref in targets[:MAX_QUERY_RESULTS]:
        try:
            v = verify_claim(led, ref, method, provenance=provenance)
            results.append({"claim": v["claim"], "outcome": v["outcome"], "detail": v["detail"]})
            counts[v["outcome"]] = counts.get(v["outcome"], 0) + 1
        except LedgerError as e:
            results.append({"claim": ref, "error": str(e)})
            counts["error"] = counts.get("error", 0) + 1
    return {"method": method, "counts": counts, "results": results,
            **({"truncated": True} if len(targets) > MAX_QUERY_RESULTS else {})}


def task_context(conversation_id: Optional[str] = None) -> Dict[str, Any]:
    """Provenance the server knows about the current call, never the caller.

    Conversation (the given id, else the request's), and inside a Task Card
    the run, block and source from the usage-attribution contextvar the task
    executor sets, plus the enclosing Repeat iteration (owner block and
    index; a stand-in for an attempt number until reruns exist).  Shared by
    the ledger tools and the shell scope token so the two cannot drift.
    """
    out: Dict[str, Any] = {}
    try:
        from app.context import (
            get_conversation_id_or_none, get_task_iteration_context, get_usage_attribution,
        )
        cid = conversation_id or get_conversation_id_or_none()
        if cid:
            out["conversation_id"] = cid
        attr = get_usage_attribution() or {}
        for k in ("run_id", "block_id", "source"):
            if attr.get(k):
                out[k] = attr[k]
        it = get_task_iteration_context()
        if it and it.get("block_id") is not None and it.get("index") is not None:
            out["iteration"] = {"block_id": it["block_id"], "index": int(it["index"])}
    except Exception:  # noqa: BLE001 -- provenance is best effort, never blocks a record
        pass
    return out


def default_namespace() -> Optional[str]:
    """The ``evidence_namespace`` a State block declared for this task, if any."""
    try:
        from app.context import get_evidence_namespace
        return get_evidence_namespace()
    except Exception:  # noqa: BLE001
        return None


def ledger_root(fallback: Optional[str]) -> Optional[str]:
    """The project whose ledger to use: the task's card root when set, else ``fallback``.

    ``fallback`` is the caller's workspace, which inside a task with a scope
    ``cwd`` is a subdirectory; see ``app.context.set_ledger_root``.
    """
    try:
        from app.context import get_ledger_root
        return get_ledger_root() or fallback
    except Exception:  # noqa: BLE001
        return fallback


def provenance_from_context(**extra: Any) -> Dict[str, Any]:
    """Provenance for a record made through a builtin tool."""
    out: Dict[str, Any] = {"via": "tool", **task_context()}
    out.update({k: v for k, v in extra.items() if v})
    return out


def claims_recorded(project_root: str, *, run_id: str, block_id: str,
                    since: float = 0.0) -> List[str]:
    """Pins (``id@vN``) of claim versions recorded by one task block.

    Matches provenance ``run_id`` and ``block_id`` (tool and shell records
    both carry them) and ``at >= since`` so a block re-run in a loop lists
    only its own pass.  Reads nothing when the project has no ledger.
    """
    if not (project_root and run_id and block_id):
        return []
    led = ledger_for(project_root)
    out: List[str] = []
    for c in led.read("claim"):
        p = c.get("provenance") or {}
        if p.get("run_id") == run_id and p.get("block_id") == block_id and c.get("at", 0) >= since:
            out.append(f"{c['id']}@v{c['version']}")
    return out
