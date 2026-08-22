"""
SHARED export-fidelity fixture and measurement helpers.

Used by all three export-fidelity cards:
  * Card I  (PDF)      — renders this conversation to PDF and asserts colours,
                         backgrounds, images, spacing and page breaks.
  * Card II (HTML)     — renders it to self-contained HTML and asserts the same
                         renderer structures + markers survive.
  * Card III (Markdown)— asserts the source-fence / marker round-trips.

Deliberately NOT PDF-specific and NOT in ``tests/pdf_export/``: it lives here
so the sibling cards import the SAME conversation, the SAME marker strings and
the SAME expected-signal constants rather than each inventing their own
fixture.  This module is the shared *contract* across the three cards — the
marker lists below are the promised interface; changing them is a
cross-card-breaking change.

The fixture is a single synthetic-but-realistic conversation, in the exact
persisted ``Message`` shape (``app/storage/chats.py`` -> ``app/models/chat.py``
``Message``: ``id``/``role``/``content``/``timestamp``), that exercises every
reported PDF defect class:

  defect (1) colors absent            -> syntax-highlighted Python (Prism tokens)
  defect (2) renderer images missing  -> three Mermaid diagrams (normal/wide/tall)
  defect (3) text highlighting lost   -> ``==highlighted==`` / <mark> spans
  defect (4) diff add/remove lost     -> a unified diff (add + remove + context)
  defect (5) bizarre white/black space-> a long code block + tall figure
  defect (6) dark not converted       -> the DARK variant (mermaid dark theme
                                         bakes fills into an internal <style>)
  defect (7) nonsensical page breaks  -> long code crossing a boundary, a wide
                                         table, and a heading immediately
                                         followed by a tall figure (orphan case)
Plus a KaTeX math block, a wide table, and a collapsed <details> section.

Every distinctive element carries a UNIQUE MARKER STRING so any export format
can assert its survival independently of styling.
"""
from __future__ import annotations

from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# MARKER STRINGS — the shared contract.
#
# UNIQUE_TEXT_MARKERS: each MUST appear EXACTLY ONCE in the extracted text of a
# fully-rendered export (with ``includeCollapsed=True``, the default).  These
# drive the format-neutral ``content_completeness`` check — a silently dropped
# message or truncated element makes one of these vanish (count 0) or, if the
# renderer duplicates content, appear >1.  Cards II and III assert against this
# SAME dict.  Chosen to be improbable literals so a substring match cannot be
# accidentally satisfied by ordinary prose or by CSS/class names.
# ---------------------------------------------------------------------------
UNIQUE_TEXT_MARKERS: Dict[str, str] = {
    "human_prompt": "MRK_HUMAN_PROMPT_7q3",
    "intro_prose": "MRK_INTRO_PROSE_3k8",
    "code_function": "greet_MRK_fn_5c",       # identifier inside the code block
    "highlight_phrase": "MRK_HILITE_9z1",      # inside ==...== / <mark>
    "diff_context": "MRK_diff_ctx_c2",         # unchanged context line
    "diff_removed": "MRK_diff_del_d1",         # removed line
    "diff_added": "MRK_diff_add_a1",           # added line
    "wide_table_cell": "MRK_TBLCELL_5t2",      # a cell in the wide table
    "details_summary": "MRK_DETAILS_SUM_8d4",  # <details> summary (always shown)
    "details_body": "MRK_DETAILS_BODY_4e6",    # <details> body (shown when expanded)
    "long_code_start": "MRK_LONGCODE_START_1a", # first line of the page-spanning block
    "long_code_end": "MRK_LONGCODE_END_9b",     # last line of the page-spanning block
    "orphan_heading": "MRK_ORPHAN_HEADING_6h5", # heading immediately before a tall figure
    "math_caption": "MRK_MATH_CAP_2m0",         # prose caption next to the KaTeX block
    "closing_prose": "MRK_CLOSING_2f7",
}

