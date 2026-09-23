"""
Tests for app/utils/document_preprocess.py — the generic markdown body passes
applied before an authored IR document reaches the /print renderer.

Focus: the linear-chain diagram re-layout (defect D-001).  A purely linear
mermaid/graphviz chain must be re-emitted flowing left-to-right (wrapping onto
rows for long chains) REGARDLESS of the author's declared direction, while any
non-linear or unparseable diagram passes through byte-for-byte unchanged.

These tests fail against the pre-fix tree (the module did not exist / did not
rewrite) and pass once linearize_diagram_chains is in place.
"""
import re

import pytest

from app.utils.document_preprocess import (
    preprocess_document_body,
    linearize_diagram_chains,
)


def _fence(lang: str, body: str) -> str:
    return "```" + lang + "\n" + body + "\n```"


# ---------------------------------------------------------------------------
# Mermaid: linear chains are re-laid-out left-to-right
# ---------------------------------------------------------------------------

CHAIN_8_TD = _fence("mermaid", (
    "graph TD\n"
    "  A[Ingress listener] --> B[TLS terminator]\n"
    "  B --> C[Header parser]\n"
    "  C --> D[AddrUtil.parse]\n"
    "  D --> E[Policy evaluator]\n"
    "  E --> F[Route selector]\n"
    "  F --> G[Backend client]\n"
    "  G --> H[Audit record writer]"
))


def test_linear_td_chain_becomes_wrapped_graphviz_snake():
    """A long linear chain wraps onto rows.  Because mermaid cannot draw an
    inter-row connector without collapsing the LR layout (see the collapse
    regression test below), a MULTI-ROW chain is re-emitted as a graphviz DOT
    snake: rankdir=TB + one rank=same group per row, every edge drawn."""
    out = linearize_diagram_chains(CHAIN_8_TD)
    # The vertical mermaid TD declaration is gone; the fence is now graphviz.
    assert "graph TD" not in out
    assert "```graphviz" in out
    assert "rankdir=TB" in out
    # 8 nodes -> 2 rows of 4, each row a rank=same horizontal rank.
    assert out.count("rank=same") == 2
    assert "{ rank=same; A B C D }" in out
    assert "{ rank=same; E F G H }" in out
    # Every original node label is preserved (converted to DOT label attrs).
    for label in ("Ingress listener", "TLS terminator", "Header parser",
                  "AddrUtil.parse", "Policy evaluator", "Route selector",
                  "Backend client", "Audit record writer"):
        assert f'label="{label}"' in out


def test_lr_declared_linear_chain_triggers_identically():
    """A chain the author already declared LR is detected the same way:
    detection is by graph shape, not by the declared direction keyword."""
    lr_src = CHAIN_8_TD.replace("graph TD", "flowchart LR")
    out_td = linearize_diagram_chains(CHAIN_8_TD)
    out_lr = linearize_diagram_chains(lr_src)
    # Same rewritten layout regardless of the declared direction.
    assert out_td == out_lr


def test_bt_declared_linear_chain_triggers():
    bt_src = CHAIN_8_TD.replace("graph TD", "graph BT")
    out = linearize_diagram_chains(bt_src)
    assert "graph BT" not in out
    # Detected by shape regardless of declared direction -> wrapped graphviz.
    assert "```graphviz" in out
    assert "rankdir=TB" in out
    assert out.count("rank=same") == 2


def test_wrapped_chain_draws_inter_row_connector():
    """Quality-bar criterion 4 / defect D-003: a wrapped linear chain must draw
    the connector from the end of one row to the start of the next, not rely on
    reading order.  The graphviz snake emits EVERY chain edge, including the
    inter-row one (D -> E for two rows of four), and graphviz — unlike mermaid
    subgraphs — draws it without collapsing the L->R layout."""
    out = linearize_diagram_chains(CHAIN_8_TD)
    # The cross-row connector (end of row 0 -> start of row 1) is drawn.
    assert "D -> E;" in out
    # And it is a real graphviz directed edge, not an invisible mermaid link.
    assert "~~~" not in out
    # Every in-row edge is present too.
    for edge in ("A -> B;", "B -> C;", "C -> D;",
                 "E -> F;", "F -> G;", "G -> H;"):
        assert edge in out


def test_short_linear_chain_is_single_lr_row():
    src = _fence("mermaid", "graph TD\n  A[One] --> B[Two] --> C[Three]")
    out = linearize_diagram_chains(src)
    assert "flowchart LR" in out
    # Short enough for one row: no subgraph wrapping needed.
    assert "subgraph" not in out
    assert "A[One] --> B[Two] --> C[Three]" in out


