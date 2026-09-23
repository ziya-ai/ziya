"""
Document markdown pre-processing — GENERIC body rewrites applied before the
authored IR reaches the ``/print`` renderer.

This module owns transforms that improve every exported document's layout
without touching per-document content.  Each transform is a pure function
``str -> str`` over the markdown BODY (front-matter already stripped by
:func:`app.utils.document_ir.parse_document`), so it is trivially unit-testable
and side-effect free.

Currently implemented
---------------------
``linearize_diagram_chains`` — the linear-chain re-layout.  A diagram fence
(``mermaid`` or ``graphviz``/``dot``) whose graph is a *purely linear chain*
(A→B→C→…, every node with at most one edge in and one out, forming a single
path) is laid out top-to-bottom by default in mermaid's ``graph TD`` and by
graphviz's default rank direction.  On a portrait page that wastes an entire
page on a one-dimensional structure and — being page-tall — forces the figure
onto its own page.

The pass detects such a chain *regardless of the author's declared direction*
and re-emits it flowing LEFT-TO-RIGHT across the printable width, wrapping onto
additional rows (with the inter-row connector drawn) when the chain is long.
A branching / cyclic / multi-edge graph is NOT linear and is passed through
byte-for-byte unchanged, as is any fence using features this pass does not
confidently parse (subgraphs, styling, edge labels, mixed arrow kinds).

Design contract
---------------
* Conservative: when in doubt, return the fence unchanged.  A false negative
  (a linear chain left vertical) is a cosmetic miss; a false positive
  (mangling a real graph) is a correctness bug.  We bias hard to the former.
* Idempotent-ish: a chain already emitted by this pass (``flowchart LR`` /
  row subgraphs) is either left alone (single-row LR) or re-wrapped to the
  same shape; running twice does not degrade it.
* Portable: the output is still valid mermaid/graphviz that renders in GitHub
  and any other viewer, so the IR's portability contract is preserved.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

# Maximum nodes on a single row before the chain snakes onto another row.
# Chosen so a row of average-length labels fits the ~178mm printable width of
# an A4 page at 16mm margins without mermaid shrinking the type illegibly.
_MAX_NODES_PER_ROW = 5

# Minimum chain length worth re-laying-out.  A 2-node chain stacked vertically
# is not worth rewriting; 3+ nodes in a column is where the wasted-page problem
# begins.
_MIN_CHAIN_LEN = 3

# A fenced code block: ```lang\n ... \n``` .  Captures the info string and body
# so we can dispatch on language and rewrite only the body.
_FENCE_RE = re.compile(
    r'(?P<fence>^[ \t]*```+)[ \t]*(?P<info>[^\n`]*)\n'
    r'(?P<body>.*?)\n'
    r'(?P=fence)[ \t]*$',
    re.DOTALL | re.MULTILINE,
)

# Tokens whose presence means the mermaid graph uses features this pass does
# not parse; their appearance makes us bail (pass the fence through unchanged).
#
# Note what is deliberately NOT here: ``classDef`` / ``class`` styling, inline
# ``:::class`` assignments and dotted ``-.`` annotation edges.  Those are the
# hallmarks of a hand-authored, status-coloured *spine* diagram (a linear flow
# whose colours carry meaning, with one or two dotted call-out edges).  Bailing
# on them left a real one-in/one-out chain rendering as a tall vertical column
# (defect D-201).  We now PARSE them: the solid ``-->`` edges must still form a
# single linear chain, the dotted edges are captured as non-spine annotations,
# and the classDef colours are preserved through the re-layout.
_MERMAID_BAIL_TOKENS = (
    'subgraph', 'linkStyle', 'click', 'direction',
    '&', '|',                   # multi-target / edge labels
    '==', '<--', '<-.',         # thick / bidirectional / leftward dotted
    'o--', 'x--', '---',        # circle/cross endpoints, undirected links
)

# A mermaid dotted / "annotation" edge: ``A -.-> B`` or ``A -. "label" .-> B``.
# These are call-outs layered over the spine, not part of the linear flow, so
# they must not count toward a node's in/out degree when testing linearity.
_MERMAID_DOTTED_EDGE_RE = re.compile(
    r'^\s*([A-Za-z0-9_]+)\s*-\.\s*(?:"([^"]*)"|\'([^\']*)\')?\s*\.?-+>\s*'
    r'([A-Za-z0-9_]+)\s*$'
)

# A mermaid ``classDef NAME fill:#..,stroke:#..,color:#..;`` line.
_MERMAID_CLASSDEF_RE = re.compile(
    r'^\s*classDef\s+([A-Za-z0-9_]+)\s+(.*?)\s*;?\s*$'
)

# A standalone ``class NODE1,NODE2 CLASSNAME`` assignment line.
_MERMAID_CLASS_ASSIGN_RE = re.compile(
    r'^\s*class\s+([A-Za-z0-9_,\s]+?)\s+([A-Za-z0-9_]+)\s*;?\s*$'
)

# An inline class assignment suffix on a node token: ``A["x"]:::partial``.
_MERMAID_INLINE_CLASS_RE = re.compile(r':::([A-Za-z0-9_]+)\s*$')

_MERMAID_HEADER_RE = re.compile(r'^\s*(graph|flowchart)\b(.*)$', re.IGNORECASE)

# A mermaid node token: an id followed by an optional shape/label wrapper.
# ``A`` , ``A[label]`` , ``A(label)`` , ``A{label}`` , ``A([label])`` , ...
_NODE_ID_RE = re.compile(r'^([A-Za-z0-9_]+)(.*)$', re.DOTALL)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def preprocess_document_body(body: str) -> str:
    """Apply every generic body transform in order.  Pure ``str -> str``.

    This is the single hook the exporter calls.  Currently it runs
    :func:`linearize_diagram_chains`; future passes chain here.
    """
    if not body:
        return body
    return linearize_diagram_chains(body)


def linearize_diagram_chains(body: str) -> str:
    """Rewrite every linear-chain mermaid/graphviz fence to a wrapped LR flow.

    Non-diagram fences, and diagram fences that are not purely linear chains,
    are returned unchanged.
    """
    def _replace(m: re.Match) -> str:
        info = (m.group('info') or '').strip().lower()
        lang = info.split()[0] if info else ''
        inner = m.group('body')
        if lang == 'mermaid':
            rewritten = _rewrite_mermaid_chain(inner)
        elif lang in ('graphviz', 'dot'):
            rewritten = _rewrite_graphviz_chain(inner)
        else:
            rewritten = None
        if rewritten is None:
            return m.group(0)
        fence = m.group('fence')
        if isinstance(rewritten, tuple):
            # (new_lang, body): the transform changed the fence language (e.g.
            # a multi-row mermaid chain re-emitted as a graphviz snake so the
            # inter-row connector can be drawn — see _rewrite_mermaid_chain).
            new_lang, new_body = rewritten
            return f"{fence}{new_lang}\n{new_body}\n{fence}"
        return f"{fence}{m.group('info')}\n{rewritten}\n{fence}"

    return _FENCE_RE.sub(_replace, body)


# ---------------------------------------------------------------------------
# Chain model
# ---------------------------------------------------------------------------

class _Chain:
    """An ordered linear chain: node ids in path order + their full defs."""

    def __init__(self, order: List[str], defs: dict):
        self.order = order          # e.g. ['A', 'B', ..., 'H']
        self.defs = defs            # id -> full token text ('A[Ingress]' etc.)
        # Optional mermaid styling captured by _parse_mermaid (empty for
        # unstyled chains and for graphviz sources).
        self.classdefs: dict = {}           # class name -> parsed style dict
        self.node_class: dict = {}          # node id -> class name
        self.annotations: List[Tuple[str, str, str]] = []  # (src, dst, label)

    def is_styled(self) -> bool:
        """True when the chain carries status colours or dotted annotations,
        so it must be re-emitted through the colour-preserving DOT snake."""
        return bool(self.classdefs or self.node_class or self.annotations)

    def token(self, node_id: str) -> str:
        """The full definition token for a node (id + shape/label)."""
        return self.defs.get(node_id, node_id)


def _linear_order(nodes_in_order: List[str],
                  edges: List[Tuple[str, str]]) -> Optional[List[str]]:
    """Return the node ids in chain order iff the graph is a single linear path.

    Requirements for linearity:
      * every node has in-degree <= 1 and out-degree <= 1
      * exactly one start (in-degree 0) and one end (out-degree 0)
      * edge count == node count - 1
      * following out-edges from the start visits every node exactly once
        (single connected path, no cycle, no fork)
    Returns None when any requirement fails.
    """
    nodes = list(dict.fromkeys(nodes_in_order))
    n = len(nodes)
    if n < _MIN_CHAIN_LEN:
        return None
    if len(edges) != n - 1:
        return None

    out_edge: dict = {}
    indeg: dict = {k: 0 for k in nodes}
    outdeg: dict = {k: 0 for k in nodes}
    for src, dst in edges:
        if src not in indeg or dst not in indeg:
            return None
        if src in out_edge:      # a node points at two targets -> fork
            return None
        out_edge[src] = dst
        outdeg[src] += 1
        indeg[dst] += 1
    if any(v > 1 for v in indeg.values()):
        return None
    if any(v > 1 for v in outdeg.values()):
        return None

    starts = [k for k in nodes if indeg[k] == 0]
    ends = [k for k in nodes if outdeg[k] == 0]
    if len(starts) != 1 or len(ends) != 1:
        return None

    order: List[str] = []
    seen = set()
    cur = starts[0]
    while cur is not None:
        if cur in seen:          # cycle
            return None
        seen.add(cur)
        order.append(cur)
        cur = out_edge.get(cur)
    if len(order) != n:          # disconnected remainder
        return None
    return order


def _rows_for(order: List[str]) -> List[List[str]]:
    """Partition a chain into balanced rows of <= _MAX_NODES_PER_ROW nodes."""
    n = len(order)
    if n <= _MAX_NODES_PER_ROW:
        return [list(order)]
    import math
    num_rows = math.ceil(n / _MAX_NODES_PER_ROW)
    per_row = math.ceil(n / num_rows)
    return [order[i:i + per_row] for i in range(0, n, per_row)]


# ---------------------------------------------------------------------------
# Shared DOT emission (the drawn-connector snake)
# ---------------------------------------------------------------------------

# A mermaid node shape wrapper: A[label], A(label), A{label}, A>label], etc.
# We only need the label text for the graphviz conversion.
_MERMAID_LABEL_RE = re.compile(r'^[\[\(\{>]+(.*?)[\]\)\}]+$', re.DOTALL)


def _dot_escape(s: str) -> str:
    """Escape a label for a double-quoted graphviz string."""
    return s.replace('\\', '\\\\').replace('"', '\\"')


def _dot_label(text: str) -> str:
    """Turn a mermaid label into a graphviz quoted-string body.

    Mermaid line breaks (``<br/>`` / ``<br>``) become graphviz's ``\\n`` newline
    escape.  We escape the raw text FIRST (so a real backslash in the label is
    doubled) and only THEN inject the ``\\n`` escapes, so they survive as newline
    directives rather than being turned into a literal backslash-n.
    """
    escaped = _dot_escape(text)
    return re.sub(r'<br\s*/?>', r'\\n', escaped)


def _parse_classdef_style(style: str) -> dict:
    """Parse a mermaid classDef style body (``fill:#f00,stroke:#333,color:#111``)
    into a ``{prop: value}`` dict.  Unknown / malformed pairs are ignored."""
    out: dict = {}
    for pair in style.split(','):
        if ':' not in pair:
            continue
        k, v = pair.split(':', 1)
        k = k.strip().lower()
        v = v.strip()
        if k and v:
            out[k] = v
    return out


def _classdef_to_dot_attrs(style: dict) -> List[str]:
    """Translate a parsed mermaid classDef style into graphviz node attributes.

    ``fill`` -> filled ``fillcolor``; ``stroke`` -> border ``color``;
    ``color`` -> ``fontcolor``.  Returns a list of ``key=value`` attr fragments
    (colours quoted).  A generic mapping — it carries any status palette through
    the re-layout, not just this document's red/amber/green.
    """
    attrs: List[str] = []
    fill = style.get('fill')
    stroke = style.get('stroke')
    font = style.get('color')
    if fill:
        attrs.append('style=filled')
        attrs.append(f'fillcolor="{_dot_escape(fill)}"')
    if stroke:
        attrs.append(f'color="{_dot_escape(stroke)}"')
    if font:
        attrs.append(f'fontcolor="{_dot_escape(font)}"')
    return attrs


def _emit_dot_snake_styled(order: List[str], label_of, class_of,
                           classdefs: dict, annotations: List[Tuple[str, str, str]],
                           rows: List[List[str]]) -> str:
    """Emit a left-to-right / snaked DOT chain that PRESERVES status colours.

    Same rank-per-row snake as :func:`_emit_dot_snake`, but each node also
    carries the graphviz translation of its mermaid class's classDef style, and
    the captured dotted *annotation* edges are drawn dashed with
    ``constraint=false`` so a call-out does not distort the spine's ranking.

    ``label_of(nid)`` -> label text or None; ``class_of(nid)`` -> class name or
    None; ``classdefs`` maps a class name to a parsed style dict; ``annotations``
    is a list of ``(src, dst, label)`` dotted edges.
    """
    multi = len(rows) > 1
    out: List[str] = ["digraph {"]
    out.append("  rankdir=TB;" if multi else "  rankdir=LR;")
    out.append("  node [shape=box];")
    for nid in order:
        parts: List[str] = []
        lbl = label_of(nid)
        if lbl is not None:
            parts.append(f'label="{_dot_label(lbl)}"')
        cls = class_of(nid)
        if cls and cls in classdefs:
            parts.extend(_classdef_to_dot_attrs(classdefs[cls]))
        if parts:
            out.append(f"  {nid} [{', '.join(parts)}];")
    for a, b in zip(order, order[1:]):
        out.append(f"  {a} -> {b};")
    for src, dst, alabel in annotations:
        attrs = ['style=dashed', 'constraint=false']
        if alabel:
            attrs.append(f'label="{_dot_label(alabel)}"')
        out.append(f"  {src} -> {dst} [{', '.join(attrs)}];")
    if multi:
        for row in rows:
            out.append(f"  {{ rank=same; {' '.join(row)} }}")
    out.append("}")
    return '\n'.join(out)


def _mermaid_label(token: str, node_id: str) -> Optional[str]:
    """Extract the human label from a mermaid node token, or None if bare.

    ``A[Ingress listener]`` -> ``Ingress listener`` ; ``A`` -> ``None``.
    """
    rest = token[len(node_id):].strip()
    if not rest:
        return None
    m = _MERMAID_LABEL_RE.match(rest)
    if not m:
        return None
    label = m.group(1).strip().strip('"').strip("'").strip()
    return label or None


def _emit_dot_snake(order: List[str], label_of, rows: List[List[str]]) -> str:
    """Emit a graphviz DOT chain that flows left-to-right and snakes onto rows.

    Single row -> ``rankdir=LR`` straight flow.  Multiple rows -> ``rankdir=TB``
    with one ``{ rank=same; ... }`` group per row: each row is a horizontal rank
    and the chain edge that crosses from the end of one row to the start of the
    next drops to the following rank and is DRAWN (graphviz, unlike mermaid
    subgraphs, does not collapse the layout when an edge crosses a rank group).
    ``label_of`` maps a node id to its label text (or None to show the bare id).
    """
    multi = len(rows) > 1
    out: List[str] = ["digraph {"]
    out.append("  rankdir=TB;" if multi else "  rankdir=LR;")
    out.append("  node [shape=box];")
    for nid in order:
        lbl = label_of(nid)
        if lbl is not None:
            out.append(f'  {nid} [label="{_dot_escape(lbl)}"];')
    for a, b in zip(order, order[1:]):
        out.append(f"  {a} -> {b};")
    if multi:
        for row in rows:
            out.append(f"  {{ rank=same; {' '.join(row)} }}")
    out.append("}")
    return '\n'.join(out)


# ---------------------------------------------------------------------------
# Mermaid
# ---------------------------------------------------------------------------

def _split_node_token(tok: str) -> Optional[Tuple[str, str]]:
    """Split ``A[label]`` -> ('A', 'A[label]').  None if not a valid node."""
    tok = tok.strip()
    if not tok:
        return None
    m = _NODE_ID_RE.match(tok)
    if not m:
        return None
    node_id = m.group(1)
    return node_id, tok


def _parse_mermaid(inner: str) -> Optional[_Chain]:
    """Parse a mermaid flowchart into a _Chain (spine-aware), or None.

    The *spine* is the chain formed by the solid ``-->`` edges: those alone must
    form a single linear path (via :func:`_linear_order`).  ``classDef`` styles,
    inline ``:::class`` / standalone ``class`` assignments and dotted ``-.->``
    annotation edges are CAPTURED (not bailed on) and attached to the returned
    chain so the re-layout can preserve the status palette and draw the
    call-outs.  Returns None when the solid edges are not a linear chain, or when
    an unparseable feature (subgraph, edge label, multi-target, ...) appears.
    """
    lines = inner.split('\n')
    header_dir = None
    content_lines: List[str] = []
    seen_header = False
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith('%%'):   # mermaid comment
            continue
        if not seen_header:
            hm = _MERMAID_HEADER_RE.match(s)
            if not hm:
                return None      # first content line isn't a graph/flowchart
            header_dir = hm.group(2).strip()
            seen_header = True
            continue
        content_lines.append(s)

    if not seen_header:
        return None

    classdefs: dict = {}                # class name -> parsed style dict
    node_class: dict = {}               # node id -> class name
    annotations: List[Tuple[str, str, str]] = []   # (src, dst, label)

    # First pass: pull out styling / annotation lines so they neither bail nor
    # get treated as spine edges.  What remains is the plain node/edge grammar.
    remaining: List[str] = []
    for s in content_lines:
        cd = _MERMAID_CLASSDEF_RE.match(s)
        if cd:
            classdefs[cd.group(1)] = _parse_classdef_style(cd.group(2))
            continue
        ca = _MERMAID_CLASS_ASSIGN_RE.match(s)
        if ca and not _split_has_edge(s):
            for nid in re.split(r'[,\s]+', ca.group(1).strip()):
                if nid:
                    node_class[nid] = ca.group(2)
            continue
        de = _MERMAID_DOTTED_EDGE_RE.match(s)
        if de:
            label = de.group(2) or de.group(3) or ''
            annotations.append((de.group(1), de.group(4), label))
            continue
        remaining.append(s)

    # Bail on any feature we still don't confidently parse.
    for s in remaining:
        for bad in _MERMAID_BAIL_TOKENS:
            if bad in s:
                return None

    defs: dict = {}
    order_seen: List[str] = []
    edges: List[Tuple[str, str]] = []

    def _record(tok: str) -> Optional[str]:
        tok = tok.strip()
        # Strip a trailing inline class assignment: ``A["x"]:::partial``.
        inline_cls = None
        ic = _MERMAID_INLINE_CLASS_RE.search(tok)
        if ic:
            inline_cls = ic.group(1)
            tok = tok[:ic.start()].strip()
        parsed = _split_node_token(tok)
        if not parsed:
            return None
        node_id, full = parsed
        if inline_cls:
            node_class[node_id] = inline_cls
        if node_id not in defs:
            order_seen.append(node_id)
            defs[node_id] = full
        elif len(full) > len(defs[node_id]):
            # Keep the richest definition seen (the one carrying the label).
            defs[node_id] = full
        return node_id

    for s in remaining:
        s = s.rstrip(';').strip()
        if not s:
            continue
        if '-->' in s:
            parts = [p.strip() for p in s.split('-->')]
            ids = []
            for p in parts:
                nid = _record(p)
                if nid is None:
                    return None
                ids.append(nid)
            for a, b in zip(ids, ids[1:]):
                edges.append((a, b))
        else:
            # Standalone node definition.
            if _record(s) is None:
                return None

    # The SPINE (solid edges) alone must be a single linear chain.  Annotation
    # edges are intentionally excluded from this degree test.
    order = _linear_order(order_seen, edges)
    if order is None:
        return None

    # Annotation endpoints must be spine nodes (a dangling call-out to an
    # unknown id means we mis-parsed — bail conservatively).
    known = set(order)
    for src, dst, _ in annotations:
        if src not in known or dst not in known:
            return None

    chain = _Chain(order, defs)
    chain.classdefs = classdefs
    chain.node_class = node_class
    chain.annotations = annotations
    return chain


def _split_has_edge(s: str) -> bool:
    """True if a line contains any edge operator (so ``class`` on it is not an
    assignment statement but part of an edge — a paranoia guard)."""
    return '-->' in s or '-.' in s or '==' in s or '---' in s


def _rewrite_mermaid_chain(inner: str) -> Optional[str]:
    """Return the rewritten mermaid body, or None to leave the fence as-is."""
    chain = _parse_mermaid(inner)
    if chain is None:
        return None
    rows = _rows_for(chain.order)

    if chain.is_styled():
        # A status-coloured / annotated spine (e.g. a red/amber/green readiness
        # chain with a dotted call-out).  Re-emit through the DOT snake so the
        # layout flows L->R and snakes onto rows while the classDef palette and
        # the dotted annotation edges are preserved.  Graphviz is used for BOTH
        # single- and multi-row here (a single-row LR digraph keeps the colours
        # too), so the vertical-column defect is fixed regardless of length.
        dot = _emit_dot_snake_styled(
            chain.order,
            lambda nid: _mermaid_label(chain.token(nid), nid),
            lambda nid: chain.node_class.get(nid),
            chain.classdefs,
            chain.annotations,
            rows,
        )
        return ('graphviz', dot)

    if len(rows) == 1:
        # Short unstyled chain: a plain left-to-right mermaid flow is enough.
        row = rows[0]
        chained = ' --> '.join(chain.token(n) for n in row)
        return f"flowchart LR\n  {chained}"

    # Long chain: mermaid CANNOT draw an inter-row connector while keeping the
    # per-row ``direction LR``.  Mermaid honors a subgraph's LR direction ONLY
    # when no edge crosses the subgraph boundary; a visible ``last --> first``
    # connector between rows makes it discard the per-row direction and collapse
    # the whole diagram back into a single vertical column (observed iter1 and
    # re-confirmed on the live renderer in iter3).  An INVISIBLE ``~~~`` link
    # avoids the collapse but then no connector is drawn between rows (defect
    # D-003).
    #
    # Graphviz has no such limitation: with ``rankdir=TB`` and one
    # ``{ rank=same; ... }`` group per row, each row is a horizontal rank that
    # flows left-to-right, and the chain edge crossing from the end of one row
    # to the start of the next simply drops to the following rank and IS DRAWN.
    # So a multi-row chain is re-emitted as a DOT snake and the fence language is
    # switched to graphviz (the /print renderer supports graphviz; the authored
    # source file is never modified — this transform is export-only).  This
    # keeps the L->R wrapped layout of D-001 while adding the drawn inter-row
    # connector required by quality-bar criterion 4 (closes D-003).
    dot = _emit_dot_snake(
        chain.order,
        lambda nid: _mermaid_label(chain.token(nid), nid),
        rows,
    )
    return ('graphviz', dot)


# ---------------------------------------------------------------------------
# Graphviz / DOT
# ---------------------------------------------------------------------------

_DOT_HEADER_RE = re.compile(r'^\s*(strict\s+)?(di)?graph\b', re.IGNORECASE)
_DOT_EDGE_RE = re.compile(
    r'^\s*"?([A-Za-z0-9_]+)"?\s*->\s*"?([A-Za-z0-9_]+)"?'
    r'(?:\s*->\s*"?[A-Za-z0-9_]+"?)*\s*;?\s*$'
)
_DOT_NODE_DEF_RE = re.compile(
    r'^\s*"?([A-Za-z0-9_]+)"?\s*(\[[^\]]*\])\s*;?\s*$'
)
_DOT_BAIL_TOKENS = ('subgraph', 'rank', '--', 'cluster')


def _parse_graphviz(inner: str) -> Optional[Tuple[_Chain, List[str]]]:
    """Parse a simple ``digraph { A -> B -> ... }`` into a chain + node attr lines.

    Returns ``(chain, attr_lines)`` where ``attr_lines`` are per-node attribute
    statements (``A [label="x"];``) to preserve, or None if not a linear chain.
    """
    text = inner.strip()
    if not _DOT_HEADER_RE.match(text):
        return None
    open_brace = text.find('{')
    close_brace = text.rfind('}')
    if open_brace < 0 or close_brace <= open_brace:
        return None
    inner_body = text[open_brace + 1:close_brace]

    # Split statements on ';' and newlines.
    raw_stmts = re.split(r'[;\n]', inner_body)
    edges: List[Tuple[str, str]] = []
    order_seen: List[str] = []
    node_attr: dict = {}
    defs: dict = {}

    def _touch(nid: str):
        if nid not in defs:
            defs[nid] = nid
            order_seen.append(nid)

    for stmt in raw_stmts:
        s = stmt.strip()
        if not s:
            continue
        low = s.lower()
        for bad in _DOT_BAIL_TOKENS:
            if bad in low:
                return None
        # Global graph/node/edge attribute statement e.g. rankdir=LR, node[...]
        if re.match(r'^(graph|node|edge)\b', low) or '=' in s.split('[')[0]:
            # A bare "rankdir=LR" or "node [shape=box]" — skip (we re-emit our
            # own layout attrs), but do not bail.
            if '->' not in s:
                continue
        em = re.match(
            r'^"?([A-Za-z0-9_]+)"?(?:\s*->\s*"?([A-Za-z0-9_]+)"?)+\s*(\[[^\]]*\])?\s*$',
            s,
        )
        if '->' in s:
            # Chained edges: A -> B -> C
            ids = re.findall(r'"?([A-Za-z0-9_]+)"?', s.split('[')[0])
            if len(ids) < 2:
                return None
            for nid in ids:
                _touch(nid)
            for a, b in zip(ids, ids[1:]):
                edges.append((a, b))
            continue
        nm = _DOT_NODE_DEF_RE.match(s)
        if nm:
            nid, attr = nm.group(1), nm.group(2)
            _touch(nid)
            node_attr[nid] = attr
            continue
        # Unrecognized statement -> bail (conservative).
        return None

    order = _linear_order(order_seen, edges)
    if order is None:
        return None
    attr_lines = [f'  {nid} {node_attr[nid]};' for nid in order if nid in node_attr]
    return _Chain(order, defs), attr_lines


def _rewrite_graphviz_chain(inner: str) -> Optional[str]:
    """Rewrite a linear DOT chain to rankdir=LR with snaked rank rows."""
    parsed = _parse_graphviz(inner)
    if parsed is None:
        return None
    chain, attr_lines = parsed
    rows = _rows_for(chain.order)

    header_m = _DOT_HEADER_RE.match(inner.strip())
    is_digraph = 'digraph' in inner.strip().lower().split('{', 1)[0]
    gtype = 'digraph' if is_digraph else 'graph'

    # Single row -> rankdir=LR straight flow.  Multiple rows -> rankdir=TB so
    # each row is a horizontal rank (via rank=same) and the edge crossing from
    # the end of one row to the start of the next drops to the following rank
    # and IS DRAWN.  (rankdir=LR + rank=same would stack a row VERTICALLY, which
    # is the opposite of what we want — that was the iter1/2 graphviz bug.)
    rankdir = "TB" if len(rows) > 1 else "LR"
    out: List[str] = [f"{gtype} {{", f"  rankdir={rankdir};", "  node [shape=box];"]
    out.extend(attr_lines)
    # Edges along the chain (the real, visible flow — including the inter-row
    # connector, which graphviz draws without collapsing the layout).
    for a, b in zip(chain.order, chain.order[1:]):
        out.append(f"  {a} -> {b};")
    # Force each row onto the same rank so rows stack top-to-bottom while the
    # chain still flows left-to-right within a row.
    if len(rows) > 1:
        for row in rows:
            same = ' '.join(f'"{n}"' for n in row)
            out.append(f"  {{ rank=same; {same} }}")
    out.append("}")
    return '\n'.join(out)
