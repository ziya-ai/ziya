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
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Resolve the app package so this runs both as a console-script and via
# ``sudo python3 -m app.utils.ziya_approve``.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import scope_canonical as sc  # noqa: E402


def _provision(force: bool) -> int:
    """One-time, per-machine root setup: generate the Ed25519 approval keypair
    and the locked-down sudoers entry that gates ``ziya-approve`` (ASR
    F-004/F-007, design §4.0/§5). Pure-Python so it ships with the console entry
    point and needs no path-dependent file on toolbox/pip installs.

    After this runs:
        /etc/ziya/approve_ed25519        root:root 0600  (private — signer only)
        /etc/ziya/approve_ed25519.pub    root:root 0644  (public  — verifier)
        /etc/sudoers.d/ziya-approve      0440            (timestamp_timeout=0)
    """
    if os.geteuid() != 0:
        sys.stderr.write("--provision must run as root: sudo ziya-approve --provision\n")
        return 2

    real_user = os.environ.get("SUDO_USER")
    if not real_user:
        sys.stderr.write(
            "Could not determine the invoking user (SUDO_USER unset). "
            "Re-run with: sudo ziya-approve --provision\n"
        )
        return 2

    priv = Path(sc.private_key_path())
    pub = Path(sc.public_key_path())
    key_dir = priv.parent
    key_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(key_dir, 0o755)

    if priv.exists() and not force:
        sys.stdout.write(
            f"Keypair already present at {priv} (use --force to regenerate). "
            f"Leaving as-is.\n"
        )
    else:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        key = Ed25519PrivateKey.generate()
        priv_bytes = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        pub_bytes = key.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
        )
        # Write private key 0600 from creation (never briefly world-readable).
        fd = os.open(str(priv), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        try:
            os.write(fd, priv_bytes)
        finally:
            os.close(fd)
        pub.write_bytes(pub_bytes + b"\n")

    import grp
    try:
        root_grp = grp.getgrgid(0).gr_name
    except KeyError:
        root_grp = "wheel"
    for p, mode in ((priv, 0o600), (pub, 0o644)):
        shutil.chown(str(p), user="root", group=root_grp)
        os.chmod(p, mode)
    if os.stat(priv).st_mode & 0o077:
        sys.stderr.write(f"ERROR: {priv} is not owner-only after chmod; aborting.\n")
        return 2
    sys.stdout.write(f"  private: {priv} (0600 root:{root_grp})\n")
    sys.stdout.write(f"  public:  {pub} (0644 root:{root_grp})\n")

    # sudoers entry: gate ziya-approve, force re-auth every invocation
    # (timestamp_timeout=0 → no cached credential an agent-timed call could
    # ride). No NOPASSWD: the password / Touch-ID prompt IS the human gate.
    approve_bin = shutil.which("ziya-approve")
    if approve_bin:
        cmnd = approve_bin
    else:
        pybin = shutil.which("python3") or sys.executable
        cmnd = f"{pybin} -m app.utils.ziya_approve"
    sudoers_body = (
        "# Ziya escalation approval — re-auth every invocation (no cached "
        "timestamp).\n"
        "# Managed by `ziya-approve --provision`. Do not edit by hand.\n"
        "Defaults!ZIYA_APPROVE timestamp_timeout=0\n"
        f"Cmnd_Alias ZIYA_APPROVE = {cmnd}\n"
        f"{real_user} ALL=(root) ZIYA_APPROVE\n"
    )
    sudoers_path = Path("/etc/sudoers.d/ziya-approve")
    import tempfile
    tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".sudoers")
    try:
        tmp.write(sudoers_body)
        tmp.close()
        # Validate before installing — a malformed sudoers file locks out sudo.
        chk = subprocess.run(["visudo", "-cf", tmp.name], capture_output=True)
        if chk.returncode != 0:
            sys.stderr.write(
                "ERROR: generated sudoers entry failed visudo validation; "
                "NOT installing.\n" + chk.stderr.decode("utf-8", "replace")
            )
            return 2
        shutil.copy(tmp.name, str(sudoers_path))
        os.chmod(sudoers_path, 0o440)
        shutil.chown(str(sudoers_path), user="root", group=root_grp)
    finally:
        os.unlink(tmp.name)
    sys.stdout.write(f"  sudoers: {sudoers_path} (validated, timestamp_timeout=0)\n")
    sys.stdout.write(
        "\nProvisioning complete. Approve escalations with:  sudo ziya-approve\n"
        "The Ziya agent (normal user) cannot read the private key and cannot "
        "run sudo to effect.\n"
    )
    return 0