def test_row_count_capped_and_balanced():
    # 12-node chain -> 3 rows of 4 (<= _MAX_NODES_PER_ROW, balanced).
    nodes = [f"N{i}[Step {i}]" for i in range(12)]
    body = "graph TD\n  " + " --> ".join(nodes)
    out = linearize_diagram_chains(_fence("mermaid", body))
    # Wrapped -> graphviz snake with one rank=same group per row.
    assert "```graphviz" in out
    assert out.count("rank=same") == 3


# ---------------------------------------------------------------------------
# Mermaid: non-linear / unparseable graphs pass through unchanged
# ---------------------------------------------------------------------------

def test_branching_graph_unchanged():
    src = _fence("mermaid", (
        "graph TD\n"
        "  A --> B\n"
        "  A --> C\n"
        "  B --> D\n"
        "  C --> D"
    ))
    assert linearize_diagram_chains(src) == src


def test_cycle_unchanged():
    src = _fence("mermaid", "graph LR\n  A --> B\n  B --> C\n  C --> A")
    assert linearize_diagram_chains(src) == src


def test_graph_with_edge_labels_unchanged():
    # Edge labels use `|...|`, a feature the pass deliberately does not parse.
    src = _fence("mermaid", "graph TD\n  A -->|yes| B\n  B --> C\n  C --> D")
    assert linearize_diagram_chains(src) == src


# ---------------------------------------------------------------------------
# Mermaid: STATUS-COLOURED / ANNOTATED spine chains (defect D-201)
#
# A hand-authored readiness chain declares `flowchart TD`, colours each node
# with a `classDef` + inline `:::class`, and layers a dotted `-.->` call-out
# over the spine.  Before the spine-aware fix the pass BAILED on classDef/:::/
# `-.` and the graph rendered as a tall vertical column.  It must now detect the
# solid-edge spine, re-lay it out L->R / snaked, and PRESERVE the palette and
# the dotted annotation.  These assertions fail against the pre-fix tree (which
# returned the fence unchanged).
# ---------------------------------------------------------------------------

def test_styled_linear_chain_relaid_out_not_bailed():
    """A short classDef-styled linear chain is no longer passed through
    unchanged; it is re-emitted through the colour-preserving DOT snake."""
    src = _fence("mermaid", (
        "graph TD\n"
        "  A --> B\n  B --> C\n  C --> D\n"
        "  classDef hot fill:#f00,stroke:#900,color:#fff\n"
        "  class A hot"
    ))
    out = linearize_diagram_chains(src)
    # The authored vertical mermaid is gone; re-emitted as graphviz.
    assert "graph TD" not in out
    assert "```graphviz" in out
    # classDef colour is carried through: fill -> filled fillcolor, stroke ->
    # border color, color -> fontcolor.
    assert "style=filled" in out
    assert 'fillcolor="#f00"' in out
    assert 'color="#900"' in out
    assert 'fontcolor="#fff"' in out
    # The spine is still drawn end to end.
    for edge in ("A -> B;", "B -> C;", "C -> D;"):
        assert edge in out


def test_inline_class_and_dotted_annotation_spine():
    """The real report's shape: inline `:::class` on `["..."]` nodes, a status
    palette, and a dotted `-.->` call-out with a label.  The solid `-->` edges
    are the spine; the dotted edge is preserved as a dashed, non-constraining
    annotation and does NOT break linearity even though it gives its source a
    second out-edge."""
    src = _fence("mermaid", (
        "flowchart TD\n"
        "    classDef absent fill:#f8b4b4,stroke:#b91c1c,color:#111;\n"
        "    classDef ok fill:#a7f3d0,stroke:#047857,color:#111;\n"
        '    P["Prov<br/>line two"]:::absent\n'
        '    C["Model"]:::ok\n'
        '    D["Dataplane"]:::ok\n'
        "    P --> C --> D\n"
        '    P -. "missing hop<br/>note" .-> D\n'
    ))
    out = linearize_diagram_chains(src)
    assert "```graphviz" in out
    assert "flowchart TD" not in out
    # Spine edges drawn.
    assert "P -> C;" in out
    assert "C -> D;" in out
    # Dotted annotation preserved as a dashed, non-constraining edge with label.
    assert "style=dashed" in out
    assert "constraint=false" in out
    assert 'label="missing hop\\nnote"' in out
    # Per-node palette from the two classDefs is applied (absent=red border,
    # ok=green border).
    assert 'color="#b91c1c"' in out   # absent stroke
    assert 'color="#047857"' in out   # ok stroke
    # `<br/>` line breaks in a node label become graphviz newline escapes.
    assert 'label="Prov\\nline two"' in out


