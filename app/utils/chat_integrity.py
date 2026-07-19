"""
Cross-project chat integrity validation and autorecovery.

Background
----------
A chat belongs to exactly one project — the one named by its ``projectId``
field, whose file lives in ``~/.ziya/projects/<projectId>/chats/<id>.json``.
Global chats (``isGlobal`` true, or whose group is effectively global) are
*surfaced* into other projects' sidebars for read access; they are not
supposed to be copied there.

A defect in the bulk-sync path (``bulk_sync_chats``) wrote every chat in the
sync payload into the currently-viewed project's directory, with no check
that the chat belonged there.  Because the frontend merges surfaced global
chats into IndexedDB and pushes the whole set back on the next sync, each
global chat got cloned into whatever project it was viewed from and
re-stamped with that project's id.  The clones carried divergent
``groupId``/``isGlobal`` values, so the same conversation surfaced under the
wrong global group in one project and appeared "lost" (demoted to ungrouped)
in another.

This module detects those cross-project duplicates and reconciles them:
keep the canonical copy, salvage any grouping/global metadata a shadow copy
retained but the canonical one lost, and remove the shadows.  It is the
generalized, data-set-independent counterpart to the prevention guard in
``bulk_sync_chats``.

Everything here is read-only unless ``reconcile_chat_integrity`` is called
with ``dry_run=False``.  It never touches the (plaintext) ``_groups.json``
files or non-duplicated chats.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from app.utils.logging_utils import logger


def _read_json_maybe_encrypted(path: Path) -> Optional[dict]:
    """Read a chat/group JSON file, transparently decrypting ALE envelopes.

    Returns None on any read/parse/decrypt failure (caller treats the file as
    unreadable and skips it — never raises, so one bad file can't abort a
    whole-workspace scan)."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    try:
        from app.utils.encryption import is_encrypted, get_encryptor
        if is_encrypted(raw):
            raw = get_encryptor().decrypt(raw)
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001 — unreadable file is non-fatal
        logger.debug("chat_integrity: skipping unreadable %s: %s", path, exc)
        return None


@dataclass
class ChatCopy:
    """One on-disk copy of a chat within a specific project directory."""
    chat_id: str
    dir_project_id: str          # the project directory this copy lives in
    path: Path
    stated_project_id: Optional[str]   # the chat's own projectId field
    group_id: Optional[str]
    is_global: Optional[bool]
    last_active: int
    message_count: int

    @property
    def owner_matches_dir(self) -> bool:
        """True if the copy's own projectId equals the directory it sits in.

        This is the strongest single signal that a copy is canonical: the
        chat claims to belong to exactly the project whose folder holds it."""
        return bool(self.stated_project_id) and self.stated_project_id == self.dir_project_id


@dataclass
class DuplicateSet:
    """A chat id that appears in more than one project directory."""
    chat_id: str
    copies: List[ChatCopy]
    canonical: ChatCopy
    shadows: List[ChatCopy] = field(default_factory=list)
    # A group id present on a shadow but missing from the canonical copy —
    # salvageable metadata that reconciliation restores before deleting the
    # shadow, so no grouping is lost.
    salvageable_group_id: Optional[str] = None
    salvageable_is_global: Optional[bool] = None


def _choose_canonical(copies: List[ChatCopy]) -> ChatCopy:
    """Pick the authoritative copy among duplicates.

    Priority:
      1. The copy whose own ``projectId`` matches its directory (true owner).
      2. Among those, or if none match, the most-recently-active copy, then
         the one with the most messages, as a stable tie-break.
    """
    owner_matched = [c for c in copies if c.owner_matches_dir]
    pool = owner_matched or copies
    return max(pool, key=lambda c: (c.last_active, c.message_count))


