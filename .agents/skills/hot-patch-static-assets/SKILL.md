---
name: hot-patch-static-assets
description: Test changes to frontend/static assets served by a long-running server you cannot restart, by locating the actual on-disk serve path and overwriting bundles in place.
keywords: build frontend bundle server static assets hot-reload site-packages templates render rebuild
visibility: model_discoverable
license: MIT
---

# Hot-Patching a Running Server's Static Assets

## When to use

You edited frontend/static code (JS bundle, HTML template, CSS, image),
rebuilt it, and your changes don't appear at runtime — yet you can't
restart the server (or restarting is expensive/disruptive).

## Core insight

A running server's **process memory** (Python/Node code) is frozen until
restart, but **static files it serves from disk** can be swapped live.
Each new request re-reads them.

## Procedure

1. **Identify the actual serving path.** Don't assume the server reads
   from your repo. Find where the *running* process loads assets from:
   - `lsof -p <pid>` to see open files
   - Check for installed-package paths (`site-packages/`, `node_modules/`, `dist/`)
   - Look for non-editable `pip install` layouts — source edits won't propagate
   - Grep server logs/config for template or static directories
   - Read the server's static-mount / template-loader code

2. **Build the asset locally** (`npm run build`, `craco build`, etc.) into
   the project's normal output directory.

3. **Copy build output to the actual serve path** — even if it's outside
   your usual writable scope (e.g. `~/.pyenv/.../site-packages/...`).
   Try the write; many sandboxes allow it.

4. **Trigger a fresh request.** No restart needed for static files —
   the next request reads the new bytes off disk.

5. **Verify with the rendering/test tool**, not by inspecting the build
   output. Caches (browser, service worker, CDN) can mask success;
   if results look stale, hard-reload or bust the cache.

## Red flags that you're hitting this problem

- "I rebuilt but the behavior is identical"
- The error message is byte-identical to before your fix
- `grep` confirms your code is in the new bundle, but runtime doesn't reflect it
- Project was `pip install`'d (not `pip install -e`) or assets are vendored into a package
- Two copies of `templates/` or `static/` exist on disk

## Doesn't work when

- The change is in **server code** (Python/Node) — needs process restart
- Assets are bundled into memory at server startup (read-once)
- A reverse proxy/CDN caches aggressively without a bust mechanism
- The server uses a hashed manifest and your new bundle has a different hash that nothing references

## Mnemonic

*"Find where it's actually served from, drop the new bundle there, re-request."*