# PRESENCE_MARKERS: appear AT LEAST ONCE (not necessarily exactly once — a
# diagram label may be duplicated by the renderer, e.g. an SVG <title> plus a
# visible <text>, and diagram text may or may not survive into a PDF text layer
# at all).  Used by ``image_presence`` reasoning and by Cards that assert a
# diagram *label* is somewhere in the output, without the exactly-once
# constraint.
PRESENCE_MARKERS: Dict[str, str] = {
    "diagram_normal_label": "NodeAlphaMRK",
    "diagram_wide_label": "WideColMRK",
    "diagram_tall_label": "TallRowMRK",
}

# Source fences the markdown card (Card III) must find verbatim in exported
# markdown.  (PDF/HTML render these; markdown round-trips them.)
EXPECTED_MARKDOWN_FENCES: List[str] = ["```python", "```diff", "```mermaid", "$$"]


# TEXT_QUALITY_PHRASES (Card IV — QUAL text_quality check): known source prose
# baked into the canonical fixture that a well-typeset export must yield back
# INTACT when copy-pasted.  Deliberately ligature-prone (ff/fi/fl clusters) and
# multi-word so the check can catch (a) ligature codepoints leaking into the
# text layer instead of the ASCII pair, and (b) dropped inter-word spaces that
# fuse words.  These are FIDELITY probes, distinct from the exactly-once
# presence markers: they are plain English so ordinary extraction returns them
# verbatim from a healthy PDF, and mangles them in a broken one.  The sentence
# is emitted (as ``_text_quality_probe()``) inside the assistant turn.
TEXT_QUALITY_PHRASES: List[str] = [
    "The office workflow is efficient and fluent",
    "affluent scaffolding fulfils the difficult specification",
]


def _text_quality_probe() -> str:
    """One prose sentence packed with ligature clusters + normal word spacing.

    Kept marker-free (no UNIQUE_TEXT_MARKER) so it does not perturb the
    content_completeness exactly-once accounting; its words ARE the probe.
    """
    return (
        "The office workflow is efficient and fluent; the affluent scaffolding "
        "fulfils the difficult specification without waffle."
    )


# ---------------------------------------------------------------------------
# Expected rendered-DOM signals (Cards I PDF + II HTML both render through the
# real MarkdownRenderer, so both should emit these structures).  Selector lists
# are permissive because the exact class depends on renderer version, not card.
# ---------------------------------------------------------------------------
EXPECTED_DOM_SIGNALS: Dict[str, List[str]] = {
    "prism_tokens": ["span.token"],
    "diff_insert": [".diff-code-insert", ".diff-line-insert", '[class*="insert"]'],
    "diff_delete": [".diff-code-delete", ".diff-line-delete", '[class*="delete"]'],
    "katex": [".katex"],
    "diagram": ["svg", ".mermaid-output", "[data-processed]", ".diagram"],
    "highlight": ["mark", ".highlight", "[class*='highlight']"],
    "table": ["table", "<td", "<th"],
    "details": ["<details", "<summary"],
}


# Expected color signals for a rendered (light-theme) capture.  Cards that
# rasterise a render assert these appear; the markdown card ignores them.
# GitHub-light palette the chat UI uses; tolerances absorb antialiasing.
EXPECTED_COLOR_SIGNALS: Dict[str, Dict[str, Any]] = {
    "diff_insert_green": {"rgb": (230, 255, 236), "tol": 24, "min_pixels": 200},
    "diff_delete_red": {"rgb": (255, 235, 233), "tol": 24, "min_pixels": 200},
    "page_is_light": {"rgb": (255, 255, 255), "tol": 8, "min_fraction": 0.5},
    # NOTE (PDF-01 iteration): a "highlight_yellow" min_pixels signal was
    # considered to gate defect #3 independently, but was NOT added: Ziya's
    # renderer has no content-highlight construct (see the highlight marker note
    # above), so such a signal could never be satisfied by any faithful export.
    # #fff176 == (255,241,118) is recorded here for Card II should a real
    # highlight feature ever be added.
}


# ---------------------------------------------------------------------------
# Conversation content builders
# ---------------------------------------------------------------------------
_M = UNIQUE_TEXT_MARKERS
_P = PRESENCE_MARKERS


