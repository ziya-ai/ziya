#!/usr/bin/env bash
#
# provision_approve_key.sh — one-time, per-machine setup for Ziya's
# escalation-config integrity control (ASR F-004 / F-007, design doc §4.0 / §5).
#
# Provisions the root-owned Ed25519 approval keypair and the locked-down sudoers
# entry that gates `ziya-approve`. After this runs:
#
#   /etc/ziya/approve_ed25519        root:root 0600   (private — signer only)
#   /etc/ziya/approve_ed25519.pub    root:root 0644   (public  — verifier reads)
#   /etc/sudoers.d/ziya-approve      0440             (timestamp_timeout=0)
#
# The whole control rests on the private key being readable ONLY by root: the
# Ziya agent runs as the normal user and gets PermissionError, so it can never
# mint a signature for an escalation it wants. The public key is world-readable
# (public keys are public); the shell-server subprocess uses it to verify.
#
# Generalizes to macOS and Linux (all users have sudo per the deployment).
# Idempotent: re-running detects an existing key and leaves it in place unless
# --force is given.
#
# Usage:
#   sudo ./scripts/provision_approve_key.sh            # provision (no-op if present)
#   sudo ./scripts/provision_approve_key.sh --force    # regenerate the keypair
#
# NOTE: regenerating the keypair (--force) invalidates every existing signature,
# so all previously-approved escalations drop to the floor until re-approved.

set -euo pipefail

KEY_DIR="/etc/ziya"
PRIV="${KEY_DIR}/approve_ed25519"
PUB="${KEY_DIR}/approve_ed25519.pub"
SUDOERS="/etc/sudoers.d/ziya-approve"
FORCE=0

[ "${1:-}" = "--force" ] && FORCE=1

if [ "$(id -u)" -ne 0 ]; then
  echo "This script must run as root (use: sudo $0)." >&2
  exit 1
fi

# Resolve the real (non-root) user who will run ziya-approve via sudo, so the
# sudoers entry names them. Prefer SUDO_USER; fall back to the dir owner.
REAL_USER="${SUDO_USER:-}"
if [ -z "$REAL_USER" ]; then
  echo "Could not determine the invoking user (SUDO_USER unset)." >&2
  echo "Re-run with: sudo $0" >&2
  exit 1
fi

# Locate the ziya-approve entry point for the sudoers command path. Prefer an
# installed console script; fall back to the module invocation.
APPROVE_BIN="$(command -v ziya-approve 2>/dev/null || true)"

mkdir -p "$KEY_DIR"
chmod 0755 "$KEY_DIR"

if [ -f "$PRIV" ] && [ "$FORCE" -ne 1 ]; then
  echo "Keypair already present at $PRIV (use --force to regenerate). Leaving as-is."
else
  echo "Generating Ed25519 approval keypair at $PRIV ..."
  # -N "" : no passphrase (the file-permission boundary is the protection, not a
  #         passphrase the signer would have to be handed non-interactively).
  # -C    : comment for provenance.
  rm -f "$PRIV" "$PRIV.pub"
  ssh-keygen -t ed25519 -N "" -C "ziya-escalation-approval" -f "$PRIV" >/dev/null
  # ssh-keygen writes <file> and <file>.pub; normalize the public name.
  if [ -f "$PRIV.pub" ] && [ "$PRIV.pub" != "$PUB" ]; then
    mv -f "$PRIV.pub" "$PUB"
  fi
fi

# Root's primary group is "root" on Linux but "wheel" on macOS; resolve it
# rather than hardcoding (matches the sudoers-install handling below).
ROOT_GRP="$(id -gn root 2>/dev/null || echo wheel)"
chown "root:${ROOT_GRP}" "$PRIV" "$PUB"
chmod 0600 "$PRIV"
chmod 0644 "$PUB"
echo "  private: $PRIV  ($(stat -f '%Sp %Su' "$PRIV" 2>/dev/null || stat -c '%A %U' "$PRIV"))"
echo "  public:  $PUB  ($(stat -f '%Sp %Su' "$PUB" 2>/dev/null || stat -c '%A %U' "$PUB"))"

# --- sudoers entry: gate ziya-approve, force re-auth every time --------------
# timestamp_timeout=0 means no credential caching, so a subsequent agent-timed
# call cannot ride a still-valid sudo timestamp from a recent human approval.
# We do NOT use NOPASSWD: the password / Touch-ID prompt IS the human gate.
TMP_SUDOERS="$(mktemp)"
{
  echo "# Ziya escalation approval — re-auth every invocation (no cached timestamp)."
  echo "# Managed by scripts/provision_approve_key.sh. Do not edit by hand."
  echo "Defaults!ZIYA_APPROVE timestamp_timeout=0"
  if [ -n "$APPROVE_BIN" ]; then
    echo "Cmnd_Alias ZIYA_APPROVE = ${APPROVE_BIN}"
    echo "${REAL_USER} ALL=(root) ${APPROVE_BIN}"
  else
    # Console script not on PATH yet (e.g. editable/dev install) — gate the
    # module invocation instead. PYTHON path is resolved at run time by the user.
    PYBIN="$(command -v python3)"
    REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
    echo "Cmnd_Alias ZIYA_APPROVE = ${PYBIN} -m app.utils.ziya_approve, ${PYBIN} ${REPO_ROOT}/app/utils/ziya_approve.py"
    echo "${REAL_USER} ALL=(root) ${PYBIN} -m app.utils.ziya_approve, ${PYBIN} ${REPO_ROOT}/app/utils/ziya_approve.py"
  fi
} > "$TMP_SUDOERS"

# Validate before installing — a malformed sudoers file can lock out sudo.
if visudo -cf "$TMP_SUDOERS" >/dev/null 2>&1; then
  install -m 0440 -o root -g "$(id -gn root 2>/dev/null || echo wheel)" "$TMP_SUDOERS" "$SUDOERS" 2>/dev/null \
    || install -m 0440 "$TMP_SUDOERS" "$SUDOERS"
  chown root:wheel "$SUDOERS" 2>/dev/null || chown root:root "$SUDOERS" 2>/dev/null || true
  chmod 0440 "$SUDOERS"
  echo "  sudoers: $SUDOERS (validated, timestamp_timeout=0)"
else
  echo "ERROR: generated sudoers entry failed visudo validation; NOT installing." >&2
  rm -f "$TMP_SUDOERS"
  exit 1
fi
rm -f "$TMP_SUDOERS"

echo
echo "Provisioning complete. Approve escalations with:  sudo ziya-approve"
echo "The Ziya agent (normal user) cannot read $PRIV and cannot run sudo to effect."
