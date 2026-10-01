"""Per-domain activity clocks for probationary memory aging.

The original clock was one global counter, advanced on every extraction
run, i.e. on every streamed response in any project.  Measured on the live
store (2026-09-24..26): ~44 ticks per active day, 85% of them from Ziya
projects, so ARCHIVAL_AGE_THRESHOLD=7 was about four hours of *any* work.
A Kuiper proposal made on a Ziya-heavy day decayed before a Kuiper
conversation ever had a chance to confirm it.

Here a proposal ages only with work in its own domain:

  * One clock per project path, plus a global clock for proposals with no
    project.
  * A tick is a *session*: at most one per (conversation, day).  A
    40-turn conversation is one chance to confirm a fact, not forty; five
    parallel jobs in one domain are five.
  * A proposal records a snapshot of every clock when it is made.  Its
    domain age is the ticks since then summed over the projects in its
    collection (app/utils/memory_collections.py).  Because the snapshot is
    per project, the age stays right if the grouping changes later.
  * Backstop: a proposal whose domain has had no session at all for
    DORMANT_DAYS since it was made is archived as dormant.

Proposals without a snapshot (legacy) keep the global-counter rule; see
``effective_counter``.
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set

CLOCK_FILE = "domain_clocks.json"
# Dedup keys older than this are dropped; a conversation resumed later
# is a new session anyway.
SEEN_KEEP_DAYS = 3
DORMANT_DAYS = 90

# Overridable in tests.
_default_path: Optional[Path] = None


def _path(path: Optional[Path] = None) -> Path:
    if path is not None:
        return Path(path)
    if _default_path is not None:
        return _default_path
    from app.utils.paths import get_ziya_home
    return get_ziya_home() / "memory" / CLOCK_FILE


def _empty() -> Dict[str, Any]:
    return {"projects": {}, "global": 0, "last": {}, "seen": {}}


def load(path: Optional[Path] = None) -> Dict[str, Any]:
    """Current clock state; an unreadable or malformed file reads as empty."""
    p = _path(path)
    state = _empty()
    try:
        if p.exists():
            with open(p) as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                for k in state:
                    if isinstance(raw.get(k), type(state[k])):
                        state[k] = raw[k]
    except Exception:
        pass
    return state


@contextmanager
def _locked(p: Path):
    try:
        from filelock import FileLock
    except ImportError:  # pragma: no cover
        yield
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(p) + ".lock", timeout=10):
        yield


def _day(now_ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now_ms / 1000))


def snapshot(state: Optional[Dict[str, Any]] = None,
             path: Optional[Path] = None,
             now_ms: Optional[int] = None) -> Dict[str, Any]:
    """The stamp a new proposal carries."""
    s = load(path) if state is None else state
    return {"projects": dict(s["projects"]), "global": int(s["global"]),
            "ts": int(now_ms if now_ms is not None else time.time() * 1000)}


def tick_session(conversation_id: Optional[str], project_path: Optional[str],
                 path: Optional[Path] = None,
                 now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Count one session for ``project_path`` (and the global clock), at
    most once per (conversation, day).  Returns the post-tick snapshot, so
    a proposal made in this session does not count the session itself as
    a chance to confirm it."""
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    today = _day(now_ms)
    p = _path(path)
    with _locked(p):
        s = load(p)
        key = f"{conversation_id or project_path or '-'}|{today}"
        if key not in s["seen"]:
            s["seen"][key] = today
            s["global"] = int(s["global"]) + 1
            if project_path:
                s["projects"][project_path] = int(s["projects"].get(project_path, 0)) + 1
                s["last"][project_path] = now_ms
            cutoff = _day(now_ms - SEEN_KEEP_DAYS * 86400_000)
            s["seen"] = {k: d for k, d in s["seen"].items() if d >= cutoff}
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(s, f)
            os.replace(tmp, p)
            ticked = key
        else:
            ticked = None
    out = snapshot(s, now_ms=now_ms)
    # Which (conversation, day) key THIS call added, so a turn abandoned on
    # an infrastructure outage can undo it: a session whose extraction never
    # ran is not a chance to confirm anything (see revert_session).
    out["session_key"] = ticked
    return out


def revert_session(session_key: Optional[str], project_path: Optional[str],
                   path: Optional[Path] = None) -> bool:
    """Undo a tick made by ``tick_session`` (its returned ``session_key``).
    No-op when the key is None or already gone.  Returns True if reverted."""
    if not session_key:
        return False
    p = _path(path)
    with _locked(p):
        s = load(p)
        if session_key not in s["seen"]:
            return False
        del s["seen"][session_key]
        s["global"] = max(0, int(s["global"]) - 1)
        if project_path and project_path in s["projects"]:
            s["projects"][project_path] = max(0, int(s["projects"][project_path]) - 1)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(s, f)
        os.replace(tmp, p)
    return True


def members_by_project(coll_of_project: Dict[str, str]) -> Dict[str, Set[str]]:
    """``project -> every project in its collection``."""
    groups: Dict[str, Set[str]] = defaultdict(set)
    for proj, key in (coll_of_project or {}).items():
        groups[key].add(proj)
    return {proj: groups[key] for proj, key in (coll_of_project or {}).items()}


def build_context(coll_of_project: Optional[Dict[str, str]] = None,
                  path: Optional[Path] = None,
                  now_ms: Optional[int] = None) -> Dict[str, Any]:
    return {"clocks": load(path), "members": members_by_project(coll_of_project or {}),
            "now": int(now_ms if now_ms is not None else time.time() * 1000)}


def domain_age(stamp: Any, project_paths: Iterable[str],
               ctx: Optional[Dict[str, Any]]) -> Optional[int]:
    """Sessions in the proposal's domain since ``stamp``, or None when the
    proposal has no stamp (legacy) or there is no context."""
    if not isinstance(stamp, dict) or not ctx:
        return None
    cur = ctx["clocks"]
    projects = [p for p in (project_paths or []) if p]
    if not projects:
        return max(0, int(cur["global"]) - int(stamp.get("global", 0) or 0))
    members: Set[str] = set()
    for p in projects:
        members |= ctx["members"].get(p, {p})
    then = stamp.get("projects") or {}
    return sum(max(0, int(cur["projects"].get(q, 0)) - int(then.get(q, 0) or 0))
               for q in members)


def _paths(proposal: Dict[str, Any]) -> list:
    return (proposal.get("scope") or {}).get("project_paths") or []


def effective_counter(proposal: Dict[str, Any], current_counter: int,
                      ctx: Optional[Dict[str, Any]]) -> int:
    """The counter value to hand the lifecycle evaluators for this proposal.

    The evaluators compute ``age = counter - activity_count_at_proposal``,
    so returning ``activity_count_at_proposal + domain_age`` makes them see
    the domain age without changing them.  Legacy proposals get the global
    counter unchanged."""
    age = domain_age(proposal.get("domain_clock"), _paths(proposal), ctx)
    if age is None:
        return current_counter
    return int(proposal.get("activity_count_at_proposal") or 0) + age


def is_dormant(proposal: Dict[str, Any], ctx: Optional[Dict[str, Any]],
               days: int = DORMANT_DAYS) -> bool:
    """No session in the proposal's domain since it was made, for ``days``."""
    stamp = proposal.get("domain_clock")
    age = domain_age(stamp, _paths(proposal), ctx)
    if age is None or age > 0:
        return False
    return ctx["now"] - int(stamp.get("ts", ctx["now"]) or ctx["now"]) >= days * 86400_000
