"""
Credential auto-detection and first-run setup guidance.

A brand-new public (Community Edition) user who runs ``ziya`` for the first
time with no configuration lands on the Bedrock endpoint (the default) and,
if they have no AWS credentials, previously got a bare "set up your AWS
credentials" message that (a) didn't explain how, (b) ignored the other
providers Ziya supports, and (c) repeated on every turn.

This module centralises three things:

* :func:`detect_available_providers` — which endpoints have usable creds.
* :func:`maybe_autoselect_endpoint` — if the user didn't pick an endpoint and
  the default (bedrock) has no creds but exactly one other provider does,
  switch to it and announce that once.
* :func:`build_setup_help` — a single comprehensive message listing every
  provider, the env var it needs, and the ``--profile`` hint for AWS.
"""
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.utils.logging_utils import logger


@dataclass(frozen=True)
class ProviderCredential:
    """How to tell whether one endpoint has usable credentials.

    The single source of truth. Before this existed the same env var names
    were restated in _check_openai_credentials / _check_zai_credentials /
    _check_meta_credentials, in providers/factory.py, in services/
    model_resolver.py, and in prose in build_setup_help() and the README —
    six places, already drifted (the README documents ZAI_API_TOKEN while
    every reader looks up ZAI_API_KEY, so a user following the README got
    "credentials not found").

    Fields:
        keys:     env vars any one of which constitutes a credential. The
                  FIRST is canonical (what setup help tells users to set);
                  the rest are accepted aliases.
        label:    human name for setup messages.
        example:  value shape shown in setup help.
        note:     optional extra guidance line.
        autoselectable:
                  whether first-run auto-select may silently switch to this
                  provider. False for meta, whose contributor tier trains on
                  submitted prompts — never chosen implicitly (see README).
    """
    endpoint: str
    keys: Tuple[str, ...]
    label: str
    example: str = "..."
    note: Optional[str] = None
    autoselectable: bool = True

    @property
    def canonical_key(self) -> str:
        return self.keys[0]


# Every endpoint in MODEL_CONFIGS must appear here; tests/ asserts that.
# bedrock is declared separately below because its credential check is
# file/profile-based rather than a single env var.
PROVIDER_CREDENTIALS: Tuple[ProviderCredential, ...] = (
    ProviderCredential(
        "anthropic", ("ANTHROPIC_API_KEY",), "Anthropic", "sk-ant-...",
    ),
    ProviderCredential(
        "openai", ("OPENAI_API_KEY", "OPENAI_BASE_URL"), "OpenAI", "sk-...",
        note="Or set OPENAI_BASE_URL for a compatible local server.",
    ),
    ProviderCredential(
        "google", ("GOOGLE_API_KEY",), "Google", "...",
        note="Or run: gcloud auth application-default login",
    ),
    ProviderCredential(
        # ZAI_API_KEY is canonical; ZHIPUAI_API_KEY is the upstream SDK name
        # and ZAI_API_TOKEN is accepted because the README documented it.
        "zai", ("ZAI_API_KEY", "ZHIPUAI_API_KEY", "ZAI_API_TOKEN"),
        "z.ai (GLM)", "...",
        note="Coding Plan: export ZAI_BASE_URL=https://api.z.ai/api/coding/paas/v4",
    ),
    ProviderCredential(
        # Meta's own docs call this MODEL_API_KEY, too generic to claim in a
        # multi-provider process, so META_API_KEY is canonical.
        "meta", ("META_API_KEY", "MODEL_API_KEY"), "Meta (Muse Spark)", "...",
        autoselectable=False,
    ),
)

_BY_ENDPOINT: Dict[str, ProviderCredential] = {
    p.endpoint: p for p in PROVIDER_CREDENTIALS
}

# Retained as a mapping view for the pre-registry call/test surface.
_PROVIDER_ENV: Dict[str, Tuple[str, ...]] = {
    p.endpoint: p.keys for p in PROVIDER_CREDENTIALS
}


