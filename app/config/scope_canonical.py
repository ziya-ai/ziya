"""
Canonical scope serialization + escalation-delta computation + Ed25519
signature verification for Ziya's escalation-config integrity control.

This module is the SINGLE shared definition imported by BOTH:
  - the verifier  (mcp_servers/shell_server.py, at subprocess init), and
  - the signer    (the root-invoked `ziya-approve` CLI).

The two MUST agree byte-for-byte on (a) which fields are privilege-bearing,
(b) what the default "floor" is, and (c) the canonical JSON encoding -- or
every signature fails to verify. Keeping it in one module IS the enforcement
of that agreement (design doc section 4.0 / section 8 Q4).

Security model (docs/ASR/task-authorization-design.md section 4.0):
  - Escalations beyond the floor take effect ONLY if accompanied by a valid
    Ed25519 signature over the *exact* escalation delta, made by the root-owned
    private key (/etc/ziya/approve_ed25519). Verification uses the
    world-readable public key (/etc/ziya/approve_ed25519.pub) -- the agent
    reading the public key is harmless.
  - Absent / invalid signature, or a delta that does not match the signed one
    -> escalations are dropped and policy falls back to the floor.
  - An EMPTY delta (config at or within the floor -- including narrowing) needs
    NO signature and is applied live. Only privilege *increments* require
    approval. This is what keeps the shell-config / write-policy GUIs live for
    everyday edits.
"""

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

# Public verification key. Overridable via env for tests / non-standard installs;
# the default sits beside the root-owned private key in the root-owned dir.
_DEFAULT_PUBLIC_KEY_PATH = "/etc/ziya/approve_ed25519.pub"
_DEFAULT_PRIVATE_KEY_PATH = "/etc/ziya/approve_ed25519"


def public_key_path() -> str:
    """Resolve the public-key path at call time (not import time).

    Reading ``ZIYA_APPROVE_PUBKEY`` here rather than binding it to a module
    constant at import lets the location be overridden after import (tests,
    non-standard installs) and avoids a frozen-at-import coupling bug.
    """
    return os.environ.get("ZIYA_APPROVE_PUBKEY", _DEFAULT_PUBLIC_KEY_PATH)


def private_key_path() -> str:
    """Resolve the root-owned private-key path at call time.

    Only the root-invoked signer (``ziya-approve``) ever reads this; the normal
    user / agent gets PermissionError on the ``root:root 0600`` file. Override
    via ``ZIYA_APPROVE_PRIVKEY`` for tests / non-standard installs.
    """
    return os.environ.get("ZIYA_APPROVE_PRIVKEY", _DEFAULT_PRIVATE_KEY_PATH)

# Env var that carries a pre-existing root-minted signature into the subprocess.
SIG_ENV_KEY = "ZIYA_SCOPE_SIG"

# Privilege-bearing list-valued env fields. Anything NOT named here is not gated
# (descriptions, timeouts, view prefs, etc. flow freely).
_LIST_FIELDS = (
    "ALLOW_COMMANDS",
    "SAFE_WRITE_PATHS",
    "ALLOWED_WRITE_PATTERNS",
    "ALLOWED_INTERPRETERS",
    "SAFE_GIT_OPERATIONS",
)
# ALWAYS_BLOCKED_COMMANDS is deliberately excluded: it is a hardcoded floor,
# additions only restrict, and removals are ignored downstream -- so it can never
# be an escalation. YOLO_MODE is handled separately (boolean).

# The complete set of env vars that carry privilege state across the
# parent -> shell-subprocess boundary, plus the signature that authorizes them.
# This is the SINGLE SOURCE OF TRUTH for "which env keys are escalation-bearing":
# the parent (mcp/manager.py) forwards exactly these with task-override
# precedence, and the verifier (shell_server) gates exactly these. Forwarding the
# signature (ZIYA_SCOPE_SIG) alongside the values it covers is mandatory -- a
# task that sets escalation values without its matching signature reaching the
# subprocess would verify against a stale/absent signature and be dropped to the
# floor even though it was legitimately approved.
ESCALATION_ENV_KEYS = _LIST_FIELDS + (
    "YOLO_MODE",
    SIG_ENV_KEY,
    # Ephemeral session-grant transport (design: runtime consent tier). The
    # nonce is minted per server-start by the manager and forwarded so the
    # subprocess can bind a grant to THIS server lifetime; the grant is the
    # base64 JSON session-grant record. Both are escalation-bearing in the
    # sense that they participate in the spawn-time gate, so they ride the
    # same forwarding path as ZIYA_SCOPE_SIG.
    "ZIYA_SESSION_NONCE",
    "ZIYA_SESSION_GRANT",
)

