"""Loss attribution: who took a memory out of the active store, and why.

``MemoryStorage._save_memories`` is the single choke point every memory
write goes through (save, save_many, delete -- in both arms; the baseline
arm's cleanup HARD-deletes via ``delete``).  The wrapper diffs the rows being
written against a shadow ``id -> (status, layer, project)`` map and appends
one row per change to ``transitions.jsonl``:

    {t, id, from, to, cause, caller, layer, project}

``from``/``to`` are statuses (``None`` = not in the store, ``deleted`` = hard
delete).  Every creation and status change is logged, so the active set at
any simulated time can be rebuilt from this file alone.  ``cause`` comes from
the call stack:

    cleanup_merge   cleanup_corpus frame whose ``absorb_id`` is this memory
    cleanup_remove  any other cleanup_corpus frame
    repair          repair_mindmap on the stack
    rem             app.memory.rem
    lifecycle       app.memory.lifecycle (promotion creates; decay)
    extract         app.memory.extractor (supersede/update)
    save_tool       app.mcp.tools.memory_tools
    maintenance     app.memory.maintenance (outside cleanup)
    organize        app.memory.organizer (outside cleanup)
    delete_other / other   anything else (loss / non-loss)

Proposal transitions (``mark_archived`` with its reason as cause, and
``mark_promoted``) go to the same file with ``kind: proposal``.

Text is needed for the relearn metric, and hard-deleted text is otherwise
gone, so the content of every memory that LEAVES active, and of every
proposal ``add``, is appended to ``texts.enc`` -- one line per record,
base64 of an ALE-encrypted JSON blob.  transitions.jsonl holds ids only.
"""
from __future__ import annotations

import base64
import inspect
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

LOSS_TO = ("archived", "contested", "dormant", "deleted", "superseded", "merged")


class EncLog:
    """Append-only encrypted JSON-lines log."""

    def __init__(self, path: Path) -> None:
        from app.utils.encryption import get_encryptor
        self.path = Path(path)
        self._enc = get_encryptor()
        if not self._enc.is_enabled("session_data"):
            raise RuntimeError("ALE encryption disabled; refusing to write memory text")
        self._lock = threading.Lock()

    def append(self, obj: Dict[str, Any]) -> None:
        blob = self._enc.encrypt(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "session_data")
        with self._lock, open(self.path, "ab") as f:
            f.write(base64.b64encode(blob) + b"\n")

    @staticmethod
    def read(path: Path) -> List[Dict[str, Any]]:
        from app.utils.encryption import get_encryptor
        enc = get_encryptor()
        out = []
        if not Path(path).exists():
            return out
        for line in open(path, "rb"):
            line = line.strip()
            if line:
                out.append(json.loads(enc.decrypt(base64.b64decode(line))))
        return out


def _project(row: Dict[str, Any]) -> Optional[str]:
    paths = ((row.get("scope") or {}).get("project_paths") or [])
    return os.path.basename(str(paths[0]).rstrip("/")) if paths else None


def classify(mid: str, lost: bool) -> Tuple[str, str]:
    """(cause, caller) from the current call stack."""
    cause, caller = None, ""
    frames = inspect.stack(context=0)
    try:
        for fi in frames[2:]:
            mod = fi.frame.f_globals.get("__name__", "")
            if not caller and mod.startswith("app.") and mod != "app.storage.memory":
                caller = f"{mod}:{fi.function}"
            if fi.function == "cleanup_corpus":
                cause = "cleanup_merge" if fi.frame.f_locals.get("absorb_id") == mid else "cleanup_remove"
                break
            if fi.function == "repair_mindmap":
                cause = "repair"
                break
        if cause is None:
            for fi in frames[2:]:
                mod = fi.frame.f_globals.get("__name__", "")
                for prefix, c in (("app.memory.rem", "rem"), ("app.memory.lifecycle", "lifecycle"),
                                  ("app.memory.extractor", "extract"),
                                  ("app.mcp.tools.memory_tools", "save_tool"),
                                  ("app.memory.maintenance", "maintenance"),
                                  ("app.memory.organizer", "organize")):
                    if mod == prefix:
                        cause = c
                        break
                if cause:
                    break
    finally:
        del frames
    return cause or ("delete_other" if lost else "other"), caller


