#!/usr/bin/env python3
"""
Probe: can we screenshot a real chat message rendered by the production UI?

This exists to answer ONE question empirically, before any of it is wired into
render_diagram as a supported type: if a conversation is seeded server-side and
a fresh headless browser is pointed at the real app root ("/"), does the app
bootstrap far enough to render that conversation's message so it can be
element-screenshotted?

Reading the frontend could not settle this.  ChatContext's startup path is
conditional (IndexedDB shells first, a server pull only under an
IDB_REPAIR heuristic, lazy per-conversation message-body fetch, a startup GC
that purges empty conversations, per-tab restore of the selected conversation
id from sessionStorage).  A fresh Playwright profile has an EMPTY IndexedDB,
which is a state the normal user flow rarely produces, so the only honest way
to know which branch runs is to run it.

What this does:
  1. resolves the current project via GET /api/v1/projects/current
  2. creates a chat (POST .../chats) and puts a single assistant message into
     it (PUT .../chats/{id}) -- create() hardcodes messages=[], so the message
     MUST go in via update, not create
  3. launches Chromium, pre-seeds localStorage ZIYA_LAST_PROJECT_ID and
     sessionStorage ZIYA_CURRENT_CONVERSATION_ID via add_init_script (both are
     read during app bootstrap, so they must exist BEFORE app JS runs)
  4. navigates to "/", waits for the message to actually render, scrolls it
     into view so the real LazyMarkdownRenderer IntersectionObserver fires,
     and screenshots the .message element
  5. deletes the seeded chat

Every failure path prints diagnostics (console tail, DOM probe, which wait
condition timed out) rather than just "timeout", because the point of the probe
is to learn WHERE the bootstrap stops if it stops.

Usage:
    python3 scripts/chat_screenshot_probe.py [--port 6969] [--out /tmp/probe.png]
    python3 scripts/chat_screenshot_probe.py --keep-chat   # skip cleanup
    python3 scripts/chat_screenshot_probe.py --headed      # watch it happen
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

# Marker in the seeded content.  Used as the "did the real message render"
# probe -- a unique string is far more reliable than asserting on structure,
# and it also proves the text came from OUR chat and not a pre-existing one.
CONTENT_MARKER = "ZIYAPROBEMARKER"

# Deliberately over 400 chars (INLINE_THRESHOLD_CHARS in Conversation.tsx):
# below that threshold LazyMarkdownRenderer mounts immediately and the probe
# would pass without ever exercising the deferred-mount path that every real
# adversarial spec will hit.
PROBE_MARKDOWN = f"""{CONTENT_MARKER} probe message.

Inline math: $x^2 + \\alpha$ should typeset. A currency control that must stay
literal: $900 deposit + $300 fee. Filler prose exists here purely to push this
message past the four-hundred character lazy-mount threshold so the deferred
IntersectionObserver path in LazyMarkdownRenderer is genuinely exercised rather
than bypassed, because bypassing it is exactly how a probe passes while the
real specs fail.

Display math:

$$\\frac{{-b \\pm \\sqrt{{b^2 - 4ac}}}}{{2a}}$$

A fenced block whose dollars must NOT be treated as math:

```python
total = "$$ not math $$"
```

