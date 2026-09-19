# Upstream PR: register Ziya in `vercel-labs/skills` (`npx skills`)

Status: **ready to submit** (not yet opened). Ziya-side contract is pinned by
`tests/test_skill_installer_contract.py`.

The `skills` CLI has no discovery protocol — every agent is a hardcoded entry
in `src/agents.ts`. Registration is a small PR following their documented
process (`AGENTS.md` → "Adding a New Agent"):

1. Add the definition to `src/agents.ts` and the union member to `src/types.ts`.
2. `pnpm run -C scripts validate-agents.ts` (checks duplicate `displayName`).
3. `pnpm run -C scripts sync-agents.ts` (regenerates the README agent count,
   the Supported Agents table, the skill-discovery path list, and
   `package.json` keywords — do **not** hand-edit those regions).
4. `pnpm format`, `pnpm type-check`, `pnpm test`.

## Design choices

| Field | Value | Why |
|---|---|---|
| `name` | `ziya` | `--agent ziya`; matches the pip package and CLI binary |
| `displayName` | `Ziya` | validator requires case-insensitive uniqueness — no clash |
| `skillsDir` | `.agents/skills` | Ziya already scans `.agents/skills` (`app/services/skill_discovery.py` `DISCOVERY_PATHS`). Using the universal dir means `npx skills add X` with no `--agent` flag lands where Ziya reads, and a project shared with Cursor/Codex/Copilot installs each skill once. `.ziya/skills` stays Ziya's own higher-precedence root for hand-authored skills. |
| `globalSkillsDir` | `$ZIYA_HOME/skills`, default `~/.ziya/skills` | Ziya's user-global root (`discover_user_skills`, via `get_ziya_home()`) |
| `detectInstalled` | `existsSync($ZIYA_HOME)` | `~/.ziya` is created on first run (`app/utils/paths.py`), same semantics as `~/.claude` for Claude Code |

`ZIYA_HOME` follows the existing pattern for `CODEX_HOME`, `CLAUDE_CONFIG_DIR`,
`VIBE_HOME`, etc.

Ziya follows symlinked skill directories in both scopes (the CLI's default
install method), and rejects a link whose name differs from the SKILL.md
`name` — the CLI always names the link after the skill, so this only affects
hand-made links.

## `src/types.ts`

Insert after `| 'zenflow'`:

```ts
  | 'ziya'
```

## `src/agents.ts`

Add with the other `*Home` constants near the top (after `sarvamHome`):

```ts
const ziyaHome = process.env.ZIYA_HOME?.trim() || join(home, '.ziya');
```

Insert after the `zenflow` entry:

```ts
  ziya: {
    name: 'ziya',
    displayName: 'Ziya',
    // Ziya reads .agents/skills natively (alongside its own .ziya/skills),
    // so it shares the universal project location rather than duplicating.
    skillsDir: '.agents/skills',
    globalSkillsDir: join(ziyaHome, 'skills'),
    detectInstalled: async () => {
      return existsSync(ziyaHome);
    },
  },
```

## `README.md` — Related Links

Hand-edit (this list is not generated). Insert before the Vercel entry:

```md
- [Ziya Skills Documentation](https://github.com/ziya-ai/ziya/blob/main/Docs/Skills.md)
```

Everything else in `README.md` (agent count, Supported Agents table,
Skill Discovery list) and `package.json` `keywords` is produced by
`sync-agents.ts`. Expected new table row:

```
| Ziya | `ziya` | `.agents/skills/` | `~/.ziya/skills/` |
```

## PR description (paste)

> **Add Ziya agent**
>
> [Ziya](https://github.com/ziya-ai/ziya) is a self-hosted, bring-your-own-model
> AI harness (browser + CLI) with native agentskills.io `SKILL.md` support.
>
> - Project skills: `.agents/skills/` (Ziya scans this directory natively, so
>   it joins the universal group — no symlink needed for project installs)
> - Global skills: `$ZIYA_HOME/skills`, default `~/.ziya/skills`
> - Detection: `~/.ziya` (or `$ZIYA_HOME`), created on first launch
>
> Ziya follows symlinked skill directories in both scopes, so the default
> symlink install method works. Ziya docs: `Docs/Skills.md`.
>
> Ran `validate-agents.ts`, `sync-agents.ts`, `pnpm format`, `pnpm type-check`,
> `pnpm test`.

## Verification after merge

```bash
mkdir -p ~/.ziya                      # or run ziya once
npx skills@latest add vercel-labs/agent-skills --list
npx skills@latest add vercel-labs/agent-skills --skill web-design-guidelines -a ziya -y
ls -la .agents/skills/                # project scope
npx skills@latest add vercel-labs/agent-skills --skill web-design-guidelines -a ziya -g -y
ls -la ~/.ziya/skills/                # global scope
```

Then in Ziya, the skill appears under **Contexts → Skills** with the
`project` / `user` badge respectively.

## Not in this PR (tracked separately)

- `src/detect-agent.ts` maps `@vercel/detect-agent` names to `AgentType` for
  "am I running *inside* an agent" prompts. Ziya would need to be added to
  `@vercel/detect-agent` first (it keys on env vars the agent sets in its
  shell). Ziya's shell tool would need to export a marker such as
  `ZIYA_AGENT=1` for that to be meaningful.
- Ziya does not currently scan `~/.agents/skills` (the universal *global*
  dir used by Cline/Zed/Warp/Kimi). Not required for this PR — the CLI
  symlinks into `~/.ziya/skills` — but adding it to `USER_HARNESS_PATHS`
  would make skills installed globally for those agents visible too.
