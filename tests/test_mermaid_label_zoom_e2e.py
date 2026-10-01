"""
E2E: mermaid htmlLabels survive the narrow-viewport body zoom.

Field report (Sep 2026): on Firefox and Chromium windows narrower than ~1300
CSS px, every multi-line mermaid flowchart label rendered in Ziya's chat
showed only its first line, vertically centred in a two-line-tall node with
a sliver of line 2 beneath it.  Cause: utils/uiScale.ts applies CSS `zoom`
on <body> at narrow widths; mermaid measures its htmlLabels with
getBoundingClientRect() (viewport px) in a scratch div appended to that
zoomed body and writes the numbers back as CSS px, so each foreignObject
came out short by the zoom factor (134x16 for a 200x48 label at z = 0.67).

The /render harness is exempt from UI zoom, so the gfx sweep never sees
this.  This test drives the REAL app at "/" with a 900-px viewport (which
snaps to zoom 0.67), seeds a one-message conversation through the HTTP API,
and asserts on the rendered DOM:

  * body zoom really is < 1 (the hazard was exercised, not skipped);
  * for every flowchart node label, the foreignObject's own height/width
    attributes cover the inner HTML label's unzoomed CSS size (offsetHeight /
    offsetWidth are CSS px, immune to zoom) -- this is the assertion that
    FAILS against unpatched code (16 < 48);
  * the label <p> keeps mermaid's own white-space rather than Ziya's prose
    reset, so a label measured as one line is not re-wrapped.

Requires playwright + chromium and a running Ziya server on :6969.
Integration-marked; skipped by default.
"""

import json
import os
import time
import urllib.request

import pytest

try:
    import playwright.async_api  # noqa: F401
    _HAVE_PW = True
except ImportError:  # pragma: no cover
    _HAVE_PW = False

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _HAVE_PW, reason="playwright not installed"),
]

BASE = f"http://127.0.0.1:{os.environ.get('ZIYA_PORT', '6969')}"
NARROW_WIDTH = 900  # 900 / 1300 -> nearest step 0.67

MARKDOWN = (
    "Plan:\n\n```mermaid\nflowchart TB\n"
    '    START["Hardcoded literal: Amazon Cognito"]\n'
    '    START --> Q{"What is in scope for<br/>the entitization pass?"}\n'
    '    Q -->|"Brandon\'s bet:<br/>governance + house style"| B["Entitize AND restyle<br/>first-mention per topic"]\n'
    '    B --> BR["Rendered text may shift<br/>short literal becomes long entity"]\n'
    "```\n"
)

PROBE_JS = """
() => {
  const svg = document.querySelector('.mermaid-wrapper svg');
  if (!svg) return { error: 'no svg' };
  const labels = [];
  svg.querySelectorAll('.node foreignObject').forEach(fo => {
    const div = fo.firstElementChild;
    const p = div && div.querySelector('p');
    labels.push({
      text: (div && div.textContent || '').trim().slice(0, 40),
      foW: fo.width.baseVal.value,
      foH: fo.height.baseVal.value,
      divW: div ? div.offsetWidth : null,
      divH: div ? div.offsetHeight : null,
      divWhiteSpace: div ? getComputedStyle(div).whiteSpace : null,
      pWhiteSpace: p ? getComputedStyle(p).whiteSpace : null,
    });
  });
  return {
    bodyZoom: getComputedStyle(document.body).zoom,
    uiZoomVar: getComputedStyle(document.documentElement).getPropertyValue('--ui-zoom').trim(),
    measureHostsLeft: document.querySelectorAll('.ziya-unzoomed-measure-host').length,
    labels,
  };
}
"""