def scan_chat_integrity(ziya_home: Path) -> List[DuplicateSet]:
    """Scan every project for chat ids that appear in more than one project.

    Returns one DuplicateSet per duplicated chat id, with the canonical copy
    chosen and shadows/salvageable metadata identified.  Read-only.
    """
    projects_dir = ziya_home / "projects"
    if not projects_dir.exists():
        return []

    by_id: Dict[str, List[ChatCopy]] = {}
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        chats_dir = project_dir / "chats"
        if not chats_dir.exists():
            continue
        for chat_file in chats_dir.glob("*.json"):
            name = chat_file.name
            if name.startswith("_") or name.endswith(".bindings.json"):
                continue
            data = _read_json_maybe_encrypted(chat_file)
            if not data or "id" not in data:
                continue
            msgs = data.get("messages")
            copy = ChatCopy(
                chat_id=chat_file.stem,
                dir_project_id=project_dir.name,
                path=chat_file,
                stated_project_id=data.get("projectId"),
                group_id=data.get("groupId"),
                is_global=data.get("isGlobal"),
                last_active=data.get("lastActiveAt") or 0,
                message_count=len(msgs) if isinstance(msgs, list) else 0,
            )
            by_id.setdefault(chat_file.stem, []).append(copy)

    dup_sets: List[DuplicateSet] = []
    for chat_id, copies in by_id.items():
        if len(copies) < 2:
            continue
        canonical = _choose_canonical(copies)
        shadows = [c for c in copies if c is not canonical]

        # Salvage grouping/global metadata: if the canonical copy lost its
        # group (demoted to None) but a shadow still carries one, restore it.
        salv_group = None
        salv_global = None
        if canonical.group_id is None:
            for s in shadows:
                if s.group_id is not None:
                    salv_group = s.group_id
                    break
        if not canonical.is_global:
            for s in shadows:
                if s.is_global:
                    salv_global = True
                    break

        dup_sets.append(DuplicateSet(
            chat_id=chat_id,
            copies=copies,
            canonical=canonical,
            shadows=shadows,
            salvageable_group_id=salv_group,
            salvageable_is_global=salv_global,
        ))
    return dup_sets


def report_dict(dup_sets: List[DuplicateSet]) -> Dict[str, object]:
    """JSON-serializable summary of a scan, for the HTTP report endpoint.

    One entry per duplicated chat id, listing the chosen canonical copy and
    every shadow (project + path), plus the salvageable metadata that a
    reconcile would restore.  Read-only — this only describes state so a
    human can eyeball the canonical choices before mutating anything.
    """
    total_shadows = sum(len(d.shadows) for d in dup_sets)
    salvageable = sum(
        1 for d in dup_sets if d.salvageable_group_id or d.salvageable_is_global
    )
    sets = []
    for d in dup_sets:
        sets.append({
            "chat_id": d.chat_id,
            "copy_count": len(d.copies),
            "canonical": {
                "project": d.canonical.dir_project_id,
                "stated_project_id": d.canonical.stated_project_id,
                "owner_matches_dir": d.canonical.owner_matches_dir,
                "group_id": d.canonical.group_id,
                "is_global": d.canonical.is_global,
                "last_active": d.canonical.last_active,
                "message_count": d.canonical.message_count,
            },
            "shadows": [
                {
                    "project": s.dir_project_id,
                    "stated_project_id": s.stated_project_id,
                    "group_id": s.group_id,
                    "is_global": s.is_global,
                    "last_active": s.last_active,
                    "message_count": s.message_count,
                }
                for s in d.shadows
            ],
            "salvageable_group_id": d.salvageable_group_id,
            "salvageable_is_global": d.salvageable_is_global,
        })
    return {
        "duplicate_sets": len(dup_sets),
        "shadow_copies": total_shadows,
        "sets_with_salvageable_metadata": salvageable,
        "sets": sets,
    }


def format_report(dup_sets: List[DuplicateSet]) -> str:
    """Human-readable summary of a scan (for CLI / logs)."""
    if not dup_sets:
        return "chat integrity: no cross-project duplicates found."
    total_shadows = sum(len(d.shadows) for d in dup_sets)
    lines = [
        f"chat integrity: {len(dup_sets)} chat id(s) duplicated across "
        f"projects; {total_shadows} shadow copy(ies) total.",
    ]
    for d in dup_sets:
        lines.append(
            f"  {d.chat_id}: canonical in {d.canonical.dir_project_id[:8]} "
            f"(group={d.canonical.group_id}, global={d.canonical.is_global}); "
            f"{len(d.shadows)} shadow(s)"
            + (f"; salvage group={d.salvageable_group_id}" if d.salvageable_group_id else "")
            + ("; salvage isGlobal" if d.salvageable_is_global else "")
        )
    return "\n".join(lines)