def _human_content() -> str:
    # NOTE: must not embed any UNIQUE_TEXT_MARKER other than 'human_prompt' —
    # every unique marker must appear exactly once across the WHOLE rendered
    # conversation (human + assistant), which is the content_completeness
    # premise.  So the prompt describes the elements without quoting their
    # markers.
    return (
        f"{_M['human_prompt']}: please show a highlighted function, a diff, three "
        f"diagrams, a wide table, a collapsed section, some math, a long code "
        f"block, and a highlighted phrase."
    )


def _assistant_content() -> str:
    # A long code block whose body is padded so it reliably crosses an A4 page
    # boundary (~50 lines at print font size); first and last lines are marked.
    long_code_lines = [f"    line_{i:03d} = {i} * 2  # filler to force a page break"
                        for i in range(1, 61)]
    long_code_lines[0] = f"    {_M['long_code_start']} = 0  # first line of long block"
    long_code_lines[-1] = f"    {_M['long_code_end']} = 999  # last line of long block"
    long_code = "\n".join(long_code_lines)

    # A wide table (many columns) — exercises horizontal overflow / page fit.
    table_header = "| " + " | ".join(
        [f"Col{c}" for c in range(1, 11)]) + " |"
    table_sep = "| " + " | ".join(["---"] * 10) + " |"
    table_row1 = ("| " + " | ".join(
        [f"{_M['wide_table_cell']}" if c == 1 else f"r1c{c}_data_value"
         for c in range(1, 11)]) + " |")
    table_row2 = ("| " + " | ".join([f"r2c{c}_data_value" for c in range(1, 11)]) + " |")

    # A WIDE mermaid diagram: a long left->right chain overflowing page width.
    wide_nodes = " --> ".join([f"{_P['diagram_wide_label']}{i}[{_P['diagram_wide_label']}{i}]"
                               for i in range(1, 13)])
    # A TALL mermaid diagram: a long top->down chain overflowing one page height.
    tall_nodes = " --> ".join([f"{_P['diagram_tall_label']}{i}[{_P['diagram_tall_label']}{i}]"
                               for i in range(1, 25)])

    return "\n".join([
        f"{_M['intro_prose']}. Here is a Python function with syntax highlighting:",
        "",
        "```python",
        f"def {_M['code_function']}(name: str) -> str:",
        '    """Return a greeting."""',
        "    count = 42",
        '    return f"hi {name} ({count})"',
        "```",
        "",
        # Text-"highlight" marker (defect #3).  NOTE (verified this iteration):
        # Ziya's shared MarkdownRenderer does NOT render a highlight construct —
        # marked/GFM has no `==…==` syntax (renders literal text) and a raw
        # <mark> is HTML-escaped by the renderer's inline-HTML guard to
        # `<span>&lt;mark&gt;</span>`.  The only <mark> in the app is search
        # highlighting, injected as React elements outside markdown.  So there
        # is no content-highlight colour to lose in ANY export; this marker just
        # carries the unique string for the content_completeness contract.  Kept
        # as `==…==` (harmless literal) so the marker still appears exactly once.
        f"A ==({_M['highlight_phrase']})== highlighted phrase and some `inline code`.",
        "",
        "A unified diff (removed, added and context lines):",
        "",
        "```diff",
        "diff --git a/f.py b/f.py",
        "--- a/f.py",
        "+++ b/f.py",
        "@@ -1,3 +1,3 @@",
        f" {_M['diff_context']} = 0",
        f"-{_M['diff_removed']} = 1",
        f"+{_M['diff_added']} = 2",
        "```",
        "",
        "A normal diagram (dark-mode-leak case — mermaid bakes theme colours "
        "into an internal <style>):",
        "",
        "```mermaid",
        "graph LR",
        f"  {_P['diagram_normal_label']}[{_P['diagram_normal_label']}] --> B{{Decision}}",
        "  B -->|yes| C[Do it]",
        "  B -->|no| D[Skip]",
        "```",
        "",
        "A diagram wider than the page:",
        "",
        "```mermaid",
        f"graph LR",
        f"  {wide_nodes}",
        "```",
        "",
        f"### {_M['orphan_heading']}",
        "",
        "A diagram taller than one page (immediately after the heading — the "
        "orphaned-heading case):",
        "",
        "```mermaid",
        "graph TD",
        f"  {tall_nodes}",
        "```",
        "",
        "A wide table:",
        "",
        table_header,
        table_sep,
        table_row1,
        table_row2,
        "",
        "<details>",
        f"<summary>{_M['details_summary']}</summary>",
        "",
        f"Collapsed content: {_M['details_body']} lives inside the details block.",
        "",
        "</details>",
        "",
        f"{_M['math_caption']}, inline math $E = mc^2$ and a block:",
        "",
        "$$\\int_0^1 x^2 \\, dx = \\frac{1}{3}$$",
        "",
        "A long code block that crosses a page boundary:",
        "",
        "```python",
        long_code,
        "```",
        "",
        _text_quality_probe(),
        "",
        f"{_M['closing_prose']}. That is everything.",
    ])