def credential_keys_for(endpoint: str) -> Tuple[str, ...]:
    """Env vars that satisfy ``endpoint``; () for bedrock/unknown."""
    p = _BY_ENDPOINT.get(endpoint)
    return p.keys if p else ()


def resolve_credential(endpoint: str) -> Optional[str]:
    """Return the credential value for ``endpoint``, or None if unset.

    Tries the registered keys in declaration order (canonical first, then
    accepted aliases) and returns the first non-blank value. This is the
    accessor every credential READER should use, so the alias set lives in
    exactly one place.

    Before this existed, each reader spelled its own ``os.environ.get(A) or
    os.environ.get(B)`` chain -- ModelManager._check_*_credentials,
    providers/factory.py and services/model_resolver.py each had their own
    copy, and none of them accepted ZAI_API_TOKEN even though the README
    told users to set it.

    Returns None for bedrock (whose credentials are not a single env var)
    and for unknown endpoints.
    """
    for key in credential_keys_for(endpoint):
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()
    return None


def credential_error(endpoint: str) -> str:
    """Actionable "credentials not found" message for ``endpoint``.

    Generated from the registry so a provider's guidance cannot go stale or
    name a variable the code no longer reads.
    """
    p = _BY_ENDPOINT.get(endpoint)
    if not p:
        return f"No credential configuration registered for endpoint '{endpoint}'."
    lines = [
        f"{p.label} credentials not found. Please set {p.canonical_key}:",
        f"  export {p.canonical_key}={p.example}",
    ]
    if p.note:
        lines.append(p.note)
    if len(p.keys) > 1:
        lines.append(f"Also accepted: {', '.join(p.keys[1:])}")
    return "\n".join(lines)
def available_aws_profiles() -> List[str]:
    """Return configured AWS profile names, or [] if none / boto3 missing."""
    try:
        import botocore.session
        return list(botocore.session.Session().available_profiles)
    except Exception:
        return []


def _has_bedrock_credentials() -> bool:
    """Best-effort check for *any* AWS credential source (no network call)."""
    if (os.environ.get("AWS_ACCESS_KEY_ID")
            or os.environ.get("AWS_SECRET_ACCESS_KEY")
            or os.environ.get("AWS_SESSION_TOKEN")):
        return True
    if os.path.exists(os.path.expanduser("~/.aws/credentials")):
        return True
    if os.path.exists(os.path.expanduser("~/.aws/config")):
        return True
    # A named profile (incl. SSO) also counts as configured.
    return bool(available_aws_profiles())


def detect_available_providers() -> Dict[str, bool]:
    """Map every supported endpoint to whether it has usable credentials.

    This is a cheap, offline check (env vars + presence of AWS config files).
    It does not validate that the credentials actually work — that happens
    later in the endpoint-specific auth flow.
    """
    result = {"bedrock": _has_bedrock_credentials()}
    for p in PROVIDER_CREDENTIALS:
        result[p.endpoint] = any(
            (os.environ.get(v) or "").strip() for v in p.keys
        )
    return result


# Startup snapshot of detect_available_providers(), so the /api/endpoints
# route doesn't re-probe the filesystem (~/.aws/*, botocore profile
# enumeration) on every modal open. The probe is milliseconds, but it runs
# on the event loop and the modal polls, so it is taken once.
#
# Env vars can legitimately change after startup only via an in-process
# mutation, which we cannot observe; refresh_availability() exists so a
# caller that knowingly changes credentials can invalidate the snapshot.
_availability_cache: Optional[Dict[str, bool]] = None


def refresh_availability() -> Dict[str, bool]:
    """Re-probe credentials and replace the cached snapshot."""
    global _availability_cache
    _availability_cache = detect_available_providers()
    return dict(_availability_cache)