def _mcp_config_path() -> Path:
    """The config file whose shell env block carries the escalation + signature.

    Resolved for the *invoking* user. Under ``sudo`` the HOME may be root's, so we
    honor ``ZIYA_APPROVE_CONFIG`` (set by the wrapper / tests) and fall back to
    ``SUDO_USER``'s home before the process HOME.
    """
    override = os.environ.get("ZIYA_APPROVE_CONFIG")
    if override:
        return Path(override)
    # Mirror app.utils.paths.get_ziya_home()'s ZIYA_HOME override, which the
    # server (writer of ~/.ziya/.approval_max_ttl and mcp_config.json) honors
    # first. Without this the writer would drop those files under $ZIYA_HOME
    # while this signer looked under SUDO_USER's ~/.ziya, so the auto-stamp
    # breadcrumb would never be found and every approval would be minted
    # unbounded. ZIYA_HOME is an absolute, user-agnostic path, so it applies
    # identically under sudo. Resolve only — never mkdir/chmod here: this runs
    # as root under sudo and must not create or re-own dirs in the user's tree.
    ziya_home = os.environ.get("ZIYA_HOME")
    if ziya_home:
        return Path(ziya_home) / "mcp_config.json"
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


def _read_policy_max_ttl() -> Optional[int]:
    """Read the enterprise approval-TTL bound (seconds) the server dropped for
    the signer, or None if unbounded/absent.

    Uses the same invoking-user resolution as _mcp_config_path (root's HOME is
    useless under sudo). UX breadcrumb only — scope_approvals enforces the bound
    server-side and fail-closed regardless of what the signer stamps.
    """
    try:
        path = _mcp_config_path().parent / ".approval_max_ttl"
        raw = path.read_text().strip()
        return int(raw) if raw else None
    except (OSError, ValueError):
        return None


def _resolve_expires_at(approved_at: int,
                        ttl_days: Optional[float]) -> Optional[int]:
    """Compute the signed expires_at for an approval, or None for unbounded.

    Precedence: an explicit --ttl-days and the server policy bound are both
    honored; when both are present the most restrictive (soonest) wins. When
    neither is present the approval is unbounded — the open-source default,
    preserving prior behavior.
    """
    explicit = int(ttl_days * 86400) if ttl_days is not None else None
    policy = _read_policy_max_ttl()
    candidates = [t for t in (explicit, policy) if t is not None and t > 0]
    if not candidates:
        return None
    return int(approved_at) + min(candidates)


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
            f"Run 'sudo ziya-approve --provision' first.\n"
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


def _find_block_with_ancestors(block, target_id, ancestors=()):
    """Like ``_find_block`` but also returns the root→parent tuple of
    ancestor scopes above the match, so the caller can hash/sign the
    block's EFFECTIVE (merged) escalation rather than just its own.
    Returns ``(block, ancestor_scopes)`` or ``None`` if not found."""
    if getattr(block, "id", None) == target_id:
        return block, ancestors
    for child in (getattr(block, "body", None) or []):
        hit = _find_block_with_ancestors(
            child, target_id, ancestors + (getattr(block, "scope", None),))
        if hit is not None:
            return hit
    return None


def _iter_task_blocks(block, ancestors=()):
    """Depth-first yield of ``(task_block, ancestor_scopes)`` for every leaf
    TASK block in a card tree — mirrors ``scope_audit._iter_task_blocks``.
    Only ``block_type == "task"`` blocks are gated at runtime (see
    ``_approve_task``'s docstring / block_executor), so container blocks are
    walked for their scope (contributed via ``ancestors``) but never yielded
    themselves."""
    if getattr(block, "block_type", None) == "task":
        yield block, ancestors
    for child in (getattr(block, "body", None) or []):
        yield from _iter_task_blocks(
            child, ancestors + (getattr(block, "scope", None),))


