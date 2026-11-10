# Conversation References — plan

**Status:** Slice 1 (store + pickup capture + prompt notice) landed 2026-11-09: `app/utils/conversation_references.py`, wired into `stream_chunks` / `build_messages_for_streaming`; tests `tests/test_conversation_references.py`. Open questions 1–2 resolved by default (link-only; citation primary, re-read ≥ 2 turns secondary); 3 (memories) deferred to v2. Slice 2 (API `…/chats/{id}/references` GET/POST, `/{rid}/pin`, DELETE; `references_updated` SSE frame before `done`; `ReferencesLine` in `ActiveContextBar` + `📎 N` in `CollapsedPanelStrip`; `useConversationReferences`) written 2026-11-09, tests `tests/test_api_chat_references.py`, `frontend/src/components/__tests__/referencesLine.test.tsx`. Not yet: paperclip "Add reference" menu item (§Surface 3), per-message hover icon (§Surface 4), composer paste/drop capture.
**Created:** 2026-11-09
**Related:** per-conversation file tracking (FileStateManager pinned / model-added / auto / tracked), `ActiveContextBar` and the ✦ pickup grammar (`capabilities-hub.md` slice 5), `ziya://` links (`app/utils/ziya_links.py`), `conversation-handoff.md`.

---

## Problem

A conversation leans on things outside its message text: URLs, files, PDFs,
other threads (`ziya://chat/…`), memories. Today only the *file* subset is
tracked (context files and files the conversation writes). Everything else
is either inlined into one message (the composer's "attach document", which
copies the bytes) or exists only as a string the model once fetched. There
is:

1. no place to put a set of references when starting a discussion
   ("here are the eight things this is about");
2. no record of what a tool fetched or read on the conversation's behalf, so
   a URL `mcp_fetch` pulled at turn 4 and the model cited at turns 6 and 11
   is not recoverable without rereading the transcript;
3. no way to tell, for an exchange, which artifacts it depended on.

## Model

- **Reference**: `{id, kind, locator, title, provenance, added_turn, uses[]}`
  stored on the conversation record (same home as the conversation layer of
  the bench: chat record when one exists, tab state for ephemeral chats).
- **Kinds** (v1): `url`, `file` (project-relative or absolute), `pdf`
  (file with a page index), `chat` (`ziya://chat/<id>[/<idx>]`), `memory`
  (`m_…`; see §Open 3). Kind is inferred from the locator.
- **Provenance**, two classes, shown differently and never merged:
  - **pinned** — the user put it there. Static; listed first; never ages out.
  - **picked up** — a tool touched it during a turn (`mcp_fetch`, `file_read`,
    `pdf_read_pages`, `chat_read`, `context_add_file`, …). Recorded
    automatically with usage evidence. One click promotes to pinned.
- **Uses** are appended per turn: `read` (a tool returned its content),
  `cited` (its locator/title appears in the assistant reply), `written`
  (a diff was emitted or a write made against it — this one feeds the
  existing tracked-files mechanism), `opened` (the user clicked it).
- **Useful** = cited at least once, or read in ≥ 2 turns, or written. A
  picked-up reference with no use after N turns (default 8) is *faded*: kept
  in the count, not listed. Pinned items are never faded.
- **Link-only by default**: a reference does not put bytes in the prompt.
  The model sees one line per reference in the volatile tail (kind, title,
  token estimate, last-read turn, "changed on disk since" for files) and
  expands it with the tools that already exist. This is the same pattern as
  tracked-but-not-in-context files. "Snapshot into context" is the existing
  `context_add_file` / attach path and stays a separate, explicit action.
- **Resolution at add time**: a pinned URL is HEAD/GET-checked, a file must
  exist, a `ziya://` link must parse (`parse_ziya_link`) and resolve, so a
  dead reference is caught when it is added rather than when the model
  trips on it.
- **Scope**: conversation. A fork or handoff continuation inherits the
  pinned set (and the picked-up set with its uses, so usefulness is not
  reset). Cross-conversation sharing is an explicit "also pin to project"
  action, not a default.

## Surface (agreed)

Zero footprint until asked. Three touches, all on existing controls; an
earlier always-on pass (panel + per-message footer + add form) was rejected
as "more in the way than out of the way".

1. **Count** — one line in `ActiveContextBar`, under the skill pills, using
   the bar's existing dot grammar: solid dot = pinned, dashed teal = picked
   up and useful, dashed grey = picked up and not. Text: `📎 7 references ·
   2 picked up`. A teal marker when something was picked up since the user
   last opened it. In `CollapsedPanelStrip` it is just `📎 7`.
2. **Popover** — click the line: pinned items listed (icon, title, use
   count or state such as *changed* / *p4–7*), then one folded line
   `▸ 5 picked up by tools · 2 look useful · pin both`. Expanding lists them
   dimmed with 📌 on hover. Faded items are not listed. A popover, not a
   pane; nothing is mounted until clicked.
3. **Add** — the composer's existing paperclip (`SendChatContainer`,
   `PaperClipOutlined`) becomes a two-item menu: *Attach image or document
   — contents go into this message* (today's behaviour) and *Add reference —
   URL, file, ziya:// link; stays with the conversation*. Also: a bare URL or
   `ziya://` link pasted into the composer is recorded as a reference with no
   menu (it is going to the model anyway), and a file or sidebar
   conversation dropped on the 📎 line is pinned.
4. **Per message** — the existing assistant hover toolbar gains a 📎 icon
   whose tooltip lists that turn's uses (`read x · cited y · new: z`). No
   per-message footer; if the user never hovers, nothing exists.

Not a header: the main pane has none (Conversation → StreamedContent →
composer dock), and inventing one for this was the wrong direction.

## Seams

- Picked-up events originate where tool results are produced (the
  `stream_chunks` / tool-executor path already derives `capability_used`
  from tool_display chunks; references are the same shape of event keyed on
  tool name + locator argument).
- `cited` is computed server-side from the final assistant text against the
  conversation's reference locators and titles.
- The file kind reuses FileStateManager: a pinned or picked-up file that is
  also tracked reports "changed on disk since turn N" from the same baseline.
- Prompt: references go in the per-turn volatile tail
  (`precision_prompt_system.build_messages`), never in the cached system
  prefix.

## Open

1. **Default for pinned items** — link-only (proposed) vs snapshot. Link-only
   fits the existing tracked-file pattern and keeps a reference list cheap.
2. **Usefulness signals** — citation in reply text as the primary signal,
   re-read / written / opened as tie-breakers; or an explicit 👍 on the item.
3. **Memories as references** — natural, but overlaps the memory system's own
   scoping. Include in v1 or defer.
4. **Fade window** — 8 turns is a guess.