def make_fidelity_conversation() -> List[Dict[str, Any]]:
    """Return the canonical fidelity fixture (light/default variant).

    A list of message dicts matching the persisted ``Message`` shape so they
    can be fed to the /print route, the markdown exporter, or the HTML exporter
    unchanged.
    """
    return [
        {"id": "m1", "role": "human", "content": _human_content(), "timestamp": 1},
        {"id": "m2", "role": "assistant", "content": _assistant_content(), "timestamp": 2},
    ]


def make_fidelity_conversation_dark() -> List[Dict[str, Any]]:
    """Return the DARK-themed-diagram variant.

    Identical content to :func:`make_fidelity_conversation`, but the mermaid
    fences carry an ``%%{init}%%`` dark-theme directive so mermaid bakes DARK
    fills into the SVG's internal ``<style>``.  This is the defect-#6 probe: a
    faithful light-page export must composite these onto white and must NOT
    leave large dark regions on the page.  The unique markers are unchanged, so
    ``content_completeness`` holds across both variants.
    """
    convo = make_fidelity_conversation()
    dark_init = "%%{init: {'theme':'dark'}}%%\n"
    convo[1]["content"] = convo[1]["content"].replace("```mermaid\n", "```mermaid\n" + dark_init)
    return convo


# ---------------------------------------------------------------------------
# EXPORT-HYGIENE fixtures (Card III markdown + Card IV PDF share these).
#
# These probe the two user-reported export-hygiene defects that apply to EVERY
# export format: superseded diffs and live-session UI chrome.  They are kept in
# this SHARED module (not a card-local file) so Card IV asserts against the
# EXACT same marker strings for the PDF path.
#
# SUPERSESSION_MARKERS: a message that diffs the SAME file TWICE in one turn.
# The UI (MarkdownRenderer via diffUtils.findSupersededDiffParts) greys out the
# earlier diff; a faithful export must DROP it.  The pair is deliberately built
# so the TS supersession heuristic (findSupersededDiffIndices) classifies it as
# a genuine supersession, NOT a "sequential pair":
#   * same file path, overlapping hunk range (>50% overlap);
#   * the EARLIER diff adds >1 line (earlierAdds>1) so isSequentialPair() is
#     False — a purely-subtractive-then-additive pair would be treated as two
#     complementary edits and BOTH kept.
# SUPERSEDED_ADD is the added line of the earlier (greyed-out) diff: it MUST be
# ABSENT from a hygienic export.  FINAL_ADD is the added line of the surviving
# diff: it MUST be present.  SUPERSEDE_INTRO/CLOSING bracket the message so a
# check can confirm the surrounding prose was NOT collateral-damaged when the
# stale diff was removed.
# ---------------------------------------------------------------------------
SUPERSESSION_MARKERS: Dict[str, str] = {
    "intro": "MRK_SUPERSEDE_INTRO_5s1",          # prose before the diffs (kept)
    "superseded_add": "MRK_SUPERSEDED_ADD_STALE", # added line of the STALE diff (must be DROPPED)
    "final_add": "MRK_FINAL_ADD_LIVE_9f2",        # added line of the LIVE diff (kept)
    "closing": "MRK_SUPERSEDE_CLOSING_7s8",        # prose after the diffs (kept)
}

