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
from typing import Dict, List, Optional

from app.utils.logging_utils import logger

# Endpoint → the environment variable(s) that indicate the user has creds.
# Order here is the fixed precedence used when reporting; auto-select only
# fires when exactly ONE non-bedrock provider is available, so precedence
# does not silently pick among several.
_PROVIDER_ENV = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
    "google": ("GOOGLE_API_KEY",),
}


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
    for endpoint, env_vars in _PROVIDER_ENV.items():
        result[endpoint] = any(os.environ.get(v) for v in env_vars)
    return result


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

    detected = [ep for ep, ok in available.items() if ok and ep != "bedrock"]
    if len(detected) == 1:
        return detected[0]
    return None


def announce_autoselect(chosen: str) -> None:
    """Tell the user (once) that we picked a provider for them.

    setup_environment() runs once per process, so this fires a single time
    per invocation — satisfying the "say what it's doing, but just the first
    time" requirement without a persistent flag.
    """
    var = _PROVIDER_ENV.get(chosen, ("",))[0]
    logger.info(
        f"No AWS credentials found; auto-selected the '{chosen}' endpoint "
        f"because {var} is set. Use --endpoint to choose a different provider."
    )


def build_setup_help(include_profile_hint: bool = True) -> str:
    """Return the comprehensive first-run credential setup message.

    Lists all four providers, the env var each needs, and — for AWS —
    the ``--profile`` shortcut plus any profiles already configured.
    """
    lines = [
        "⚠️  No AI provider credentials found.",
        "",
        "Ziya needs credentials for at least one of these providers. Set the matching",
        "environment variable (or use --endpoint to pick the provider):",
        "",
        "  • Anthropic : export ANTHROPIC_API_KEY=sk-ant-...   (--endpoint anthropic)",
        "  • OpenAI    : export OPENAI_API_KEY=sk-...          (--endpoint openai)",
        "  • Google    : export GOOGLE_API_KEY=...             (--endpoint google)",
        "  • AWS Bedrock (default):",
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
