"""
What happens when an MCP server's tool definitions change.

The rug-pull gate (app/mcp/manager.py) compares each server's tool
definitions against a baseline and holds the server when they differ.  This
module owns the parts of that decision that are not connection handling:

* the baseline file, written one server at a time under a lock so several
  Ziya processes sharing ``~/.ziya`` do not overwrite each other's approvals
  with a copy they loaded at startup;
* a per-tool snapshot kept beside the baseline, so a change can be described
  ("2 added, 1 description changed") instead of shown as two hashes;
* the update policy: ``ask`` (hold on every change, the default) or
  ``follow`` (accept a change without asking, provided nothing in it matches
  an injection pattern), set per server or per registry provider;
* ``evaluate_connect``, the verdict the manager acts on.

``follow`` is narrower than ``"trusted": true`` in mcp_config.json: the
poisoning scan stays on, every accepted change is written to the security
audit log, and a per-server grant is bound to the command that launches the
server, so repointing the entry at something else returns it to ``ask``.

Files (plain JSON, in the directory that holds mcp_tool_fingerprints.json):
  mcp_tool_snapshots.json   {server: {fingerprint, tools, accepted_at, accepted_by, last_change}}
  mcp_update_policy.json    {servers: {name: {policy, launch, set_at}}, publishers: {id: {policy, set_at}}}
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from app.utils.logging_utils import logger

ASK = "ask"
FOLLOW = "follow"
POLICIES = (ASK, FOLLOW)

SNAPSHOT_FILE = "mcp_tool_snapshots.json"
POLICY_FILE = "mcp_update_policy.json"

# fingerprint_tools([]): what a connect whose tools/list failed hashes to.
EMPTY_FINGERPRINT = hashlib.sha256(b"[]").hexdigest()

_DESCRIPTION_KEEP = 240


# -- plain JSON files, locked read-modify-write ------------------------------

def default_state_dir() -> Path:
    try:
        from app.utils.paths import get_ziya_home
        return Path(get_ziya_home())
    except Exception:  # noqa: BLE001
        return Path.home() / ".ziya"


def state_dir_of(manager: Any) -> Path:
    """The directory the manager keeps its baseline in (tests redirect it)."""
    p = getattr(manager, "_fingerprint_store_path", None)
    return Path(p).parent if isinstance(p, (str, os.PathLike)) else default_state_dir()


def _read(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _update(path: Path, fn: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
    """Apply ``fn`` to the document on disk under a lock and replace it atomically."""
    from app.storage.json_state import JsonStateStore
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with JsonStateStore(path).locked():
        doc = _read(path)
        fn(doc)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w") as f:
            json.dump(doc, f, indent=2)
        os.replace(tmp, path)
        return doc


# -- baseline (the manager's {server: sha256} file) ---------------------------

def read_baselines(path: Path) -> Dict[str, str]:
    return {k: v for k, v in _read(Path(path)).items() if isinstance(v, str)}


def read_baseline(path: Path, server: str) -> Optional[str]:
    """The baseline on disk right now, which another process may have just approved."""
    return read_baselines(path).get(server)


def write_baseline(path: Path, server: str, fingerprint: Optional[str]) -> Dict[str, str]:
    """Set (or, with None, remove) one server's baseline, leaving the rest as found on disk."""
    def apply(doc: Dict[str, Any]) -> None:
        if fingerprint is None:
            doc.pop(server, None)
        else:
            doc[server] = fingerprint
    return {k: v for k, v in _update(Path(path), apply).items() if isinstance(v, str)}


# -- snapshots ---------------------------------------------------------------