# UI_CHROME_MARKERS: a message carrying the live-session "auto-added context"
# banner plus a comparable "Remove via the A button" affordance and a real
# answer.  The banner + affordance MUST be ABSENT from an export; the real
# answer (CHROME_ANSWER) MUST survive.  The banner text is a VERBATIM copy of
# the MarkdownRenderer.tsx affordance (~line 3554) so the check matches what the
# UI actually emits.
UI_CHROME_MARKERS: Dict[str, str] = {
    "answer": "MRK_CHROME_ANSWER_KEEP_3c9",   # real assistant content (kept)
    # The literal banner + affordance substrings a hygienic export must NOT contain.
    # (Not "unique markers": they are the strings being asserted ABSENT.)
}

# The exact live-session chrome strings an export must strip.  Kept as a list so
# both Card III (markdown) and Card IV (PDF) assert the SAME literals absent.
UI_CHROME_FORBIDDEN_SUBSTRINGS: List[str] = [
    "Auto-added",
    "file(s) to context",
    "available for subsequent queries",
    "Remove via the A button",
    "Files panel",
]


def _supersession_content() -> str:
    """Assistant message that diffs app_super.py twice; earlier diff is stale.

    Built so ``diffUtils.findSupersededDiffIndices`` marks index 0 superseded:
    same file, overlapping hunk, and the earlier diff adds 2 lines
    (earlierAdds>1) so it is not treated as a sequential prepare-then-add pair.
    """
    _m = SUPERSESSION_MARKERS
    return "\n".join([
        f"{_m['intro']}: first attempt at the fix.",
        "",
        "```diff",
        "diff --git a/app_super.py b/app_super.py",
        "--- a/app_super.py",
        "+++ b/app_super.py",
        "@@ -1,4 +1,5 @@",
        " import os",
        " import sys",
        f"-old_removed = 1",
        f"+{_m['superseded_add']} = 'first'",
        f"+extra_super_line = 2",
        " return None",
        "```",
        "",
        "Wait, that is wrong. Here is the corrected diff:",
        "",
        "```diff",
        "diff --git a/app_super.py b/app_super.py",
        "--- a/app_super.py",
        "+++ b/app_super.py",
        "@@ -1,4 +1,4 @@",
        " import os",
        " import sys",
        f"-old_removed = 1",
        f"+{_m['final_add']} = 'final'",
        " return None",
        "```",
        "",
        f"{_m['closing']}. That is the final version.",
    ])


def make_superseded_diff_conversation() -> List[Dict[str, Any]]:
    """A conversation whose assistant turn diffs one file twice (one stale).

    A hygienic export contains only the LIVE diff: ``final_add`` present,
    ``superseded_add`` absent, and the surrounding prose (``intro``/``closing``)
    intact.
    """
    return [
        {"id": "s_h", "role": "human",
         "content": "Please fix app_super.py.", "timestamp": 1},
        {"id": "s_a", "role": "assistant",
         "content": _supersession_content(), "timestamp": 2},
    ]


def _ui_chrome_content() -> str:
    """Assistant message polluted with the live-session auto-added-context banner.

    The banner + affordance are VERBATIM the MarkdownRenderer chrome; a hygienic
    export strips them but keeps ``answer``.
    """
    _m = UI_CHROME_MARKERS
    return "\n".join([
        "Auto-added 3 file(s) to context (app_super.py, util.py, main.py) — "
        "available for subsequent queries. Remove via the A button in the Files panel.",
        "",
        f"{_m['answer']}: the function now returns the greeting as requested.",
    ])


def make_ui_chrome_conversation() -> List[Dict[str, Any]]:
    """A conversation whose assistant turn carries the auto-added-context banner.

    A hygienic export drops every string in ``UI_CHROME_FORBIDDEN_SUBSTRINGS``
    but keeps ``UI_CHROME_MARKERS['answer']``.
    """
    return [
        {"id": "c_h", "role": "human",
         "content": "Fix the greeting.", "timestamp": 1},
        {"id": "c_a", "role": "assistant",
         "content": _ui_chrome_content(), "timestamp": 2},
    ]