class Attribution:
    def __init__(self, run_dir: Path, clock: Any) -> None:
        self.run_dir = Path(run_dir)
        self.clock = clock
        self.path = self.run_dir / "transitions.jsonl"
        self.texts = EncLog(self.run_dir / "texts.enc")
        self.shadow: Dict[str, Tuple[Optional[str], Optional[str], Optional[str]]] = {}
        self._undo: List[Tuple[Any, str, Any]] = []
        self._lock = threading.Lock()
        self.event_index = -1
        self._last_text: Dict[str, str] = {}

    def _emit(self, row: Dict[str, Any]) -> None:
        with self._lock, open(self.path, "a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    def seed(self, rows: List[Dict[str, Any]]) -> None:
        """Initialise the shadow from the store on disk (fresh run or resume)."""
        self.shadow = {r["id"]: (r.get("status", "active"), r.get("layer"), _project(r))
                       for r in rows if r.get("id")}
        self._last_text = {r["id"]: r.get("content", "") for r in rows if r.get("id")}

    def _on_write(self, rows: List[Dict[str, Any]]) -> None:
        t = self.clock.now_ms()
        new = {r.get("id"): r for r in rows if r.get("id")}
        for mid, r in new.items():
            st = r.get("status", "active") or "active"
            prev = self.shadow.get(mid)
            if prev is None or prev[0] != st:
                lost = (prev is not None and prev[0] == "active" and st != "active")
                cause, caller = classify(mid, lost)
                self._emit({"t": t, "ev": self.event_index, "id": mid, "kind": "memory",
                            "from": prev[0] if prev else None, "to": st, "cause": cause,
                            "caller": caller, "layer": r.get("layer"), "project": _project(r),
                            "reason": (r.get("archive_reason") or "")[:24] if lost else None})
                if lost:
                    self.texts.append({"t": t, "id": mid, "kind": "lost", "content": r.get("content", "")})
            self.shadow[mid] = (st, r.get("layer"), _project(r))
        for mid in [m for m in self.shadow if m not in new]:
            st, layer, proj = self.shadow.pop(mid)
            lost = st == "active"
            cause, caller = classify(mid, lost)
            self._emit({"t": t, "ev": self.event_index, "id": mid, "kind": "memory", "from": st,
                        "to": "deleted", "cause": cause, "caller": caller, "layer": layer, "project": proj})
            if lost:
                self._pending_deleted_text(mid)

    def _pending_deleted_text(self, mid: str) -> None:
        txt = self._last_text.get(mid)
        self.texts.append({"t": self.clock.now_ms(), "id": mid, "kind": "lost", "content": txt or ""})

    def install(self, MemoryStorage: Any, ProposalsStore: Any) -> None:
        att = self
        orig_save = MemoryStorage._save_memories

        def _save_memories(store_self, memories):
            # Keep the text of every row so a later hard delete can log it.
            for r in memories:
                if r.get("id"):
                    att._last_text[r["id"]] = r.get("content", "")
            att._on_write(memories)
            return orig_save(store_self, memories)

        self._undo.append((MemoryStorage, "_save_memories", orig_save))
        MemoryStorage._save_memories = _save_memories

        orig_arch = ProposalsStore.mark_archived

        def mark_archived(ps_self, proposal_id, reason="decay"):
            ok = orig_arch(ps_self, proposal_id, reason)
            if ok:
                _, caller = classify(proposal_id, True)
                att._emit({"t": att.clock.now_ms(), "ev": att.event_index, "id": proposal_id,
                           "kind": "proposal", "from": "open", "to": "archived",
                           "cause": str(reason)[:40], "caller": caller})
            return ok

        orig_prom = ProposalsStore.mark_promoted

        def mark_promoted(ps_self, proposal_id, target_memory_id):
            ok = orig_prom(ps_self, proposal_id, target_memory_id)
            if ok:
                att._emit({"t": att.clock.now_ms(), "ev": att.event_index, "id": proposal_id,
                           "kind": "proposal", "from": "open", "to": "promoted",
                           "cause": "promote", "target": target_memory_id})
            return ok

        orig_add = ProposalsStore.add

        def add(ps_self, proposal, activity_count=0):
            open_before = {r.get("id") for r in ps_self.list_open()}
            pid = orig_add(ps_self, proposal, activity_count)
            fresh = pid not in open_before
            att.texts.append({"t": att.clock.now_ms(), "id": pid, "kind": "proposal",
                              "ev": att.event_index, "content": proposal.content,
                              "layer": getattr(proposal, "layer", None),
                              "project": _project({"scope": (proposal.scope.model_dump()
                                                             if getattr(proposal, "scope", None) else {})}),
                              "conv": getattr(proposal, "conversation_id", None), "new": fresh})
            return pid

        for obj, attr, fn in ((ProposalsStore, "mark_archived", mark_archived),
                              (ProposalsStore, "mark_promoted", mark_promoted),
                              (ProposalsStore, "add", add)):
            self._undo.append((obj, attr, getattr(obj, attr)))
            setattr(obj, attr, fn)

    def uninstall(self) -> None:
        for obj, attr, val in reversed(self._undo):
            setattr(obj, attr, val)
        self._undo.clear()
