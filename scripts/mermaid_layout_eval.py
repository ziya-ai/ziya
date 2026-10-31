#!/usr/bin/env python3
"""Compare Mermaid's dagre and ELK layouts on hard diagrams, per diagram type.

Each spec is rendered twice in headless Chromium with the mermaid build in
frontend/node_modules (the same config mermaidPlugin.ts passes: classic look,
default theme, Arial 14, flowchart htmlLabels/basis), once per layout.  The
geometry is scored in the page from the live SVG, so the numbers do not
depend on a screenshot:

  crossings        edge/edge intersections away from shared endpoints
  edge_thru_node   (edge, node) pairs where an edge passes through a node
                   that is not one of its ends
  edge_thru_label  (edge, label) pairs where an edge crosses another edge's label
  label_label      overlapping edge-label pairs
  label_node       edge labels overlapping a node
  end_on_cluster   edge ends that stop on a cluster border, not on a node
  area, aspect     viewBox size; edge_len is total edge length
  ms               render time

Mermaid's own preprocessing is not applied (our enhancer pipeline is the same
for both arms), so specs here are written in syntax mermaid accepts as-is.

Usage:  python3 scripts/mermaid_layout_eval.py [--only NAME,...] [--out DIR]
Writes  <out>/results.jsonl and <out>/<spec>.<layout>.{svg,png}.
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MERMAID_JS = ROOT / "frontend/node_modules/mermaid/dist/mermaid.min.js"
LAYOUTS = ("dagre", "elk")


def _flow_60() -> str:
    rnd = random.Random(7)
    n = 60
    lines = ["flowchart TD"]
    for i in range(n):
        for j in rnd.sample(range(i + 1, min(n, i + 12)), k=min(2, max(0, n - i - 1))):
            lines.append(f"    N{i} --> N{j}")
    for _ in range(8):  # feedback edges
        a = rnd.randrange(20, n)
        lines.append(f"    N{a} -.-> N{rnd.randrange(0, a - 10)}")
    return "\n".join(lines)


def _flow_hub() -> str:
    lines = ["flowchart LR", "    H((Dispatcher))"]
    lines += [f"    H -->|route {i}| S{i}[Service {i}]" for i in range(1, 16)]
    lines += ["    S1 --> S2", "    S4 --> S9", "    S12 --> S3", "    S15 -.-> H"]
    return "\n".join(lines)


def _er_25() -> str:
    rels = [
        "CUSTOMER ||--o{ ADDRESS : has", "CUSTOMER ||--o{ ORDER : places",
        "ORDER ||--|{ ORDER_ITEM : contains", "PRODUCT ||--o{ ORDER_ITEM : \"ordered as\"",
        "CATEGORY ||--o{ PRODUCT : groups", "CATEGORY |o--o{ CATEGORY : parent",
        "SUPPLIER ||--o{ PRODUCT : supplies", "WAREHOUSE ||--o{ STOCK : holds",
        "PRODUCT ||--o{ STOCK : \"stocked as\"", "ORDER ||--o{ SHIPMENT : \"ships in\"",
        "CARRIER ||--o{ SHIPMENT : carries", "WAREHOUSE ||--o{ SHIPMENT : \"ships from\"",
        "ORDER ||--o{ PAYMENT : \"paid by\"", "PAYMENT ||--o| INVOICE : produces",
        "PAYMENT ||--o{ REFUND : \"refunded by\"", "CUSTOMER ||--o{ REVIEW : writes",
        "PRODUCT ||--o{ REVIEW : receives", "CUSTOMER ||--o| CART : owns",
        "CART ||--o{ CART_ITEM : contains", "PRODUCT ||--o{ CART_ITEM : \"added as\"",
        "COUPON }o--o{ ORDER : \"applied to\"", "PROMOTION ||--o{ COUPON : issues",
        "PROMOTION }o--o{ PRODUCT : covers", "EMPLOYEE ||--o{ REFUND : approves",
        "EMPLOYEE |o--o{ EMPLOYEE : manages", "ORDER_ITEM ||--o| RETURN : \"returned as\"",
        "RETURN ||--o| REFUND : triggers", "REGION ||--o{ TAX_RATE : defines",
        "ADDRESS }o--|| REGION : \"located in\"", "WAREHOUSE }o--|| REGION : \"located in\"",
    ]
    return "erDiagram\n" + "\n".join("    " + r for r in rels)


SPECS = {
    # ---- state -----------------------------------------------------------
    "state-macmove": ("state", """stateDiagram-v2
    [*] --> OnPrimary
    OnPrimary --> OnPrimary : MAC-move → redundant,<br/>primary reads UP<br/>(set pending, return GT_BAD_PARAM)
    OnPrimary --> OnRedundant : MAC-move → redundant,<br/>primary reads DOWN<br/>(or primary not "direct")
    state OnPrimary {
        [*] --> idle
        idle --> pending : spurious/flap MAC-move refused
        pending --> idle : MAC-move → primary<br/>(SwitchToPrimary clears pending)
        pending --> pending : Poll#58; primary still UP
    }
    OnPrimary --> OnRedundant : Poll (counters thread) sees<br/>pending && primary DOWN<br/>→ complete failover
    OnRedundant --> OnPrimary : MAC-move → primary"""),
    "state-nested3": ("state", """stateDiagram-v2
    [*] --> Idle
    Idle --> Active : start
    state Active {
        [*] --> Loading
        Loading --> Running : loaded
        state Running {
            [*] --> Normal
            Normal --> Degraded : error rate high
            Degraded --> Normal : recovered
            state Degraded {
                [*] --> Retrying
                Retrying --> Backoff : retry failed
                Backoff --> Retrying : timer fired
            }
        }
        Running --> Paused : pause
        Paused --> Running : resume
    }
    Active --> Idle : stop
    Backoff --> Failed : retries exhausted
    Failed --> Idle : operator reset
    Active --> [*]"""),
    "state-breaker-forkjoin": ("state", """stateDiagram-v2
    [*] --> Closed
    Closed --> Open : failures exceed threshold<br/>within window
    Closed --> Closed : success<br/>reset counter
    Closed --> Closed : failure<br/>increment counter
    Open --> HalfOpen : cooldown elapsed
    Open --> Open : request<br/>reject fast
    HalfOpen --> Closed : probe succeeded
    HalfOpen --> Open : probe failed
    HalfOpen --> HalfOpen : concurrent request<br/>rejected
    state fork1 <<fork>>
    state join1 <<join>>
    Closed --> fork1 : shutdown
    fork1 --> Drain
    fork1 --> Flush
    Drain --> join1
    Flush --> join1
    join1 --> [*]"""),
    "state-concurrent": ("state", """stateDiagram-v2
    [*] --> Device
    state Device {
        [*] --> Off
        Off --> On : power
        On --> Off : power
        --
        [*] --> Disconnected
        Disconnected --> Connecting : link up
        Connecting --> Connected : handshake ok
        Connecting --> Disconnected : timeout
        Connected --> Disconnected : link down
    }
    Device --> [*] : unplug"""),
    # ---- flowchart -------------------------------------------------------
    "flow-feedback": ("flowchart", """flowchart LR
    A[Ingest] --> B[Parse] --> C[Validate] --> D[Enrich] --> E[Store] --> F[Index]
    C -->|invalid| A
    E -->|write conflict| C
    F -->|reindex request| B
    D -.->|cache miss| B
    F --> G[Notify]
    G -->|ack timeout| E"""),
    "flow-subgraphs": ("flowchart", """flowchart TB
    subgraph Client
        direction LR
        UI[Web UI] --> SDK[Client SDK]
        CLI[CLI] --> SDK
    end
    subgraph Edge
        LB[Load balancer] --> GW[API gateway]
        GW --> Auth[Auth service]
    end
    subgraph Core
        direction LR
        Orders[Order service] --> Inv[Inventory]
        Orders --> Pay[Payments]
        Pay --> Ledger[(Ledger DB)]
        Inv --> InvDB[(Inventory DB)]
    end
    subgraph Async
        Q[[Event bus]] --> W1[Email worker]
        Q --> W2[Analytics worker]
    end
    SDK --> LB
    GW --> Orders
    Auth -.-> Orders
    Orders --> Q
    Pay --> Q
    W2 --> Ledger
    Inv -->|low stock| Q"""),
    "flow-hub15": ("flowchart", _flow_hub()),
    "flow-60x120": ("flowchart", _flow_60()),
    "flow-longlabels": ("flowchart", """flowchart TD
    Start([Request]) --> Check{Cache<br/>hit?}
    Check -->|yes, entry fresh<br/>and signature valid| Serve[Serve from cache]
    Check -->|no, or stale<br/>beyond grace window| Fetch[Fetch from origin<br/>with conditional headers]
    Fetch -->|304 not modified<br/>refresh TTL| Serve
    Fetch -->|200 OK<br/>store new entry| Store[Write cache<br/>and update index]
    Fetch -->|5xx or timeout<br/>after 3 retries| Stale{Stale entry<br/>available?}
    Stale -->|yes, within<br/>max-stale| Serve
    Stale -->|no| Fail[Return 503<br/>with Retry-After]
    Store --> Serve"""),
    # ---- class -----------------------------------------------------------
    "class-hierarchy": ("class", """classDiagram
    class Shape {
        <<abstract>>
        +area() float
        +perimeter() float
    }
    Shape <|-- Polygon
    Shape <|-- Ellipse
    Polygon <|-- Triangle
    Polygon <|-- Quadrilateral
    Quadrilateral <|-- Rectangle
    Quadrilateral <|-- Rhombus
    Rectangle <|-- Square
    Rhombus <|-- Square
    Ellipse <|-- Circle
    Canvas "1" o-- "*" Shape : contains
    Canvas --> Renderer : uses
    Renderer ..> Shape : draws
    Layer "1" *-- "*" Shape
    Canvas "1" *-- "*" Layer
    Shape --> Style : styled by
    Style --> Color
    Square --> Color : fill"""),
    "class-namespaces": ("class", """classDiagram
    namespace Domain {
        class Order {
            +UUID id
            +total() Money
        }
        class OrderLine {
            +String sku
            +int qty
        }
        class Customer
    }
    namespace Infra {
        class OrderRepo {
            +save(Order o)
            +find(UUID id) Order
        }
        class EventBus
    }
    Order "1" *-- "1..*" OrderLine
    Customer "1" --> "*" Order : places
    Customer --> Customer : referred by
    OrderRepo ..> Order
    OrderRepo --> EventBus : publishes
    Order --> Order : replaces"""),
    # ---- ER ----------------------------------------------------------------
    "er-school": ("er", """erDiagram
    STUDENT ||--o{ ENROLLMENT : has
    COURSE ||--o{ SECTION : "offered as"
    SECTION ||--o{ ENROLLMENT : enrolls
    COURSE ||--o{ COURSE_PREREQ : requires
    COURSE ||--o{ COURSE_PREREQ : "is prereq of"
    INSTRUCTOR ||--o{ SECTION : teaches
    DEPARTMENT ||--o{ COURSE : offers
    DEPARTMENT ||--o{ INSTRUCTOR : employs
    INSTRUCTOR |o--o{ INSTRUCTOR : mentors
    STUDENT }o--o{ CLUB : joins
    STUDENT {
        int id PK
        string name
    }
    ENROLLMENT {
        int student_id FK
        int section_id FK
        string grade
    }"""),
    "er-commerce25": ("er", _er_25()),
}

MEASURE_JS = r"""
async ([id, def, layout]) => {
  mermaid.initialize({
    startOnLoad: false, theme: 'default', look: 'classic', layout,
    securityLevel: 'loose', maxEdges: 2000, maxTextSize: 90000,
    fontFamily: '"Arial", sans-serif', fontSize: 14,
    flowchart: { htmlLabels: true, curve: 'basis', padding: 20, nodeSpacing: 60,
                 rankSpacing: 50, diagramPadding: 8 },
  });
  const host = document.getElementById('host');
  host.innerHTML = '';
  const t0 = performance.now();
  let svgText;
  try { ({ svg: svgText } = await mermaid.render(id, def)); }
  catch (e) { return { error: String(e && e.message || e) }; }
  const ms = performance.now() - t0;
  host.innerHTML = svgText;
  const svg = host.querySelector('svg');
  const vb = svg.viewBox.baseVal;
  svg.style.maxWidth = 'none';
  svg.setAttribute('width', vb.width); svg.setAttribute('height', vb.height);

  const box = el => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; };
  const inside = (p, b, pad) => p.x > b.x - pad && p.x < b.x + b.w + pad && p.y > b.y - pad && p.y < b.y + b.h + pad;
  const overlap = (a, b) => Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) *
                            Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));

  const nodeEls = [...svg.querySelectorAll('g.node')];
  const nodes = nodeEls.map(box).filter(b => b.w > 0 && b.h > 0);
  const clusters = [...svg.querySelectorAll('g.cluster, g.statediagram-cluster')].map(box).filter(b => b.w > 0);

  const edgeEls = [...new Set(svg.querySelectorAll('g.edgePaths > path, g.edges > path, path.relation, path.relationshipLine'))]
    .filter(p => !p.closest('marker'));
  const edges = edgeEls.map(p => {
    const L = p.getTotalLength(); const m = p.getScreenCTM();
    const n = Math.max(2, Math.ceil(L / 6)); const pts = [];
    for (let i = 0; i <= n; i++) {
      const q = p.getPointAtLength(L * i / n);
      pts.push({ x: m.a * q.x + m.c * q.y + m.e, y: m.b * q.x + m.d * q.y + m.f });
    }
    const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
    return { id: p.getAttribute('data-id') || p.id, L, pts,
             bb: { x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) } };
  });

  const labelEls = [...svg.querySelectorAll('g.edgeLabel')].filter(g => (g.textContent || '').trim());
  const labels = labelEls.map(g => {
    const inner = g.querySelector('[data-id]');
    return { id: inner ? inner.getAttribute('data-id') : null, b: box(g) };
  }).filter(l => l.b.w > 0 && l.b.h > 0);

  // edge/edge crossings, ignoring points near any endpoint (shared nodes)
  const segX = (p1, p2, p3, p4) => {
    const d = (p2.x - p1.x) * (p4.y - p3.y) - (p2.y - p1.y) * (p4.x - p3.x);
    if (Math.abs(d) < 1e-9) return null;
    const t = ((p3.x - p1.x) * (p4.y - p3.y) - (p3.y - p1.y) * (p4.x - p3.x)) / d;
    const u = ((p3.x - p1.x) * (p2.y - p1.y) - (p3.y - p1.y) * (p2.x - p1.x)) / d;
    return (t >= 0 && t <= 1 && u >= 0 && u <= 1) ? { x: p1.x + t * (p2.x - p1.x), y: p1.y + t * (p2.y - p1.y) } : null;
  };
  const near = (p, q, r) => Math.hypot(p.x - q.x, p.y - q.y) < r;
  let crossings = 0;
  for (let i = 0; i < edges.length; i++) for (let j = i + 1; j < edges.length; j++) {
    const A = edges[i], B = edges[j];
    if (overlap({ ...A.bb, w: A.bb.w + 1, h: A.bb.h + 1 }, { ...B.bb, w: B.bb.w + 1, h: B.bb.h + 1 }) <= 0) continue;
    const ends = [A.pts[0], A.pts[A.pts.length - 1], B.pts[0], B.pts[B.pts.length - 1]];
    const hits = [];
    for (let a = 0; a < A.pts.length - 1; a++) for (let b = 0; b < B.pts.length - 1; b++) {
      const x = segX(A.pts[a], A.pts[a + 1], B.pts[b], B.pts[b + 1]);
      if (x && !ends.some(e => near(x, e, 14)) && !hits.some(h => near(x, h, 8))) hits.push(x);
    }
    crossings += hits.length;
  }

  let edgeThruNode = 0, endOnCluster = 0, edgeThruLabel = 0;
  for (const e of edges) {
    const a = e.pts[0], z = e.pts[e.pts.length - 1];
    for (const n of nodes) {
      if (inside(a, n, 12) || inside(z, n, 12)) continue;
      const shrunk = { x: n.x + 3, y: n.y + 3, w: n.w - 6, h: n.h - 6 };
      if (shrunk.w > 0 && e.pts.some(p => inside(p, shrunk, 0))) edgeThruNode++;
    }
    for (const end of [a, z]) {
      if (nodes.some(n => inside(end, n, 12))) continue;
      if (clusters.some(c => inside(end, c, 6) && !inside(end, { x: c.x + 6, y: c.y + 6, w: c.w - 12, h: c.h - 12 }, 0))) endOnCluster++;
    }
    for (const l of labels) {
      if (l.id && l.id === e.id) continue;
      const s = { x: l.b.x + 2, y: l.b.y + 2, w: l.b.w - 4, h: l.b.h - 4 };
      if (e.pts.some(p => inside(p, s, 0))) edgeThruLabel++;
    }
  }
  let labelLabel = 0, labelNode = 0;
  for (let i = 0; i < labels.length; i++) {
    for (let j = i + 1; j < labels.length; j++) if (overlap(labels[i].b, labels[j].b) > 4) labelLabel++;
    for (const n of nodes) if (overlap(labels[i].b, n) > 4) labelNode++;
  }
  return {
    ms: Math.round(ms), w: Math.round(vb.width), h: Math.round(vb.height),
    area: Math.round(vb.width * vb.height), aspect: +(vb.width / vb.height).toFixed(2),
    nodes: nodes.length, edges: edges.length, labels: labels.length, clusters: clusters.length,
    crossings, edge_thru_node: edgeThruNode, edge_thru_label: edgeThruLabel,
    label_label: labelLabel, label_node: labelNode, end_on_cluster: endOnCluster,
    edge_len: Math.round(edges.reduce((s, e) => s + e.L, 0)),
    svg: svgText,
  };
}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default=str(ROOT / ".ziya/layout-eval"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    names = [n for n in SPECS if not args.only or n in args.only.split(",")]

    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 2400, "height": 1600})
        page.set_content('<html><body style="margin:0;background:#fff"><div id="host" '
                         'style="display:inline-block"></div></body></html>')
        page.add_script_tag(path=str(MERMAID_JS))
        version = page.evaluate("() => (window.mermaid && mermaid.version) ? mermaid.version() : '?'")
        for name in names:
            dtype, definition = SPECS[name]
            for layout in LAYOUTS:
                r = page.evaluate(MEASURE_JS, [f"m{int(time.time()*1000)}", definition, layout])
                svg = r.pop("svg", None)
                if svg:
                    (out / f"{name}.{layout}.svg").write_text(svg)
                    page.locator("#host svg").screenshot(path=str(out / f"{name}.{layout}.png"))
                rec = {"spec": name, "type": dtype, "layout": layout, "mermaid": version, **r}
                results.append(rec)
                print(json.dumps(rec))
        browser.close()
    with open(out / "results.jsonl", "w") as f:
        for rec in results:
            f.write(json.dumps(rec) + "\n")


if __name__ == "__main__":
    main()
