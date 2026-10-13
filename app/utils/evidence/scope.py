"""Ledger scope tokens (design/evidence-ledger.md, Write paths and Security).

A shell command run through the shell MCP server receives two environment
variables, ``ZIYA_LEDGER_URL`` and ``ZIYA_LEDGER_SCOPE``, so a script can
record observations and claims by POSTing to the running server.  The scope
token is the provenance the server will stamp on those records: the project
root (which ledger), and the conversation, run, block and attempt the
command was issued from.  The script never supplies provenance itself.

The token is ``base64url(payload JSON) . HMAC-SHA256(payload)`` under the
per-user ledger key, with an expiry.  A leaked token lets its holder append
records attributed to that block until it expires, and nothing else: it is
accepted only by the localhost ledger routes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

from .store import canonical_json, signing_key

ENV_URL = "ZIYA_LEDGER_URL"
ENV_SCOPE = "ZIYA_LEDGER_SCOPE"
HEADER = "X-Ziya-Ledger-Scope"

# A shell command may be a long analysis; a token expiring mid-run would
# turn a successful script into a 401 at its last step.
DEFAULT_TTL_S = 4 * 3600

_PROVENANCE_KEYS = ("conversation_id", "run_id", "block_id", "iteration", "source")


def issue(project_root: str, provenance: Optional[Dict[str, Any]] = None,
          ttl_s: float = DEFAULT_TTL_S, namespace: Optional[str] = None) -> str:
    """``namespace`` is the inherited default namespace for claims recorded
    with this token (a State block's ``evidence_namespace``)."""
    payload = {"project_root": str(project_root), "exp": time.time() + ttl_s}
    for k in _PROVENANCE_KEYS:
        v = (provenance or {}).get(k)
        if v not in (None, ""):
            payload[k] = v
    if namespace:
        payload["namespace"] = namespace
    body = canonical_json(payload)
    sig = hmac.new(signing_key(), body, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(body).decode().rstrip("=") + "." + sig


def verify(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """The token's payload if its signature verifies and it has not expired."""
    if not token or "." not in token:
        return None
    b64, _, sig = token.rpartition(".")
    try:
        body = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        payload = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or not payload.get("project_root"):
        return None
    want = hmac.new(signing_key(), canonical_json(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, want):
        return None
    if float(payload.get("exp", 0)) < time.time():
        return None
    return payload


def provenance_from_scope(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The provenance stamped on records written with this token."""
    out = {"via": "shell"}
    out.update({k: payload[k] for k in _PROVENANCE_KEYS if k in payload})
    return out


def build_envelope(project_root: Optional[str],
                   conversation_id: Optional[str] = None) -> Optional[Dict[str, str]]:
    """``{url, token}`` for the shell server to hand to child processes.

    Provenance comes from the server's own context: the conversation id the
    caller passed, and the task run/block ids from the usage-attribution
    contextvar the task executor sets.  Never raises: a failure here must
    not block a shell command, it just leaves the variables unset.
    """
    try:
        if not project_root:
            return None
        from app.config.env_registry import ziya_env
        try:
            port = int(ziya_env("ZIYA_PORT") or 6969)
        except (TypeError, ValueError):
            port = 6969
        from .service import default_namespace, ledger_root, task_context
        return {"url": f"http://127.0.0.1:{port}/api/ledger",
                "token": issue(ledger_root(project_root), task_context(conversation_id),
                               namespace=default_namespace())}
    except Exception:  # noqa: BLE001 -- see docstring
        return None