def reconcile_chat_integrity(
    ziya_home: Path,
    dry_run: bool = True,
) -> Dict[str, object]:
    """Reconcile cross-project chat duplicates.

    For each duplicated chat: salvage any grouping/global metadata a shadow
    retained onto the canonical copy, then delete the shadow files.  With
    ``dry_run=True`` (default) nothing is written — the return value describes
    what WOULD happen.

    Returns a dict: {scanned, duplicate_sets, shadows_removed,
    metadata_salvaged, dry_run, details}.
    """
    dup_sets = scan_chat_integrity(ziya_home)
    shadows_removed = 0
    metadata_salvaged = 0
    details: List[dict] = []

    for d in dup_sets:
        salvaged = False
        # Restore salvageable metadata onto the canonical copy first, so the
        # kept copy never loses grouping that only a shadow retained.
        if d.salvageable_group_id or d.salvageable_is_global:
            if not dry_run:
                data = _read_json_maybe_encrypted(d.canonical.path)
                if data is not None:
                    if d.salvageable_group_id and data.get("groupId") is None:
                        data["groupId"] = d.salvageable_group_id
                    if d.salvageable_is_global and not data.get("isGlobal"):
                        data["isGlobal"] = True
                    _write_canonical(d.canonical.path, data)
            salvaged = True
            metadata_salvaged += 1

        removed_paths = []
        for s in d.shadows:
            removed_paths.append(str(s.path))
            if not dry_run:
                try:
                    s.path.unlink()
                except OSError as exc:
                    logger.warning("chat_integrity: could not remove shadow %s: %s", s.path, exc)
                    continue
            shadows_removed += 1

        details.append({
            "chat_id": d.chat_id,
            "canonical_project": d.canonical.dir_project_id,
            "salvaged_metadata": salvaged,
            "salvaged_group_id": d.salvageable_group_id,
            "salvaged_is_global": d.salvageable_is_global,
            "shadows_removed": removed_paths,
        })

    result = {
        "scanned": True,
        "duplicate_sets": len(dup_sets),
        "shadows_removed": shadows_removed,
        "metadata_salvaged": metadata_salvaged,
        "dry_run": dry_run,
        "details": details,
    }
    logger.info(
        "chat_integrity.reconcile: dup_sets=%d shadows_removed=%d "
        "metadata_salvaged=%d dry_run=%s",
        len(dup_sets), shadows_removed, metadata_salvaged, dry_run,
    )
    return result


def _write_canonical(path: Path, data: dict) -> None:
    """Re-write a canonical chat file, preserving its at-rest encryption.

    Routes through BaseStorage so the file is re-encrypted under the same
    category policy it was read with — never downgrades an encrypted chat to
    plaintext."""
    try:
        from app.storage.base import BaseStorage

        class _W(BaseStorage):  # minimal concrete subclass for _write_json
            def get(self, i): ...
            def list(self): ...
            def create(self, d): ...
            def update(self, i, d): ...
            def delete(self, i): ...

        writer = _W(path.parent)
        writer._write_json(path, data)
    except Exception as exc:  # noqa: BLE001 — salvage is best-effort
        logger.warning("chat_integrity: could not rewrite canonical %s: %s", path, exc)


def run_startup_check(ziya_home: Path, auto_reconcile: Optional[bool] = None) -> Dict[str, object]:
    """Self-detection hook for server startup.

    Always scans and logs a summary so cross-project shadow copies surface in
    the logs the moment they exist — the "shouldn't be in this situation"
    early-warning.  Deleting files is opt-in: reconciliation runs ONLY when
    ``auto_reconcile`` is true (resolved from ``ZIYA_AUTO_RECONCILE_CHATS``
    when the arg is None).  Warn-only by default because canonical-copy
    selection is a heuristic — a boot-time process must not silently delete
    chat data without an explicit operator opt-in.

    Never raises: a failure here must not block server startup.  Returns the
    reconcile-style result dict (dry_run reflects whether anything was
    written).
    """
    if auto_reconcile is None:
        import os
        auto_reconcile = os.environ.get(
            "ZIYA_AUTO_RECONCILE_CHATS", ""
        ).strip().lower() in ("1", "true", "yes", "on")
    try:
        dup_sets = scan_chat_integrity(ziya_home)
    except Exception as exc:  # noqa: BLE001 — never block startup
        logger.warning("chat_integrity startup scan failed (non-fatal): %s", exc)
        return {"scanned": False, "error": str(exc)}

    if not dup_sets:
        logger.debug("chat_integrity: startup scan clean (no cross-project duplicates)")
        return {"scanned": True, "duplicate_sets": 0, "shadows_removed": 0,
                "metadata_salvaged": 0, "dry_run": not auto_reconcile, "details": []}

    total_shadows = sum(len(d.shadows) for d in dup_sets)
    if auto_reconcile:
        logger.warning(
            "chat_integrity: %d duplicated chat id(s) / %d shadow copy(ies) "
            "found; ZIYA_AUTO_RECONCILE_CHATS is set — reconciling now.",
            len(dup_sets), total_shadows,
        )
        return reconcile_chat_integrity(ziya_home, dry_run=False)

    logger.warning(
        "chat_integrity: %d duplicated chat id(s) / %d shadow copy(ies) found "
        "across projects. Review GET /api/v1/chat-integrity, then reconcile via "
        "POST /api/v1/chat-integrity/reconcile (or set ZIYA_AUTO_RECONCILE_CHATS=1).",
        len(dup_sets), total_shadows,
    )
    return reconcile_chat_integrity(ziya_home, dry_run=True)
