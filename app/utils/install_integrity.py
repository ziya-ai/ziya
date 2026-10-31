"""
Detect a running server whose installation was removed underneath it.

Observed 2026-10-28: an upgrade installed a new version into a fresh venv and
pruned the old version's directory, which the still-running server had been
started from.  botocore opens the CA bundle
(certifi's ``cacert.pem``, inside that venv) for every new TLS connection,
so every Bedrock call then failed with::

    SSL validation failed for https://bedrock-runtime... [Errno 2] No such file or directory

That string matched the "SSL validation failed" connection-drop indicator,
so it was retried five times as a read timeout and reported to the browser
as a ``throttling_error``.  Nothing about it is transient; only a restart
fixes it.

IMPORTANT: this module must be imported at module level by its callers.
When the install directory is gone, a lazy ``from app.utils...`` inside an
error handler cannot load this file from disk, and the handler would raise
instead of classifying.  For the same reason the functions below import
nothing that is not already loaded.
"""
import os
import sys
from typing import Optional

_MISSING_FILE = "No such file or directory"
_TLS_MARKERS = ("SSL validation failed", "SSLError", "CA bundle", "ca_certs")

# Env vars that point botocore / requests / OpenSSL at a CA bundle.
_CA_ENV_VARS = ("AWS_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE")


def install_dir_missing() -> Optional[str]:
    """Directory the running ``app`` package was loaded from, if it no
    longer exists; else None."""
    mod = sys.modules.get("app")
    path = getattr(mod, "__file__", None) if mod is not None else None
    if not path:
        return None
    d = os.path.dirname(os.path.abspath(path))
    return None if os.path.isdir(d) else d


def ca_bundle_missing() -> Optional[str]:
    """First configured or default CA bundle path that does not exist."""
    candidates = [os.environ.get(v) for v in _CA_ENV_VARS]
    certifi = sys.modules.get("certifi")
    if certifi is not None:
        try:
            candidates.append(certifi.where())
        except Exception:  # noqa: BLE001 -- where() reads package resources
            pass
    for p in candidates:
        if p and not os.path.exists(p):
            return p
    return None


def is_missing_ca_file_error(error_str: str) -> bool:
    """A TLS failure caused by a certificate file that cannot be opened."""
    s = error_str or ""
    return _MISSING_FILE in s and any(m in s for m in _TLS_MARKERS)


def missing_install_message(error_str: str) -> Optional[str]:
    """User-facing explanation for a missing-certificate-file TLS failure,
    or None when ``error_str`` is not one."""
    if not is_missing_ca_file_error(error_str):
        return None
    gone = install_dir_missing()
    if gone:
        return (f"Ziya's installation directory ({gone}) was removed while this "
                f"server was running, most likely by an upgrade that "
                f"replaced the old version. The server can no longer read the files "
                f"it needs, including the TLS certificate bundle, so every model "
                f"request fails. Restart ziya to run the new version.")
    bundle = ca_bundle_missing()
    if bundle:
        return (f"The TLS certificate bundle {bundle} does not exist, so HTTPS "
                f"requests cannot be verified. If AWS_CA_BUNDLE or ZIYA_CA_BUNDLE "
                f"is set, check that path. If Ziya was upgraded while running, "
                f"restart it.")
    return ("TLS verification failed because a certificate file could not be "
            "found. If Ziya was upgraded while running, restart it; otherwise "
            "check AWS_CA_BUNDLE / ZIYA_CA_BUNDLE.")
