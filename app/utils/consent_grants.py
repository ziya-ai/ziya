"""
Standing grants from human approval (design/scope-consent.md D2–D4).

A grant is what an approval leaves behind for later calls:

    (tool, root, level, [conversation_id], op | path+kind, rule, used)

Two families share one store:

  * **tool/op grants** — an ``ask``-mode tool approved for ``conversation``
    or ``project`` runs that op without asking again (``covers``);
  * **path grants** — paths outside the write policy approved at a rung of
    the scope ladder: an exact ``file``, a ``dir`` (everything under it) or
    the whole ``project``.  ``covers_paths`` matches by prefix and re-checks
    the floor per path, so a remembered file later turned into a symlink
    out of the project, or anything in ``.git``, is never covered.

Levels: ``conversation`` grants are keyed on the conversation id (a fork
starts cold); ``project`` grants apply to every conversation of the
project.  Both persist in ``<root>/.ziya/consent-grants.json`` so a server
restart does not forget them; grants made without a project root (tests,
CLI without a project) stay in memory.

The *scope ladder* (``scope_ladder``) and the *rule sentence*
(``rule_sentence``) are produced here so that what the panel shows is the
text that is stored on the grant.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from app.utils import write_grants as wg

LEVEL_CONVERSATION = "conversation"
LEVEL_PROJECT = "project"
LEVELS = (LEVEL_CONVERSATION, LEVEL_PROJECT)

KIND_FILE = "file"
KIND_DIR = "dir"
KIND_PROJECT = "project"
KINDS = (KIND_FILE, KIND_DIR, KIND_PROJECT)

GRANTS_FILE = Path(".ziya") / "consent-grants.json"

# Directories a rung's file count skips; the count is a hint for the
# human, not a security bound, so it only needs to be fast and plausible.
_COUNT_SKIP = {".git", ".ziya", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache"}
_COUNT_CAP = 5000


@dataclass
class Grant:
    id: str
    tool: str
    level: str
    root: str                               # realpath of the project root, "" if none
    conversation_id: Optional[str] = None   # level == conversation only
    op: Optional[str] = None                # tool/op grant
    path: Optional[str] = None              # path grant: realpath of the file / dir / root
    kind: Optional[str] = None              # file | dir | project
    rule: str = ""
    granted_at: float = field(default_factory=time.time)
    used: int = 0

    def visible_to(self, conversation_id: Optional[str]) -> bool:
        if self.level == LEVEL_PROJECT:
            return True
        return bool(conversation_id) and self.conversation_id == conversation_id

    def covers_real(self, real_path: str) -> bool:
        """Does this path grant cover an already-resolved, non-floor path?"""
        if self.path is None:
            return False
        if self.kind == KIND_FILE:
            return real_path == self.path
        return wg._within(real_path, self.path)

    def public(self, root: str) -> Dict[str, Any]:
        out = asdict(self)
        if self.path is not None and root:
            rel = os.path.relpath(self.path, root) if self.path != root else ""
            out["path_display"] = "" if self.kind == KIND_PROJECT else rel
        return out


def _real_root(root: Optional[str]) -> str:
    return wg._real_root(root)


class GrantStore:
    """Process-wide store; one JSON file per project root, loaded lazily."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_root: Dict[str, List[Grant]] = {}
        self._loaded: Set[str] = set()
        self._rootless: List[Grant] = []
        # D3: the rung last chosen per (conversation, tool), whatever the
        # duration — "once" stores no grant but the panel should still
        # reopen at the rung the human walked up to.
        self._last_rung: Dict[Tuple[str, str], str] = {}

    # ── persistence ──────────────────────────────────────────────────────

    @staticmethod
    def _file(root: str) -> Path:
        return Path(root) / GRANTS_FILE

    def _grants_for(self, root: str) -> List[Grant]:
        if not root:
            return self._rootless
        if root not in self._loaded:
            self._loaded.add(root)
            self._by_root.setdefault(root, [])
            try:
                f = self._file(root)
                if f.is_file():
                    data = json.loads(f.read_text(encoding="utf-8"))
                    for row in data.get("grants") or []:
                        try:
                            g = Grant(**{k: row.get(k) for k in Grant.__dataclass_fields__ if k in row})
                        except TypeError:
                            continue
                        if g.level in LEVELS and g.tool:
                            self._by_root[root].append(g)
            except Exception:  # noqa: BLE001 -- an unreadable file means no grants
                pass
        return self._by_root[root]

    def _save(self, root: str) -> None:
        if not root or not os.path.isdir(root):
            return
        try:
            f = self._file(root)
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(
                {"version": 1, "grants": [asdict(g) for g in self._by_root.get(root, [])]},
                indent=1), encoding="utf-8")
            os.replace(tmp, f)
        except Exception:  # noqa: BLE001 -- persistence is best effort
            pass

    def _add(self, g: Grant) -> Grant:
        with self._lock:
            self._grants_for(g.root).append(g)
            self._save(g.root)
        return g

    # ── tool/op grants ───────────────────────────────────────────────────

    def covers(self, tool: str, op: str, conversation_id: Optional[str],
               root: Optional[str] = None) -> bool:
        rr = _real_root(root)
        with self._lock:
            # A tool/op grant is not path-bound: a lookup without a root sees
            # every root loaded so far as well as the root-less grants.
            pools = [self._rootless] + (
                [self._grants_for(rr)] if rr else list(self._by_root.values()))
            for g in (x for pool in pools for x in pool):
                if g.op == op and g.tool == tool and g.path is None and g.visible_to(conversation_id):
                    g.used += 1
                    self._save(g.root)
                    return True
        return False

    def remember(self, scope: str, tool: str, op: str, conversation_id: Optional[str],
                 root: Optional[str] = None, rule: str = "") -> Optional[Grant]:
        if scope not in LEVELS:
            return None
        if scope == LEVEL_CONVERSATION and not conversation_id:
            return None
        return self._add(Grant(
            id=uuid.uuid4().hex[:12], tool=tool, level=scope, root=_real_root(root),
            conversation_id=conversation_id if scope == LEVEL_CONVERSATION else None,
            op=op, rule=rule))

    # ── path grants ──────────────────────────────────────────────────────

    def remember_paths(self, scope: str, tool: str, conversation_id: Optional[str],
                       root: Optional[str], paths: Iterable[str],
                       rung: Optional[str] = None, rule: str = "") -> List[Grant]:
        """Remember *paths* at *rung* for *scope*.

        ``rung`` is a project-relative directory ("" for the whole project)
        chosen from the scope ladder, or None for the exact files shown.
        ``once`` (or any non-standing scope) remembers nothing; floor paths
        and floor rungs are never remembered.
        """
        if scope not in LEVELS or (scope == LEVEL_CONVERSATION and not conversation_id):
            return []
        rr = _real_root(root)
        if not rr:
            return []
        conv = conversation_id if scope == LEVEL_CONVERSATION else None
        made: List[Grant] = []
        if rung is None:
            for p in paths or ():
                key = wg.grant_key(p, rr)
                if key is None:
                    continue
                made.append(self._add(Grant(
                    id=uuid.uuid4().hex[:12], tool=tool, level=scope, root=rr,
                    conversation_id=conv, path=key[1], kind=KIND_FILE, rule=rule)))
            return made
        if rung in ("", "."):
            made.append(self._add(Grant(
                id=uuid.uuid4().hex[:12], tool=tool, level=scope, root=rr,
                conversation_id=conv, path=rr, kind=KIND_PROJECT, rule=rule)))
            return made
        key = wg.grant_key(rung, rr)
        if key is None:
            return []
        made.append(self._add(Grant(
            id=uuid.uuid4().hex[:12], tool=tool, level=scope, root=rr,
            conversation_id=conv, path=key[1], kind=KIND_DIR, rule=rule)))
        return made

    def covers_paths(self, tool: str, conversation_id: Optional[str],
                     root: Optional[str], paths: Iterable[str]) -> bool:
        """True only when EVERY path is covered for this tool.  The floor is
        re-checked per path here.  A hit counts as a use of each grant."""
        paths = list(paths or ())
        if not paths:
            return False
        rr = _real_root(root)
        if not rr:
            return False
        with self._lock:
            pool = [g for g in self._grants_for(rr)
                    if g.tool == tool and g.path is not None and g.visible_to(conversation_id)]
            hit: List[Grant] = []
            for p in paths:
                key = wg.grant_key(p, rr)
                if key is None:
                    return False
                g = next((g for g in pool if g.covers_real(key[1])), None)
                if g is None:
                    return False
                hit.append(g)
            for g in {id(g): g for g in hit}.values():
                g.used += 1
            self._save(rr)
        return True

    # ── D3: last rung ────────────────────────────────────────────────────

    def note_rung(self, conversation_id: Optional[str], tool: str,
                  root: Optional[str], rung: Optional[str]) -> None:
        rr = _real_root(root)
        if not conversation_id or not rr or rung is None:
            return
        real = rr if rung in ("", ".") else wg._resolve(rung, rr)
        self._last_rung[(conversation_id, tool)] = real

    def suggested_rung(self, conversation_id: Optional[str], tool: str,
                       root: Optional[str], paths: Iterable[str]) -> Optional[str]:
        """The project-relative rung last chosen for (conversation, tool) if
        it contains every path, else None (the panel defaults to the file)."""
        rr = _real_root(root)
        if not conversation_id or not rr:
            return None
        real = self._last_rung.get((conversation_id, tool))
        if not real or not wg._within(real, rr):
            return None
        for p in paths or ():
            rp = wg._resolve(p, rr)
            if not (real == rr or wg._within(rp, real)):
                return None
        return "" if real == rr else os.path.relpath(real, rr)

    # ── D4: listing, revocation, forgetting ──────────────────────────────

    def list(self, conversation_id: Optional[str], root: Optional[str]) -> List[Dict[str, Any]]:
        rr = _real_root(root)
        with self._lock:
            pool = self._grants_for(rr) if rr else self._rootless
            return [g.public(rr) for g in pool if g.visible_to(conversation_id)]

    def revoke(self, grant_id: str, root: Optional[str]) -> bool:
        rr = _real_root(root)
        with self._lock:
            pool = self._grants_for(rr) if rr else self._rootless
            before = len(pool)
            pool[:] = [g for g in pool if g.id != grant_id]
            if len(pool) != before:
                self._save(rr)
                return True
        return False

    def forget_conversation(self, conversation_id: str) -> None:
        with self._lock:
            touched = []
            for root, pool in list(self._by_root.items()) + [("", self._rootless)]:
                kept = [g for g in pool if g.conversation_id != conversation_id]
                if len(kept) != len(pool):
                    pool[:] = kept
                    touched.append(root)
            for root in touched:
                self._save(root)
            self._last_rung = {k: v for k, v in self._last_rung.items() if k[0] != conversation_id}

    def reset(self) -> None:
        with self._lock:
            self._by_root.clear()
            self._loaded.clear()
            self._rootless.clear()
            self._last_rung.clear()


