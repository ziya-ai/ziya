# Agent overlay: running a packaged agent in a conversation

Status: **design** (direction accepted 2026-11-13, unbuilt). Builds on
`design/capabilities-hub.md` (Bench, placements), `design/mcp-on-demand.md`
and `design/model-spawned-subthreads.md` (child conversations,
server-started turns).

## Goal

A package manager can install agent definitions: a system prompt, a model,
skills, MCP servers, a tool list, permission rules and, for a lead agent, a
set of specialist agents it may dispatch. A user can run one of these in a
conversation as the definition specifies, and stop at any point.

Priorities, in the order they were set:

1. Discover installed packages and make their skills and servers usable for
   anything (done: capability metadata providers, Bench "by package").
2. Run an agent as specified (this note).
3. Let the user change how it runs: models, enabled specialists (later; a
   "Configure" entry on the chip's detail panel).

## Principle: an agent is an overlay on a conversation

The conversation stays an ordinary conversation. One field on the chat
record names the active agent. While it is set, each turn is assembled with
the agent's contributions on top of what the conversation already has.
Turning the agent off clears the field; nothing else was written, so the
conversation is exactly as it was.

There is no agent mode for the application, no agent picker on screen and no
session an agent owns. Fork, mute, handoff and model switching keep working.

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | How the agent's skills and servers attach | **Overlay.** Not conversation-layer placements. The Bench shows them as one read-only "brought by the agent" row at the conversation lens; the user's own placements are untouched and the lens count does not change. |
| 2 | Model | Entering uses the agent's model **unless the conversation has a model pin**. The chip shows the model in use and, when a pin overrides the agent, offers the agent's model. Turning the agent off returns to the previous model. |
| 3 | Specialist dispatch | **Non-blocking.** The lead's reply ends at dispatch. Specialists run as child conversations; the block in the lead's reply updates live. The user can keep talking. When the last specialist returns, the server starts a lead turn with the reports. |
| 4 | Permission rules | **Deny rules are enforced before an agent can run.** They apply to the lead and every specialist and cannot be approved from inside the agent. |
| 5 | Run length | **No cap imposed by Ziya.** The lead dispatches and continues until it decides it is done. Only restrictions the agent definition carries as fields are enforced. Limits written in an agent's prompt ("one extra round", "three minutes") are instructions to the model, not rules Ziya enforces. |
| 6 | Turning the agent off while specialists are out | Specialists finish and their reports stay in the block. The automatic lead continuation is not started. |
| 7 | Start-up command | A definition may name a command to run when the agent starts. It **runs when the agent is turned on**, provided the import view shows it first and it can later be switched off per conversation. |
| 8 | Allow and deny lists | **Additive on Ziya's baseline** (2026-11-14). The baseline is Ziya's own shell and write policy: not "allow all" because a deny list exists, and not "allow nothing" because an allow list exists. Deny lists subtract (refused, not asked). Allow lists add: package scripts for the shell, listed directories for `file_write`. An allow list never removes something the baseline permits. MCP servers are not restricted by the definition's per-tool allow list; the tool list's server names are the only MCP limit. |

## What "on" and "off" mean

- **On:** `/agent <name>` in the composer, or "use in this chat" on an agent
  listed under its package in the Bench. Applies from the next turn.
- **Off:** the × on the chip, or `/agent off`. A turn in flight finishes
  under the agent; the chip reads "leaves after this reply" with an undo
  until it does.
- Switching conversations or closing the tab is neither. Specialists run on
  the server and the lead continues when they return.
- The transcript is kept in both directions. A thin seam marks where the
  agent started and ended.

## What the overlay contributes to a turn

| From the definition | Applied as |
|---|---|
| System prompt, context files | Added to the system prompt for the turn |
| Model | Model for the turn, unless the conversation is pinned (decision 2) |
| Skills | On hand for the turn, in addition to the conversation's own |
| MCP servers | Loaded for the turn. Servers not configured here are named when the agent is turned on; the user may continue without them |
| Tool list | A **filter** on the tool list sent to the model |
| Allow / deny rules (shell, file read, file write, MCP) | Enforced for every tool call in the turn, deny first |
| Subagents | The names the dispatch tool accepts |

### How the prompt block is framed (2026-11-16)

The agent's block is appended to the system prompt; the base prompt is not
removed or reordered. Decided: the agent **runs on top of Ziya and does not
supersede it**, so an agent's work can use diagrams, memory, the write policy
and every built-in tool. The block therefore opens with a frame
(`agent_overlay.agent_frame`) that says so, in this order:

1. The header `[Active Agent: <name>]`.
2. Ziya's instructions stay in force; the agent's role and procedures apply
   to the work it covers; asked who it is, the model answers as the agent
   running in Ziya. Ziya wins on safety, permissions and how files are
   changed; the agent wins on what the work is and how to do it.
3. Tool names in the agent's text were written for another client and may be
   listed here under a different name.
4. The definition's prompt, context files, the procedures it names
   (`[Agent procedure: <name>]`, below), a line naming procedures that were
   not found, a line naming servers it expects that are not configured here
   (`missing_servers`), then start-up output.

Without the frame the model had two unrelated role statements and answered
from the longer, earlier one (observed with `kuiper-chronicle`: asked what it
specialized in, it described a coding assistant).

### Procedures (Agent SOPs)

The public Agent SOP project (github.com/strands-agents/agent-sop) defines a
file format, `<name>.sop.md`, and three deliveries: as a system prompt, as an
MCP prompt, and converted to an Agent Skill. It defines no location on disk
(its tools take `--sop-paths`) and no way for an agent to declare the SOPs it
uses. The split follows from that:

- **Open core** (`app/utils/agent_sops.py`) reads `*.sop.md` from
  `ZIYA_SOP_PATHS`, `<project>/.ziya/sops`, `~/.ziya/sops` and roots a plugin
  registers (`register_sop_root_provider`); first found wins. Discovery lists
  each as a skill of kind `sop`, user-selectable, body verbatim.
  `AgentDefinition.sops` names the ones an agent uses; the overlay loads them
  in full every turn, preferring a copy under the agent's `install_root`.
- **A plugin** says where its packages keep SOPs and which ones an agent
  names. The AIM plugin registers each package's `agent-sops/` directory and
  maps `dependencies.agentSops.agentSopNames` to `sops`. Another client
  receives the same files through launch arguments of an MCP server
  (`--agent-sop-paths`), which Ziya does not pass.

Decided 2026-11-16: its own kind (a package may ship a skill and a SOP of one
name); always loaded while the agent is on (an agent's prompt says "follow
the SOP exactly", and on-demand loading depends on the model fetching it); no
guessed project location such as a bare `agent-sops/`.

Not done: the Bench, the catalog and `get_skill_details` are keyed by name,
so a SOP sharing a skill's name is not listed beside it. SOP Parameters are
not collected by the interface. Files beside a SOP (a package's `scripts/`)
get no read or shell grant.

Not done: a server the agent names under one id while an equivalent is
configured here under another (`kuiper-jira-mcp` named, `kuiper-jira-tool`
configured) is reported as missing, and the equivalent's tools are withheld
by the tool list. There is no aliasing.

Two existing paths are deliberately not reused:

- A skill's `toolIds` only reorders the tool list (`app/server.py`, the
  SKILL_TOOLS block). The overlay needs a real filter.
- A skill's `modelOverrides.model` is not applied; only temperature and
  max output tokens are. The overlay sets the model itself.

## Interface

Nothing is shown when no agent is active.

- **Chip** above the composer while active: `name · model · read-only ×`,
  plus `· N specialists out` while any are running. Click for a detail
  panel: model and where it came from, skills added, servers available and
  missing, whether tools are limited, specialists available, rule counts,
  and Leave / Switch.
- **`/agent` menu:** installed agents by package. Specialists are hidden
  behind their lead by default.
- **Missing servers:** one line on turn-on naming them, with Start anyway /
  Show in Bench / Cancel.
- **Specialists block:** one collapsed line in the lead's reply
  (`3 specialists · 2 done · 1 running · 0:58`, stop all). Expanded: one row
  per specialist with status, time, a one-line result, and open (the child
  conversation) or stop.
- **Continuation seam:** `specialists finished · agent continuing` before
  the lead's next reply.
- **Refused call:** the tool result says which rule refused it.

## Import view

How a definition was imported is inspectable before the agent is turned on
and while it is active: "inspect" on the agent's row in the Bench, and the
chip's detail panel. It lists, per part of the definition, what the source
said and what Ziya does with it:

- applied as written (prompt, context files, skills, rules, subagents);
- mapped (model name to a Ziya model, tool names to Ziya tools), with
  anything unmapped named;
- not available here (servers not configured, skills not on disk);
- not used, with the reason (for example another harness's auto-approve
  list);
- a start-up command, with the command line and its time limit.

A definition's start-up command runs when the agent is turned on, through
the normal shell policy, and its output is added to the agent's context as
data. The import view shows it before that happens.

Each row is to become a per-conversation switch (run the start-up command
or not, use a server or not, enable a specialist or not). The first version
is read-only; the switches arrive with step 4, and the view is built in
step 1 so the import is never opaque.

## Specialist dispatch

A tool available only while an agent with subagents is active. The lead
passes a list of `{agent, prompt}`. For each:

- a child conversation is created (`lineageKind="subthread"`, linked to the
  parent), running under that specialist's own overlay: its prompt, model,
  skills, tool filter and rules;
- the first turn is started by the server;
- its final reply is the report.

The tool returns at once with the run's id; the lead's reply ends. Reports
are attached to the parent as they arrive. A user message sent meanwhile is
answered by the lead with the reports received so far.

When the last specialist finishes (or is stopped), the server starts a lead
turn whose input is the full set of reports. The lead may dispatch again;
there is no limit on how many times (decision 5).

A specialist gets the dispatch tool only if its own definition lists
subagents.

## Where definitions come from

Open core defines the overlay and a provider seam for agent definitions,
alongside the capability metadata providers. An enterprise plugin reads its
package manager's format and returns definitions in Ziya's terms: resolved
prompt text and file paths, a model tier or name, skill names, server names,
tool names, rule lists, subagent names, and the package it belongs to.
Template tokens in a vendor format are resolved by the plugin.

Client-specific sections of a vendor format (for example a tool list written
for another harness) are mapped by the plugin; where a name has no Ziya
equivalent it is reported as unmapped and shown in the chip's detail panel.

## Build order

1. **Overlay and chip.** The chat-record field, the provider seam, prompt /
   context / skills / model applied per turn, `/agent`, the chip and its
   panel, the seams. No specialists, and no agent whose definition has
   rules can be turned on yet.
2. **Rules and tool filter.** Deny and allow enforcement for shell, file and
   MCP calls; the tool list filter. Agents with rules can now run.
3. **Dispatch.** Child conversations, the block, reports, the
   server-started continuation.
4. **Configure.** Per-conversation changes to models and enabled
   specialists.

## Tests (at the connection points)

- Turning the agent on then off leaves the chat record's placements, model
  pin and additional prompt byte-identical.
- The tool list sent to the model contains only the definition's tools; a
  tool outside it is absent, and one inside it is present.
- A denied shell command is refused for the lead and for a specialist, and
  an allowed one runs, in the same test.
- A pinned conversation keeps its model; an unpinned one takes the agent's
  and returns to the previous model when the agent is turned off.
- Dispatch: the lead's turn ends; a user message during the run gets a
  reply; the continuation starts once, after the last report.
- Turning the agent off with specialists out: reports arrive, no
  continuation starts.

## Open

- **Server-started turns.** `chat_turn_relay.start_turn` exists, but a new
  submission supersedes (cancels) a running turn. The continuation must
  queue behind a user turn in flight, not cancel it. Not yet traced.
- **Stopping child runs.** Whether "stop all" can cancel turns started by
  the server has not been checked.
- **Shell policy for packaged scripts.** Checked 2026-11-14: Ziya's shell
  policy refuses a package script run directly by absolute path, and allows
  the same script run as `python3 <path>`. The start-up command is run with
  a grant for exactly its own command line. Decided 2026-11-14: a
  definition's shell allow rules for scripts inside its own install
  directory become grants for `run_shell_command` in the conversation while
  the agent is on, counted in the import view. Allow rules for general
  commands are not granted, and `job_start` does not receive the grants.
- **Limit fields.** The definitions read so far carry no round, turn or time
  limit fields; one lead definition was read only in part.
- **Skills outside the package.** One known package references skills in a
  directory its own setup script creates. Discovery of those is not built.