# ---------------------------------------------------------------------------
# NEW-1 — FLOW-AWARE FIGURE SHRINKING fixture (Card IV).
#
# A figure that TECHNICALLY fits on a page but cannot fit ALONGSIDE the prose
# that introduces it, so pagination bumps it whole to its own page — stranding
# it from its context and leaving a large empty band behind (user-reported).
# USER RULING: it is acceptable to shrink such a figure to as small as 75% of
# its original size in favour of flow; 0.75 is the FLOOR for flow-driven
# shrinking, not a target.
#
# To reproduce the "fits-but-wrecks-flow" case we precede a moderately-tall
# mermaid diagram with enough prose that the diagram at natural size no longer
# fits below the prose on page 1 (so a naive layout bumps it to page 2 and
# leaves a band on page 1), yet the diagram alone DOES fit on a page (so
# fitOversizedFigures' `if (scale >= 1) continue` leaves it untouched — the
# exact gap NEW-1 closes).  FLOW_INTRO/FLOW_AFTER bracket it; FlowFigMRK is the
# diagram's node label (a PRESENCE marker — diagram text may or may not survive
# a PDF text layer, so checks key on raster geometry, not this string).
# ---------------------------------------------------------------------------
FLOW_FIGURE_MARKERS: Dict[str, str] = {
    "intro": "MRK_FLOW_INTRO_f1",   # prose that introduces the figure (kept, above it)
    "after": "MRK_FLOW_AFTER_f2",   # prose after the figure (kept)
}
FLOW_FIGURE_PRESENCE = "FlowFigMRK"  # diagram node label (>=0 in PDF text layer)


def _flow_figure_content() -> str:
    """Assistant turn: lots of intro prose, then a mid-height diagram, then prose.

    The prose is long enough that the diagram at natural size cannot share
    page 1 with it; the diagram is short enough to fit alone on a page.  That is
    the flow-cascade case NEW-1 addresses.
    """
    _m = FLOW_FIGURE_MARKERS
    # ~24 lines of intro prose to consume most of page 1's height.
    intro_para = "\n\n".join(
        f"Paragraph {i}: this is introductory prose that explains, at some "
        f"length, the diagram that follows so the reader has context before it. "
        f"It intentionally runs long to fill the page above the figure."
        for i in range(1, 13)
    )
    # A ~10-node top-down diagram: taller than the remaining space on page 1 but
    # comfortably shorter than a whole page.
    nodes = " --> ".join(f"{FLOW_FIGURE_PRESENCE}{i}[{FLOW_FIGURE_PRESENCE}{i}]"
                         for i in range(1, 11))
    return "\n".join([
        f"{_m['intro']}: here is a diagram introduced by the prose above and below it.",
        "",
        intro_para,
        "",
        "```mermaid",
        "graph TD",
        f"  {nodes}",
        "```",
        "",
        f"{_m['after']}: the prose continues immediately after the figure.",
    ])


def make_flow_figure_conversation() -> List[Dict[str, Any]]:
    """A conversation whose figure fits a page but not alongside its intro prose.

    A well-typeset export keeps the figure NEAR its introducing prose (shrinking
    it up to — but never below — 0.75x if needed), rather than bumping it whole
    to its own otherwise-empty page.
    """
    return [
        {"id": "f_h", "role": "human",
         "content": "Explain the pipeline with a diagram.", "timestamp": 1},
        {"id": "f_a", "role": "assistant",
         "content": _flow_figure_content(), "timestamp": 2},
    ]


# ---------------------------------------------------------------------------
# NEW-3 — EMPTY BAND BEFORE A WRAPPED DIFF fixture (Card IV).
#
# A diff is preceded by a large blank band after its 'Modify: <path>' header:
# the header sits high on the page and the body starts far below, OR the header
# is stranded at a page bottom with the body on the next page (user screenshot).
# ROOT CAUSE (diagnosed by Card IV brief): print.css `break-inside: avoid` on
# the bare `body.ziya-print-mode table` selector ALSO catches diff tables
# (diffs render as `table.diff-table`), forcing a page-tall diff whole and
# leaving the band.  A well-typeset export lets a long diff FLOW across the
# boundary and binds the 'Modify:' header to its body (break-after: avoid).
#
# The diff is deliberately long (many hunks / lines) so it exceeds the space
# remaining below its header near a page boundary — the condition that produces
# the band.  HDRBIND_INTRO precedes it; the body's first and last lines carry
# BODY_START/BODY_END so a check can measure header<->body separation and body
# span.  The file is app_hdrbind.py so the rendered header is
# 'Modify: app_hdrbind.py'.
# ---------------------------------------------------------------------------
HEADER_BINDING_MARKERS: Dict[str, str] = {
    "intro": "MRK_HDRBIND_INTRO_h1",         # prose before the diff (kept)
    "body_start": "MRK_HDRBIND_BODY_START_h2",  # first changed line of the diff body
    "body_end": "MRK_HDRBIND_BODY_END_h3",      # last changed line of the diff body
}
HEADER_BINDING_FILE = "app_hdrbind.py"   # -> rendered 'Modify: app_hdrbind.py' header