# ── scope ladder ──────────────────────────────────────────────────────────

def _count_files(directory: str) -> Tuple[int, bool]:
    """Files under *directory*, skipping ``_COUNT_SKIP`` dirs, capped."""
    n = 0
    try:
        for cur, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if d not in _COUNT_SKIP]
            n += len(files)
            if n >= _COUNT_CAP:
                return _COUNT_CAP, True
    except Exception:  # noqa: BLE001
        pass
    return n, False


def scope_ladder(root: Optional[str], paths: Iterable[str]) -> List[Dict[str, Any]]:
    """The rungs a human may approve at, narrowest first.

    ``[{kind: file, paths: [...]}, {kind: dir, path: "a/b", files: N,
    files_capped: bool}, ..., {kind: project, path: "", files: N}]``.
    Rungs are project-relative.  Floor paths are left out; an empty list
    means nothing here can be granted.
    """
    rr = _real_root(root)
    if not rr:
        return []
    rels: List[str] = []
    for p in paths or ():
        key = wg.grant_key(p, rr)
        if key is None:
            continue
        rels.append(os.path.relpath(key[1], rr))
    if not rels:
        return []
    ladder: List[Dict[str, Any]] = [{"kind": KIND_FILE, "paths": sorted(set(rels))}]
    common = os.path.commonpath([os.path.dirname(r) or "." for r in rels])
    cur = "" if common == "." else common
    while cur:
        n, capped = _count_files(os.path.join(rr, cur))
        ladder.append({"kind": KIND_DIR, "path": cur, "files": n, "files_capped": capped})
        cur = os.path.dirname(cur)
    n, capped = _count_files(rr)
    ladder.append({"kind": KIND_PROJECT, "path": "", "files": n, "files_capped": capped})
    return ladder


