## Amazon Slack Enterprise for GenAI

[Source Code](https://code.amazon.com/packages/Slack-MCP-Server/trees/mainline) | [#slack-mcp-server](https://amzn-community.slack.com/archives/C0AMB7YUTUZ) | [Joseph Bueno (buejosep@)](https://phonetool.amazon.com/users/buejosep) | [Adi Sridharan (aditsrid@)](https://phonetool.amazon.com/users/aditsrid)

---

A Slack MCP server for Amazon Enterprise Slack, giving AI assistants full access to messages, channels, search, reactions, files, canvases, lists, reminders, scheduling, drafts, DMs, Slackbot AI, channel management, status, unreads, and saved messages. 90 tools, 11 resources, 4 prompts with enriched results, thread context, and advanced search.

**MCS authentication, zero config.** The server authenticates via MCS (hardware-bound PoP) using your existing Midway session. Run `mwinit -s` and your AI assistant has secure access to your Slack workspace immediately.

**Or run it as a bot.** Set `SLACK_BOT_TOKEN_SECRET_ARN` and the server authenticates as a Slack bot instead: the `xoxb-` token lives in AWS Secrets Manager (never in config files), messages post under the bot's own name and avatar, and the tool surface automatically narrows to what the bot's token type and OAuth scopes can actually do. Built for headless agents and shared services. See the [Bot Mode Guide](https://code.amazon.com/packages/Slack-MCP-Server/blobs/mainline/--/docs/BOT_MODE_GUIDE.md) for step-by-step setup.

---

### Installation

**AIM CLI**

```
# 1. Install AIM CLI (one-time)
toolbox install aim

# 2. Install the MCP server
aim mcp install slack-mcp
```

If `aim mcp install` does not auto-configure your client, have Kiro add manually:

```json
{
  "mcpServers": {
    "slack-mcp": {
      "type": "stdio",
      "command": "slack-mcp",
      "args": [],
      "env": {}
    }
  }
}
```

**Optional: Enable file uploads** — File uploads are disabled by default. Configure allowed directories to enable:

```json
{
  "mcpServers": {
    "slack-mcp": {
      "type": "stdio",
      "command": "slack-mcp",
      "args": [],
      "env": {
        "UPLOAD_ALLOWED_DIRS": "/home/user/Downloads, /tmp/exports"
      }
    }
  }
}
```

> `UPLOAD_ALLOWED_DIRS` is **required** to enable the `upload_file` tool. Without it, uploads are blocked with a `SecurityError`. Sensitive files (credentials, keys, tokens, cookies) are always blocked regardless of directory. COMMA SEPERATED LIST FOR MULTIPLE DIRECTORY ALLOWLISTING

**Optional: Human-in-the-loop mode** — Block all write operations and require human review:

```json
{
  "mcpServers": {
    "slack-mcp": {
      "type": "stdio",
      "command": "slack-mcp",
      "args": [],
      "env": {
        "SAFE_MODE": "true"
      }
    }
  }
}
```

> When `SAFE_MODE` is `true`, all 46 write tools are blocked (channel management, list operations, reminders, scheduling, file uploads, message editing/deletion, sections, saved messages, status, canvas creation) and `post_message` is redirected to the draft queue for human review in the Slack compose box. Read-only tools and `post_draft` continue to work normally.

**Optional: Bot token mode** — Run the server as a Slack bot (headless agents, shared services) instead of as yourself:

```json
{
  "mcpServers": {
    "slack-mcp": {
      "type": "stdio",
      "command": "slack-mcp",
      "args": [],
      "env": {
        "SLACK_BOT_TOKEN_SECRET_ARN": "arn:aws:secretsmanager:us-west-2:<account>:secret:slack-mcp/bot-token-Ab12Cd",
        "AWS_PROFILE": "slack-mcp-bot",
        "SLACK_BOT_USERNAME": "My Agent",
        "SLACK_BOT_ICON_EMOJI": ":robot_face:"
      }
    }
  }
}
```

> The `xoxb-` bot token is stored in **AWS Secrets Manager** — never in config files or environment variables (a plaintext `SLACK_BOT_TOKEN` is rejected at startup). The server fetches it at startup via the AWS SDK default credential chain, verifies it with `auth.test`, and picks up rotated secrets automatically. The tool surface adapts in two layers: user-session-only tools (search, canvases, lists, reminders, ...) are excluded for bot tokens, and after startup the set narrows further to what the token's granted OAuth scopes permit (via `tools/list_changed`). Optional `SLACK_BOT_USERNAME` / `SLACK_BOT_ICON_URL` / `SLACK_BOT_ICON_EMOJI` set the per-message display identity (requires the `chat:write.customize` scope). Full setup guide, IAM least-privilege pattern, and the internal (OPUS) bot-token acquisition process: [Bot Mode Guide](https://code.amazon.com/packages/Slack-MCP-Server/blobs/mainline/--/docs/BOT_MODE_GUIDE.md).

**Optional: Rate limiting configuration** — Per-tool rate limiting is enabled by DEFAULT. This is a security safety guard to protect against rouge bots and we do not recommend modifying. However, you may override the limits via env vars for unique test cases:

| Variable               | Default | Description                                                   |
| ---------------------- | ------- | ------------------------------------------------------------- |
| `RATE_LIMIT_ENABLED`   | `true`  | Set to `false` to disable rate limiting                       |
| `RATE_LIMIT_READ`      | `120`   | Max calls/min for read tools                                  |
| `RATE_LIMIT_WRITE`     | `30`    | Max calls/min for write tools (`post_message` defaults to 10) |
| `RATE_LIMIT_EXPENSIVE` | `15`    | Max calls/min for expensive tools (search, etc.)              |

---

### Requirements

- **Node.js**: v20+
- **Slack**: Logged into [Amazon Enterprise Slack](https://amazon.enterprise.slack.com) (desktop or browser)
- **Auth**: Valid Midway credentials (`mwinit -s -o`)

> Amazon Enterprise Slack credentials (`SSB_INSTANCE_ID` and `ENTITY_ID`) are built in. If you do not use Amazon Enterprise Slack, set these as environment variables in the above config to override the defaults. Changing these credentials do not apply to 99% of Amazon employees.

---

### Tools (90)

**Messages (13 tools)**

- `get_messages` — Fetch messages with enriched user details, thread replies, file attachments, and pagination. Supports date filtering (`since`/`until`) and thread control (`includeThreadReplies`, `maxThreadsToFetch`)
- `get_thread` — Get all replies in a thread with full user enrichment and reaction details
- `post_message` — Post messages with mrkdwn formatting, threading, link preview control, and reply broadcast ("Also send to channel")
- `post_block_message` — Post rich messages using Block Kit (validated: max 50 blocks, max 5 nesting depth) with reply broadcast and unfurl control
- `bulk_post_message` — Fan out the same text message to multiple channels in parallel with per-channel success/failure reporting
- `bulk_post_block_message` — Fan out the same Block Kit message to multiple channels in parallel with per-channel success/failure reporting
- `edit_message` — Edit an existing message you authored
- `edit_block_message` — Edit a Block Kit message
- `delete_message` — Delete a message you authored from a channel or conversation
- `add_reaction` — Add a single emoji reaction to a message
- `add_reactions` — Add multiple emoji reactions in parallel (up to 25 at once)
- `remove_reaction` — Remove your own emoji reaction from a message
- `get_reactions` — Get all emoji reactions on a message with user IDs and counts

**Read State (1 tool)**

- `set_last_read` — Mark a channel as read up to a specific message timestamp

**Channels (3 tools)**

- `list_my_channels` — List channels organized by sidebar sections with compact output option
- `get_channel` — Get detailed channel information by name or ID with fuzzy matching
- `get_conversation_details` — Get full conversation details including members and settings

**Channel Management (13 tools)**

- `create_channel` — Create a new public or private channel
- `rename_channel` — Rename a channel
- `set_channel_topic` — Set or update a channel's topic
- `set_channel_purpose` — Set or update a channel's purpose/description
- `invite_to_channel` — Invite one or more users to a channel
- `remove_from_channel` — Remove a user from a channel
- `leave_channel` — Leave a channel
- `list_channel_members` — List all members of a channel with auto-pagination (up to 10,000 members)
- `list_channel_managers` — List the managers/admins of a channel
- `add_channel_manager` — Add users as channel managers
- `remove_channel_manager` — Remove users from channel manager role
- `mute_channel` — Mute a channel to stop notifications
- `unmute_channel` — Unmute a channel to resume notifications

**Sidebar Sections (5 tools)**

- `create_channel_section` — Create a new sidebar section for organizing channels
- `update_channel_section` — Update a sidebar section's name or emoji
- `bulk_update_channel_sections` — Move channels into sections in bulk
- `delete_channel_section` — Delete a sidebar section
- `reorder_channel_section` — Reorder a sidebar section

**Search (1 tool)**

- `search` — Search messages and files with sort control (`score`/`timestamp`), scope filtering (`messages`/`files`/`all`), and automatic thread parent resolution for reply matches

**Files and Emojis (3 tools)**

- `download_file` — Download file content by ID; text files return UTF-8, binary files return base64
- `upload_file` — Upload files to Slack with auto-detected syntax highlighting for code snippets
- `list_emojis` — List all custom workspace emojis with 1-week caching

**Canvases (7 tools)**

- `get_canvas_content` — Read a Slack Canvas document as text (capped at 50,000 characters)
- `get_channel_tabs` — List all tabs (bookmarks) pinned to a channel, including canvases, lists, and links
- `list_canvases` — Browse your Canvas documents sorted by last engaged
- `list_canvas_sections` — List a canvas's sections with stable IDs for targeted edits
- `create_canvas` — Create a new Slack Canvas from markdown content
- `edit_canvas` — Edit a canvas in place: insert, replace, delete, move, and setChecked operations with batch support, heading anchors, tables, checklists, mentions, dates, and inline formatting
- `share_canvas` — Share a canvas to a channel

**Lists (16 tools)**

- `get_list_content` — Read a Slack List's columns and records as structured data
- `list_lists` — Browse your Slack Lists sorted by last engaged
- `create_list` — Create a new Slack List
- `update_list` — Rename a Slack List
- `update_list_record` — Edit a cell in a Slack List. Supports all column types: text, number, date, select (pipe-separated option IDs), user/assignee, channel, checkbox, email, phone, vote, rating, and attachment
- `bulk_update_list_cells` — Update many cells across rows in one call
- `add_list_item` — Add a new row to a Slack List with optional initial cell values
- `delete_list_item` — Delete a row from a Slack List
- `archive_list_item` — Archive a row in a Slack List
- `reorder_list_item` — Move a row to a new position in a Slack List
- `subscribe_list_item` — Subscribe to notifications for a list row
- `unsubscribe_list_item` — Unsubscribe from notifications for a list row
- `add_list_column` — Add a column to a Slack List (types: text, number, date, select, user, email, phone, checkbox, attachment, channel, vote, rating)
- `delete_list_column` — Delete a column from a Slack List
- `rename_list_column` — Rename a column in a Slack List
- `update_select_options` — Add or remove options on a select column in a Slack List

**Users and DMs (3 tools)**

- `lookup_user` — Look up a user by alias, email, display name, or user ID
- `open_dm_channel` — Open or get a direct message channel (supports group DMs)
- `list_dms` — List recent DM conversations

**Scheduling (4 tools)**

- `schedule_message` — Schedule a message for future delivery (up to 120 days)
- `schedule_block_message` — Schedule a Block Kit message for future delivery (up to 120 days, validated: max 50 blocks, max 5 nesting depth)
- `list_scheduled_messages` — List all pending scheduled messages with raw Block Kit blocks
- `delete_scheduled_message` — Cancel a scheduled message before delivery

**Drafts (3 tools)** — _Add `"SAFE_MODE": "true"` to your MCP config `env` to make `post_message` save drafts automatically_

- `post_draft` — Save a message draft to a channel's compose box for human review before sending. Supports threading
- `list_drafts` — List all unsent message drafts sitting in channel compose boxes
- `delete_draft` — Delete an unsent message draft

**Reminders (6 tools)**

- `set_reminder` — Create a personal reminder with natural language time support
- `list_reminders` — List reminders filtered by state (saved, completed, archived) with pagination
- `edit_reminder` — Edit a reminder's text and/or due date
- `complete_reminder` — Mark a reminder as completed
- `delete_reminder` — Permanently delete a reminder
- `archive_reminder` — Archive a reminder

**Saved Messages (3 tools)**

- `list_saved_messages` — List messages saved for later
- `save_message` — Save a message for later
- `remove_saved_message` — Remove a message from saved items

**Status (2 tools)**

- `get_status` — Get a user's Slack status
- `set_status` — Set your Slack status with expiration

**Unreads (2 tools)**

- `get_unreads` — Get all unread messages across channels, DMs, and threads
- `get_unread_counts` — Lightweight unread discovery: channel/DM names and mention counts without fetching message content

**Slackbot AI (1 tool)**

- `ask_slackbot` — Ask Slack's built-in AI assistant for recaps, summaries, or information with multi-turn conversation support

**Batch (2 tools)**

- `batch_get_messages` — Fetch messages from multiple channels in parallel
- `batch_get_threads` — Fetch multiple threads in parallel

**Confirm (1 tool)**

- `confirm_message` — Execute a previewed write operation (confirm mode only)

**Diagnostics (2 tools)**

- `health_check` — Quick server health status with cache warmup state
- `get_diagnostics` — Detailed server metrics, cache stats, configuration, and connection pool health

---

### Features

- **90 tools, 11 resources, 4 prompts** — Messages, channels, channel management, sidebar sections, search, reactions, files, canvases, lists, emojis, users, DMs, scheduling, drafts, reminders, saved messages, status, unreads, Slackbot AI, batch operations, diagnostics, and pre-built workflow prompts
- **Enriched results** — User IDs resolved to names/profiles, channel IDs to names, thread parents auto-fetched for search reply matches
- **Advanced search** — Sort by relevance or timestamp, scope to messages or files only, Slack modifiers (`in:`, `from:`, `has:`, `before:`, `after:`)
- **MCS authentication** — Hardware-bound Midway SSO via MCS Keys Process, automatic SAML flow, background token refresh
- **Bot token mode** — Authenticate as a Slack bot via AWS Secrets Manager (`SLACK_BOT_TOKEN_SECRET_ARN`): startup `auth.test` verification, rotation-aware refetch on 401, terminal revocation guidance, audit attribution to the bot identity, custom display name/avatar, and a tool surface that auto-adapts to the token type and granted OAuth scopes
- **Caching** — LRU cache with TTL for channels and users, background warmup on startup, request coalescing
- **Input validation** — Zod schemas on all tool inputs with length limits and format constraints
- **SAFE_MODE** — Add `"SAFE_MODE": "true"` to block all 46 write tools and redirect `post_message` to drafts for human review. Agents compose, humans approve.
- **File upload security** — Uploads disabled by default. Set `UPLOAD_ALLOWED_DIRS` to enable with directory allowlisting. Sensitive files (credentials, keys, tokens) always blocked.
- **Per-tool rate limiting** — Sliding window limits across 3 tiers (read 120/min, write 30/min, expensive 15/min); `post_message` 10/min. Enabled by default, configurable via env vars
- **Security** — Content security (datamarking, safety directives), SSRF protection, hardcoded API URLs, broadcast mention filtering, PII redaction in logs, bidirectional transport size guards
- **Structured logging** — RFC 5424 JSON logs to stderr with MCP client notifications

---

### Troubleshooting ACCESS_DENIED_ERROR

If installation fails with `ACCESS_DENIED_ERROR` or `Failed to resolve version set`, your deemed exports citizenship status likely needs updating:

1. Go to [AtoZ Profile](https://atoz.amazon.work/profile)
2. Select **Manage personal information** → **Work authorization and citizenship**
3. In the **Deemed exports** section, click **Edit** and answer the questions

This is required for access to internal packages. Changes propagate within a few hours.

---

### Troubleshooting (Mac)

If installation fails with authentication errors or missing dependencies, run these steps in order:

**1. Refresh Midway credentials**

```sh
mwinit -s -o
```

**2. Install/update Builder Toolbox**

```sh
curl -X POST \
    --data '{"os":"osx"}' \
    -H "Authorization: $(curl -L \
      --cookie $HOME/.midway/cookie \
      --cookie-jar $HOME/.midway/cookie \
      "https://midway-auth.amazon.com/SSO?client_id=https://us-east-1.prod.release-service.toolbox.builder-tools.aws.dev&response_type=id_token&nonce=$RANDOM&redirect_uri=https://us-east-1.prod.release-service.toolbox.builder-tools.aws.dev:443")" \
    https://us-east-1.prod.release-service.toolbox.builder-tools.aws.dev/v1/bootstrap \
    > ~/toolbox-bootstrap.sh
bash ~/toolbox-bootstrap.sh
source ~/.$(basename "$SHELL")rc
rm ~/toolbox-bootstrap.sh
```

**3. Install/update AIM CLI and BrazilCLI**

```sh
toolbox install aim
toolbox install brazilcli
```

> You may need to restart your laptop after installing BrazilCLI.

**4. Start the package cache**

```sh
brazil-package-cache start
```

**5. Install slack-mcp**

```sh
aim mcp install slack-mcp
```

---

### Troubleshooting (Windows)

Windows requires **Amazon WSL** (standard Ubuntu from the Microsoft Store won't work). Amazon WSL is a custom internal distribution from Client Engineering pre-configured with `mwinit`, `wssh`, and `toolbox`.

**Prerequisites**

- Windows 11 (Windows 10 is not supported)
- You must be in the `toolbox-users-misc` POSIX group. Check at [permissions.amazon.com](https://permissions.amazon.com/user.mhtml). If missing, ask your manager to add you (takes 4-8 hrs to propagate).

**1. Enable WSL**

Open PowerShell as Admin:

```sh
wsl --install --no-distribution
```

Restart your computer.

**2. Install Amazon WSL**

Download the `AmazonWSL.msi` from the [Client Engineering wiki](https://w.amazon.com/bin/view/ClientEng/Ubuntu_Platform/AmazonWSL/). Run the installer (it completes silently, no success dialog). **Disconnect from VPN before launching.**

**3. Launch Amazon WSL**

Open from Start menu (look for the **black Amazon penguin** icon, not the blue one). Let the bootstrap complete (10-15 min). **Do not close the window.** Create a Linux password when prompted.

**4. SSH Setup**

```sh
ssh-keygen -t ecdsa
```

Press Enter through all prompts, then:

```sh
wssh setup
```

**5. Authenticate with Midway**

```sh
mwinit -o -s
```

Enter your PIN + security key. You'll need to do this once daily.

**6. Install Toolbox**

```sh
toolbox install toolbox
```

Then reload your shell:

```sh
source ~/.bashrc
```

**7. Install AIM and slack-mcp**

```sh
toolbox install aim
aim mcp install slack-mcp
```

**Common Windows issues**

- **"Authentication failed"** on `mwinit` → Disconnect from VPN.
- **"FAILED to get certificate... required to use third-factor with OTP"** → Follow the [OTP Software Certificate instructions](https://w.amazon.com/bin/view/NextGenMidway/UserGuide/OTPSoftwareCertificate/), then retry `mwinit -o -s`.
- **"command not found: toolbox"** → Re-run `mwinit -o -s` and try again. Midway auth may have expired.

> For community WSL support, join [#wsl-interest](https://app.slack.com/client/T016N5XPKB0/C01885CTEFN).

---

Still having issues? Visit [#slack-mcp-server](https://amzn-community.slack.com/archives/C0AMB7YUTUZ) and tag `@slack-mcp-help` for support.

---