def _header_binding_content(pad_lines: int = 40) -> str:
    """Assistant turn: intro prose then a LONG single-file diff on app_hdrbind.py.

    The diff has ``pad_lines`` context/changed lines so it is taller than the
    space left below its header near a page boundary — reproducing the blank
    band before the body (and, when it lands at a page bottom, the stranded
    header).
    """
    _m = HEADER_BINDING_MARKERS
    f = HEADER_BINDING_FILE
    body = []
    body.append(f"diff --git a/{f} b/{f}")
    body.append(f"--- a/{f}")
    body.append(f"+++ b/{f}")
    body.append(f"@@ -1,{pad_lines + 2} +1,{pad_lines + 3} @@")
    body.append(f"+{_m['body_start']} = 'first changed line'")
    for i in range(pad_lines):
        # alternate context / additions so it renders as a tall diff table with
        # real add-coloured rows (keeps expected_color_presence honest too).
        if i % 2 == 0:
            body.append(f" context_line_{i:02d} = {i}")
        else:
            body.append(f"+added_line_{i:02d} = {i} * 2")
    body.append(f"+{_m['body_end']} = 'last changed line'")
    diff = "\n".join(body)
    return "\n".join([
        f"{_m['intro']}: here is the change to {f}.",
        "",
        "```diff",
        diff,
        "```",
    ])


def make_header_binding_conversation() -> List[Dict[str, Any]]:
    """A conversation with a long diff prone to a blank band after its header.

    A well-typeset export shows the 'Modify: app_hdrbind.py' header immediately
    above (same page as) its diff body — no large blank band between them, and
    the body flows across page boundaries rather than being forced whole onto a
    fresh page.
    """
    return [
        {"id": "hb_h", "role": "human",
         "content": f"Modify {HEADER_BINDING_FILE}.", "timestamp": 1},
        {"id": "hb_a", "role": "assistant",
         "content": _header_binding_content(), "timestamp": 2},
    ]


# ---------------------------------------------------------------------------
# QUAL-03 — INLINE BODY-LINK coverage fixture (Card IV).
#
# The canonical fixture exercises exactly ONE URL — the footer's Ziya link — so
# the ``link_annotations`` check could never PROVE that an inline markdown BODY
# link ``[label](https://…)`` becomes a real clickable /Link annotation in the
# PDF (as opposed to dead blue text).  This fixture closes that coverage gap: it
# carries several inline body links (bare autolink, labelled link, a link whose
# visible label differs from its URL, and a reference-style link) so the audit
# can assert every body URL that appears as text is BACKED by a Link annotation
# (``n_unbacked_url_texts == 0``).  If a body link turned out to be dead, this
# fixture would flip ``link_annotations`` to FAIL and PROMOTE the coverage gap
# to a real defect — which is exactly what QUAL-03 asked us to determine.
#
# The URLs are deliberately DISTINCT, improbable literals (unique host paths)
# so a substring match cannot be satisfied by the footer link or by ordinary
# prose, and so the check's "backed" test (annotation URI startswith/equals the
# text URL) is unambiguous.
# ---------------------------------------------------------------------------
BODY_LINK_MARKERS: Dict[str, str] = {
    "intro": "MRK_BODYLINK_INTRO_l1",     # prose before the links (kept)
    "closing": "MRK_BODYLINK_CLOSING_l9",  # prose after the links (kept)
}
# Each URL is a unique literal; the check must find a Link annotation whose /URI
# equals (or is a prefix of, to tolerate text-layer truncation) each of these.
BODY_LINK_URLS: List[str] = [
    "https://example.com/ziya-bodylink-autolink-a1",
    "https://example.com/ziya-bodylink-labelled-b2",
    "https://example.com/ziya-bodylink-refstyle-c3",
]