def rung_for(ladder: List[Dict[str, Any]], path_scope: Optional[str]) -> Optional[Dict[str, Any]]:
    """The ladder rung a client's ``path_scope`` names, or None for the
    exact files (also the answer to anything not on the ladder)."""
    if path_scope is None:
        return None
    want = "" if path_scope in ("", ".") else path_scope.strip("/")
    for rung in ladder:
        if rung.get("kind") in (KIND_DIR, KIND_PROJECT) and rung.get("path", None) == want:
            return rung
    return None


# ── rule sentences ────────────────────────────────────────────────────────

def _duration_clause(scope: str) -> str:
    if scope == LEVEL_CONVERSATION:
        return "for the rest of this conversation"
    return "in every conversation of this project, until revoked"


def rule_sentence(tool: str, scope: str, rung: Optional[Dict[str, Any]],
                  files: Optional[List[str]] = None, op: Optional[str] = None) -> str:
    """The rule an approval stores, as shown to the human.  "" for ``once``."""
    if scope not in LEVELS:
        return ""
    label = f"`{tool}`" + (f" `{op}`" if op and op != "*" else "")
    if rung is None:
        files = list(files or [])
        if not files:
            return f"{label} will run without asking {_duration_clause(scope)}."
        what = f"`{files[0]}`" if len(files) == 1 else f"these {len(files)} files"
        return f"{label} may write {what} {_duration_clause(scope)} without asking."
    if rung.get("kind") == KIND_PROJECT:
        return (f"{label} may write anywhere in this project (never `.git` or the install "
                f"directory) {_duration_clause(scope)} without asking.")
    return f"{label} may write anything under `{rung.get('path')}/` {_duration_clause(scope)} without asking."


def ladder_with_rules(ladder: List[Dict[str, Any]], tool: str) -> List[Dict[str, Any]]:
    """Attach the sentence each (rung, duration) would store."""
    out = []
    for rung in ladder:
        files = rung.get("paths") if rung.get("kind") == KIND_FILE else None
        r = None if rung.get("kind") == KIND_FILE else rung
        out.append({**rung, "rules": {lvl: rule_sentence(tool, lvl, r, files) for lvl in LEVELS}})
    return out
