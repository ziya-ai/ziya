/**
 * @jest-environment jsdom
 *
 * G-4686aa / D-125 regression: the eight w4 lexical-malformation specs must
 * survive repairGraphvizSource and reach layout as well-formed DOT, instead of
 * dying pre-layout and being delivered as a silent 30s watchdog timeout
 * (signature no-lexical-repair-stage:timeout).
 *
 * Seven of the eight were already recovered by the D-127 pipeline. The residual
 * failure was graphviz-w4-15: the JSON envelope IS unwrapped, but the label it
 * carried — `a [label=Build Step]` — is an UNQUOTED MULTIWORD value, a hard
 * Viz.js parse error at the second word. `normalizeGraphvizUnquotedMultiwordValues`
 * closes that gap. The DIRECTION check below asserts the raw unwrapped body is
 * broken and only the repaired body is well-formed, so this file FAILS against
 * the pre-fix module (the export did not exist and the value stayed unquoted).
 *
 * Well-formed here means the structural invariants a DOT lexer requires:
 * no markdown fence, no smart quotes, balanced braces, an edge operator that
 * matches the graph keyword, no comma node-group, and no unquoted multiword
 * attribute value. These are the exact constructs that each died pre-layout.
 */
import {
    repairGraphvizSource,
    normalizeGraphvizUnquotedMultiwordValues,
} from '../graphvizPlugin';

// The eight defect specs, verbatim from .ziya/gfx-sweep/specs/graphviz/.
const SPECS: Record<string, string> = {
    'w4-01': '```dot\ndigraph G {\n  rankdir=LR;\n  a [label="Fetch"];\n  b [label="Parse"];\n  c [label="Store"];\n  a -> b -> c;\n}\n```',
    'w4-02': 'digraph G {\n  rankdir=TB;\n  subgraph cluster_api {\n    label="API";\n    login; logout;\n  }\n  login -> logout;\n  logout -> done;\n',
    'w4-03': 'digraph G {\n  rankdir=LR;\n  a [label=\u201CDeploy\u201D];\n  b [label=\u201CUser\u2019s Session\u201D];\n  c [label=\u2018Cleanup\u2019];\n  a -> b -> c;\n}',
    'w4-09': 'graph G {\n  rankdir=LR;\n  a [label="Start"];\n  b [label="Middle"];\n  c [label="End"];\n  a -> b -> c;\n  a -> c [label="skip"];\n}',
    'w4-10': 'digraph G {\n  rankdir=LR;\n  a [label="Node A"];\n  b [label="Node B"];\n  c [label="Node C"];\n  a -- b -- c;\n  a -- c;\n}',
    'w4-13': "digraph G {\n  rankdir=LR;\n  node [shape=box style=filled fillcolor='#dbeafe' fontcolor='#000000'];\n  a [label='Login'];\n  b [label='Verify'];\n  c [label='Grant'];\n  a -> b [label='ok' color='steelblue'];\n  b -> c [label='token'];\n}",
    'w4-14': 'digraph G {\n  rankdir=LR;\n  node [shape=box, style=filled, fillcolor="#fef3c7", fontcolor="#000000",];\n  a [label="One",];\n  b [label="Two",];\n  c [label="Three",];\n  d [label="Sink"];\n  { a, b, c, } -> d;\n  a -> b [label="ok", color=green,];\n  c -> d [,];\n}',
    'w4-15': '{"type": "graphviz", "definition": "digraph G { rankdir=LR; a [label=Build Step]; b [label=\\"Test\\"]; c [label=\\"Ship\\"]; a -> b -> c; }"}',
};

/** Mask double-quoted strings so the structural checks below only inspect the
 *  DOT skeleton, never label text (which may legitimately contain -> or ,). */
function maskStrings(s: string): string {
    return s.replace(/"(?:\\.|[^"\\])*"/g, '""');
}

function assertWellFormed(id: string, out: string): void {
    const skel = maskStrings(out);
    // no markdown fence survived
    expect(out).not.toContain('```');
    // no unicode smart quotes survived
    expect(out).not.toMatch(/[\u201C\u201D\u2018\u2019]/);
    // braces balanced
    expect((skel.match(/\{/g) || []).length).toBe((skel.match(/\}/g) || []).length);
    // edge operator matches the graph keyword
    const directed = /\b(?:strict\s+)?digraph\b/i.test(skel);
    if (directed) {
        expect(skel).not.toMatch(/[^-]--[^-]/); // no undirected op in a digraph
    } else if (/\b(?:strict\s+)?graph\b/i.test(skel)) {
        expect(skel).not.toContain('->'); // no directed op in an undirected graph
    }
    // no comma-separated node group survived
    const groups = skel.match(/\{[^{}]*\}/g) || [];
    for (const g of groups) {
        const body = g.slice(1, -1);
        if (!/[=;]|->|--/.test(body)) {
            expect(body).not.toContain(',');
        }
    }
    // no unquoted multiword attribute value survived (label=Build Step)
    expect(skel).not.toMatch(/=\s*[A-Za-z_][\w.#-]*[ \t]+[A-Za-z_][\w.#-]*\s*[,;\]]/);
}

describe('D-125 lexical malformations reach layout as well-formed DOT', () => {
    for (const [id, def] of Object.entries(SPECS)) {
        it(`repairs graphviz-${id}`, () => {
            assertWellFormed(id, repairGraphvizSource(def));
        });
    }
});

describe('D-125 residual: unquoted multiword attribute value (w4-15)', () => {
    it('quotes an unquoted multiword value; leaves single-word and quoted values alone', () => {
        // DIRECTION: the unwrapped w4-15 body is a parse error until quoted.
        const rawBody = 'digraph G { rankdir=LR; a [label=Build Step]; b [label="Test"]; }';
        expect(rawBody).toMatch(/label=Build Step\]/); // broken input
        const fixed = normalizeGraphvizUnquotedMultiwordValues(rawBody);
        expect(fixed).toContain('label="Build Step"');
        // single-word values untouched
        expect(fixed).toContain('rankdir=LR');
        // already-quoted value untouched
        expect(fixed).toContain('label="Test"');
    });

    it('does not quote space-separated single-word attributes (shape=box style=filled)', () => {
        const s = 'digraph{ node [shape=box style=filled color=red] }';
        expect(normalizeGraphvizUnquotedMultiwordValues(s)).toBe(s);
    });

    it('is idempotent', () => {
        const s = 'digraph G { a [label=Build Step]; }';
        const once = normalizeGraphvizUnquotedMultiwordValues(s);
        expect(normalizeGraphvizUnquotedMultiwordValues(once)).toBe(once);
    });

    it('the full pipeline recovers the w4-15 envelope end-to-end', () => {
        const out = repairGraphvizSource(SPECS['w4-15']);
        expect(out).not.toContain('{"type"'); // envelope unwrapped
        expect(out).toContain('label="Build Step"'); // multiword value quoted
    });
});
