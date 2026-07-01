"""
``ziya-approve`` — the root-invoked escalation signer (ASR F-004 / F-007, design
doc §4.0 / §4.3).

This is the *minting* half of the escalation-config integrity control. The
verifier (``app/mcp_servers/shell_server.py``) refuses any escalation beyond the
default floor unless it carries a valid Ed25519 signature over the exact
escalation delta, made by the root-owned private key. This CLI is the only thing
that produces such a signature, and it is gated by ``sudo``:

    sudo ziya-approve            # show the pending escalation delta + sign it
    sudo ziya-approve --show     # print the delta only, do not sign (dry run)

Why this is the human gate the agent cannot satisfy:
  - The private key is ``root:root 0600`` (``/etc/ziya/approve_ed25519``). The
    agent runs as the normal user and gets PermissionError reading it.
  - Running this CLI to effect requires ``sudo``, which the agent's shell tool
    cannot drive: ``sudo``/``su`` are on the shell allowlist's ``always_blocked``
    set, and even a direct attempt hits a password / Touch-ID prompt on a TTY
    the agent's piped stdin cannot answer (it gets EOF).
  - The confirmation reads the escalation diff from the *config being approved*
    and previews exactly what is being granted, so the human signs a specific,
    visible privilege set — never a blank cheque.

What it signs: the **delta vs the default floor** (only the privilege *increment*
needs approval; a config at/within the floor produces an empty delta and needs no
signature). The signature is written back into the same env carrier the verifier
reads — ``ZIYA_SCOPE_SIG`` in ``mcpServers.shell.env`` of
``~/.ziya/mcp_config.json`` — so the next shell-server (re)start picks it up.

Editing any granted privilege later changes the delta hash, so the old signature
no longer verifies and the config silently drops back to the floor until
re-approved. That is the "authorization binds to content" property (§4.1).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Resolve the app package so this runs both as a console-script and via
# ``sudo python3 -m app.utils.ziya_approve``.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import scope_canonical as sc  # noqa: E402


def _mcp_config_path() -> Path:
    """The config file whose shell env block carries the escalation + signature.

    Resolved for the *invoking* user. Under ``sudo`` the HOME may be root's, so we
    honor ``ZIYA_APPROVE_CONFIG`` (set by the wrapper / tests) and fall back to
    ``SUDO_USER``'s home before the process HOME.
    """
    override = os.environ.get("ZIYA_APPROVE_CONFIG")
    if override:
        return Path(override)
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        # Best-effort: the real user's ~/.ziya, not root's.
        import pwd
        try:
            home = pwd.getpwnam(sudo_user).pw_dir
            return Path(home) / ".ziya" / "mcp_config.json"
        except KeyError:
            pass
    return Path.home() / ".ziya" / "mcp_config.json"


def _read_config(path: Path) -> Dict[str, Any]:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def _shell_env(cfg: Dict[str, Any]) -> Dict[str, str]:
    """Return the shell server's env block (read-only view; {} if absent)."""
    return cfg.get("mcpServers", {}).get("shell", {}).get("env", {})


