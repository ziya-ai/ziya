"""Explain a Bedrock data-retention rejection.

A call to a Covered Model fails with "data retention mode 'X' is not
available for this model" when the account-level retention switch is below
what the model requires.  The switch is per account AND per region; the one
that applies is the region the call was SENT to (the client's region).
CloudTrail records the rejected call as a ValidationException in that
region only, with no ``inferenceRegion`` -- searching the session's usual
region finds nothing.  Observed 2026-10-07: ``global.anthropic.claude-fable-5-1``
was rejected in eu-west-1 (switch never set) because a region-router
failover client had moved the server process's AWS_REGION there.  For an
inference profile the other regions it can route to are checked as well.

``diagnose_retention_gate`` answers that after the fact: which account the
call ran in, which regions the profile can route to, and the switch in each.
Everything here is read-only and best-effort; any failure (no
``bedrock:GetInferenceProfile`` / ``GetAccountDataRetention`` permission,
expired credentials) is reported in the result instead of raised.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger

# Cross-region inference profile prefixes.  A bare model id has none.
_PROFILE_PREFIXES = ("us.", "eu.", "apac.", "jp.", "au.", "ca.", "us-gov.", "global.")
ANY_REGION = "*"           # a profile entry with no region (global. profiles)
CACHE_TTL_S = 300.0

_cache: Dict[Tuple[str, str, str], Tuple[float, Dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def is_inference_profile(model_id: str) -> bool:
    return bool(model_id) and model_id.startswith(_PROFILE_PREFIXES)


def profile_regions(bedrock_client, model_id: str) -> List[str]:
    """Regions the inference profile can route to; ANY_REGION for an unbounded entry."""
    prof = bedrock_client.get_inference_profile(inferenceProfileIdentifier=model_id)
    out: List[str] = []
    for m in prof.get("models") or []:
        parts = str(m.get("modelArn", "")).split(":")
        region = parts[3] if len(parts) > 3 else ""
        region = region or ANY_REGION
        if region not in out:
            out.append(region)
    return out


def read_retention_mode(creds, region: str) -> Tuple[Optional[str], Optional[str]]:
    """(mode, error) for the classic account switch in *region*."""
    import requests
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    url = f"https://bedrock.{region}.amazonaws.com/data-retention"
    req = AWSRequest(method="GET", url=url, headers={})
    SigV4Auth(creds, "bedrock", region).add_auth(req)
    try:
        resp = requests.get(url, headers=dict(req.headers), timeout=8)
    except Exception as e:  # noqa: BLE001 -- diagnostic only
        return None, f"{type(e).__name__}"
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"
    return resp.json().get("mode", "inherit"), None


def diagnose_retention_gate(model_id: str, session_region: str, profile: Optional[str],
                            required_mode: str) -> Dict[str, Any]:
    """Read-only facts about the regions a rejected call may have run in."""
    key = (profile or "", model_id, session_region)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_S:
            return hit[1]

    from app.utils.aws_utils import create_fresh_boto3_session, retention_mode_satisfies
    diag: Dict[str, Any] = {"model_id": model_id, "session_region": session_region,
                            "profile": profile, "required": required_mode,
                            "account": None, "routes": None, "modes": {},
                            "unsatisfied": [], "errors": []}
    try:
        session = create_fresh_boto3_session(profile_name=profile)
        creds = session.get_credentials().get_frozen_credentials()
    except Exception as e:  # noqa: BLE001
        diag["errors"].append(f"credentials: {e}")
        return diag
    try:
        diag["account"] = session.client("sts", region_name=session_region) \
            .get_caller_identity().get("Account")
    except Exception as e:  # noqa: BLE001
        diag["errors"].append(f"sts: {type(e).__name__}")

    regions = [session_region]
    if is_inference_profile(model_id):
        try:
            routes = profile_regions(session.client("bedrock", region_name=session_region), model_id)
            diag["routes"] = routes
            regions = [r for r in routes if r != ANY_REGION] or regions
            if session_region not in regions:
                regions.insert(0, session_region)
        except Exception as e:  # noqa: BLE001
            diag["errors"].append(f"get_inference_profile: {str(e)[:120]}")

    for region in regions:
        mode, err = read_retention_mode(creds, region)
        diag["modes"][region] = mode if mode is not None else f"? ({err})"
        if mode is not None and not retention_mode_satisfies(mode, required_mode):
            diag["unsatisfied"].append(region)

    with _cache_lock:
        _cache[key] = (now, diag)
    return diag


def format_diagnosis(diag: Dict[str, Any]) -> str:
    """One log line."""
    routes = diag.get("routes")
    route_txt = ("n/a (not an inference profile)" if routes is None else
                 ",".join("ANY-COMMERCIAL-REGION" if r == ANY_REGION else r for r in routes))
    modes = " ".join(f"{r}={m}" for r, m in (diag.get("modes") or {}).items())
    parts = [f"model={diag.get('model_id')}", f"account={diag.get('account') or '?'}",
             f"profile={diag.get('profile') or 'default'}",
             f"session_region={diag.get('session_region')}",
             f"required>={diag.get('required')}", f"routes=[{route_txt}]",
             f"modes[{modes}]", f"below_required={diag.get('unsatisfied') or '-'}"]
    if diag.get("errors"):
        parts.append(f"errors={diag['errors']}")
    return " ".join(parts)


def user_summary(diag: Dict[str, Any]) -> str:
    """Short sentence appended to the error the user sees."""
    routes = diag.get("routes") or []
    bits = []
    if ANY_REGION in routes:
        bits.append(f"{diag.get('model_id')} is a global inference profile and can run in "
                    f"any commercial region, not only {diag.get('session_region')}")
    elif routes:
        bits.append(f"{diag.get('model_id')} can run in {', '.join(routes)}")
    if diag.get("unsatisfied"):
        bits.append(f"regions below '{diag.get('required')}' in account "
                    f"{diag.get('account') or '?'}: {', '.join(diag['unsatisfied'])}")
    elif diag.get("modes") and ANY_REGION in routes:
        bits.append("every listed region is set, so the call likely ran in an unlisted one")
    return ("Diagnosis: " + "; ".join(bits) + ".") if bits else ""


def log_retention_diagnosis(model_id: str, session_region: str, profile: Optional[str],
                            required_mode: str) -> Dict[str, Any]:
    diag = diagnose_retention_gate(model_id, session_region, profile, required_mode)
    logger.warning(f"🔐 RETENTION_DIAG {format_diagnosis(diag)}")
    return diag