# Env var carrying the current server-start nonce, and the session-grant record.
SESSION_NONCE_ENV_KEY = "ZIYA_SESSION_NONCE"
SESSION_GRANT_ENV_KEY = "ZIYA_SESSION_GRANT"


def _floor() -> Dict[str, set]:
    """The default privilege floor, derived from the canonical config sources.

    Computed (not duplicated) so it cannot drift from the real defaults.
    ALLOW_COMMANDS floor includes the destructive commands and interpreters that
    shell_server unconditionally adds to its allowlist (they are gated by the
    write-policy path-checker, not by the command allowlist), so requesting them
    is not a command-allowlist escalation.

    The floor MUST be plugin-independent. The three parties to the signature
    gate compute it in different process contexts: the signer (ziya_approve)
    and the shell_server enforcer do not initialize plugins, while the web
    server (status API) does. Using the plugin-merged config here made the
    status API compute a different delta than the bytes that were actually
    signed, producing a false "unsigned escalation" banner even though the
    enforcer accepted the signature. Use the base config so all three agree.
    """
    from app.config.shell_config import get_base_shell_config
    from app.config.write_policy import DEFAULT_WRITE_POLICY

    cfg = get_base_shell_config()
    return {
        "ALLOW_COMMANDS": (
            set(cfg["allowedCommands"])
            | set(DEFAULT_WRITE_POLICY.get("destructive_commands", []))
            | set(DEFAULT_WRITE_POLICY.get("allowed_interpreters", []))
        ),
        "SAFE_WRITE_PATHS": set(DEFAULT_WRITE_POLICY.get("safe_write_paths", [])),
        "ALLOWED_WRITE_PATTERNS": set(DEFAULT_WRITE_POLICY.get("allowed_write_patterns", [])),
        "ALLOWED_INTERPRETERS": set(DEFAULT_WRITE_POLICY.get("allowed_interpreters", [])),
        "SAFE_GIT_OPERATIONS": set(cfg["safeGitOperations"]),
    }


def parse_env_scope(env: Dict[str, str]) -> Dict[str, Any]:
    """Extract the privilege-bearing values from a subprocess env mapping."""
    scope: Dict[str, Any] = {}
    for key in _LIST_FIELDS:
        raw = (env.get(key) or "").strip()
        if raw:
            scope[key] = [v.strip() for v in raw.split(",") if v.strip()]
    yolo = (env.get("YOLO_MODE") or "false").strip().lower()
    scope["YOLO_MODE"] = yolo in ("true", "1", "yes")
    return scope


def compute_delta(scope: Dict[str, Any]) -> Dict[str, Any]:
    """Return only the escalations beyond the floor.

    An empty dict means "no escalation" (apply live, no signature needed).
    List entries are sorted so the canonical form is order-independent.
    """
    floor = _floor()
    delta: Dict[str, Any] = {}
    for key in _LIST_FIELDS:
        requested = scope.get(key, [])
        extra = [v for v in requested if v not in floor[key]]
        if extra:
            delta[key] = sorted(set(extra))
    if scope.get("YOLO_MODE"):
        delta["YOLO_MODE"] = True
    return delta


