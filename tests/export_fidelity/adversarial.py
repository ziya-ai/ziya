"""
SHARED adversarial fixtures for the export-fidelity apparatus.

These are the beyond-the-canonical-fixture stressors that the final Card-I
validation exercised and that Cards II/III should also run against their own
backends.  They live here (not in a PDF-only location) so every card imports
the SAME stressors and the SAME survival markers.

Each builder returns a conversation in the persisted Message shape and pairs
with a list of UNIQUE markers that MUST survive into any faithful export's
extracted text.  A marker that is present in the rendered DOM but missing from
the exported text is a silent content loss (the class of defect PDF-09, the
horizontal-overflow clip, belongs to).

Rationale for each stressor:

  * tall_figure         — a diagram taller than one page must be scaled to fit
                          (not clipped) and must not strand its heading.
  * wide_table          — a table far wider than the page must not silently drop
                          its right-hand columns off the margin.
  * long_unbroken_line  — a single code line longer than the content width must
                          WRAP, not be clipped at the right margin (PDF-09 fix:
                          print.css `pre { white-space: pre-wrap }`).
  * many_messages       — a 30+ message conversation must paginate sanely with a
                          single header at the top and a single footer at the end.
  * malformed_diagram   — a broken diagram spec must degrade to VISIBLE error
                          content, never a blank region or a failed export.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def tall_figure() -> Tuple[List[Dict[str, Any]], List[str]]:
    tall = " --> ".join(f"TALLADVMRK{i}[TALLADVMRK{i}]" for i in range(1, 60))
    msgs = [
        {"id": "h", "role": "human", "content": "ADVTALL_PROMPT show a very tall diagram", "timestamp": 1},
        {"id": "a", "role": "assistant",
         "content": "ADVTALL_INTRO before a figure taller than a full page:\n\n"
                    "```mermaid\ngraph TD\n  " + tall + "\n```\n\nADVTALL_AFTER closing line.",
         "timestamp": 2},
    ]
    return msgs, ["ADVTALL_INTRO", "TALLADVMRK1", "TALLADVMRK59", "ADVTALL_AFTER"]


def wide_table() -> Tuple[List[Dict[str, Any]], List[str]]:
    cols = 20
    header = "| " + " | ".join(f"WCOL{c}" for c in range(cols)) + " |"
    sep = "| " + " | ".join(["---"] * cols) + " |"
    row = "| " + " | ".join(f"WIDECELL_{c}_xxxxxxxx" for c in range(cols)) + " |"
    msgs = [
        {"id": "h", "role": "human", "content": "ADVWIDE_PROMPT wide table", "timestamp": 1},
        {"id": "a", "role": "assistant",
         "content": "ADVWIDE_INTRO wide table:\n\n" + header + "\n" + sep + "\n" + row +
                    "\n\nADVWIDE_CLOSING.",
         "timestamp": 2},
    ]
    # NOTE: the rightmost cell marker (WIDECELL_19) is the survival probe — it is
    # the one a right-margin clip drops.  Callers that KNOW the table-clip is not
    # yet fixed (PDF-09 table half) may assert on the left/closing markers only.
    return msgs, ["ADVWIDE_INTRO", "WIDECELL_0", "WIDECELL_19", "ADVWIDE_CLOSING"]


def long_unbroken_line() -> Tuple[List[Dict[str, Any]], List[str]]:
    long_line = "x = " + " + ".join(f"LONGTOK{i}" for i in range(80)) + "  # LONGLINE_END_MARK"
    msgs = [
        {"id": "h", "role": "human", "content": "ADVLINE_PROMPT long unbroken code line", "timestamp": 1},
        {"id": "a", "role": "assistant",
         "content": "ADVLINE_INTRO a very long unbroken code line:\n\n```python\n" + long_line +
                    "\nSHORTLINE_AFTER = 1\n```\n\nADVLINE_CLOSING.",
         "timestamp": 2},
    ]
    # LONGTOK79 + LONGLINE_END_MARK are past the content margin: they only survive
    # if the line WRAPS instead of clipping (the PDF-09 pre-wrap fix).
    return msgs, ["ADVLINE_INTRO", "LONGTOK0", "LONGTOK79", "LONGLINE_END_MARK", "ADVLINE_CLOSING"]


def many_messages(n: int = 32) -> Tuple[List[Dict[str, Any]], List[str]]:
    msgs = []
    for i in range(n):
        role = "human" if i % 2 == 0 else "assistant"
        body = (f"MSGMRK_{i:03d} message number {i}. " +
                ("Here is code:\n\n```python\nv_{0} = {0}\n```\n".format(i) if i % 3 == 0 else
                 "A prose paragraph long enough to take a couple of lines and exercise the flow "
                 "across many messages so header/footer and breaks are tested at scale."))
        msgs.append({"id": f"m{i}", "role": role, "content": body, "timestamp": i + 1})
    return msgs, [f"MSGMRK_{0:03d}", f"MSGMRK_{n-1:03d}"]


def malformed_diagram() -> Tuple[List[Dict[str, Any]], List[str]]:
    msgs = [
        {"id": "h", "role": "human", "content": "ADVBAD_PROMPT malformed diagram", "timestamp": 1},
        {"id": "a", "role": "assistant",
         "content": "ADVBAD_INTRO a deliberately broken mermaid spec follows:\n\n"
                    "```mermaid\ngraph TD\n  A --> \n  ((((( totally invalid ]]]] syntax @@@ \n"
                    "  --> --> -->\n```\n\nADVBAD_AFTER text that must still render after the bad diagram.",
         "timestamp": 2},
    ]
    # Both prose markers must survive; the renderer must show an error panel in
    # between (asserted via the presence of an error string in the extracted
    # text, e.g. "Parsing Error"/"error") rather than a blank region.
    return msgs, ["ADVBAD_INTRO", "ADVBAD_AFTER"]


def xss_attempt() -> Tuple[List[Dict[str, Any]], List[str]]:
    """A conversation whose content contains XSS payloads (Card II security).

    Exercises the three payload classes the exporter hardening (PenPal #51/#116)
    must neutralize in ANY export mode:
      * a raw ``<script>`` tag in prose;
      * an ``<img onerror=...>`` handler in prose;
      * a ``[label](javascript:...)`` markdown link.
    Each payload calls ``window.__ziya_mark_xss()`` — the canary the HTML
    backend injects — so if any executes on open, ``check_xss_neutralized``
    sees ``xss_fired=True``.  A faithful export renders them as inert text.

    The survival markers are the surrounding PROSE (which must still appear),
    NOT the payloads (which must be neutralized, i.e. shown escaped as text).
    """
    msgs = [
        {"id": "h", "role": "human",
         "content": "ADVXSS_PROMPT please echo this content back.", "timestamp": 1},
        {"id": "a", "role": "assistant",
         "content": (
             "ADVXSS_INTRO here is some untrusted content:\n\n"
             "A script tag: <script>window.__ziya_mark_xss&&window.__ziya_mark_xss()</script> end.\n\n"
             "An image handler: <img src=x onerror=\"window.__ziya_mark_xss&&window.__ziya_mark_xss()\"> end.\n\n"
             "A bad link: [click me](javascript:window.__ziya_mark_xss&&window.__ziya_mark_xss()) end.\n\n"
             "ADVXSS_CLOSING done."
         ),
         "timestamp": 2},
    ]
    return msgs, ["ADVXSS_INTRO", "ADVXSS_CLOSING"]


ALL_ADVERSARIAL = {
    "tall_figure": tall_figure,
    "wide_table": wide_table,
    "long_unbroken_line": long_unbroken_line,
    "many_messages": many_messages,
    "malformed_diagram": malformed_diagram,
    "xss_attempt": xss_attempt,
}
