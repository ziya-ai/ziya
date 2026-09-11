"""Shadow session registry (design doc §3).

Live sessions register as ~/.ziya/shadow/sessions/<id>.json with a
companion Unix socket <id>.sock and journal <id>.journal in the same
directory.  All files are 0600, the directory 0700 — same-user access
is the entire local authn model in v1.

Every reader MUST verify liveness and unlink stale entries; the shadow
process also removes its entry on clean exit.  Journals are unlinked
on session exit (resolved design Q2): the journal is a live buffer,
not an archive.
"""
import json
import os
import secrets
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

from app.shadow import SHADOW_PROTOCOL_VERSION


def sessions_dir() -> Path:
    """Return the sessions directory, creating it 0700 if needed.

    Refuses to proceed if the path is a symlink or not owned by us — the
    directory is the whole same-user access model (§3).
    """
    from app.shadow.sock_server import assert_private_dir
    d = Path(os.path.expanduser("~/.ziya/shadow/sessions"))
    d.mkdir(parents=True, exist_ok=True)
    assert_private_dir(str(d))
    return d


def _is_shadow_owned(path: Path) -> bool:
    """True if ``path`` lives directly in a directory this module manages.

    ``remove()`` unlinks the paths stored in a registry entry; confining
    them means a corrupt or foreign entry can never turn a routine reap
    (``shadow_list``) into an arbitrary-unlink primitive.
    """
    try:
        from app.shadow.sock_server import short_socket_dir
        allowed = {os.path.realpath(str(sessions_dir())),
                   os.path.realpath(short_socket_dir())}
    except Exception:  # noqa: BLE001 — a bad temp dir must not block cleanup of ours
        allowed = {os.path.realpath(str(sessions_dir()))}
    parent = os.path.realpath(os.path.dirname(str(path)))
    return parent in allowed


@dataclass
class SessionEntry:
    """Registry entry for one shadow session (§3)."""
    session_id: str
    pid: int
    label: str
    argv: List[str]
    cwd: str
    started_at: str
    socket: str
    journal: str
    version: int = SHADOW_PROTOCOL_VERSION
    meta: Dict[str, str] = field(default_factory=dict)
    allow_exec: bool = False
    # Shadow-side ceiling for control leases (§6.1):
    # "none" | "gated" | "unrestricted"
    control_ceiling: str = "none"
    # Headless sessions (§6.2): agent-spawned, no human terminal.
    headless: bool = False
    # Spawning conversation provenance ({conversation_id, turn}); null
    # for interactive sessions.
    spawned_by: Optional[Dict[str, object]] = None
    # Best segmentation currently active: "osc133" | "prompt-heuristic" | "raw"
    segmentation: str = "raw"
    # Set when a shadow-initiated ask (§8) is queued and unanswered
    pending_ask: bool = False
    # Chat attachment (phase 2): the conversation bound to this session for
    # observation — {conversation_id, mode, attached_at}.  Observation is
    # shareable, so this is a single display slot (last-writer-wins) that
    # names the primary attacher; every attach/detach is journaled.
    attached: Optional[Dict[str, object]] = None

    @property
    def display(self) -> str:
        """Display form always carries the id: 'prod-42 (a3f21e)' (§3)."""
        return f"{self.label} ({self.session_id})"

    def path(self) -> Path:
        return sessions_dir() / f"{self.session_id}.json"

    def is_alive(self) -> bool:
        """Liveness = the registering pid still exists."""
        try:
            os.kill(self.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but isn't ours (pid reuse across users) —
            # treat as dead for registry purposes: our sessions are
            # always same-UID.
            return False

    def save(self) -> None:
        """Write the entry atomically with 0600 perms."""
        p = self.path()
        tmp = p.with_suffix(".json.tmp")
        data = asdict(self)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)

    def remove(self) -> None:
        """Remove registry entry, socket, and journal (unlink-on-exit)."""
        for path in (self.path(), Path(self.socket), Path(self.journal)):
            if not _is_shadow_owned(path):
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass  # Best-effort cleanup; stale files are reaped by readers


def new_session_id() -> str:
    """Short random hex id, collision-checked against live entries."""
    while True:
        sid = secrets.token_hex(3)  # e.g. 'a3f21e'
        if not (sessions_dir() / f"{sid}.json").exists():
            return sid


def create_session(label: str, argv: List[str], *,
                   allow_exec: bool = False,
                   control_ceiling: str = "none",
                   meta: Optional[Dict[str, str]] = None,
                   headless: bool = False,
                   spawned_by: Optional[Dict[str, object]] = None) -> SessionEntry:
    """Create and persist a new session entry for the current process."""
    if control_ceiling not in ("none", "gated", "unrestricted"):
        raise ValueError(f"invalid control ceiling: {control_ceiling!r}")
    sid = new_session_id()
    d = sessions_dir()
    entry = SessionEntry(
        session_id=sid,
        pid=os.getpid(),
        label=label,
        argv=list(argv),
        cwd=os.getcwd(),
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        socket=str(d / f"{sid}.sock"),
        journal=str(d / f"{sid}.journal"),
        allow_exec=allow_exec,
        control_ceiling=control_ceiling,
        meta=dict(meta or {}),
        headless=headless,
        spawned_by=spawned_by,
    )
    entry.save()
    return entry


def load_session(session_id: str) -> Optional[SessionEntry]:
    """Load one entry by id.  Reaps and returns None if stale."""
    p = sessions_dir() / f"{session_id}.json"
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    known = {f.name for f in SessionEntry.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    entry = SessionEntry(**{k: v for k, v in data.items() if k in known})
    if not entry.is_alive():
        entry.remove()
        return None
    return entry


def list_sessions() -> List[SessionEntry]:
    """All live sessions, reaping stale entries as encountered (§3)."""
    out: List[SessionEntry] = []
    for p in sorted(sessions_dir().glob("*.json")):
        entry = load_session(p.stem)
        if entry is not None:
            out.append(entry)
    return out


def resolve(ref: str) -> List[SessionEntry]:
    """Resolve a session reference to candidate entries (§3 addressing).

    Accepted forms, checked in order:
      <id>          — exact session id
      <label>:<id>  — label-qualified id (id wins; label not verified)
      <label>       — all live sessions whose label matches exactly

    Returns all candidates; the caller handles disambiguation
    interactively when len > 1 (labels are not required to be unique).
    """
    by_id = load_session(ref)
    if by_id is not None:
        return [by_id]
    if ":" in ref:
        _, _, maybe_id = ref.rpartition(":")
        by_id = load_session(maybe_id)
        if by_id is not None:
            return [by_id]
    return [e for e in list_sessions() if e.label == ref]
