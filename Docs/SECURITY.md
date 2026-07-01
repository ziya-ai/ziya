# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Ziya, please report it responsibly.

**Do not open a public GitHub issue for security vulnerabilities.**

Email: **chroma@gmail.com** with the subject line `[SECURITY] Ziya vulnerability report`

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will acknowledge receipt within 48 hours and aim to provide a fix or mitigation plan within 7 days.

## Security Architecture

Ziya includes several security controls relevant to self-hosted deployments:

- **Shell command allowlisting** — only explicitly permitted commands can be executed
- **Shell privilege escalation gate** — widening the shell's default privilege floor (commands, interpreters, write paths) is honored only when backed by a root-signed artifact, verified at subprocess spawn; durable (`ziya-approve`) and ephemeral nonce-bound session grants (`ziya-approve --session`) are both signed, with a pluggable consent-provider trust anchor
- **MCP tool poisoning detection** — external tool descriptions are scanned for prompt injection patterns
- **MCP tool shadowing prevention** — external tools cannot override built-in tools
- **MCP rug-pull detection** — tool definitions are fingerprinted at connect time; changes on reconnect trigger warnings
- **Enterprise encryption-at-rest** — envelope encryption via the `EncryptionProvider` plugin interface
- **Data retention policies** — configurable TTLs per data category via the `DataRetentionProvider` plugin
- **Endpoint restriction** — enterprise plugins can restrict which model providers are available

See [Docs/MCPSecurityControls.md](Docs/MCPSecurityControls.md) for details on MCP security (including §9, the shell privilege escalation gate).
See [Docs/ThreatModel.md](Docs/ThreatModel.md) for the public threat model and trust boundaries.
See [Docs/AuthProviders.md](Docs/AuthProviders.md) for the pluggable consent-provider architecture.
See [Docs/Enterprise.md](Docs/Enterprise.md) for the full plugin system documentation.

## Supported Versions

Security fixes are applied to the latest release. We do not maintain older release branches.