def _body_link_content() -> str:
    """Assistant turn with several inline markdown BODY links.

    Covers the link shapes a real answer uses: a bare autolink, a labelled
    ``[text](url)`` link whose label is prose (not the URL), and a
    reference-style ``[text][ref]`` link.  All three URLs are distinct literals
    so the audit can match each against a Link annotation independently of the
    footer link.
    """
    _m = BODY_LINK_MARKERS
    autolink, labelled, refstyle = BODY_LINK_URLS
    return "\n".join([
        f"{_m['intro']}: see the references below for details.",
        "",
        f"- A bare autolink: {autolink}",
        f"- A labelled link: [the labelled reference]({labelled}) explains the rest.",
        f"- A reference-style link: [see the spec][spec].",
        "",
        f"{_m['closing']}: that concludes the references.",
        "",
        f"[spec]: {refstyle}",
    ])


def make_body_link_conversation() -> List[Dict[str, Any]]:
    """A conversation whose assistant turn contains inline markdown body links.

    A well-made export renders every body link as a real clickable /Link
    annotation (``link_annotations`` PASSES with ``n_unbacked_url_texts == 0``
    including the body URLs, not just the footer link).
    """
    return [
        {"id": "bl_h", "role": "human",
         "content": "Point me at the documentation links.", "timestamp": 1},
        {"id": "bl_a", "role": "assistant",
         "content": _body_link_content(), "timestamp": 2},
    ]


def all_variants() -> Dict[str, List[Dict[str, Any]]]:
    """Return every fixture variant keyed by name (for the audit runner)."""
    return {
        "light": make_fidelity_conversation(),
        "dark": make_fidelity_conversation_dark(),
    }


# ---------------------------------------------------------------------------
# Measurement helpers — SHARED so every card measures fidelity the same way.
# Dependency-light (numpy only for raster).  Kept for backward compatibility
# with the Stage-2 test; the richer analyzers live in ``checks.py``.
# ---------------------------------------------------------------------------

def count_color_pixels(rgb_array, target_rgb, tol: int) -> int:
    """Count pixels within ``tol`` (sum of abs channel diffs) of ``target_rgb``.

    ``rgb_array`` is an HxWx3 (or Nx3) uint8/int numpy array.
    """
    import numpy as np
    arr = rgb_array.reshape(-1, 3).astype(int)
    t = np.array(target_rgb)
    return int((np.abs(arr - t).sum(axis=1) <= tol).sum())


def assert_dom_has_signals(html: str, signals: List[str], *, label: str) -> None:
    """Assert at least one of ``signals`` (CSS-ish selectors reduced to
    substring/class checks) is present in ``html``.  Substring check so it needs
    no DOM parser; the shared contract is "the renderer emitted a recognisable
    structure", which siblings can tighten with a real parser.
    """
    hits = []
    for sel in signals:
        needle = (
            sel.replace("span.", "")
            .replace(".", "")
            .replace('[class*="', "")
            .replace('"]', "")
            .replace("[class*='", "")
            .replace("']", "")
            .replace("<", "")
        )
        if needle and needle in html:
            hits.append(sel)
    assert hits, f"{label}: none of {signals} found in rendered HTML"


def render_pdf_page_to_rgb(pdf_bytes_or_path, page_index: int = 0, scale: float = 2.0):
    """Rasterise a PDF page to an HxWx3 numpy array using pypdfium2.

    Accepts bytes or a filesystem path.  Shared so PDF (Card I) and any HTML->PDF
    path (Card II) rasterise identically.
    """
    import pypdfium2 as pdfium
    if isinstance(pdf_bytes_or_path, (bytes, bytearray)):
        doc = pdfium.PdfDocument(bytes(pdf_bytes_or_path))
    else:
        doc = pdfium.PdfDocument(pdf_bytes_or_path)
    bmp = doc[page_index].render(scale=scale).to_numpy()
    if bmp.shape[2] == 4:
        bmp = bmp[:, :, :3]
    return bmp