def _resolve_deck_scope(project_id: str):
    """Read a project's deck-level (project-wide) taskScope, namespace-
    wrapped.  Decrypt-soft (mirrors ``_resolve_card``): returns None on
    any unreadable/undecryptable/malformed project.json, or when the
    project has no deck scope configured — never raises."""
    from app.utils.paths import get_project_dir
    project_dir = (
        Path(os.environ["ZIYA_APPROVE_PROJECTS_DIR"]) / project_id
        if os.environ.get("ZIYA_APPROVE_PROJECTS_DIR")
        else get_project_dir(project_id)
    )
    try:
        raw = (project_dir / "project.json").read_bytes()
    except OSError:
        return None
    from app.utils.encryption import is_encrypted, get_encryptor
    try:
        text = get_encryptor().decrypt(raw) if is_encrypted(raw) else raw.decode("utf-8")
        data = json.loads(text)
    except Exception:  # noqa: BLE001 — no KEK out-of-process; deck scope unknown
        return None
    if not isinstance(data, dict):
        return None
    return _ns((data.get("settings") or {}).get("taskScope"))


def _render_task_escalation(*scopes) -> str:
    """Human-readable preview of the EFFECTIVE (merged) privilege-bearing
    escalation across one or more scope layers — deck, card, ancestors,
    and the target block's own scope, in that order."""
    esc = sc.task_escalation_block(*scopes)
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
                  assume_yes: bool, ttl_days: Optional[float] = None) -> int:
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
    found = _find_block_with_ancestors(card.root, block_id) if card is not None else None
    if found is not None:
        block_obj, ancestor_scopes = found
        # Full effective chain: deck scope (project-wide) + card scope +
        # every ancestor container block's scope + the target block's own.
        # Matches EXACTLY what ExecutionContext.effective_scope grants at
        # run time — see app/agents/block_executor.py.
        deck_scope = _resolve_deck_scope(project_id)
        card_scope = getattr(card, "scope", None)
        scope_chain = (deck_scope, card_scope) + ancestor_scopes + (getattr(block_obj, "scope", None),)
    else:
        # Card missing/plaintext-absent, OR encrypted and undecryptable by the
        # out-of-process signer. Fall back to the server-staged decrypted
        # scope — the server (which holds the KEK) already staged the FULL
        # EFFECTIVE (merged) scope via get_card_scope_status, so this is
        # already a single, complete chain of one.
        block_obj = _resolve_staged_block(project_id, card_id, block_id)
        scope_chain = (getattr(block_obj, "scope", None),) if block_obj is not None else ()
    if block_obj is None:
        sys.stderr.write(
            f"Block {block_id!r} not found for card {card_id!r}.\n"
            "If the card is encrypted, open it in the Ziya UI (which stages the "
            "block scope for signing) and retry while the server is running.\n"
        )
        return 2

    scope_hash = sc.task_scope_hash(*scope_chain)

    sys.stdout.write(
        f"Task card: {card_id}  block: {block_id} "
        f"({getattr(block_obj, 'name', '')!r})\n"
    )
    sys.stdout.write("Pending EFFECTIVE task-scope escalation (deck + card + "
                     "ancestors + block, vs default floor):\n")
    sys.stdout.write(_render_task_escalation(*scope_chain) + "\n")

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
    expires_at = _resolve_expires_at(approved_at, ttl_days)
    try:
        sig = sc.sign_approval_record(block_id, scope_hash, approved_by,
                                      approved_at, expires_at=expires_at)
    except PermissionError:
        sys.stderr.write(
            f"PermissionError reading the private key ({sc.private_key_path()}). "
            f"Run via 'sudo ziya-approve' — only root may sign.\n"
        )
        return 2
    except FileNotFoundError:
        sys.stderr.write(
            f"Private key not found at {sc.private_key_path()}. "
            f"Run 'sudo ziya-approve --provision' first.\n"
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
    if expires_at is not None:
        record["expires_at"] = expires_at
    path = sa.save_record(record)
    sys.stdout.write(
        f"\n✓ Signed. Approval record written to {path}.\n"
        f"  The escalation takes effect on the task's next run.\n"
        f"  Editing any granted privilege voids this approval until re-approved.\n"
    )
    return 0


def _approve_all_task(project_id: str, card_id: str,
                      assume_yes: bool, ttl_days: Optional[float] = None) -> int:
    """Sign every unapproved leaf-task block in a card with ONE confirmation.

    A card with several escalating blocks otherwise needs one
    ``sudo ziya-approve --task ... --block ...`` invocation (and one
    interactive confirmation) per block, which is impractical for anything
    beyond a couple of blocks. This walks the card's leaf TASK blocks (see
    ``_iter_task_blocks`` — containers are never gated at runtime, so they are
    walked for scope only, not individually signed), computes each block's
    EFFECTIVE scope hash exactly as ``_approve_task`` does, previews the union
    of every still-unsigned escalation, and — after a single confirmation —
    signs and persists one approval record per unsigned block.

    Blocks already signed for their current scope hash are skipped (reported,
    not re-signed) so re-running ``--all`` after editing only one block does
    not require re-confirming the ones already approved.
    """
    from app.utils import scope_approvals as sa

    card = _resolve_card(project_id, card_id)
    if card is None:
        sys.stderr.write(
            f"Card {card_id!r} not found or undecryptable out-of-process.\n"
            "If the card is encrypted, open it in the Ziya UI (which stages "
            "each block's decrypted scope for signing) and retry while the "
            "server is running.\n"
        )
        return 2

    deck_scope = _resolve_deck_scope(project_id)
    card_scope = getattr(card, "scope", None)

    pending = []   # (block_id, name, scope_chain, scope_hash)
    already = []   # (block_id, name)
    for block_obj, ancestor_scopes in _iter_task_blocks(getattr(card, "root", card)):
        scope_chain = (deck_scope, card_scope) + ancestor_scopes + (
            getattr(block_obj, "scope", None),)
        scope_hash = sc.task_scope_hash(*scope_chain)
        if not scope_hash:
            continue  # no escalation on this block — runs at the floor already
        block_id = getattr(block_obj, "id", "") or ""
        name = getattr(block_obj, "name", "") or block_id
        if sa.is_scope_authorized(block_id, *scope_chain):
            already.append((block_id, name))
            continue
        pending.append((block_id, name, scope_chain, scope_hash))

    sys.stdout.write(f"Task card: {card_id}\n")
    if already:
        sys.stdout.write(f"Already approved ({len(already)}): "
                         + ", ".join(f"{n!r}" for _, n in already) + "\n")
    if not pending:
        sys.stdout.write("Nothing to approve — every escalating block is "
                         "already signed for its current scope.\n")
        return 0

    sys.stdout.write(f"\nPending escalations across {len(pending)} block(s):\n")
    for block_id, name, scope_chain, _ in pending:
        sys.stdout.write(f"\n  Block {block_id!r} ({name!r}):\n")
        preview = _render_task_escalation(*scope_chain)
        sys.stdout.write("\n".join(f"  {line}" for line in preview.splitlines())
                         + "\n")

    if not assume_yes and not _confirm(
        f"\nSign ALL {len(pending)} escalation(s) above with the root "
        f"approval key? [y/N] "
    ):
        sys.stdout.write("Aborted; no approval records written.\n")
        return 1

    approved_by = os.environ.get("SUDO_USER") or os.environ.get("USER") or "unknown"
    signed = 0
    for block_id, name, _scope_chain, scope_hash in pending:
        approved_at = int(time.time())
        expires_at = _resolve_expires_at(approved_at, ttl_days)
        try:
            sig = sc.sign_approval_record(block_id, scope_hash, approved_by,
                                          approved_at, expires_at=expires_at)
        except PermissionError:
            sys.stderr.write(
                f"PermissionError reading the private key "
                f"({sc.private_key_path()}). Run via 'sudo ziya-approve' — "
                f"only root may sign.\n"
            )
            return 2
        except FileNotFoundError:
            sys.stderr.write(
                f"Private key not found at {sc.private_key_path()}. "
                f"Run 'sudo ziya-approve --provision' first.\n"
            )
            return 2
        except Exception as e:  # noqa: BLE001 — surface any key/sign failure clearly
            sys.stderr.write(f"Signing failed for block {block_id!r}: {e}\n")
            return 2

        record = {
            "task_id": block_id,
            "scope_hash": scope_hash,
            "approved_by": approved_by,
            "approved_at": approved_at,
            "signature": sig,
        }
        if expires_at is not None:
            record["expires_at"] = expires_at
        sa.save_record(record)
        sys.stdout.write(f"  ✓ Signed block {block_id!r} ({name!r}).\n")
        signed += 1

    sys.stdout.write(
        f"\n✓ Signed {signed} approval record(s) for card {card_id!r}.\n"
        f"  Each escalation takes effect on that block's next run.\n"
        f"  Editing any granted privilege voids its approval until re-approved.\n"
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
                      assume_yes: bool, ttl_days: Optional[float] = None) -> int:
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
    expires_at = _resolve_expires_at(approved_at, ttl_days)
    try:
        sig = sc.sign_approval_record(task_key, scope_hash, approved_by,
                                      approved_at, expires_at=expires_at)
    except PermissionError:
        sys.stderr.write(
            f"PermissionError reading the private key ({sc.private_key_path()}). "
            f"Run via 'sudo ziya-approve' — only root may sign.\n"
        )
        return 2
    except FileNotFoundError:
        sys.stderr.write(
            f"Private key not found at {sc.private_key_path()}. "
            f"Run 'sudo ziya-approve --provision' first.\n"
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
    if expires_at is not None:
        record["expires_at"] = expires_at
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
        "--provision", action="store_true",
        help="One-time root setup: generate the /etc/ziya approval keypair and "
             "the locked-down sudoers entry. Run as: sudo ziya-approve --provision.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="With --provision, regenerate the keypair (voids all existing "
             "approvals until re-signed).",
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
    parser.add_argument(
        "--ttl-days", type=float, default=None, dest="ttl_days",
        help="Approval lifetime in days. The signed record carries an "
             "expires_at; the runtime gate denies it once expired. If omitted, "
             "the enterprise policy bound (if any) is auto-applied; with no "
             "policy and no --ttl-days the approval is unbounded (OSS default). "
             "When both are present the more restrictive wins.",
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
    parser.add_argument(
        "--all", action="store_true", dest="all_blocks",
        help="With --task/--project (no --block), sign EVERY unapproved "
             "escalating block in the card with a single confirmation, "
             "instead of one invocation per block.",
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

    # Root provisioning — no key/config needed; must run before signing paths.
    if args.provision:
        return _provision(args.force)

    # Read-only audit mode — no signing, no key, no sudo required. Routed first
    # so it never touches the config/key paths the signing modes need.
    if args.list_audit:
        return _list_audit(args.root)

    # Route to CLI-task approval when --cli-task is supplied.
    if args.cli_task:
        return _approve_cli_task(args.cli_task, args.root, args.yes, args.ttl_days)

    # Route to task-scope approval when --task/--block/--all are supplied.
    if args.task or args.block or args.all_blocks:
        if not (args.task and args.project):
            sys.stderr.write(
                "--task mode requires --task and --project (plus --block, or "
                "--all to sign every unapproved block in the card).\n"
            )
            return 2
        if args.all_blocks:
            if args.block:
                sys.stderr.write("--all cannot be combined with --block.\n")
                return 2
            return _approve_all_task(args.project, args.task, args.yes,
                                     args.ttl_days)
        if not args.block:
            sys.stderr.write(
                "--task mode requires --block (or pass --all to sign every "
                "unapproved block in the card).\n"
            )
            return 2
        return _approve_task(args.project, args.task, args.block, args.yes,
                             args.ttl_days)

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
            f"Run 'sudo ziya-approve --provision' first.\n"
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