def test_styled_nine_node_chain_snakes_with_colours():
    """A 9-node coloured spine (> _MAX_NODES_PER_ROW) snakes onto rank rows
    while keeping the palette — the exact D-201 situation."""
    nodes = "".join(
        f'    N{i}["Step {i}"]:::s{i % 2}\n' for i in range(9)
    )
    spine = "    " + " --> ".join(f"N{i}" for i in range(9)) + "\n"
    src = _fence("mermaid", (
        "flowchart TD\n"
        "    classDef s0 fill:#fde68a,stroke:#b45309,color:#111;\n"
        "    classDef s1 fill:#a7f3d0,stroke:#047857,color:#111;\n"
        + nodes + spine
    ))
    out = linearize_diagram_chains(src)
    assert "```graphviz" in out
    assert "rankdir=TB" in out
    # 9 nodes -> 2 rank rows (snaked, not a vertical column).
    assert out.count("rank=same") == 2
    # Colours preserved.
    assert 'fillcolor="#fde68a"' in out
    assert 'fillcolor="#a7f3d0"' in out


def test_styled_branching_graph_still_unchanged():
    """Styling does not force a re-layout: a BRANCHING graph (solid edges are
    not linear) is still passed through byte-for-byte, colours and all."""
    src = _fence("mermaid", (
        "graph TD\n"
        "    A:::hot --> B\n"
        "    A --> C\n"
        "    B --> D\n"
        "    C --> D\n"
        "    classDef hot fill:#f00\n"
    ))
    assert linearize_diagram_chains(src) == src


def test_two_node_chain_below_threshold_unchanged():
    src = _fence("mermaid", "graph TD\n  A[Only] --> B[Two]")
    assert linearize_diagram_chains(src) == src


# ---------------------------------------------------------------------------
# Non-diagram fences and prose are untouched
# ---------------------------------------------------------------------------

def test_non_diagram_fence_unchanged():
    src = "before\n" + _fence("python", "a = 1\nb = 2") + "\nafter"
    assert linearize_diagram_chains(src) == src


def test_prose_around_diagram_preserved():
    doc = (
        "# Heading\n\nIntro paragraph.\n\n"
        + CHAIN_8_TD
        + "\n\nTrailing paragraph.\n"
    )
    out = preprocess_document_body(doc)
    assert out.startswith("# Heading\n\nIntro paragraph.\n\n")
    assert out.rstrip().endswith("Trailing paragraph.")
    # The 8-node chain wraps -> re-emitted as a graphviz snake, prose intact.
    assert "```graphviz" in out


def test_empty_body_is_noop():
    assert preprocess_document_body("") == ""


# ---------------------------------------------------------------------------
# Graphviz / DOT: linear chains become rankdir=LR with snaked rank rows
# ---------------------------------------------------------------------------

def test_graphviz_linear_chain_wraps_to_tb_snake():
    # 7-node chain -> wraps to 2 rows.  A wrapped graphviz chain uses rankdir=TB
    # (rows = horizontal ranks) so the inter-row connector is drawn; rankdir=LR
    # with rank=same would stack a row vertically (the iter1/2 graphviz bug).
    src = _fence("graphviz", "digraph G {\n  A -> B -> C -> D -> E -> F -> G;\n}")
    out = linearize_diagram_chains(src)
    assert "rankdir=TB" in out
    assert "rankdir=LR" not in out
    # At least one rank=same row group for snaking.
    assert "rank=same" in out
    # Real chain edges preserved, including the inter-row connector D -> E.
    assert "A -> B;" in out
    assert "D -> E;" in out


def test_graphviz_single_row_chain_stays_lr():
    # 4-node chain fits one row -> straight rankdir=LR, no rank groups.
    src = _fence("graphviz", "digraph { A -> B -> C -> D }")
    out = linearize_diagram_chains(src)
    assert "rankdir=LR" in out
    assert "rank=same" not in out


def test_graphviz_dot_lang_alias_handled():
    src = _fence("dot", "digraph { A -> B -> C -> D }")
    out = linearize_diagram_chains(src)
    assert "rankdir=LR" in out


def test_graphviz_branching_unchanged():
    src = _fence("graphviz", "digraph { A -> B\n A -> C\n B -> D\n C -> D }")
    assert linearize_diagram_chains(src) == src


def test_graphviz_preserves_node_labels():
    src = _fence("graphviz", (
        "digraph {\n"
        '  A [label="Start"];\n'
        '  B [label="Middle"];\n'
        '  C [label="End"];\n'
        "  A -> B -> C\n"
        "}"
    ))
    out = linearize_diagram_chains(src)
    assert 'label="Start"' in out
    assert 'label="Middle"' in out
    assert "rankdir=LR" in out