def _compute_pending(cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (scope, delta) for the escalation currently sitting in the config."""
    env = _shell_env(cfg)
    scope = sc.parse_env_scope(env)
    delta = sc.compute_delta(scope)
    return scope, delta


def _render_delta(delta: Dict[str, Any]) -> str:
    if not delta:
        return "  (no escalation beyond the default floor — nothing to approve)"
    lines = []
    for key, val in sorted(delta.items()):
        if key == "YOLO_MODE":
            lines.append("  + YOLO_MODE: true   (DISABLES the command allowlist)")
        else:
            for item in val:
                lines.append(f"  + {key}: {item}")
    return "\n".join(lines)


def _write_signature(path: Path, sig: str) -> None:
    """Write ZIYA_SCOPE_SIG into the shell env block, preserving the rest."""
    cfg = _read_config(path)
    cfg.setdefault("mcpServers", {}).setdefault("shell", {}).setdefault("env", {})
    cfg["mcpServers"]["shell"]["env"][sc.SIG_ENV_KEY] = sig
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)


# ── Ephemeral session-grant minting (runtime consent tier) ────────────────────
# The ephemeral sibling of _write_signature: instead of writing a durable
# ZIYA_SCOPE_SIG into the config, mint a session-grant record bound to the
# running server's current nonce and drop it where the manager picks it up.
# Paths are derived from the invoking user's config dir (config_path.parent,
# already SUDO_USER-resolved), matching where the manager wrote the nonce.
_SESSION_GRANT_FILENAME = "session_grant_shell.json"


def _session_nonce_path(config_path: Path) -> Path:
    return config_path.parent / ".session_nonce"


def _pending_session_path(config_path: Path) -> Path:
    return config_path.parent / "pending_session_shell.json"


def _session_grant_path(config_path: Path) -> Path:
    return config_path.parent / _SESSION_GRANT_FILENAME


def _read_session_nonce(config_path: Path) -> Optional[str]:
    """The current server-start nonce, or None if the server isn't running."""
    try:
        nonce = _session_nonce_path(config_path).read_text().strip()
        return nonce or None
    except OSError:
        return None


def _read_pending_session_env(config_path: Path) -> Optional[Dict[str, str]]:
    """The transient requested shell env for an ephemeral grant, or None.

    Written by the /shell-config/request-session-grant endpoint from the modal
    fields. Same shape as the config's mcpServers.shell.env block, but it lives
    ONLY in this file — it is never merged into the durable config. The signer
    derives the delta from it with the same canonical code used for the config,
    so an ephemeral grant cannot claim a wider scope than what was requested.
    """
    try:
        data = json.loads(_pending_session_path(config_path).read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_session_grant(config_path: Path, record: Dict[str, Any]) -> Path:
    path = _session_grant_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    # Under `sudo ziya-approve --session` the file is created root-owned, but
    # the Ziya server runs as the invoking user and must be able to READ this
    # grant (the durable path never hit this because it rewrites an existing
    # user-owned config in place). Hand ownership back to SUDO_USER so the
    # server can load it; 0600 then means user-only, which is what we want.
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            import pwd
            pw = pwd.getpwnam(sudo_user)
            os.chown(path, pw.pw_uid, pw.pw_gid)
        except (OSError, KeyError):
            pass
    return path


def _approve_session(config_path: Path, provider: str, assume_yes: bool) -> int:
    """Mint an EPHEMERAL session grant for the pending shell-config escalation.

    Unlike the durable path, the delta is sourced from a TRANSIENT pending file
    (written by the "Apply for this session" UI action), NOT the durable
    config — so the escalation never lands on disk in the config at all. The
    signed grant carries the delta itself; the manager injects those values
    into the subprocess env at spawn and the subprocess re-verifies the grant.
    The grant authorizes the escalation for THIS server start only — void on
    the next cold start (new nonce). Durable, cross-restart privilege still
    requires plain ``ziya-approve`` + Save.
    """
    pending_env = _read_pending_session_env(config_path)
    if pending_env is None:
        sys.stderr.write(
            "No pending session request found "
            f"({_pending_session_path(config_path)}). Use \"Apply for this "
            "session\" in the shell-config UI to stage the escalation first.\n"
        )
        return 2
    _scope = sc.parse_env_scope(pending_env)
    delta = sc.compute_delta(_scope)

    sys.stdout.write(f"Config: {config_path}\n")
    sys.stdout.write(
        "Pending ephemeral escalation to grant for THIS session only "
        "(delta vs default floor):\n"
    )
    sys.stdout.write(_render_delta(delta) + "\n")

    if not delta:
        sys.stdout.write("Nothing to approve.\n")
        return 0

    nonce = _read_session_nonce(config_path)
    if not nonce:
        sys.stderr.write(
            "No current session nonce found "
            f"({_session_nonce_path(config_path)}). The Ziya server must be "
            "running to mint a session grant (the nonce is created at server "
            "start). Start Ziya, then re-run.\n"
        )
        return 2

    if not assume_yes and not _confirm(
        "\nGrant this escalation for the CURRENT server session only "
        "(ephemeral, voided on restart)? [y/N] "
    ):
        sys.stdout.write("Aborted; no session grant written.\n")
        return 1

    granted_by = os.environ.get("SUDO_USER") or os.environ.get("USER") or "unknown"
    granted_at = int(time.time())
    try:
        record = sc.sign_session_grant(nonce, delta, provider, granted_by, granted_at)
    except PermissionError:
        sys.stderr.write(
            f"PermissionError reading the private key ({sc.private_key_path()}). "
            f"Run via 'sudo ziya-approve --session' — only root may sign.\n"
        )
        return 2
    except FileNotFoundError:
        sys.stderr.write(
            f"Private key not found at {sc.private_key_path()}. "
            f"Run the provisioning script (scripts/provision_approve_key.sh) first.\n"
        )
        return 2
    except Exception as e:  # noqa: BLE001 — surface any key/sign failure clearly
        sys.stderr.write(f"Signing failed: {e}\n")
        return 2

    path = _write_session_grant(config_path, record)
    sys.stdout.write(
        f"\n✓ Signed session grant written to {path}.\n"
        f"  Provider: {provider}.  Bound to the current server session.\n"
        f"  Apply it from the shell-config UI (or restart the shell server) to\n"
        f"  take effect now; it is automatically void on the next server start.\n"
    )
    return 0


def _resolve_card(project_id: str, card_id: str):
    """Load a TaskCard by (project_id, card_id) for the invoking user.

    Honors SUDO_USER so a root-invoked signer reads the real user's project
    storage (~/.ziya/projects/<id>/task_cards), not root's. Returns the card or
    None.

    Reads the card JSON directly and wraps it in attribute-access namespaces
    rather than going through TaskCardStorage / the pydantic models. The block
    helpers (_find_block, task_escalation_block) are all duck-typed via getattr,
    so a plain namespace tree satisfies them — and this keeps the signer free of
    the app.storage import cascade (app.storage.__init__ -> projects -> pydantic
    -> ... -> asyncio). That cascade makes a signing utility needlessly fragile:
    unrelated environment rot (e.g. a stale PyPI `asyncio` backport shadowing the
    stdlib) would otherwise crash task approval before any signing logic runs.
    """
    # Point HOME at the invoking user's home for path resolution under sudo.
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and not os.environ.get("ZIYA_APPROVE_PROJECTS_DIR"):
        import pwd
        try:
            os.environ.setdefault("HOME", pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    from app.utils.paths import get_project_dir
    project_dir = (
        Path(os.environ["ZIYA_APPROVE_PROJECTS_DIR"]) / project_id
        if os.environ.get("ZIYA_APPROVE_PROJECTS_DIR")
        else get_project_dir(project_id)
    )
    card_file = project_dir / "task_cards" / f"{card_id}.json"
    try:
        raw = card_file.read_bytes()
    except OSError:
        return None
    # Auto-detect ALE-encrypted vs plaintext, mirroring BaseStorage._read_json
    # but without importing the storage layer.
    from app.utils.encryption import is_encrypted, get_encryptor
    if is_encrypted(raw):
        # The signer runs out-of-process under sudo with no plugin system, so
        # the encryption provider / file-KEK that the server holds is NOT
        # available here and decryption will fail (keyring is None). Fail soft
        # and return None so _approve_task falls back to the server-staged
        # decrypted scope (the running server, which holds the KEK, stages it
        # via the scope-status endpoint). Never crash the signer on this path.
        try:
            text = get_encryptor().decrypt(raw)
        except Exception:  # noqa: BLE001 — any decrypt failure -> staged fallback
            return None
    else:
        text = raw.decode("utf-8")
    try:
        data = json.loads(text)
    except ValueError as e:
        sys.stderr.write(f"Could not parse task card {card_file}: {e}\n")
        return None
    if not isinstance(data, dict) or "root" not in data:
        return None
    return _ns(data)


def _ns(obj):
    """Recursively wrap dicts/lists in attribute-access namespaces.

    The signer's block helpers access fields via getattr (block.id, block.body,
    block.scope, scope.shell_commands, scope.paths, entry.path, entry.write), so
    a SimpleNamespace tree is a sufficient stand-in for the pydantic models and
    avoids importing them.
    """
    from types import SimpleNamespace
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_ns(v) for v in obj]
    return obj


def _find_block(block, target_id):
    """Depth-first walk of a block tree to find a block by id. None if absent."""
    if getattr(block, "id", None) == target_id:
        return block
    for child in (getattr(block, "body", None) or []):
        hit = _find_block(child, target_id)
        if hit is not None:
            return hit
    return None


def _render_task_escalation(block_obj) -> str:
    """Human-readable preview of a task block's privilege-bearing escalation."""
    esc = sc.task_escalation_block(getattr(block_obj, "scope", None))
    if not esc:
        return "  (no escalation beyond the default floor — nothing to approve)"
    lines = []
    for cmd in esc.get("shell_commands", []):
        lines.append(f"  + shell command: {cmd}")
    for path in esc.get("writable_paths", []):
        lines.append(f"  + writable path: {path}")
    return "\n".join(lines)


def _pending_task_approvals_path() -> Path:
    """Where the server stages decrypted block scopes for the signer.

    Honors SUDO_USER so a root-invoked signer reads the real user's ~/.ziya,
    matching where the running server (which holds the KEK) wrote it.
    """
    home = os.environ.get("HOME")
    return Path(home) / ".ziya" / "pending_task_approvals.json" if home else \
        Path.home() / ".ziya" / "pending_task_approvals.json"


def _resolve_staged_block(project_id: str, card_id: str, block_id: str):
    """Fallback block source when the card can't be decrypted out-of-process.

    The running server stages each unapproved block's DECRYPTED scope (keyed by
    "project:card:block") via the scope-status endpoint. The signer reads that
    and wraps it so the same getattr-based helpers work. This never widens
    authority: the runtime gate independently recomputes task_scope_hash from
    the real card, so a stale/spoofed staging just fails the hash match and
    clamps to the floor — identical fail-closed behavior to session grants.
    """
    key = f"{project_id}:{card_id}:{block_id}"
    try:
        staged = json.loads(_pending_task_approvals_path().read_text())
        entry = staged[key]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(entry, dict):
        return None
    # Wrap into a block-like namespace exposing .name and .scope, matching what
    # _find_block would have returned from a decrypted card.
    return _ns({
        "id": block_id,
        "name": entry.get("name", ""),
        "scope": entry.get("scope") or {},
    })


def _approve_task(project_id: str, card_id: str, block_id: str,
                  assume_yes: bool) -> int:
    """Sign a task-scope approval record for one block of a card.

    Mirrors the shell-config flow: preview the escalation, require the genuine
    confirmation (unless --yes), sign with the root key, and persist a record to
    the approval store keyed by the block id. Editing the block's scope later
    changes its hash, so the record stops matching and the escalation drops to
    the floor until re-approved.
    """
    # Local imports so the shell-config path has no task dependencies.
    from app.utils import scope_approvals as sa

    card = _resolve_card(project_id, card_id)
    block_obj = _find_block(card.root, block_id) if card is not None else None
    if block_obj is None:
        # Card missing/plaintext-absent, OR encrypted and undecryptable by the
        # out-of-process signer. Fall back to the server-staged decrypted scope.
        block_obj = _resolve_staged_block(project_id, card_id, block_id)
    if block_obj is None:
        sys.stderr.write(
            f"Block {block_id!r} not found for card {card_id!r}.\n"
            "If the card is encrypted, open it in the Ziya UI (which stages the "
            "block scope for signing) and retry while the server is running.\n"
        )
        return 2

    scope = getattr(block_obj, "scope", None)
    scope_hash = sc.task_scope_hash(scope)

    sys.stdout.write(
        f"Task card: {card_id}  block: {block_id} "
        f"({getattr(block_obj, 'name', '')!r})\n"
    )
    sys.stdout.write("Pending task-scope escalation (vs default floor):\n")
    sys.stdout.write(_render_task_escalation(block_obj) + "\n")

    if not scope_hash:
        sys.stdout.write("Nothing to approve.\n")
        return 0

    if not assume_yes and not _confirm(
        "\nSign this task-scope escalation with the root approval key? [y/N] "
    ):
        sys.stdout.write("Aborted; no approval record written.\n")
        return 1

    approved_by = os.environ.get("SUDO_USER") or os.environ.get("USER") or "unknown"
    approved_at = int(time.time())
    try:
        sig = sc.sign_approval_record(block_id, scope_hash, approved_by, approved_at)
    except PermissionError:
        sys.stderr.write(
            f"PermissionError reading the private key ({sc.private_key_path()}). "
            f"Run via 'sudo ziya-approve' — only root may sign.\n"
        )
        return 2
    except FileNotFoundError:
        sys.stderr.write(
            f"Private key not found at {sc.private_key_path()}. "
            f"Run the provisioning script (scripts/provision_approve_key.sh) first.\n"
        )
        return 2
    except Exception as e:  # noqa: BLE001 — surface any key/sign failure clearly
        sys.stderr.write(f"Signing failed: {e}\n")
        return 2

    record = {
        "task_id": block_id,
        "scope_hash": scope_hash,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "signature": sig,
    }
    path = sa.save_record(record)
    sys.stdout.write(
        f"\n✓ Signed. Approval record written to {path}.\n"
        f"  The escalation takes effect on the task's next run.\n"
        f"  Editing any granted privilege voids this approval until re-approved.\n"
    )
    return 0


def _render_cli_allow(allow: Dict[str, Any]) -> str:
    block = sc.cli_escalation_block(allow)
    if not block:
        return "  (no escalation beyond the default floor — nothing to approve)"
    label = {"commands": "shell command", "git_operations": "git operation",
             "write_patterns": "writable pattern"}
    lines = []
    for field in ("commands", "git_operations", "write_patterns"):
        for item in block.get(field, []):
            lines.append(f"  + {label[field]}: {item}")
    return "\n".join(lines)


def _approve_cli_task(task_name: str, root: Optional[str],
                      assume_yes: bool) -> int:
    """Sign an approval record for a CLI (tasks.yaml) task's ``allow`` block.

    Mirrors the card flow but for the CLI surface: resolves the tasks file that
    defines *task_name* (project-local overrides global, matching load_tasks),
    keys the record on ``cli:<realpath>#<name>`` so it is stable across symlinked
    access paths, hashes the ``allow`` escalation, and persists a signed record
    to the same store the card path uses (design §6 — one ledger, one chokepoint).
    """
    from app.utils import scope_approvals as sa
    from app.task_runner import load_tasks, resolve_task_source_file, validate_task_allow

    tasks = load_tasks(root)
    if task_name not in tasks:
        sys.stderr.write(f"Unknown task {task_name!r} (run 'ziya task --list').\n")
        return 2
    task_def = tasks[task_name]
    allow = task_def.get("allow")

    errors = validate_task_allow(task_def)
    if errors:
        sys.stderr.write(f"Task {task_name!r} has an invalid allow block:\n")
        for e in errors:
            sys.stderr.write(f"  • {e}\n")
        return 2

    src = resolve_task_source_file(task_name, root)
    if src is None:
        # builtin or undefined-in-file — no allow block to approve
        sys.stdout.write(f"Task {task_name!r} defines no approvable escalation.\n")
        return 0
    task_key = sa.cli_task_key(str(src), task_name)
    scope_hash = sc.cli_task_hash(allow)

    sys.stdout.write(f"CLI task: {task_name}  (from {src})\n")
    sys.stdout.write(f"Store key: {task_key}\n")
    sys.stdout.write("Pending escalation (vs default floor):\n")
    sys.stdout.write(_render_cli_allow(allow) + "\n")

    if not scope_hash:
        sys.stdout.write("Nothing to approve.\n")
        return 0

    if not assume_yes and not _confirm(
        "\nSign this CLI-task escalation with the root approval key? [y/N] "
    ):
        sys.stdout.write("Aborted; no approval record written.\n")
        return 1

    approved_by = os.environ.get("SUDO_USER") or os.environ.get("USER") or "unknown"
    approved_at = int(time.time())
    try:
        sig = sc.sign_approval_record(task_key, scope_hash, approved_by, approved_at)
    except PermissionError:
        sys.stderr.write(
            f"PermissionError reading the private key ({sc.private_key_path()}). "
            f"Run via 'sudo ziya-approve' — only root may sign.\n"
        )
        return 2
    except FileNotFoundError:
        sys.stderr.write(
            f"Private key not found at {sc.private_key_path()}. "
            f"Run the provisioning script (scripts/provision_approve_key.sh) first.\n"
        )
        return 2
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"Signing failed: {e}\n")
        return 2

    record = {
        "task_id": task_key,
        "scope_hash": scope_hash,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "signature": sig,
    }
    path = sa.save_record(record)
    sys.stdout.write(
        f"\n✓ Signed. Approval record written to {path}.\n"
        f"  The escalation takes effect on the task's next run.\n"
        f"  Editing any granted privilege voids this approval until re-approved.\n"
    )
    return 0


def _confirm(prompt: str) -> bool:
    """Read a yes/no confirmation from a genuine interactive terminal.

    Two acquisition paths, both of which a piped/agent invocation fails:
      1. ``/dev/tty`` — the controlling terminal. The agent's shell tool runs
         with no controlling TTY, so the open raises and we fall through.
      2. stdin, but ONLY when ``sys.stdin.isatty()`` is true. The agent's stdin
         is a pipe (``isatty() == False``), so this branch is unreachable for it.
         This fallback exists because ``/dev/tty`` is not reliably openable under
         ``sudo`` on macOS even for a genuine human (verified on a real machine,
         design §8 Q5) — without it the legitimate operator gets refused.

    Either way the security property holds: a caller with no controlling TTY and
    a non-interactive stdin cannot answer, and we fail closed (return False /
    refuse) rather than auto-approving. ``sudo`` itself remains the primary gate;
    this confirmation is the secondary, human-presence check on top of it.
    """
    # Path 1: the controlling terminal (strongest — independent of stdin).
    try:
        with open("/dev/tty", "r+") as tty:
            tty.write(prompt)
            tty.flush()
            ans = tty.readline().strip().lower()
        return ans in ("y", "yes")
    except (OSError, IOError):
        pass  # no controlling TTY — try the interactive-stdin fallback

    # Path 2: interactive stdin only. A piped stdin (the agent) is NOT a TTY, so
    # this branch refuses it; only a real terminal session reaches the prompt.
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            sys.stderr.write(prompt)
            sys.stderr.flush()
            ans = sys.stdin.readline().strip().lower()
            return ans in ("y", "yes")
    except (OSError, IOError, ValueError):
        pass

    # Neither path available (no TTY anywhere) — refuse rather than auto-approve.
    sys.stderr.write(
        "No interactive terminal available; refusing to sign non-interactively. "
        "Run from a real terminal, or pass --yes (still sudo-gated).\n"
    )
    return False


def _summarize_escalation(surface: str, esc: Dict[str, Any]) -> str:
    """One-line privilege summary for an audit row."""
    parts: list[str] = []
    if surface == "card":
        if esc.get("shell_commands"):
            parts.append("cmds:" + ",".join(esc["shell_commands"]))
        if esc.get("writable_paths"):
            parts.append("write:" + ",".join(esc["writable_paths"]))
    else:  # cli
        if esc.get("commands"):
            parts.append("cmds:" + ",".join(esc["commands"]))
        if esc.get("git_operations"):
            parts.append("git:" + ",".join(esc["git_operations"]))
        if esc.get("write_patterns"):
            parts.append("write:" + ",".join(esc["write_patterns"]))
    return "  ".join(parts)


def _list_audit(root: Optional[str]) -> int:
    """Print every escalating task (cards + CLI) with its signed status.

    Exit code is the compliance signal: 0 when every escalating task is signed,
    1 when any escalation is unsigned (so this doubles as a CI/audit gate).

    Card scopes are scanned globally (all registered projects). CLI tasks
    (``tasks.yaml``) are scanned relative to *root* — or the current working
    directory when no ``--root`` is given — because the project working-dir
    paths live in the at-rest-encrypted project index, which this out-of-process
    command holds no KEK to read. The scanned CLI root is printed in the header
    so the cwd-dependence is explicit: an empty CLI surface means "no
    tasks.yaml under this root", never a silently-missed escalation.
    """
    import os
    from app.utils.scope_audit import collect_audit
    cli_root = os.path.realpath(root) if root else os.getcwd()
    result = collect_audit(root)
    escalating = result.escalating

    sys.stdout.write("Escalation audit — every task requesting privilege "
                     "beyond the default floor:\n\n")
    sys.stdout.write(f"\033[90m  Cards: all projects.  CLI tasks: {cli_root}"
                     f"\n  (pass --root <dir> to audit another project's "
                     f"tasks.yaml)\033[0m\n\n")
    if not escalating:
        sys.stdout.write("  (no escalating tasks found)\n")
    else:
        for e in escalating:
            status = "\033[32m✓ signed\033[0m" if e.signed \
                else "\033[33m🔒 UNSIGNED\033[0m"
            sys.stdout.write(f"  [{e.surface}] {e.label}\n")
            sys.stdout.write(f"        status : {status}\n")
            sys.stdout.write(f"        grants : "
                             f"{_summarize_escalation(e.surface, e.escalation)}\n")
            sys.stdout.write(f"        key    : {e.store_key}\n")
            if e.surface == "cli" and not e.signed:
                sys.stdout.write(
                    f"        approve: sudo ziya-approve --cli-task {e.label}"
                    + (f" --root {root}" if root else "") + "\n")
            sys.stdout.write("\n")

    if result.encrypted_card_files:
        sys.stdout.write(
            f"\033[90m  Note: {result.encrypted_card_files} encrypted card "
            f"file(s) could not be inspected here (no KEK out-of-process). "
            f"Use the GUI/server audit to inspect those.\033[0m\n")

    unsigned = result.unsigned
    if unsigned:
        sys.stdout.write(
            f"\n\033[33m{len(unsigned)} escalating task(s) UNSIGNED — "
            f"they run at the default floor until approved.\033[0m\n")
        return 1
    sys.stdout.write("\n\033[32mAll escalating tasks are signed.\033[0m\n")
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ziya-approve",
        description="Sign the pending shell-config escalation (root-gated).",
    )
    parser.add_argument(
        "--list", action="store_true", dest="list_audit",
        help="Audit every escalating task (cards + tasks.yaml) and show its "
             "signed/unsigned status. Read-only; no key required. Exit 1 if "
             "any escalation is unsigned.",
    )
    parser.add_argument(
        "--show", action="store_true",
        help="Print the pending escalation delta and exit without signing.",
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to mcp_config.json (default: invoking user's ~/.ziya/).",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Skip the interactive confirmation (still requires the private key).",
    )
    # Task-scope approval mode (ASR F-001). When --task/--block are given, sign a
    # task-scope approval record instead of the shell-config env delta.
    parser.add_argument(
        "--task", default=None,
        help="Task card id whose block scope to approve (requires --block, --project).",
    )
    parser.add_argument(
        "--block", default=None,
        help="Block id within the card to approve.",
    )
    parser.add_argument(
        "--project", default=None,
        help="Project id owning the task card (for --task mode).",
    )
    # CLI-task approval mode (ASR F-001). When --cli-task is given, sign an
    # approval record for a tasks.yaml task's ``allow`` block instead of a card
    # scope or the shell-config env delta.
    parser.add_argument(
        "--cli-task", default=None, dest="cli_task",
        help="Name of a tasks.yaml task whose 'allow' escalation to approve.",
    )
    parser.add_argument(
        "--root", default=None,
        help="Project root for --cli-task resolution (default: cwd).",
    )
    # Ephemeral session-grant mode (runtime consent tier). Mint a grant bound to
    # the running server's current nonce instead of a durable ZIYA_SCOPE_SIG;
    # the escalation is authorized for THIS server start only and is void on the
    # next cold start. Durable, cross-restart privilege still uses plain mode.
    parser.add_argument(
        "--session", action="store_true",
        help="Grant the pending shell escalation for the CURRENT server "
             "session only (ephemeral; voided on next server start).",
    )
    parser.add_argument(
        "--provider", default="os-credential",
        help="Consent provider that authorized this session grant "
             "(default: os-credential; the trust anchor is the root key).",
    )
    args = parser.parse_args(argv)

    # Read-only audit mode — no signing, no key, no sudo required. Routed first
    # so it never touches the config/key paths the signing modes need.
    if args.list_audit:
        return _list_audit(args.root)

    # Route to CLI-task approval when --cli-task is supplied.
    if args.cli_task:
        return _approve_cli_task(args.cli_task, args.root, args.yes)

    # Route to task-scope approval when --task/--block are supplied.
    if args.task or args.block:
        if not (args.task and args.block and args.project):
            sys.stderr.write(
                "--task mode requires --task, --block, and --project together.\n"
            )
            return 2
        return _approve_task(args.project, args.task, args.block, args.yes)

    config_path = Path(args.config) if args.config else _mcp_config_path()

    # Route to ephemeral session-grant minting when --session is supplied.
    if args.session:
        return _approve_session(config_path, args.provider, args.yes)

    cfg = _read_config(config_path)
    scope, delta = _compute_pending(cfg)

    sys.stdout.write(f"Config: {config_path}\n")
    sys.stdout.write("Pending escalation (delta vs default floor):\n")
    sys.stdout.write(_render_delta(delta) + "\n")

    if args.show:
        return 0

    if not delta:
        # Nothing to sign; clear any stale signature so the file is tidy.
        sys.stdout.write("Nothing to approve.\n")
        return 0

    if not args.yes and not _confirm(
        "\nSign this escalation with the root approval key? [y/N] "
    ):
        sys.stdout.write("Aborted; no signature written.\n")
        return 1

    try:
        sig = sc.sign_delta(delta)
    except PermissionError:
        sys.stderr.write(
            f"PermissionError reading the private key ({sc.private_key_path()}). "
            f"Run via 'sudo ziya-approve' — only root may sign.\n"
        )
        return 2
    except FileNotFoundError:
        sys.stderr.write(
            f"Private key not found at {sc.private_key_path()}. "
            f"Run the provisioning script (scripts/provision_approve_key.sh) first.\n"
        )
        return 2
    except Exception as e:  # noqa: BLE001 — surface any key/sign failure clearly
        sys.stderr.write(f"Signing failed: {e}\n")
        return 2

    _write_signature(config_path, sig)
    sys.stdout.write(
        f"\n✓ Signed. {sc.SIG_ENV_KEY} written to {config_path}.\n"
        f"  The escalation takes effect on the next shell-server (re)start.\n"
        f"  Editing any granted privilege voids this signature until re-approved.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
