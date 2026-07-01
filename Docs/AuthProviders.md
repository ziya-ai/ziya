# Consent Providers (Plugin Architecture)

Ziya's ephemeral shell-privilege grants are gated by a **consent provider**: the
thing that proves a present, authorized human approved a grant. The provider is
**pluggable** — the grant mechanics never change, only the trust anchor that
authorizes the grant does.

This document explains the seam so enterprises (and the open-source community)
can adapt the friction of ephemeral grants to their own environment without
weakening the security model.

## The core idea: a provider *is* a trust anchor

The shell subprocess honors an ephemeral session grant only if it is **signed by
a key the subprocess trusts**. The grant record carries a `provider` field, and
verification dispatches to that provider's anchor key. So "which consent
mechanism do you want?" is, cryptographically, "which key signs the grant?"

```
session grant  --->  provider field  --->  trust anchor (key)  --->  verify signature
                                                                       |
                                                  pass: honor delta    |  fail: clamp to floor
```

## Built-in / planned providers

| Provider | Trust anchor | What the human does | Available |
|---|---|---|---|
| `os-credential` (default) | root Ed25519 key | enters OS credentials (`sudo`) | everywhere — pip install, headless, remote |
| `biometric` | Secure-Enclave-backed key | Touch ID tap | requires Ziya packaged as a **signed `.app` bundle** (not pip) |
| `remote-reauth` | identity-provider key | SSO / re-authentication | cloud dev desktops, remote sessions |
| `bypass` | none | nothing | explicit, owned-risk only |

### `os-credential` (default)
Works in every deployment, including the common pip-installed case and headless
/ remote servers. The grant is **lighter artifact, same proof**: it auto-expires
and never touches your durable config, but it's still gated by one OS credential
prompt per grant. This is the honest floor of friction when no stronger anchor is
available.

### `biometric` (future)
A Touch ID tap instead of a password. Requires the OS to vouch for biometric
presence, which in practice needs Ziya to run inside a **signed application
bundle** with the appropriate entitlements — not the pip-installed CLI. Listed
here because the seam is built to accept it; it is not active in a pip install.

### `remote-reauth` (future)
For cloud dev desktops and remote sessions where there is no local biometric. The
present-human proof is a re-authentication against your identity provider.

### `bypass` (owned risk)
Honors the grant with no presence proof. This trades the gate for zero friction.
It is a legitimate choice for some environments — **but only as an explicit,
signed decision** (see below).

## The one rule that keeps this safe

**Which providers the subprocess accepts is itself durable, root-signed config.**

It has to be, because the accepted-provider set *is* the subprocess's
trust-anchor list. If an unauthenticated local caller could switch the provider
to `bypass`, they could escalate freely — the exact hole the gate exists to
close. So:

- **Provider selection** is durable-tier: it requires the root key, exactly like
  any other permanent privilege change.
- **Per-session grants** are ephemeral-tier: lightweight, auto-expiring.

You sign *which mechanism you trust* once (durably); after that, day-to-day
ephemeral grants use that mechanism.

## Configuring a provider (enterprise)

Provider selection is part of the signed shell scope. An enterprise that wants,
say, biometric consent on managed macOS fleets, or re-auth on remote desktops,
sets the accepted provider in the durable config and signs it with the root
approval key (`sudo ziya-approve`). From then on the ephemeral "Apply (this
session)" flow uses that mechanism. No code changes to the gate are required —
only a new provider verifier keyed off the `provider` field.

## See also

- `Docs/MCPSecurityControls.md` §9 — the gate and acceptance paths in depth
- `Docs/ThreatModel.md` — trust boundaries, residual risks
- `Docs/EphemeralShellPrivileges.md` — the end-user feature
- `Docs/ASR/shell-privilege-escalation-gate.md` — the security-review position