def get_availability(refresh: bool = False) -> Dict[str, bool]:
    """Cached credential availability per endpoint.

    Populated by refresh_availability() at startup; falls back to probing
    on first use so importers that never called it (tests, CLI one-shots)
    still get a correct answer.
    """
    if refresh or _availability_cache is None:
        return refresh_availability()
    return dict(_availability_cache)


def missing_credential_hint(endpoint: str) -> Optional[str]:
    """One-line "what to set" hint, or None when creds are present."""
    if get_availability().get(endpoint):
        return None
    if endpoint == "bedrock":
        return "Run 'aws configure' or set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY"
    p = _BY_ENDPOINT.get(endpoint)
    if not p:
        return None
    return f"Set {p.canonical_key}"


def maybe_autoselect_endpoint(
    endpoint: str, explicit_endpoint: bool
) -> Optional[str]:
    """Return an endpoint to switch to, or None to leave ``endpoint`` as-is.

    Only auto-selects when ALL of these hold:
      * the user did not pass ``--endpoint`` explicitly,
      * the current endpoint is the default (bedrock) and has no creds,
      * exactly one *other* provider has creds.

    Auto-selecting silently among multiple providers would be surprising, so
    that case is deliberately left to error out with the full setup menu.
    """
    if explicit_endpoint or endpoint != "bedrock":
        return None

    available = detect_available_providers()
    if available.get("bedrock"):
        return None  # Bedrock is usable; keep the default.

    # Only providers marked autoselectable are eligible. meta is excluded:
    # its contributor tier trains on submitted prompts and completions, and
    # Ziya sends source code, so it is opt-in by explicit name only.
    detected = [
        ep for ep, ok in available.items()
        if ok and ep != "bedrock" and _BY_ENDPOINT.get(ep, None) is not None
        and _BY_ENDPOINT[ep].autoselectable
    ]
    if len(detected) == 1:
        return detected[0]
    return None


def announce_autoselect(chosen: str) -> None:
    """Tell the user (once) that we picked a provider for them.

    setup_environment() runs once per process, so this fires a single time
    per invocation — satisfying the "say what it's doing, but just the first
    time" requirement without a persistent flag.
    """
def build_setup_help(include_profile_hint: bool = True) -> str:
    """Return the comprehensive first-run credential setup message.

    Lists every registered provider, the env var each needs, and — for AWS —
    the ``profile`` shortcut plus any profiles already configured.

    Generated from PROVIDER_CREDENTIALS rather than hand-written, so a new
    endpoint cannot be added without appearing here. The old hardcoded list
    had already gone stale: it omitted zai and meta entirely.
    """
    lines = [
        "⚠️  No AI provider credentials found.",
        "",
        "Ziya needs credentials for at least one of these providers. Set the matching",
        "environment variable (or use --endpoint to pick the provider):",
        "",
    ]
    width = max(len(p.label) for p in PROVIDER_CREDENTIALS)
    for p in PROVIDER_CREDENTIALS:
        lines.append(
            f"  • {p.label.ljust(width)} : export {p.canonical_key}={p.example}"
            f"   (--endpoint {p.endpoint})"
        )
        if p.note:
            lines.append(f"    {' ' * width}   {p.note}")
    lines += [
        f"  • {'AWS Bedrock'.ljust(width)} (default):",
        "        aws configure                       # set up credentials, or",
        "        export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...",
    ]

    if include_profile_hint:
        profiles = available_aws_profiles()
        if profiles:
            shown = ", ".join(profiles[:8])
            lines.append(
                f"        ziya --profile <name>               # use an existing "
                f"AWS profile ({shown})"
            )
        else:
            lines.append(
                "        ziya --profile <name>               # use a named AWS "
                "profile (aws sso login --profile <name>)"
            )

    lines.append("")
    lines.append(
        "Ziya will auto-select a provider if exactly one of the above is "
        "configured."
    )
    return "\n".join(lines)
