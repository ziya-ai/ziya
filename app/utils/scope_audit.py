"""
Read-only compliance audit of every privilege-escalating task (ASR F-001).

Walks BOTH escalation ledgers and reports, for each escalating task, whether a
valid signed approval exists:

  • Task Cards  — block scopes across all registered projects, keyed by block
    id, checked with ``scope_approvals.is_scope_authorized`` (the SAME predicate
    ``execute_task_block`` enforces with, so the audit can never disagree with
    what actually runs).
  • CLI tasks   — ``tasks.yaml`` ``allow`` blocks for a given project root
    (builtin < global < project), keyed by ``cli:<realpath>#<name>``, checked
    with ``scope_approvals.is_cli_task_authorized``.

This is the auditable artifact the ASR requires: a single view proving no
escalation runs unsigned. It is purely observational — it never signs, mutates,
or executes anything.

Scope of inspection: this runs in the ``ziya-approve`` process, which (like the
signer) loads no plugins and therefore holds no encryption KEK. ALE-encrypted
card files cannot be decrypted here, so their block scopes are reported as
``encrypted-uninspectable`` rather than silently skipped — surfacing them as a
known gap that the server-side audit (GUI parity, the C surface) closes. CLI
tasks and plaintext cards are fully inspectable here.

Cascade-free by design (mirrors ``ziya_approve._resolve_card``): reads card JSON
directly and wraps it in attribute-access namespaces rather than importing the
pydantic models / ``app.storage`` cascade, so unrelated environment rot cannot
crash the audit.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional

from app.config import scope_canonical as sc
from app.utils import scope_approvals as sa
from app.utils.logging_utils import logger


@dataclass
class AuditEntry:
    """One escalating task and its current signed/unsigned status."""
    surface: str                      # "card" | "cli"
    label: str                        # human-readable name
    store_key: str                    # approval-store key (block id / cli:...#name)
    location: str                     # project id / tasks file path
    escalation: Dict[str, Any]        # the privilege-bearing block
    scope_hash: str
    signed: bool
    note: str = ""                    # e.g. "encrypted-uninspectable"


@dataclass
class AuditResult:
    entries: List[AuditEntry] = field(default_factory=list)
    encrypted_card_files: int = 0     # card files we could not inspect here

    @property
    def unsigned(self) -> List[AuditEntry]:
        return [e for e in self.entries if not e.signed and not e.note]

    @property
    def escalating(self) -> List[AuditEntry]:
        return [e for e in self.entries if e.escalation]


# ── namespace wrapping (cascade-free card reading) ────────────────────────────
def _ns(obj: Any) -> Any:
    """Recursively wrap dicts/lists in attribute-access namespaces so the
    getattr-based scope_canonical / scope_approvals helpers work without the
    pydantic models."""
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_ns(v) for v in obj]
    return obj


def _iter_task_blocks(block: Any) -> Iterator[Any]:
    """Depth-first yield of every ``task`` block in a card tree."""
    if getattr(block, "block_type", None) == "task":
        yield block
    for child in (getattr(block, "body", None) or []):
        yield from _iter_task_blocks(child)


def _projects_base() -> Path:
    """Resolve the projects directory, honoring the test override and (under
    sudo) the invoking user's home — matching ziya_approve._resolve_card."""
    override = os.environ.get("ZIYA_APPROVE_PROJECTS_DIR")
    if override:
        return Path(override)
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and not os.environ.get("ZIYA_HOME"):
        import pwd
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir) / ".ziya" / "projects"
        except KeyError:
            pass
    from app.utils.paths import get_ziya_home
    return get_ziya_home() / "projects"


def _read_card_root(card_file: Path) -> Optional[Any]:
    """Return the card's namespace-wrapped root block, or None when the file is
    ALE-encrypted (undecryptable here) or malformed. Decrypt-soft: never raises."""
    try:
        raw = card_file.read_bytes()
    except OSError:
        return None
    from app.utils.encryption import is_encrypted, get_encryptor
    if is_encrypted(raw):
        try:
            text = get_encryptor().decrypt(raw)
        except Exception:  # noqa: BLE001 — no KEK out-of-process; report as encrypted
            return None
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or "root" not in data:
        return None
    return _ns(data)


def collect_card_entries(
    projects_base: Optional[Path] = None,
    public_key_path: Optional[str] = None,
) -> tuple[List[AuditEntry], int]:
    """Walk every project's task cards; return (escalating-block entries,
    count of encrypted card files that could not be inspected)."""
    base = projects_base or _projects_base()
    entries: List[AuditEntry] = []
    encrypted = 0
    if not base.exists():
        return entries, encrypted
    for project_dir in sorted(base.iterdir()):
        tc_dir = project_dir / "task_cards"
        if not tc_dir.is_dir():
            continue
        for card_file in sorted(tc_dir.glob("*.json")):
            root = _read_card_root(card_file)
            if root is None:
                encrypted += 1  # encrypted-uninspectable or malformed
                continue
            card_name = getattr(root, "name", None) or card_file.stem
            for blk in _iter_task_blocks(getattr(root, "root", root)):
                scope = getattr(blk, "scope", None)
                esc = sc.task_escalation_block(scope)
                if not esc:
                    continue  # no privilege-bearing escalation
                block_id = getattr(blk, "id", "") or ""
                try:
                    signed = sa.is_scope_authorized(block_id, scope, public_key_path)
                except Exception as e:  # noqa: BLE001 — a check failure is "unsigned"
                    logger.debug("card scope auth check failed for %s: %s", block_id, e)
                    signed = False
                entries.append(AuditEntry(
                    surface="card",
                    label=f"{card_name} › {getattr(blk, 'name', '') or block_id}",
                    store_key=block_id,
                    location=project_dir.name,
                    escalation=esc,
                    scope_hash=sc.task_scope_hash(scope),
                    signed=signed,
                ))
    return entries, encrypted


def collect_cli_entries(
    root: Optional[str] = None,
    public_key_path: Optional[str] = None,
) -> List[AuditEntry]:
    """Audit ``tasks.yaml`` tasks for *root* (builtin < global < project)."""
    from app.task_runner import load_tasks, resolve_task_source_file
    entries: List[AuditEntry] = []
    tasks = load_tasks(root)
    for name in sorted(tasks):
        allow = tasks[name].get("allow")
        esc = sc.cli_escalation_block(allow)
        if not esc:
            continue  # builtin / floor-only task — nothing to authorize
        src = resolve_task_source_file(name, root)
        key = sa.cli_task_key(str(src), name) if src else f"cli:?#{name}"
        try:
            signed = sa.is_cli_task_authorized(key, allow, public_key_path)
        except Exception as e:  # noqa: BLE001
            logger.debug("cli task auth check failed for %s: %s", name, e)
            signed = False
        entries.append(AuditEntry(
            surface="cli",
            label=name,
            store_key=key,
            location=str(src) if src else "(unresolved)",
            escalation=esc,
            scope_hash=sc.cli_task_hash(allow),
            signed=signed,
        ))
    return entries


def collect_audit(
    root: Optional[str] = None,
    projects_base: Optional[Path] = None,
    public_key_path: Optional[str] = None,
) -> AuditResult:
    """Full audit: all card block scopes + CLI tasks for *root*."""
    card_entries, encrypted = collect_card_entries(projects_base, public_key_path)
    cli_entries = collect_cli_entries(root, public_key_path)
    return AuditResult(entries=card_entries + cli_entries,
                       encrypted_card_files=encrypted)