def canonical(delta: Dict[str, Any]) -> bytes:
    """The exact bytes that get signed/verified. Both sides MUST use this."""
    return json.dumps(delta, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_public_key(path: Optional[str] = None):
    """Load the Ed25519 public verification key, tolerant of common formats.

    Returns None if the key is absent or unparseable -> caller fails closed.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import (
        load_pem_public_key,
        load_ssh_public_key,
    )

    path = path or public_key_path()
    p = Path(path)
    if not p.exists():
        return None
    data = p.read_bytes()

    for loader in (load_ssh_public_key, load_pem_public_key):
        try:
            key = loader(data)
            if isinstance(key, Ed25519PublicKey):
                return key
        except Exception:
            pass

    # Raw 32-byte key, optionally base64-wrapped.
    try:
        raw = base64.b64decode(data.strip(), validate=True)
        if len(raw) == 32:
            return Ed25519PublicKey.from_public_bytes(raw)
    except Exception:
        pass
    if len(data) == 32:
        return Ed25519PublicKey.from_public_bytes(data)
    return None


def verify_delta_signature(
    delta: Dict[str, Any],
    sig_b64: Optional[str],
    public_key_path: Optional[str] = None,
) -> bool:
    """True iff `sig_b64` is a valid root signature over `delta`.

    Empty delta -> True (nothing to authorize). Missing key/sig, or any
    verification failure -> False (fail closed).
    """
    if not delta:
        return True
    if not sig_b64:
        return False
    from cryptography.exceptions import InvalidSignature

    key = _load_public_key(public_key_path)
    if key is None:
        return False
    try:
        sig = base64.b64decode(sig_b64)
    except Exception:
        return False
    try:
        key.verify(sig, canonical(delta))
        return True
    except InvalidSignature:
        return False
    except Exception:
        return False


# ── CLI-task (tasks.yaml) escalation hashing ──────────────────────────────────
# CLI tasks (``ziya task <name>``) escalate via an ``allow`` block with a
# different shape than a card scope: ``commands`` / ``git_operations`` /
# ``write_patterns`` (see app/task_runner.py). They authorize through the same
# signed approval store and chokepoint as cards (design §6), but the privilege
# hash is projected over the ``allow`` fields rather than the card scope's
# shell_commands + writable paths. Same canonical() encoding, so the sign and
# verify halves cannot drift.

_CLI_ALLOW_FIELDS = ("commands", "git_operations", "write_patterns")


def cli_escalation_block(allow: Any) -> Dict[str, Any]:
    """Extract the privilege-bearing fields from a CLI task ``allow`` mapping.

    Returns {} when the allow block grants no escalation — the signal that no
    approval is required (the task runs at the floor regardless). Entries are
    de-duplicated and sorted so the canonical form is order-independent.
    """
    if not allow or not isinstance(allow, dict):
        return {}
    block: Dict[str, Any] = {}
    for field in _CLI_ALLOW_FIELDS:
        vals = sorted({
            str(x).strip() for x in (allow.get(field) or []) if str(x).strip()
        })
        if vals:
            block[field] = vals
    return block


def cli_task_hash(allow: Any) -> str:
    """SHA-256 over the canonical CLI escalation block. "" when no escalation."""
    block = cli_escalation_block(allow)
    if not block:
        return ""
    import hashlib
    return hashlib.sha256(canonical(block)).hexdigest()


def is_env_scope_authorized(
    env: Dict[str, str], public_key_path: Optional[str] = None
) -> bool:
    """Top-level verifier the shell subprocess calls at init.

    True  -> the env's escalations (if any) are approved; honor them as-is.
    False -> escalation present but unauthorized; caller must fall to floor.

    An escalation delta is honored if it is backed by EITHER:
      (1) a durable env-scope signature (ZIYA_SCOPE_SIG, root-minted by
          ``ziya-approve``) — permanent until the config changes; or
      (2) a valid session grant (ZIYA_SESSION_GRANT bound to the current
          ZIYA_SESSION_NONCE) — the ephemeral runtime-consent tier, alive only
          for this server start and re-applied to the subprocess via a shell
          restart. The default provider's trust anchor is the same root key,
          so an ephemeral grant is "lighter artifact, same proof"; alternate
          providers (biometric / remote re-auth) verify against their own
          anchor keyed off the grant's ``provider`` field.
    Either path failing closed -> the caller clamps to the floor.
    """
    scope = parse_env_scope(env)
    delta = compute_delta(scope)
    if not delta:
        return True
    if verify_delta_signature(delta, env.get(SIG_ENV_KEY, ""), public_key_path):
        return True
    return verify_session_grant(
        delta,
        env.get(SESSION_GRANT_ENV_KEY, ""),
        env.get(SESSION_NONCE_ENV_KEY, ""),
        public_key_path,
    )


def strip_escalations(env: Dict[str, str]) -> Dict[str, str]:
    """Return a copy of `env` with privilege-bearing fields clamped to the floor.

    Used when verification fails. Preserves *narrowing* (keeps requested entries
    that are within the floor) while dropping every entry beyond it; forces YOLO
    off and removes the signature var. Downstream merge logic then lands on the
    floor for the dropped escalations.
    """
    floor = _floor()
    scope = parse_env_scope(env)
    out = dict(env)
    for key in _LIST_FIELDS:
        if key in scope:
            kept = [v for v in scope[key] if v in floor[key]]
            out[key] = ",".join(kept)
    out["YOLO_MODE"] = "false"
    out.pop(SIG_ENV_KEY, None)
    return out


# ── Signing side (root-invoked ``ziya-approve`` only) ─────────────────────────
# These live here, beside verify_delta_signature, so the sign and verify halves
# share one canonical() definition and cannot drift. Co-location leaks nothing:
# security rests on the private-key file's root:root 0600 permissions, NOT on
# code secrecy — the agent can import this module but cannot read the key.

def load_private_key(path: Optional[str] = None):
    """Load the Ed25519 signing key. Raises on permission / format error.

    Accepts PKCS8/PEM (what the provisioning script writes) and OpenSSH private
    formats. Resolves the path at call time (private_key_path()) when not given.
    """
    from cryptography.hazmat.primitives.serialization import (
        load_pem_private_key,
        load_ssh_private_key,
    )
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    data = Path(path or private_key_path()).read_bytes()
    last_err: Optional[Exception] = None
    for loader in (load_pem_private_key, load_ssh_private_key):
        try:
            key = loader(data, password=None)
            if isinstance(key, Ed25519PrivateKey):
                return key
        except Exception as e:  # noqa: BLE001 — try next format
            last_err = e
    raise ValueError(f"not a usable Ed25519 private key: {last_err}")


def sign_delta(delta: Dict[str, Any], private_key=None) -> str:
    """Return the base64 Ed25519 signature over the canonical delta.

    The produced string is exactly what verify_delta_signature() checks and what
    travels to the subprocess as ZIYA_SCOPE_SIG. An empty delta is a no-op
    (returns "") — nothing to authorize, no signature needed.
    """
    if not delta:
        return ""
    key = private_key if private_key is not None else load_private_key()
    return base64.b64encode(key.sign(canonical(delta))).decode("ascii")


# ── Ephemeral session-grant primitives (runtime consent tier) ─────────────────
# A session grant authorizes an escalation delta for the lifetime of ONE server
# start only. It is the ephemeral sibling of the durable env-scope signature
# (verify_delta_signature): same canonical() encoding and same trust anchor
# (the root key, for the default `os-credential` provider), but bound to a
# per-server-start NONCE so it dies automatically on the next server restart —
# no on-disk lifecycle to expire. The `provider` field records which trust
# anchor minted it (future: biometric / remote-reauth verify against a
# different key; `bypass` is an explicit, root-config-gated owned risk).
#
# Binding rules enforced at verify time (all must hold):
#   1. record.nonce  == the current server-start nonce  (ephemerality)
#   2. record.scope_hash == sha256(canonical(delta))    (binds to exact delta)
#   3. signature verifies over the canonical record message (unforgeable)
# Any miss -> not authorized -> caller falls back to the floor (fail-closed).

_SESSION_GRANT_FIELDS = ("nonce", "scope_hash", "provider", "granted_by", "granted_at")


def session_grant_scope_hash(delta: Dict[str, Any]) -> str:
    """SHA-256 over the canonical escalation delta. "" when the delta is empty."""
    if not delta:
        return ""
    import hashlib
    return hashlib.sha256(canonical(delta)).hexdigest()


def _session_grant_message(nonce: str, scope_hash: str, provider: str,
                           granted_by: str, granted_at: int) -> bytes:
    """Canonical bytes signed/verified for a session-grant record."""
    return canonical({
        "nonce": nonce,
        "scope_hash": scope_hash,
        "provider": provider,
        "granted_by": granted_by,
        "granted_at": granted_at,
    })


def sign_session_grant(nonce: str, delta: Dict[str, Any], provider: str,
                       granted_by: str, granted_at: int,
                       private_key=None) -> Dict[str, Any]:
    """Mint a signed session-grant record for `delta`, bound to `nonce`.

    Returns the full record (including the base64 signature) ready to be
    serialized into ZIYA_SESSION_GRANT. Empty delta -> ValueError (nothing to
    grant; callers should never mint a grant for a non-escalation).
    Only the root-invoked signer holds the private key for the default
    `os-credential` provider.
    """
    if not delta:
        raise ValueError("refusing to mint a session grant for an empty delta")
    scope_hash = session_grant_scope_hash(delta)
    key = private_key if private_key is not None else load_private_key()
    msg = _session_grant_message(nonce, scope_hash, provider, granted_by, granted_at)
    sig = base64.b64encode(key.sign(msg)).decode("ascii")
    return {
        "nonce": nonce,
        "scope_hash": scope_hash,
        "delta": delta,
        "provider": provider,
        "granted_by": granted_by,
        "granted_at": granted_at,
        "signature": sig,
    }


def verify_session_grant(delta: Dict[str, Any], grant_json: Optional[str],
                         current_nonce: Optional[str],
                         public_key_path: Optional[str] = None) -> bool:
    """True iff `grant_json` is a valid session grant for `delta` this session.

    Empty delta -> True (nothing to authorize). Otherwise ALL must hold:
    parseable record, nonce matches `current_nonce`, scope_hash matches the
    delta, and the signature verifies against the trusted key. Any failure or
    a blank nonce -> False (fail-closed). The default provider's trust anchor
    is the root public key; provider-specific anchors are a future extension
    keyed off record['provider'].
    """
    if not delta:
        return True
    if not grant_json or not current_nonce:
        return False
    from cryptography.exceptions import InvalidSignature
    try:
        record = json.loads(grant_json)
        nonce = record["nonce"]
        scope_hash = record["scope_hash"]
        provider = record["provider"]
        granted_by = record["granted_by"]
        granted_at = int(record["granted_at"])
        sig_b64 = record["signature"]
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return False
    if nonce != current_nonce:
        return False
    if scope_hash != session_grant_scope_hash(delta):
        return False
    key = _load_public_key(public_key_path)
    if key is None:
        return False
    msg = _session_grant_message(nonce, scope_hash, provider, granted_by, granted_at)
    try:
        key.verify(base64.b64decode(sig_b64), msg)
        return True
    except InvalidSignature:
        return False
    except Exception:
        return False


# ── Task-scope authorization (ASR F-001, design doc §4.1/§4.2) ────────────────
# Card/CLI task escalations (shell_commands + writable paths) are authorized by
# a SEPARATE signed approval-record store keyed by (task_id, scope_hash), not by
# a field embedded on the card — so task-definition files stay plain and
# hand-editable, approvals are portable, the gate is disable-able by clearing the
# store, and there is one signed audit ledger of all authorized escalations.
# The privilege-bearing fields are shell_commands and writable paths ONLY;
# tools/skills are restrictions (allowlists that narrow capability, not
# escalations) and readable paths are advisory (F-006), so neither is hashed —
# editing them must not churn an approval.

def task_escalation_block(scope: Any) -> Dict[str, Any]:
    """Extract the privilege-bearing fields from a TaskScope-like object.

    Returns {} when the scope grants no escalation (no shell_commands, no
    writable paths) — an empty block means "no approval needed; runs at floor".
    Duck-typed (getattr) so it works on the pydantic TaskScope without importing
    the model here (keeps this module dependency-light and import-cycle-free).
    """
    if scope is None:
        return {}
    shell_cmds = sorted(set(getattr(scope, "shell_commands", []) or []))
    writable = sorted({
        getattr(e, "path", None)
        for e in (getattr(scope, "paths", []) or [])
        if getattr(e, "write", False) and getattr(e, "path", None)
    })
    block: Dict[str, Any] = {}
    if shell_cmds:
        block["shell_commands"] = shell_cmds
    if writable:
        block["writable_paths"] = writable
    return block


def task_scope_hash(scope: Any) -> str:
    """SHA-256 over the canonical task escalation block. "" when no escalation."""
    block = task_escalation_block(scope)
    if not block:
        return ""
    import hashlib
    return hashlib.sha256(canonical(block)).hexdigest()


def _approval_record_message(task_id: str, scope_hash: str,
                             approved_by: str, approved_at: int) -> bytes:
    """Canonical bytes signed/verified for an approval record."""
    return canonical({
        "task_id": task_id,
        "scope_hash": scope_hash,
        "approved_by": approved_by,
        "approved_at": approved_at,
    })


def sign_approval_record(task_id: str, scope_hash: str, approved_by: str,
                         approved_at: int, private_key=None) -> str:
    """Return the base64 Ed25519 signature over an approval record."""
    key = private_key if private_key is not None else load_private_key()
    msg = _approval_record_message(task_id, scope_hash, approved_by, approved_at)
    return base64.b64encode(key.sign(msg)).decode("ascii")


def verify_approval_record(record: Dict[str, Any],
                           public_key_path: Optional[str] = None) -> bool:
    """True iff record's signature is a valid root signature over its fields.

    Fail-closed: missing fields, missing key, or any verification error -> False.
    The caller must SEPARATELY check that record['scope_hash'] matches the
    task's CURRENT scope hash (a valid record for an old hash must not authorize
    a widened scope — that check lives in scope_approvals.is_scope_authorized).
    """
    from cryptography.exceptions import InvalidSignature
    try:
        sig_b64 = record["signature"]
        msg = _approval_record_message(
            record["task_id"], record["scope_hash"],
            record["approved_by"], int(record["approved_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return False
    key = _load_public_key(public_key_path)
    if key is None:
        return False
    try:
        key.verify(base64.b64decode(sig_b64), msg)
        return True
    except InvalidSignature:
        return False
    except Exception:
        return False