def _json(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def _server_up() -> bool:
    try:
        _json("GET", "/api/v1/projects")
        return True
    except Exception:
        return False


def _pick_project() -> str:
    projects = _json("GET", "/api/v1/projects")
    cwd = os.path.realpath(os.getcwd())
    for p in projects:
        root = p.get("rootPath") or p.get("path") or ""
        if root and os.path.realpath(root) == cwd:
            return p["id"]
    return projects[0]["id"]


@pytest.fixture
def seeded_chat():
    if not _server_up():
        pytest.skip(f"no Ziya server at {BASE}")
    pid = _pick_project()
    chat = _json("POST", f"/api/v1/projects/{pid}/chats",
                 {"title": "e2e mermaid label zoom (auto-deleted)"})
    now = int(time.time() * 1000)
    _json("POST", f"/api/v1/projects/{pid}/chats/{chat['id']}/messages",
          {"id": f"e2e-{now}", "role": "assistant", "content": MARKDOWN,
           "timestamp": now})
    try:
        yield pid, chat["id"]
    finally:
        try:
            _json("DELETE", f"/api/v1/projects/{pid}/chats/{chat['id']}")
        except Exception:
            pass


async def _probe(pid: str, cid: str, width: int, zoom_override: str | None):
    from playwright.async_api import async_playwright

    seed = (
        f"localStorage.setItem('ZIYA_LAST_PROJECT_ID', {json.dumps(pid)});"
        "localStorage.setItem('ZIYA_THEME_PREFERENCE', 'true');"
        f"sessionStorage.setItem('ZIYA_CURRENT_CONVERSATION_ID', {json.dumps(cid)});"
    )
    if zoom_override is not None:
        seed += f"localStorage.setItem('ZIYA_UI_ZOOM', {json.dumps(zoom_override)});"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": width, "height": 1000})
            await page.add_init_script(seed)
            await page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_selector(".mermaid-wrapper svg .node foreignObject",
                                         timeout=60_000)
            # Let the post-render effective-font sizing settle (it re-runs at 500ms).
            await page.wait_for_timeout(1500)
            return await page.evaluate(PROBE_JS)
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.timeout(180)
async def test_mermaid_labels_not_clipped_under_body_zoom(seeded_chat):
    pid, cid = seeded_chat
    r = await _probe(pid, cid, NARROW_WIDTH, None)
    assert "error" not in r, r

    # Positive assertion that the hazard was exercised: the app really zoomed.
    assert r["bodyZoom"] not in ("", "1", "normal"), (
        f"body zoom was {r['bodyZoom']!r} at {NARROW_WIDTH}px; the narrow-viewport "
        "zoom did not engage, so this test proves nothing. Check uiScale.ts "
        "steps or a ZIYA_UI_ZOOM override in the profile."
    )
    assert float(r["bodyZoom"]) < 1

    assert r["labels"], "no node labels found"
    bad = []
    for lab in r["labels"]:
        # The foreignObject is the clip box; the inner div is the label's real
        # (unzoomed CSS px) extent. Unpatched: foH 16 vs divH 48.
        if lab["foH"] + 1 < lab["divH"] or lab["foW"] + 1 < lab["divW"]:
            bad.append(lab)
    assert not bad, "foreignObject smaller than its label (clipped):\n" + \
        "\n".join(f"  {b['text']!r}: fo {b['foW']:.0f}x{b['foH']:.0f} vs div "
                  f"{b['divW']}x{b['divH']}" for b in bad)

    # Multi-line labels must actually be two lines tall (not squashed to one).
    two_line = [l for l in r["labels"] if l["divH"] and l["divH"] > 30]
    assert two_line, f"expected multi-line labels, got {r['labels']}"

    # CSS seam: Ziya's prose reset (.message .message-content p {white-space:
    # normal}) must not override mermaid's nowrap on the label paragraph.
    for lab in r["labels"]:
        if lab["pWhiteSpace"] is not None:
            assert lab["pWhiteSpace"] == lab["divWhiteSpace"], lab

    # Hygiene: measurement hosts are transient.
    assert r["measureHostsLeft"] == 0


@pytest.mark.asyncio
@pytest.mark.timeout(180)
async def test_mermaid_labels_unchanged_with_zoom_off(seeded_chat):
    """Control: with the zoom disabled the geometry is the known-good baseline,
    and the patched path produces the SAME label boxes as that baseline."""
    pid, cid = seeded_chat
    base = await _probe(pid, cid, NARROW_WIDTH, "off")
    assert base["bodyZoom"] in ("", "1", "normal"), base["bodyZoom"]
    zoomed = await _probe(pid, cid, NARROW_WIDTH, None)
    assert float(zoomed["bodyZoom"]) < 1

    def boxes(r):
        return sorted((l["text"], round(l["foW"]), round(l["foH"])) for l in r["labels"])

    # Label geometry is computed in CSS px either way; zoom only scales the
    # finished picture. Allow 1px of rounding.
    for (t1, w1, h1), (t2, w2, h2) in zip(boxes(base), boxes(zoomed)):
        assert t1 == t2
        assert abs(w1 - w2) <= 1, (t1, w1, w2)
        assert abs(h1 - h2) <= 1, (t1, h1, h2)
