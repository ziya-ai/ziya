"""
Credential preflight for unattended work, for every endpoint Ziya can run on.

A Task Card run, a scheduled fire or a parallel fan-out lane that starts
after the credential it needs has died does not fail fast: each lane runs
its first model call into the dead endpoint, the provider's retry ladder
spends minutes deciding it is hopeless, and the run ends with a record whose
only content is the error.  Run 5f83000a (2026-11-05) held three hours after
its credential expired.  A cheap check before the work starts turns that into
a HELD run that names the cause and can be resumed once the credential is
renewed.

What "cheap" means per endpoint:

``bedrock``
    the active ``AuthProvider``'s ``check_credentials`` (an STS round trip,
    seconds cold), so an enterprise auth plugin's notion of "valid" is the
    one consulted.  Slow enough that it runs off the event loop and is cached.
API-key endpoints (``anthropic``, ``openai``, ``zai``, ``meta``, …)
    the key is present in the environment (``provider_detection``'s registry
    of accepted variables).  Presence, not validity: a remote probe per lane
    would be a request to the very service being protected.  ``google`` also
    accepts application-default credentials.
``local``
    nothing to check: a local server has no credential.
anything else
    no check available; "cannot verify" is never "invalid".

Results are cached per endpoint for ``TTL_SECS`` so a 100-lane fan-out does
not make 100 STS calls, and ``invalidate`` is called when a lane observes an
authentication fault so the next lane re-checks instead of trusting a
minute-old OK.  ``ZIYA_SKIP_LAUNCH_PREFLIGHT=1`` disables every check (a
stubbed executor under test, a card doing only shell and file work, an
offline environment).  A failure of the check ITSELF never blocks.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

from app.utils.logging_utils import logger

TTL_SECS = 60.0
SKIP_ENV = "ZIYA_SKIP_LAUNCH_PREFLIGHT"
#: Endpoints whose model calls need no credential.
NO_CREDENTIAL_ENDPOINTS = frozenset({"local"})

_lock = threading.Lock()
_cache: Dict[str, Tuple[bool, str, float]] = {}


def skipped() -> bool:
    return os.environ.get(SKIP_ENV, "").strip().lower() in ("1", "true", "yes")


def current_endpoint() -> Optional[str]:
    """The endpoint the next model call will use, or None when no model has
    been initialised and nothing names one.

    Deliberately NOT defaulted to bedrock: a process with no model state (a
    test, a tool-only card) would otherwise run an STS probe on every lane
    and hold the run on a credential it was never going to use.  Unknown
    endpoint means "cannot verify", and that never blocks.
    """
    try:
        from app.agents.models import ModelManager
        ep = (ModelManager.get_state() or {}).get("endpoint")
        if ep:
            return str(ep)
    except Exception:  # noqa: BLE001 -- state may not be initialised (tests, CLI)
        pass
    return os.environ.get("ZIYA_ENDPOINT") or None


def invalidate(endpoint: Optional[str] = None) -> None:
    """Drop the cached verdict so the next check runs for real.  Called when
    a lane observes an authentication fault."""
    with _lock:
        if endpoint is None:
            _cache.clear()
        else:
            _cache.pop(endpoint, None)


def _google_adc_present() -> bool:
    p = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if p and Path(p).is_file():
        return True
    return (Path.home() / ".config" / "gcloud" / "application_default_credentials.json").is_file()


def _check_uncached(endpoint: str) -> Tuple[bool, str]:
    """The endpoint-specific check.  Blocking for bedrock; never raises."""
    if endpoint in NO_CREDENTIAL_ENDPOINTS:
        return True, "no credential required"
    if endpoint == "bedrock":
        from app.plugins import get_active_auth_provider
        provider = get_active_auth_provider()
        if provider is None:
            return True, "no auth provider; not verified"
        try:
            from app.agents.models import ModelManager
            state = ModelManager.get_state() or {}
        except Exception:  # noqa: BLE001
            state = {}
        ok, msg = provider.check_credentials(
            profile_name=state.get("aws_profile"), region=state.get("aws_region"))
        if ok:
            return True, msg or "credentials valid"
        # The provider's own guidance names the renewal step for THIS
        # environment (the community default says how to configure AWS
        # credentials; an enterprise plugin names its own login tool).
        help_text = provider.get_credential_help_message(msg) or msg
        return False, help_text or "credentials are not valid"
    from app.utils.provider_detection import credential_error, credential_keys_for, resolve_credential
    if not credential_keys_for(endpoint):
        return True, f"no credential check registered for endpoint {endpoint!r}"
    if resolve_credential(endpoint) is not None:
        return True, "credential present"
    if endpoint == "google" and _google_adc_present():
        return True, "application-default credentials present"
    return False, credential_error(endpoint)


def check_endpoint(endpoint: Optional[str] = None, *, ttl_s: float = TTL_SECS,
                   force: bool = False) -> Tuple[bool, str]:
    """``(ok, message)`` for ``endpoint`` (default: the current one).

    Blocking (bedrock does a network call); async callers use
    :func:`check_endpoint_async`.  ``ok`` is False only when the check ran
    and said so; a skipped, unavailable or crashed check is ``True``.
    """
    if skipped():
        return True, f"skipped ({SKIP_ENV})"
    ep = endpoint or current_endpoint()
    if not ep:
        return True, "no endpoint initialised; not verified"
    now = time.monotonic()
    if not force:
        with _lock:
            hit = _cache.get(ep)
        if hit is not None and now - hit[2] < ttl_s:
            return hit[0], hit[1]
    try:
        ok, msg = _check_uncached(ep)
    except Exception as e:  # noqa: BLE001 -- "cannot verify" is not "invalid"
        logger.debug(f"credential preflight for {ep!r} could not run: {e}")
        ok, msg = True, f"not verified ({type(e).__name__})"
    with _lock:
        _cache[ep] = (ok, msg, time.monotonic())
    if not ok:
        logger.warning(f"🔑 credential preflight: {ep!r} failed: {msg.splitlines()[0] if msg else ''}")
    return ok, msg


async def check_endpoint_async(endpoint: Optional[str] = None, *,
                               ttl_s: float = TTL_SECS, force: bool = False) -> Tuple[bool, str]:
    """``check_endpoint`` off the event loop (the bedrock path blocks)."""
    if skipped():
        return True, f"skipped ({SKIP_ENV})"
    ep = endpoint or current_endpoint()
    if not ep:
        return True, "no endpoint initialised; not verified"
    if not force:
        with _lock:
            hit = _cache.get(ep)
        if hit is not None and time.monotonic() - hit[2] < ttl_s:
            return hit[0], hit[1]
    return await asyncio.to_thread(check_endpoint, ep, ttl_s=ttl_s, force=force)


def reset_for_tests() -> None:
    invalidate()