def _h(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


def snapshot_tools(tools: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for t in tools:
        name = t.get("name")
        if not isinstance(name, str) or not name:
            continue
        desc = t.get("description")
        out[name] = {"d": _h(desc), "s": _h(t.get("inputSchema")),
                     "description": (desc if isinstance(desc, str) else "")[:_DESCRIPTION_KEEP]}
    return out


def load_snapshot(state_dir: Path, server: str) -> Optional[Dict[str, Any]]:
    rec = _read(Path(state_dir) / SNAPSHOT_FILE).get(server)
    return rec if isinstance(rec, dict) and isinstance(rec.get("tools"), dict) else None


def save_snapshot(state_dir: Path, server: str, fingerprint: str, tools: Iterable[Dict[str, Any]],
                  accepted_by: str = "first_connect", change: Optional[str] = None) -> None:
    """Record what was accepted.  ``accepted_by``: first_connect | user | policy.  Never raises."""
    tools = list(tools)
    prior = load_snapshot(state_dir, server)
    if prior and prior.get("fingerprint") == fingerprint:
        return                                  # the common case: nothing to write

    def apply(doc: Dict[str, Any]) -> None:
        prev = doc.get(server) if isinstance(doc.get(server), dict) else {}
        if prev.get("fingerprint") == fingerprint and isinstance(prev.get("tools"), dict):
            return                              # unchanged: keep who accepted it and when
        doc[server] = {"fingerprint": fingerprint, "tools": snapshot_tools(tools),
                       "accepted_at": time.time(), "accepted_by": accepted_by,
                       "last_change": change}
    try:
        _update(Path(state_dir) / SNAPSHOT_FILE, apply)
    except OSError as e:
        logger.warning(f"Could not record tool snapshot for '{server}': {e}")


def drop_server(state_dir: Path, baseline_path: Path, server: str) -> None:
    """Forget a server's baseline and snapshot (a name reused by a new install)."""
    try:
        write_baseline(baseline_path, server, None)
        _update(Path(state_dir) / SNAPSHOT_FILE, lambda d: d.pop(server, None))
    except OSError as e:
        logger.warning(f"Could not clear stored definitions for '{server}': {e}")


def diff_tools(snapshot: Optional[Dict[str, Any]], tools: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """What changed between an accepted snapshot and the tools a server now lists.

    ``known`` is False when there is no snapshot to compare against (a
    baseline recorded before snapshots existed); the lists are then empty
    and the caller must not read that as "nothing changed".
    """
    now = snapshot_tools(tools)
    if not snapshot or not isinstance(snapshot.get("tools"), dict):
        return {"known": False, "added": [], "removed": [], "changed": []}
    was = snapshot["tools"]
    changed = []
    for name in sorted(set(was) & set(now)):
        what = [label for key, label in (("d", "description"), ("s", "parameters"))
                if was[name].get(key) != now[name][key]]
        if what:
            changed.append({"name": name, "what": what})
    return {"known": True, "added": sorted(set(now) - set(was)),
            "removed": sorted(set(was) - set(now)), "changed": changed}


def summarize(diff: Dict[str, Any]) -> str:
    if not diff.get("known"):
        return "previous definitions were not recorded"
    parts = [f"{len(diff[k])} {k}" for k in ("added", "removed", "changed") if diff.get(k)]
    return ", ".join(parts) or "tool order or metadata only"


# -- policy ------------------------------------------------------------------

def launch_identity(cfg: Optional[Dict[str, Any]]) -> str:
    """What starts this server.  A per-server ``follow`` grant is bound to it."""
    cfg = cfg or {}
    return _h({k: cfg.get(k) for k in ("command", "args", "url", "remote_url",
                                         "registry_provider", "service_id")})


def publisher_of(cfg: Optional[Dict[str, Any]]) -> Optional[str]:
    """The registry provider the server was installed from, if any.

    This is provenance recorded at install time, not a verified identity:
    MCP has no signed publisher.  A publisher grant therefore means "servers
    I installed through this registry", nothing stronger.
    """
    p = (cfg or {}).get("registry_provider")
    return p if isinstance(p, str) and p else None


def get_policy(state_dir: Path, server: str, cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """``{policy, via, publisher, lapsed}``; ``via`` is server | publisher | None.

    ``lapsed``: a per-server grant exists but the launch command has changed
    since it was given, so it no longer applies.
    """
    doc = _read(Path(state_dir) / POLICY_FILE)
    publisher = publisher_of(cfg)
    prec = (doc.get("publishers") or {}).get(publisher) if publisher else None
    publisher_follows = isinstance(prec, dict) and prec.get("policy") == FOLLOW
    # publisher_follows is reported even when a per-server choice overrides
    # it, so the UI can show the grant exists.
    out: Dict[str, Any] = {"policy": ASK, "via": None, "publisher": publisher, "lapsed": False,
                           "publisher_follows": publisher_follows}
    rec = (doc.get("servers") or {}).get(server)
    if isinstance(rec, dict) and rec.get("policy") in POLICIES:
        if rec.get("policy") == ASK:
            out["via"] = "server"               # an explicit "ask" overrides a publisher grant
            return out
        if rec.get("launch") == launch_identity(cfg):
            out.update(policy=FOLLOW, via="server")
            return out
        out["lapsed"] = True
    if publisher_follows:
        out.update(policy=FOLLOW, via="publisher")
    return out


def set_server_policy(state_dir: Path, server: str, cfg: Optional[Dict[str, Any]], policy: str) -> None:
    if policy not in POLICIES:
        raise ValueError(f"unknown update policy: {policy!r}")
    rec = {"policy": policy, "launch": launch_identity(cfg), "set_at": time.time()}
    _update(Path(state_dir) / POLICY_FILE, lambda d: d.setdefault("servers", {}).__setitem__(server, rec))
    audit("policy_set", server, details={"policy": policy, "scope": "server"})


def set_publisher_policy(state_dir: Path, publisher: str, policy: str) -> None:
    if policy not in POLICIES:
        raise ValueError(f"unknown update policy: {policy!r}")
    if not publisher:
        raise ValueError("no publisher")

    def apply(doc: Dict[str, Any]) -> None:
        pubs = doc.setdefault("publishers", {})
        if policy == ASK:
            pubs.pop(publisher, None)
        else:
            pubs[publisher] = {"policy": policy, "set_at": time.time()}
    _update(Path(state_dir) / POLICY_FILE, apply)
    audit("policy_set", publisher, details={"policy": policy, "scope": "publisher"})


# -- verdict -----------------------------------------------------------------

def flagged_tools(server: str, tools: Iterable[Dict[str, Any]],
                  only: Optional[Set[str]] = None) -> Dict[str, List[str]]:
    """Tools whose description matches a blocking injection pattern.

    ``only`` limits the check to those names (the tools a change touched).
    Permission-disabled tools are skipped, as the connect-time scan skips
    them: they never reach a prompt.
    """
    from app.mcp.tool_guard import classify_warnings, scan_tool_description
    disabled: Set[str] = set()
    try:
        from app.mcp.permissions import get_permissions_manager
        perms = get_permissions_manager().get_permissions() or {}
        default = perms.get("defaults", {}).get("tool", "enabled")
        per = perms.get("servers", {}).get(server, {}).get("tools", {})
        disabled = {t.get("name") for t in tools
                    if per.get(t.get("name"), {}).get("permission", default) == "disabled"}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"update policy: permissions unavailable: {e}")
    out: Dict[str, List[str]] = {}
    for t in tools:
        name = t.get("name")
        if not isinstance(name, str) or name in disabled or (only is not None and name not in only):
            continue
        blocking, _advisory = classify_warnings(scan_tool_description(name, t.get("description")))
        if blocking:
            out[name] = blocking
    return out


def evaluate_connect(*, server: str, cfg: Optional[Dict[str, Any]], tools: List[Dict[str, Any]],
                     old_fp: Optional[str], new_fp: str, force_ok: bool,
                     state_dir: Path) -> Dict[str, Any]:
    """Decide what a connect does with the server's current definitions.

    ``action``:
      skip_empty  no tools were listed (tools/list failed or timed out):
                  record nothing, compare nothing, hold nothing
      baseline    accept and record: first sight, builtin/trusted, an empty
                  baseline left by an earlier failed listing, or a
                  fingerprint a human force-accepted
      unchanged   matches the baseline
      follow      changed, accepted under the ``follow`` policy
      quarantine  changed, hold for a human
    """
    cfg = cfg or {}
    if not tools:
        return {"action": "skip_empty"}
    if cfg.get("builtin") or cfg.get("trusted") or force_ok:
        return {"action": "baseline"}
    if old_fp is None or old_fp == EMPTY_FINGERPRINT:
        return {"action": "baseline"}
    if old_fp == new_fp:
        return {"action": "unchanged"}
    diff = diff_tools(load_snapshot(state_dir, server), tools)
    policy = get_policy(state_dir, server, cfg)
    verdict: Dict[str, Any] = {"action": "quarantine", "diff": diff, "summary": summarize(diff),
                               "policy": policy, "flagged": {}}
    if policy["policy"] != FOLLOW:
        return verdict
    touched = ({c["name"] for c in diff["changed"]} | set(diff["added"])) if diff["known"] else None
    flagged = flagged_tools(server, tools, touched)
    if flagged:
        verdict["flagged"] = flagged
        verdict["summary"] += f"; {len(flagged)} changed tool(s) match an injection pattern"
        return verdict
    verdict["action"] = "follow"
    return verdict


def audit(action: str, server: str, old_fp: Optional[str] = None, new_fp: Optional[str] = None,
          summary: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
    """One line in ~/.ziya/audit/security_<date>.jsonl per gate decision.  Never raises."""
    try:
        from app.utils.tool_audit_log import log_security_event
        log_security_event(f"mcp_definitions_{action}", source_tool=server, details={
            **({"old_fingerprint": old_fp[:16]} if old_fp else {}),
            **({"new_fingerprint": new_fp[:16]} if new_fp else {}),
            **({"summary": summary} if summary else {}),
            **(details or {}),
        })
    except Exception:  # noqa: BLE001
        pass


# -- what the Bench shows ----------------------------------------------------

def publisher_display_name(identifier: Optional[str]) -> Optional[str]:
    """The registry's own name for a ``registry_provider`` id, for display.

    The id is whatever the registry that installed the server wrote into
    mcp_config.json (``pulsemcp``, ``official-mcp``, or an id contributed by
    a plugin).  Falls back to the id when that registry is not loaded.
    """
    if not identifier:
        return None
    try:
        from app.mcp.registry.registry import get_provider_registry, initialize_registry_providers
        provider = get_provider_registry().get_provider(identifier)
        if provider is None:
            initialize_registry_providers()
            provider = get_provider_registry().get_provider(identifier)
        name = getattr(provider, "name", None)
        return name if isinstance(name, str) and name else identifier
    except Exception as e:  # noqa: BLE001
        logger.debug(f"update policy: registry name for {identifier!r} unavailable: {e}")
        return identifier


def describe(state_dir: Path, server: str, cfg: Optional[Dict[str, Any]],
             held_tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Policy and history for the drawer; ``changes`` only for a held server."""
    out = dict(get_policy(state_dir, server, cfg))
    out["publisher_name"] = publisher_display_name(out.get("publisher"))
    snap = load_snapshot(state_dir, server)
    if snap:
        out.update(accepted_at=snap.get("accepted_at"), accepted_by=snap.get("accepted_by"),
                   last_change=snap.get("last_change"))
    if held_tools is not None:
        diff = diff_tools(snap, held_tools)
        out["changes"] = {**diff, "summary": summarize(diff)}
    return out
