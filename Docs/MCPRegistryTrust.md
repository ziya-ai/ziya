# MCP Registry Trust Model

Ziya can browse MCP-server registries and install a server for you. This
document states plainly what that convenience does and does not mean, because
installing an MCP server is a decision to run third-party code inside your
session.

**A registry listing is not an endorsement, and it is not a security review.**
The registries Ziya ships with are community-published: anyone can publish to
them, and Ziya does not audit what they contain.

## The boundary in one sentence

Ziya controls **how** a registry-supplied server is installed, launched and
constrained. It does not vouch for **what that server does** once it is
running.

## What Ziya enforces

| Control | Where | What it stops |
|---|---|---|
| Executable allowlists on the install and run commands | `app/mcp/registry/command_policy.py` | A listing naming `/bin/sh`, or a relative path whose basename looks allowlisted, as the command persisted to your config and re-run on every start |
| Source-redirection filtering | same | `--index-url`, `--registry`, URL and absolute-path arguments that would repoint an installer at an attacker-chosen origin |
| Package-identifier shape check | same | A "package name" that is really a URL, a git spec, a PEP 508 direct reference, or a leading-dash flag |
| Install preview | registry UI | The exact argv, environment and config stanza an install would write, shown before you confirm |
| Environment filtering | `app/mcp/client.py`, provider env sanitisers | Registry-supplied `LD_PRELOAD`/`PYTHONPATH`-class variables being persisted; your AWS/Midway/API secrets being inherited by the server process |
| Tool-definition scanning | `app/mcp/tool_guard.py` | Prompt injection in tool descriptions, tools shadowing Ziya built-ins, and post-install changes to a server's tool set (rug-pull) |
| Result handling | `app/mcp/response_validator.py`, `app/mcp/signing.py` | Hidden-character smuggling in results; unsigned or tampered results reaching the model |
| Outbound destination check (remote servers) | `app/utils/net_guard.py` | A remote-server URL pointing at loopback, link-local/IMDS, or RFC-1918 space |
| Audit trail | `app/utils/tool_audit_log.py` | An install going unrecorded — `mcp_server_installed` captures the command that will run |

## What Ziya does not do

- **No security assessment of the server's code.** Ziya has not read it, does
  not scan it, and makes no claim about it.
- **No sandbox.** An installed MCP server is an ordinary subprocess running as
  your user, with your filesystem access. Ziya's shell allowlist and write
  policy constrain *Ziya's own* shell tool; they do not constrain a third-party
  server, which executes its own code directly.
- **No authenticity guarantee on the listing.** Transport is TLS, but Ziya
  cannot tell you that the entry was published by whoever the name suggests.
- **No behavioural monitoring.** Rug-pull detection compares tool *definitions*
  between connects. A server whose definitions are stable and whose behaviour
  is malicious will not trip it.
- **No egress control on the server itself.** A server you install can make
  its own network calls; Ziya's outbound checks cover Ziya's requests, not the
  server's.

## What remains your decision

1. **Whether to trust the publisher.** Prefer servers you can attribute to a
   known author or organisation.
2. **Reading the install preview before confirming.** It shows the concrete
   argv, the persisted command, the environment keys, and the config stanza.
   Anything surprising there — a URL where a package name belongs, an
   unfamiliar launcher — is worth stopping for.
3. **Which secrets the server can reach.** Ziya keeps its own secrets out of
   the subprocess environment, but a server you deliberately configure with a
   token has that token.
4. **Reviewing what the server can do with your project.** A filesystem- or
   git-capable server acts with your privileges.

## Enterprise and internal registries

An enterprise deployment can register additional registry providers through
the plugin interface (see `Docs/Enterprise.md`). Such a provider may apply its
own review or assessment process to the servers it lists — that claim belongs
to the provider and the organisation operating it, not to Ziya. Ziya's controls
above are unchanged either way: an enterprise listing goes through the same
command, identifier and environment validation as a community one, because the
registry response is treated as untrusted input in both cases.

## Where to look next

| Topic | Document |
|---|---|
| The registry command and identifier controls in depth | `Docs/MCPSecurityControls.md` §10 |
| All MCP threat mitigations | `Docs/MCPSecurityControls.md` |
| Trust boundaries and the public threat model | `Docs/ThreatModel.md` |
| Registering an enterprise registry provider | `Docs/Enterprise.md` |
