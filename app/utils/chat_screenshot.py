"""
Screenshot a markdown message exactly as the PRODUCTION chat UI renders it.

Why this exists
---------------
Ziya's math/markdown rendering lives entirely in the frontend chat pipeline
(MarkdownRenderer's ``$$``/fence pre-processing, inlineMathClassifier,
fenceScanner, MathRenderer).  Verifying a change to it means LOOKING at the
result, and until now there was no way to obtain those pixels headlessly:
``render_diagram`` drives the isolated ``/render`` harness, which mounts
``D3Renderer`` and therefore cannot reach the markdown pipeline at all.

The obvious fix -- mount ``MarkdownRenderer`` on ``/render`` with a copy of the
props chat passes -- was rejected.  It creates a SECOND render path that has to
be kept in sync with the chat call site by hand, and any test comparing the two
compares one transcription against another: if both drift identically the test
still passes while every verdict built on it is wrong.

So this module does not approximate the chat surface.  It seeds a real
conversation, points a browser at the real application root, and screenshots
the real ``.message`` element.  Fidelity is not a property to be verified
because there is only one path -- the artifact under inspection IS the product.

How it works
------------
1. Resolve the project and create a chat through ``ChatStorage`` in-process.
   ``ChatStorage.create`` hardcodes ``messages=[]``, so the message must be
   installed via ``update`` (``ChatUpdate.messages``) -- not ``create``.
2. Seed ``localStorage.ZIYA_LAST_PROJECT_ID`` and
   ``sessionStorage.ZIYA_CURRENT_CONVERSATION_ID`` with ``add_init_script``,
   i.e. BEFORE app JS runs, because both are read during bootstrap
   (ProjectContext restores the project; ChatContext restores the selected
   conversation via ``getTabState``).
3. Navigate to ``/`` and let the whole production stack run.
4. Scroll the message into view.  ``LazyMarkdownRenderer`` defers mounting any
   message over 400 chars behind an IntersectionObserver, and every realistic
   test document exceeds that -- so scrolling is what makes the REAL deferred
   mount happen rather than being worked around.
5. Wait for genuinely rendered output, element-screenshot the message, delete
   the chat.

The wait is deliberately positive (a ``.katex`` node exists when math was
requested; the ``…`` lazy placeholder is gone) rather than "the DOM went quiet".
Waiting for quiet is how you screenshot a placeholder and mistake it for a
rendering defect.

On timeout this still screenshots and returns, flagging
``rendered_confirmed: False``.  A pipeline bug that drops math entirely would
otherwise raise instead of producing the very picture that reveals it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Title prefix for seeded chats.  Every chat this module creates is deleted in
#: a ``finally``, but a hard crash (or a killed server) can orphan one, and an
#: identifiable prefix is what makes the sweep below possible.
CHAT_TITLE_PREFIX = "[auto] chat-message render"

#: Orphaned seeded chats older than this are swept opportunistically.
ORPHAN_MAX_AGE_MS = 60 * 60 * 1000

#: Settle delay after the render predicate passes, for post-render fixups
#: (KaTeX sizing, enhancers) that mutate already-mounted output.
SETTLE_MS = 1200

# Strong math signals.  Used only to decide whether to REQUIRE a .katex node in
# the wait predicate; a false negative merely weakens the wait, it cannot
# corrupt the screenshot.
_MATH_HINT = re.compile(r"(\$\$|\\\(|\\\[|```\s*(?:math|latex)|\\begin\{)")
_INLINE_MATH_HINT = re.compile(r"\$(?=\S)[^$\n]{1,200}(?<=\S)\$")


def expects_math(definition: str) -> bool:
    """True when the document plausibly contains math that must typeset.

    Drives the wait predicate only.  Deliberately generous: requiring a
    ``.katex`` node for a document with no math would hang every prose-only
    render, which is a worse failure than waiting a little less strictly.
    """
    if _MATH_HINT.search(definition):
        return True
    return bool(_INLINE_MATH_HINT.search(definition))


def derive_locators(definition: str, limit: int = 4) -> list[str]:
    """Words from the document likely to survive into rendered text.

    Used to identify WHICH ``.message`` node is ours.  Note what this
    deliberately does NOT do: inject a marker into the content.  A marker would
    change the very document being judged -- an extra paragraph, an extra token
    for the classifier to see -- so the locator is drawn from the caller's own
    text instead.  Words of 6+ letters are chosen because short tokens collide
    with chat chrome ("AI", "Copy", "Edit").
    """
    words = re.findall(r"[A-Za-z]{6,}", definition or "")
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        low = w.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(w)
        if len(out) >= limit:
            break
    return out


def build_seed_script(project_id: str, conversation_id: str,
                      dark: bool) -> str:
    """JS that pre-seeds bootstrap storage.  Runs before any app code.

    ``ZIYA_THEME_PREFERENCE`` holds a JSON boolean (ThemeContext does
    ``JSON.parse`` on it), not the string 'dark'/'light'.
    """
    return (
        "try {"
        f"  localStorage.setItem('ZIYA_LAST_PROJECT_ID', {json.dumps(project_id)});"
        f"  localStorage.setItem('ZIYA_THEME_PREFERENCE', {json.dumps(json.dumps(dark))});"
        f"  sessionStorage.setItem('ZIYA_CURRENT_CONVERSATION_ID', {json.dumps(conversation_id)});"
        "} catch (e) { console.error('chat-screenshot seed failed', e); }"
    )


def build_locator_js(locators: list[str], role: str) -> str:
    """JS expression evaluating to our ``.message`` element, or null.

    Two-stage: prefer a message containing one of the caller's own words, then
    prefer the one whose role class matches.  Falls back to the sole message on
    the page -- the seeded conversation has exactly one, so in the normal case
    even a locator miss resolves correctly.
    """
    role_class = "human" if role == "human" else "ai"
    return f"""
        (() => {{
            const nodes = [...document.querySelectorAll('.message')];
            if (!nodes.length) return null;
            const LOC = {json.dumps(locators)};
            let cands = nodes;
            if (LOC.length) {{
                const hit = nodes.filter(
                    n => LOC.some(w => (n.innerText || '').includes(w)));
                if (hit.length) cands = hit;
            }}
            const byRole = cands.filter(
                n => n.classList.contains({json.dumps(role_class)}));
            return byRole[0] || cands[0] || null;
        }})()
    """


def build_presence_predicate(locators: list[str], role: str) -> str:
    """Our message exists and holds some text (not just the lazy placeholder)."""
    return f"""() => {{
        const el = {build_locator_js(locators, role)};
        if (!el) return false;
        const t = (el.innerText || '').trim();
        return t.length > 0 && t !== '\u2026';
    }}"""


def build_rendered_predicate(locators: list[str], role: str,
                             require_math: bool) -> str:
    """The deferred mount completed and, when math was requested, typeset.

    The math gate is emitted CONDITIONALLY rather than as a runtime
    ``if (false)`` branch.  A dead branch works at runtime but leaves the
    ``.katex`` selector present in the predicate for every document, so
    "this prose render does not wait on math" becomes unassertable -- the
    only way to check it would be to execute the JS.  Emitting only the
    clause that applies keeps the contract statically inspectable.
    """
    math_gate = (
        "        if (!el.querySelector('.katex')) return false;\n"
        if require_math else ""
    )
    return f"""() => {{
        const el = {build_locator_js(locators, role)};
        if (!el) return false;
        const t = (el.innerText || '').trim();
        if (!t || t === '\u2026') return false;
        if (el.querySelector('.message-placeholder')) return false;
{math_gate}        return true;
    }}"""


def build_dom_probe(locators: list[str], role: str) -> str:
    """Structural facts about the rendered message.

    Reported alongside the image so a caller has machine-checkable evidence
    next to the pixels: a leaked ``MATH_INLINE`` marker or a ``.katex-error``
    is unambiguous in the DOM and easy to miss by eye at small font sizes.
    """
    return f"""() => {{
        const el = {build_locator_js(locators, role)};
        if (!el) return {{missing: true}};
        const t = el.innerText || '';
        return {{
            katex: el.querySelectorAll('.katex').length,
            katex_error: el.querySelectorAll('.katex-error').length,
            math_fallback: el.querySelectorAll('.math-fallback').length,
            code_blocks: el.querySelectorAll('pre').length,
            tables: el.querySelectorAll('table').length,
            is_lazy_placeholder: t.trim() === '\u2026',
            leaked_math_marker: /MATH_INLINE|MATH_DISPLAY|__MATH_INLINE_/.test(t),
            leaked_encoded_div: /math-display-encoded/.test(el.innerHTML),
            has_chat_chrome: !!el.querySelector('.message-sender'),
            text_len: t.length,
        }};
    }}"""


#: Ceilings on the viewport we will grow to when fitting a tall/wide message
#: for capture.  A pathological document must not drive an unbounded off-screen
#: surface; ``clamp_png_dimensions`` still bounds the emitted PNG independently
#: of these.
MAX_CAPTURE_WIDTH = 4000
MAX_CAPTURE_HEIGHT = 24000


def build_capture_prep_js(locators: list[str], role: str) -> str:
    """JS that unclips the message so a full-element screenshot is possible.

    The element-screenshot in Stage 5 is the SOLE capture path, and Playwright
    captures *painted* pixels.  When our ``.message`` lives inside a
    fixed-height ``overflow`` scroll container (the chat message list), only the
    container's visible band is painted -- so a message taller than the band is
    truncated to it (the ~1500px vertical ceiling) and the remainder of the
    canvas is padded blank, while a horizontally offset one is shaved on its
    leading edge.  Both are silent data loss with no overflow affordance in the
    picture.  The theme-dependence of the left-edge loss is just a per-theme
    layout-width difference deciding whether the clipping container scrolls
    horizontally for identical input.

    The fix is to make the whole element paint: walk the ancestor chain up to
    the document element and drop any overflow clipping and any height cap
    (``max-height``/fixed ``height``), then scroll the element to the origin and
    report its full unclipped size (plus its far edges from the page origin) so
    the caller can grow the viewport to contain it.  This does not touch the
    ``.message`` element itself or any colour/layout of the content -- it only
    removes the clip that hides pixels that were already rendered.  Returns
    ``null`` when the element is gone.
    """
    return f"""() => {{
        const el = {build_locator_js(locators, role)};
        if (!el) return null;
        let n = el.parentElement;
        while (n && n !== document.documentElement) {{
            const cs = getComputedStyle(n);
            if (cs.overflow !== 'visible' || cs.overflowX !== 'visible'
                    || cs.overflowY !== 'visible') {{
                n.style.setProperty('overflow', 'visible', 'important');
            }}
            if (cs.maxHeight && cs.maxHeight !== 'none') {{
                n.style.setProperty('max-height', 'none', 'important');
            }}
            n = n.parentElement;
        }}
        el.scrollIntoView({{block: 'start', inline: 'start'}});
        const r = el.getBoundingClientRect();
        return {{
            width: Math.ceil(r.width),
            height: Math.ceil(r.height),
            right: Math.ceil(r.right + window.scrollX),
            bottom: Math.ceil(r.bottom + window.scrollY),
            left: Math.floor(r.left + window.scrollX),
            top: Math.floor(r.top + window.scrollY),
        }};
    }}"""


# -- server-side seeding (sync; call via run_in_threadpool) ----------------

def resolve_project_id(workspace_path: Optional[str] = None) -> str:
    """Map a filesystem path to a project id.

    Tried in order: the caller's workspace, ``ZIYA_USER_CODEBASE_DIR``, cwd.
    Raises with every path attempted rather than silently seeding into an
    unrelated project -- a chat created in the wrong project would never be
    found by the frontend and the failure would look like a render timeout.
    """
    from app.storage.projects import ProjectStorage
    from app.utils.paths import get_ziya_home

    storage = ProjectStorage(get_ziya_home())
    tried: list[str] = []
    for candidate in (workspace_path,
                      os.environ.get("ZIYA_USER_CODEBASE_DIR"),
                      os.getcwd()):
        if not candidate:
            continue
        tried.append(candidate)
        project = storage.get_by_path(candidate)
        if project:
            return project.id

    # Distinguish "this path is not a registered project" from "no project is
    # READABLE at all".  With at-rest encryption enabled, a caller lacking key
    # material gets zero readable projects and every path therefore misses --
    # reporting that as an unregistered path sends the reader to the wrong
    # problem entirely.  This is not hypothetical: it is what a process outside
    # the server (which holds the KEK) actually sees.
    readable = 0
    try:
        readable = len(storage.list() or [])
    except Exception:  # pragma: no cover - defensive
        readable = 0
    if readable == 0:
        raise RuntimeError(
            "Could not resolve a Ziya project: no project records are "
            f"readable under {get_ziya_home()}. If at-rest encryption is "
            "enabled, the caller needs the same key material as the server "
            "(ZIYA_ENCRYPTION_KEY or a reachable KEK provider). "
            f"Paths tried: {tried}."
        )
    raise RuntimeError(
        "Could not resolve a Ziya project for the chat-message renderer. "
        f"Paths tried: {tried} ({readable} project(s) readable, none matching). "
        "Open the project in Ziya once so it is registered, or pass an "
        "explicit workspace path."
    )


def _chat_storage(project_id: str) -> Any:
    from app.storage.chats import ChatStorage
    from app.utils.paths import get_project_dir
    return ChatStorage(get_project_dir(project_id))


def seed_conversation(project_id: str, markdown: str,
                      role: str = "assistant") -> str:
    """Create a chat holding exactly one message; return its id."""
    from app.models.chat import ChatCreate, ChatUpdate, Message

    storage = _chat_storage(project_id)
    chat = storage.create(
        ChatCreate(title=f"{CHAT_TITLE_PREFIX} {uuid.uuid4().hex[:8]}")
    )
    now_ms = int(time.time() * 1000)
    updated = storage.update(chat.id, ChatUpdate(messages=[
        Message(id=f"shot-{now_ms}-{uuid.uuid4().hex[:6]}",
                role=role, content=markdown, timestamp=now_ms),
    ]))
    if not updated or len(getattr(updated, "messages", []) or []) != 1:
        raise RuntimeError(
            f"Seeded chat {chat.id} did not persist its message; the "
            "ChatUpdate path may have changed."
        )
    return chat.id


def delete_conversation(project_id: str, chat_id: str) -> bool:
    """Best-effort cleanup.  Never raises -- a cleanup failure must not mask
    the render result the caller is waiting on."""
    try:
        return bool(_chat_storage(project_id).delete(chat_id))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("chat-screenshot cleanup failed for %s: %s", chat_id, exc)
        return False


def sweep_orphans(project_id: str, max_age_ms: int = ORPHAN_MAX_AGE_MS) -> int:
    """Delete stale seeded chats left behind by a crashed render."""
    try:
        storage = _chat_storage(project_id)
        cutoff = int(time.time() * 1000) - max_age_ms
        removed = 0
        for entry in (storage.list() or []):
            title = getattr(entry, "title", "") or ""
            created = getattr(entry, "createdAt", None)
            if not title.startswith(CHAT_TITLE_PREFIX):
                continue
            if isinstance(created, int) and created > cutoff:
                continue
            if storage.delete(getattr(entry, "id", "")):
                removed += 1
        if removed:
            logger.info("chat-screenshot swept %d orphaned seeded chat(s)", removed)
        return removed
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("chat-screenshot orphan sweep skipped: %s", exc)
        return 0


# -- the render ------------------------------------------------------------

async def render_chat_message(
    definition: str,
    *,
    role: str = "assistant",
    theme: str = "light",
    server_port: int = 6969,
    workspace_path: Optional[str] = None,
    timeout_ms: int = 60_000,
    viewport_width: int = 1400,
    viewport_height: int = 1600,
) -> tuple[bytes, dict[str, Any]]:
    """Render ``definition`` through the real chat UI and return (png, diag).

    Raises RuntimeError only when no image could be produced at all (the app
    never mounted, or our message never appeared).  A render that completes but
    looks wrong returns normally -- the picture is the finding.
    """
    from starlette.concurrency import run_in_threadpool

    from app.services.diagram_renderer import (
        clamp_png_dimensions, get_diagram_renderer,
    )

    project_id = await run_in_threadpool(resolve_project_id, workspace_path)
    chat_id = await run_in_threadpool(
        seed_conversation, project_id, definition, role,
    )

    locators = derive_locators(definition)
    require_math = expects_math(definition)
    diag: dict[str, Any] = {
        "project_id": project_id,
        "chat_id": chat_id,
        "locators": locators,
        "require_math": require_math,
        "rendered_confirmed": False,
    }
    console: list[str] = []
    errors: list[str] = []

    renderer = await get_diagram_renderer(server_port=server_port)
    page = await renderer.acquire_page(
        viewport_width=viewport_width, viewport_height=viewport_height,
    )
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}"[:300]))
    page.on("pageerror", lambda e: errors.append(str(e)[:300]))

    try:
        await page.add_init_script(
            build_seed_script(project_id, chat_id, theme == "dark")
        )
        base = renderer.base_url or f"http://localhost:{server_port}"
        await page.goto(f"{base}/", wait_until="domcontentloaded",
                        timeout=timeout_ms)

        # Stage 1: the SPA shell mounted at all.
        try:
            await page.wait_for_selector(".message, #root > *", timeout=timeout_ms)
        except Exception as exc:
            diag["stage_failed"] = "shell-mount"
            diag["console_tail"] = console[-15:]
            diag["pageerrors"] = errors[:5]
            raise RuntimeError(
                f"The Ziya SPA never mounted at {base}/ ({exc!r}). The "
                f"chat-message renderer needs the app itself, not just the "
                f"API, to be serving."
            ) from exc

        # Stage 2: bootstrap selected OUR conversation and loaded its body.
        # This is the step that depends on the sessionStorage restore, so it
        # gets its own error: a failure here is a bootstrap problem, not a
        # rendering one, and conflating the two sends debugging into the
        # markdown pipeline for no reason.
        try:
            await page.wait_for_function(
                build_presence_predicate(locators, role), timeout=timeout_ms,
            )
        except Exception as exc:
            diag["stage_failed"] = "conversation-restore"
            diag["console_tail"] = console[-15:]
            diag["pageerrors"] = errors[:5]
            try:
                diag["dom_state"] = await page.evaluate(
                    """() => ({
                        messages: document.querySelectorAll('.message').length,
                        body_head: (document.body.innerText || '').slice(0, 300),
                        ss_conv: sessionStorage.getItem('ZIYA_CURRENT_CONVERSATION_ID'),
                        ls_project: localStorage.getItem('ZIYA_LAST_PROJECT_ID'),
                    })"""
                )
            except Exception:
                pass
            raise RuntimeError(
                f"The seeded conversation never rendered ({exc!r}). The app "
                f"loaded but did not select/load chat {chat_id}."
            ) from exc

        # Stage 3: scroll it into view so the production IntersectionObserver
        # in LazyMarkdownRenderer fires and the deferred mount happens the way
        # it does for a user.
        try:
            await page.evaluate(f"""() => {{
                const el = {build_locator_js(locators, role)};
                if (el) el.scrollIntoView({{block: 'center'}});
            }}""")
        except Exception as exc:  # pragma: no cover - defensive
            diag["scroll_error"] = repr(exc)

        # Stage 4: wait for real output.  A timeout here is NOT fatal: the
        # screenshot of a failed render is exactly what the caller needs.
        try:
            await page.wait_for_function(
                build_rendered_predicate(locators, role, require_math),
                timeout=timeout_ms,
            )
            diag["rendered_confirmed"] = True
        except Exception:
            diag["render_wait_timeout"] = True
            logger.warning(
                "chat-message render did not confirm (math expected=%s); "
                "screenshotting anyway so the defect is visible",
                require_math,
            )

        await page.wait_for_timeout(SETTLE_MS)

        # Stage 5a: unclip the message so the element screenshot captures the
        # WHOLE thing.  Playwright captures painted pixels, and a message inside
        # a fixed-height overflow scroll container is painted only for the
        # visible band -- truncating a tall render to the ~1500px ceiling and
        # shaving a horizontally offset one on the left, with the rest padded
        # blank.  Drop ancestor clipping/height caps and grow the viewport to
        # contain the full element, then re-anchor it at the origin.  Non-fatal:
        # a failure here degrades to the old (possibly clipped) capture rather
        # than losing the image entirely.
        try:
            metrics = await page.evaluate(build_capture_prep_js(locators, role))
            if metrics and metrics.get("width") and metrics.get("height"):
                fit_w = min(MAX_CAPTURE_WIDTH,
                            max(viewport_width, int(metrics["right"]) + 8))
                fit_h = min(MAX_CAPTURE_HEIGHT,
                            max(viewport_height, int(metrics["bottom"]) + 8))
                await page.set_viewport_size({"width": fit_w, "height": fit_h})
                # The resize reflows layout; re-anchor the message at the
                # top-left so element.screenshot's own scroll-into-view cannot
                # push it partly off the (now larger) viewport.
                await page.evaluate(f"""() => {{
                    const el = {build_locator_js(locators, role)};
                    if (el) el.scrollIntoView({{block: 'start', inline: 'start'}});
                }}""")
                await page.wait_for_timeout(150)
                diag["capture_fit"] = {"viewport_width": fit_w,
                                       "viewport_height": fit_h,
                                       "element": metrics}
        except Exception as exc:  # pragma: no cover - defensive
            diag["capture_prep_error"] = repr(exc)

        # Stage 5: element screenshot of the real message node.
        handle = await page.evaluate_handle(f"() => {build_locator_js(locators, role)}")
        element = handle.as_element()
        if element is None:
            diag["stage_failed"] = "element-resolve"
            raise RuntimeError(
                "Could not resolve the .message element to screenshot even "
                "though its text was present."
            )
        png = await element.screenshot()

        try:
            diag["dom"] = await page.evaluate(build_dom_probe(locators, role))
        except Exception as exc:  # pragma: no cover - defensive
            diag["dom_probe_error"] = repr(exc)

        diag["console_errors"] = [c for c in console if "error" in c.lower()][:8]
        diag["pageerrors"] = errors[:5]
        return clamp_png_dimensions(png), diag

    finally:
        try:
            await page.close()
        except Exception:  # pragma: no cover - defensive
            pass
        await run_in_threadpool(delete_conversation, project_id, chat_id)
        await run_in_threadpool(sweep_orphans, project_id)
