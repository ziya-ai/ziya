# Ephemeral Shell Privileges (Feature Summary)

Ziya's shell tool runs at a safe **default floor**: a baseline set of commands,
interpreters, write paths, and git operations. Sometimes you need more — say you
want to run `perl` for one debugging session — without permanently widening what
the agent can do.

Ziya gives you **two ways to grant extra shell privilege**, and lets you pick the
right lifetime for the situation.

## The two tiers

| | **Durable** | **This session only** |
|---|---|---|
| Button | **Save** | **Apply (this session)** |
| Sign with | `sudo ziya-approve` | `sudo ziya-approve --session` |
| Then | Restart shell server | **Apply now** |
| Lives | until you change the config | until the next server restart |
| Written to config file | yes | **no** |

Both require you to deliberately sign the change with your OS credentials. The
difference is *how long it lasts* and *whether it's written to disk*.

## How "this session only" works

1. In the shell-config panel, add what you need (e.g. add `perl` to interpreters)
   and click **Apply (this session)**. This stages your request — nothing is
   written to your durable config.
2. A banner appears asking you to run `sudo ziya-approve --session` in a terminal.
   This signs the request for the *current* server session only.
3. Click **Apply now**. The shell restarts with your grant active.
4. The grant disappears automatically the next time you restart Ziya. There is
   nothing left in your config file, and nothing to clean up.

Changed your mind before signing? Click **Discard**. Decided you want it
permanently after all? Use **Save** instead — that supersedes the session
request.

## The interactive CLI (`ziya chat`): `/shell add`

The `ziya chat` CLI has a **third**, signing-free path for session-only grants,
because it has no sudo-unreadable key to sign with and a ceremony on every
`/shell add` would be intolerable. When you type `/shell add <command>` at the
CLI prompt, the change takes effect immediately — no `ziya-approve`, no banner.

This is safe for the CLI specifically because the trust anchor is different:

- The grant is signed by a **per-process ephemeral key** that lives only in the
  CLI's memory (never disk, never env), so the agent cannot read it to forge a
  grant. It is regenerated every start, so grants are void on the next launch.
- The mint call is reachable **only** from the TTY-stdin `/shell` handler, never
  from the model. So "a human typed it at this terminal" is the gate, replacing
  the signature.

The provider name for these grants is `cli-ephemeral`. See
`Docs/MCPSecurityControls.md` §9 for the full rationale and residual-risk
analysis (ASR F-004/F-007). This path is **CLI-only** — the web UI still
requires `sudo ziya-approve` as described above.

## Why you have to sign at all

Privilege widening is gated so that *nothing* — not the model, not a background
process, not the web UI — can quietly grant itself more shell access. In the
**web UI**, every escalation (durable or ephemeral) must be signed with your OS
credentials; the ephemeral tier makes that grant *temporary*, not *unsigned*.
In the **interactive CLI**, the equivalent anchor is process-isolation secrecy
plus the human-only mint path (see `/shell add` above) rather than a signature —
the model still cannot grant itself access, but a human at the TTY does not sign.

For the security rationale, see `Docs/ThreatModel.md` and
`Docs/MCPSecurityControls.md` §9. For how the consent mechanism can be swapped
(e.g. Touch ID, enterprise SSO) see `Docs/AuthProviders.md`.