End of probe.
"""


def http(method: str, url: str, body: dict | None = None,
         headers: dict | None = None, timeout: int = 20):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:600]
    except Exception as e:  # connection refused, etc.
        return None, repr(e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("ZIYA_PORT", 6969)))
    ap.add_argument("--out", default="/tmp/ziya-chat-probe.png")
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--timeout", type=int, default=60, help="seconds for page waits")
    ap.add_argument("--keep-chat", action="store_true")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    base = f"http://127.0.0.1:{args.port}"
    print(f"== probe against {base} (root={args.root})")

    # ---- 1. project ------------------------------------------------------
    status, proj = http("GET", f"{base}/api/v1/projects/current",
                        headers={"X-Project-Root": args.root})
    if status != 200 or not isinstance(proj, dict):
        print(f"FAIL: /projects/current -> {status} {proj}")
        return 2
    pid = proj["id"]
    print(f"   project id={pid} path={proj.get('path')}")

    # ---- 2. seed chat ----------------------------------------------------
    status, chat = http("POST", f"{base}/api/v1/projects/{pid}/chats",
                        {"title": "math-fuzz probe (auto)"},
                        headers={"X-Project-Root": args.root})
    if status != 200 or not isinstance(chat, dict):
        print(f"FAIL: create chat -> {status} {chat}")
        return 2
    cid = chat["id"]
    print(f"   chat id={cid}")

    now_ms = int(time.time() * 1000)
    msg = {
        "id": f"probe-msg-{now_ms}",
        "role": "assistant",
        "content": PROBE_MARKDOWN,
        "timestamp": now_ms,
    }
    status, updated = http("PUT", f"{base}/api/v1/projects/{pid}/chats/{cid}",
                           {"messages": [msg]},
                           headers={"X-Project-Root": args.root})
    if status != 200:
        print(f"FAIL: put messages -> {status} {updated}")
        return 2
    n = len(updated.get("messages", [])) if isinstance(updated, dict) else -1
    print(f"   seeded messages={n}")
    if n != 1:
        print("FAIL: message did not persist via ChatUpdate")
        return 2

    rc = 3
    try:
        rc = drive_browser(base, pid, cid, args)
    finally:
        if args.keep_chat:
            print(f"   (kept chat {cid})")
        else:
            st, _ = http("DELETE", f"{base}/api/v1/projects/{pid}/chats/{cid}",
                         headers={"X-Project-Root": args.root})
            print(f"   cleanup delete -> {st}")
    return rc


def drive_browser(base: str, pid: str, cid: str, args) -> int:
    from playwright.sync_api import sync_playwright

    console: list[str] = []
    errors: list[str] = []
    timeout_ms = args.timeout * 1000

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        # Tall viewport: the IntersectionObserver uses rootMargin 500px, so a
        # taller window means the deferred mount is more likely to trigger from
        # layout alone -- but we still scroll explicitly below rather than
        # relying on that.
        ctx = browser.new_context(viewport={"width": 1400, "height": 1600})
        page = ctx.new_page()
        page.on("console", lambda m: console.append(f"[{m.type}] {m.text}"[:300]))
        page.on("pageerror", lambda e: errors.append(str(e)[:300]))

        # Must run BEFORE app JS: ProjectContext reads ZIYA_LAST_PROJECT_ID
        # from localStorage during init, and ChatContext reads
        # ZIYA_CURRENT_CONVERSATION_ID via getTabState (sessionStorage).
        page.add_init_script(f"""
            try {{
                localStorage.setItem('ZIYA_LAST_PROJECT_ID', {json.dumps(pid)});
                sessionStorage.setItem('ZIYA_CURRENT_CONVERSATION_ID', {json.dumps(cid)});
            }} catch (e) {{ console.error('probe seed storage failed', e); }}
        """)

        print("== navigating to /")
        t0 = time.time()
        try:
            page.goto(base + "/", wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as e:
            print(f"FAIL: goto -> {e!r}")
            dump(page, console, errors)
            return 3

        # Stage 1: does the app shell mount at all?
        try:
            page.wait_for_selector(".message, .chat-container, #root > *",
                                   timeout=timeout_ms)
            print(f"   shell mounted ({time.time()-t0:.1f}s)")
        except Exception:
            print("FAIL: app shell never mounted")
            dump(page, console, errors)
            return 3

        # Stage 2: did OUR conversation get selected and its message body
        # loaded?  This is the branch that reading the source could not settle.
        try:
            page.wait_for_function(
                "m => document.body.innerText.includes(m)",
                arg=CONTENT_MARKER, timeout=timeout_ms,
            )
            print(f"   seeded message present ({time.time()-t0:.1f}s)")
        except Exception:
            print("FAIL: seeded message never appeared -- bootstrap did not "
                  "select/load the conversation")
            dump(page, console, errors)
            return 4

        # Stage 3: scroll it into view so the real IntersectionObserver fires
        # and the deferred MarkdownRenderer mount happens the production way.
        try:
            page.evaluate("""
                () => {
                    const nodes = [...document.querySelectorAll('.message')];
                    const t = nodes.find(n => n.innerText.includes('ZIYAPROBEMARKER'));
                    if (t) t.scrollIntoView({block: 'center'});
                }
            """)
        except Exception as e:
            print(f"   warn: scroll failed {e!r}")

        # Stage 4: wait for real rendered math, and for the lazy placeholder to
        # be gone.  A '…' placeholder screenshotted as if it were the render is
        # the specific silent failure this whole probe exists to rule out.
        rendered = False
        try:
            page.wait_for_function(
                """() => {
                    const nodes = [...document.querySelectorAll('.message')];
                    const t = nodes.find(n => n.innerText.includes('ZIYAPROBEMARKER'));
                    if (!t) return false;
                    if (t.querySelector('.katex')) return true;
                    return false;
                }""",
                timeout=timeout_ms,
            )
            rendered = True
            print(f"   KaTeX rendered ({time.time()-t0:.1f}s)")
        except Exception:
            print("   WARN: no .katex found -- math may be dropped, or the "
                  "message is still a lazy placeholder. Screenshotting anyway "
                  "so the failure is visible rather than lost.")

        page.wait_for_timeout(1200)  # let post-render fixups settle

        # Stage 5: element screenshot of the real message node.
        try:
            handle = page.evaluate_handle("""
                () => {
                    const nodes = [...document.querySelectorAll('.message')];
                    return nodes.find(n => n.innerText.includes('ZIYAPROBEMARKER')) || null;
                }
            """)
            el = handle.as_element()
            if el is None:
                print("FAIL: could not resolve .message element for screenshot")
                dump(page, console, errors)
                return 5
            el.screenshot(path=args.out)
            size = os.path.getsize(args.out)
            print(f"OK: screenshot -> {args.out} ({size} bytes)")
        except Exception as e:
            print(f"FAIL: screenshot -> {e!r}")
            dump(page, console, errors)
            return 5

        # Report what the DOM actually contains, so a passing probe still tells
        # us whether the pipeline behaved (katex count, leftover $ markers).
        try:
            info = page.evaluate("""
                () => {
                    const nodes = [...document.querySelectorAll('.message')];
                    const t = nodes.find(n => n.innerText.includes('ZIYAPROBEMARKER'));
                    if (!t) return {missing: true};
                    const txt = t.innerText;
                    return {
                        katex: t.querySelectorAll('.katex').length,
                        katex_error: t.querySelectorAll('.katex-error').length,
                        fallback: t.querySelectorAll('.math-fallback').length,
                        code_blocks: t.querySelectorAll('pre').length,
                        has_placeholder_ellipsis: txt.trim() === '…',
                        raw_dollar_dollar: txt.includes('$$'),
                        currency_intact: txt.includes('$900'),
                        leaked_marker: txt.includes('MATH_INLINE') || txt.includes('MATH_DISPLAY'),
                        text_len: txt.length,
                    };
                }
            """)
            print("   dom:", json.dumps(info, indent=2))
        except Exception as e:
            print(f"   warn: dom probe failed {e!r}")

        if errors:
            print("   pageerrors:", errors[:5])
        interesting = [c for c in console if "error" in c.lower()][:8]
        if interesting:
            print("   console errors:", interesting)

        browser.close()

    return 0 if rendered else 6


def dump(page, console, errors) -> None:
    """Diagnostics on any failure path -- knowing WHERE bootstrap stopped is
    the entire value of this probe."""
    try:
        state = page.evaluate("""
            () => ({
                url: location.href,
                root_children: (document.getElementById('root')||{children:[]}).children.length,
                messages: document.querySelectorAll('.message').length,
                body_head: document.body.innerText.slice(0, 400),
                ls_project: localStorage.getItem('ZIYA_LAST_PROJECT_ID'),
                ss_conv: sessionStorage.getItem('ZIYA_CURRENT_CONVERSATION_ID'),
            })
        """)
        print("   DIAG:", json.dumps(state, indent=2)[:1500])
    except Exception as e:
        print(f"   DIAG failed: {e!r}")
    if errors:
        print("   pageerrors:", errors[:5])
    if console:
        print("   console tail:", console[-15:])


if __name__ == "__main__":
    sys.exit(main())
