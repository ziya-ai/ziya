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

## Why you have to sign at all

Privilege widening is gated so that *nothing* — not the model, not a background
process, not the web UI — can quietly grant itself more shell access. Every
escalation, durable or ephemeral, has to be signed with your OS credentials.
The ephemeral tier makes that grant *temporary*; it does not make it *unsigned*.

For the security rationale, see `Docs/ThreatModel.md` and
`Docs/MCPSecurityControls.md` §9. For how the consent mechanism can be swapped
(e.g. Touch ID, enterprise SSO) see `Docs/AuthProviders.md`.
