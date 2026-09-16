"""Tier 1 — render smoke over the committed graphics corpus.

One test per (spec, theme).  Each renders through the SAME dispatch the
render_diagram tool uses — plugin types via the /render harness, LaTeX
types via latex_renderer, chat-message via the real chat UI — so a pass
here means what a user would see, not what a unit test of a fixup
function infers.

Invariants (the ``renders`` / ``no_console_errors`` / ``ink_present`` tags
in tests/gfx_corpus/<engine>/expectations.json):

  renders            the render reached a terminal 'complete' state — no
                     timeout, no data-error, no exception.
  no_console_errors  nothing the in-page console classified as an error.
                     Warnings are allowed: they are the fixup layer doing its
                     job (auto-quote repair, ELK fallback) and are reported,
                     not failed.
  ink_present        the PNG is not blank: some pixels differ materially
                     from the dominant (background) colour.  This is the
                     cheapest possible "content-lost" check and it catches the
                     exact failure that produced the 32-byte backlog era of
                     `render-empty` / `viewport-crop-content-lost` defects.

``visual:*`` tags are recorded by the ledger for a later measured tier and
are ignored here — a smoke run must not fail on an invariant it cannot
measure.

Skipped unless ``--render`` is passed; see conftest for scope/engine/port.
"""

from __future__ import annotations

import io
from typing import Any, Dict, Tuple

import pytest

pytestmark = [pytest.mark.render, pytest.mark.timeout(120)]

# Invariants this tier can evaluate.  The ledger's DEFAULT_INVARIANTS must be
# a subset of these keys (tests/test_gfx_ledger.py pins the names).
CHECKS = ("renders", "no_console_errors", "ink_present")

# Fraction of pixels that must differ from the background for a render to
# count as having content.  0.2% of a 1280x960 capture is ~2,400 px — a
# single short label clears it; a blank canvas or a lone 50px artifact
# (the D-151 failure mode) does not.
INK_FLOOR = 0.002

LATEX_TYPES = {"tikz", "circuitikz", "chemfig", "tikz-cd"}
CHAT_TYPES = {"chat-message", "chat-markdown", "markdown"}


# ── dispatch ─────────────────────────────────────────────────────────────

async def _render_async(renderer, spec: Dict[str, Any], theme: str, port: int,
                        timeout_ms: int) -> Tuple[bytes, Dict[str, Any]]:
    """(png_bytes, diagnostics) through the production dispatch for the type."""
    dtype = str(spec.get("type", "")).strip().lower()
    definition = spec.get("definition")
    if not isinstance(definition, str):
        import json
        definition = json.dumps(definition)

    if dtype in CHAT_TYPES:
        from app.utils.chat_screenshot import render_chat_message
        png, diag = await render_chat_message(
            definition, theme=theme, server_port=port,
            timeout_ms=max(timeout_ms, 60_000),
        )
        return png, {"console_errors": list(diag.get("console_errors") or []),
                     "console_warnings": [], "dom": diag.get("dom")}

    if dtype in LATEX_TYPES:
        import asyncio
        from app.services.latex_renderer import latex_renderer
        result = await asyncio.get_running_loop().run_in_executor(
            None, lambda: latex_renderer.render(
                dtype, definition, "png", False, theme, None, None))
        if not result.ok:
            raise RuntimeError(
                f"LaTeX render failed ({result.error_kind}): {result.error}\n"
                f"{result.log_excerpt[-1500:]}")
        return result.content, {"console_errors": [],
                                "console_warnings": list(result.warnings or [])}

    png, diag = await renderer.render_diagram_with_diagnostics(
        {"type": dtype, "definition": definition, "theme": theme},
        format="png", timeout_ms=timeout_ms,
    )
    return png, diag


def _render(renderer_fixture, spec, theme, port, timeout_ms):
    loop, renderer = renderer_fixture
    return loop.run_until_complete(
        _render_async(renderer, spec, theme, port, timeout_ms))


# ── invariants ───────────────────────────────────────────────────────────

def ink_fraction(png: bytes) -> float:
    """Fraction of pixels far from the image's dominant colour.

    Dominant colour rather than a fixed white/dark: the capture background
    is theme-dependent, and a hardcoded-light-background diagram on a dark
    page is *content* for this tier's purposes (the contrast tier judges
    whether it is good content).
    """
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGB")
    # Downsample: this is a blank-vs-not test, not a measurement.
    im.thumbnail((320, 320))
    px = list(im.getdata())
    if not px:
        return 0.0
    # Quantize to 16 levels/channel so anti-aliasing does not fragment the
    # background into many near-identical colours.
    q = [(r >> 4, g >> 4, b >> 4) for r, g, b in px]
    from collections import Counter
    bg = Counter(q).most_common(1)[0][0]
    far = sum(1 for c in q if sum(abs(a - b) for a, b in zip(c, bg)) >= 3)
    return far / len(px)


# ── the test ─────────────────────────────────────────────────────────────

def test_corpus_spec_renders(corpus_case, renderer, render_port, render_timeout_ms,
                             chat_project_status):
    engine, spec_id, spec, expectations, theme = corpus_case
    wanted = [i for i in expectations.get("invariants", []) if i in CHECKS]
    if not wanted:
        pytest.skip(f"{spec_id}: no smoke-tier invariants recorded")
    if str(spec.get("type", "")).strip().lower() in CHAT_TYPES and not chat_project_status[0]:
        pytest.skip(chat_project_status[1])

    origins = ", ".join(sorted({o.get("origin", "?") for o in expectations.get("origins", [])}))
    context = f"{engine}/{spec_id} theme={theme} guards: {origins}"

    # renders — an exception here IS the failure; attach the context.
    try:
        png, diag = _render(renderer, spec, theme, render_port, render_timeout_ms)
    except Exception as exc:  # noqa: BLE001
        if "renders" in wanted:
            pytest.fail(f"[renders] {context}\n{exc}")
        raise

    failures = []
    if "no_console_errors" in wanted:
        errs = list(diag.get("console_errors") or [])
        if errs:
            failures.append(f"[no_console_errors] {len(errs)} console error(s):\n  "
                            + "\n  ".join(e[:300] for e in errs[:5]))

    if "ink_present" in wanted:
        assert png and len(png) > 8, f"[ink_present] {context}: empty image bytes"
        frac = ink_fraction(png)
        if frac < INK_FLOOR:
            failures.append(f"[ink_present] only {frac:.4%} of pixels differ from the "
                            f"background (floor {INK_FLOOR:.2%}) — blank or lost content")

    if failures:
        pytest.fail(context + "\n" + "\n".join(failures))
