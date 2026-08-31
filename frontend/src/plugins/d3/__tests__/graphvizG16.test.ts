/**
 * @jest-environment jsdom
 *
 * G-16 regression tests for the graphviz plugin.
 *
 * Covers four defects that all live in graphvizPlugin.ts:
 *   D-127  no lexical repair stage -> parse error delivered as a 30s timeout
 *   D-128  unresolvable colour -> Viz.js fallback to #000000
 *   D-126  dark-mode author light fills clobbered by positional palette cycling
 *   D-133  fill darkened in dark mode but the node's label text not re-themed
 *
 * Every assertion is written as a DIRECTION check: the pre-fix behaviour is
 * asserted on the raw input (fence present, wrong edge operator, rgb()/token
 * present, palette-index dependence, black-on-dark text) so the test would FAIL
 * against the unpatched module. The exports themselves did not exist before the
 * fix, so importing them throws against the pre-fix file.
 */
import {
    repairGraphvizSource,
    stripGraphvizFence,
    unwrapGraphvizJsonEnvelope,
    normalizeGraphvizSmartQuotes,
    normalizeGraphvizSingleQuotes,
    repairGraphvizEdgeDialect,
    repairGraphvizNodeGroups,
    balanceGraphvizBraces,
    normalizeGraphvizColors,
    darkModeNodeFill,
    readableTextColorFor,
    retintNodeLabelForFill,
} from '../graphvizPlugin';

// ---------------------------------------------------------------------------
// D-127  Lexical recovery pipeline
// ---------------------------------------------------------------------------
describe('D-127 lexical repair (recovery)', () => {
    it('strips a markdown fence (w4-01)', () => {
        const raw = '```dot\ndigraph { a -> b; }\n```';
        expect(raw).toContain('```'); // direction: broken input
        const out = stripGraphvizFence(raw);
        expect(out).not.toContain('```');
        expect(out).toContain('digraph { a -> b; }');
    });

    it('unwraps a JSON envelope (w4-15)', () => {
        const raw = '{"type":"graphviz","definition":"digraph{a->b}"}';
        expect(raw.trim()[0]).toBe('{'); // direction: DOT never starts with {
        expect(unwrapGraphvizJsonEnvelope(raw)).toBe('digraph{a->b}');
    });

    it('does NOT treat a real DOT body as an envelope', () => {
        const dot = 'digraph { a -> b }';
        expect(unwrapGraphvizJsonEnvelope(dot)).toBe(dot);
    });

    it('normalizes smart quotes (w4-03)', () => {
        const raw = 'digraph{a[label=\u201CHi\u201D]->b}';
        expect(raw).toContain('\u201C'); // direction
        const out = normalizeGraphvizSmartQuotes(raw);
        expect(out).not.toMatch(/[\u201C\u201D]/);
        expect(out).toContain('label="Hi"');
    });

    it('converts single-quoted attribute values (w4-13)', () => {
        const raw = "digraph{a[label='x']->b}";
        expect(raw).toContain("label='x'"); // direction
        expect(normalizeGraphvizSingleQuotes(raw)).toContain('label="x"');
    });

    it('converts an undirected graph carrying -> into -- (w4-09)', () => {
        const raw = 'graph G { a -> b }';
        expect(raw).toContain('->'); // direction: illegal in an undirected graph
        const out = repairGraphvizEdgeDialect(raw);
        expect(out).toContain('a -- b');
        expect(out).not.toContain('->');
    });

    it('converts a digraph carrying -- into -> (w4-10)', () => {
        const raw = 'digraph G { a -- b }';
        expect(raw).toContain('--'); // direction: illegal in a digraph
        const out = repairGraphvizEdgeDialect(raw);
        expect(out).toContain('a -> b');
        expect(out).not.toMatch(/[^-]--[^-]/);
    });

    it('does not rewrite an edge operator that appears inside a label', () => {
        const raw = 'graph G { a [label="x -> y"] a -- b }';
        const out = repairGraphvizEdgeDialect(raw);
        expect(out).toContain('label="x -> y"'); // masked, untouched
    });

    it('repairs a comma node-group but leaves a legal attr-list trailing comma (w4-14)', () => {
        const raw = 'digraph{ a -> {b, c, d} }';
        expect(raw).toContain('{b, c, d}'); // direction
        const out = repairGraphvizNodeGroups(raw);
        expect(out).toContain('{b c d}');
        // attribute-list trailing comma is legal DOT — must be left alone
        const attr = 'digraph{ a [color=red,] }';
        expect(repairGraphvizNodeGroups(attr)).toBe(attr);
    });

    it('balances unclosed braces (w4-02)', () => {
        const raw = 'digraph G { a -> b;';
        const openMinusClose =
            (raw.match(/\{/g) || []).length - (raw.match(/\}/g) || []).length;
        expect(openMinusClose).toBe(1); // direction: one brace short
        const out = balanceGraphvizBraces(raw);
        expect((out.match(/\{/g) || []).length).toBe((out.match(/\}/g) || []).length);
    });

    it('is a byte-identical no-op on clean, well-formed DOT', () => {
        const clean = 'digraph G {\n  a -> b;\n  b -> c;\n}';
        expect(repairGraphvizSource(clean)).toBe(clean);
    });

    it('composed pipeline recovers a fenced + smart-quoted + unbalanced spec', () => {
        const raw = '```graphviz\ndigraph{a[label=\u201Cx\u201D]->b;';
        const out = repairGraphvizSource(raw);
        expect(out).not.toContain('```');
        expect(out).not.toMatch(/[\u201C\u201D]/);
        expect((out.match(/\{/g) || []).length).toBe((out.match(/\}/g) || []).length);
    });
});

// ---------------------------------------------------------------------------
// D-128  Colour-form normalisation (no fallback to #000000)
// ---------------------------------------------------------------------------
describe('D-128 colour normalisation (recovery)', () => {
    it('converts rgb() to #rrggbb (w4-05)', () => {
        const raw = 'digraph{a[fillcolor="rgb(74,144,217)"]}';
        expect(raw).toContain('rgb('); // direction: Viz.js -> #000000
        expect(normalizeGraphvizColors(raw)).toContain('fillcolor="#4a90d9"');
    });

    it('converts rgba() to #rrggbb dropping alpha', () => {
        const raw = 'digraph{a[color="rgba(0,0,0,0.5)"]}';
        expect(normalizeGraphvizColors(raw)).toContain('color="#000000"');
        expect(normalizeGraphvizColors(raw)).not.toContain('rgba(');
    });

    it('drops an unresolvable token colour so the theme default is inherited (w4-08)', () => {
        const raw = 'digraph{a[fillcolor="var(--brand)" color="$primary"]}';
        expect(raw).toContain('var('); // direction
        const out = normalizeGraphvizColors(raw);
        expect(out).not.toContain('var(');
        expect(out).not.toContain('$primary');
        // no literal black substituted
        expect(out).not.toContain('#000000');
    });

    it('drops a currentColor token colour', () => {
        const raw = 'digraph{a[fontcolor=currentColor]}';
        expect(normalizeGraphvizColors(raw)).not.toMatch(/currentColor/i);
    });

    it('snaps a near-miss colour name (w4-06 cornflower)', () => {
        const raw = 'digraph{a[fillcolor=cornflower]}';
        expect(normalizeGraphvizColors(raw)).toContain('fillcolor=cornflowerblue');
    });

    it('leaves resolvable colours untouched', () => {
        const raw = 'digraph{a[fillcolor="#663399" color=teal b=rebeccapurple]}';
        // rebeccapurple/teal/#663399 all resolve in graphviz -> unchanged
        expect(normalizeGraphvizColors(raw)).toBe(raw);
    });
});

// ---------------------------------------------------------------------------
// D-126  Deterministic dark-mode fill (no positional palette confetti)
// ---------------------------------------------------------------------------
describe('D-126 deterministic dark-mode node fill', () => {
    const PALETTE_BLUES = ['#4361ee', '#3a0ca3', '#7209b7', '#4cc9f0', '#06d6a0', '#118ab2'];

    it('is a pure function of the fill — identical fills map to identical results', () => {
        // Direction: the old code returned nodeColors[nodeIndex % 7], so the
        // result varied with call order. This must NOT depend on order.
        const a = darkModeNodeFill('#ffffcc');
        const b = darkModeNodeFill('#ffffcc');
        const c = darkModeNodeFill('#ffffcc');
        expect(a).toBe(b);
        expect(b).toBe(c);
    });

    it('never returns an arbitrary palette blue for a warm author fill', () => {
        const out = darkModeNodeFill('#ffffcc'); // a deliberate "warn" yellow
        expect(PALETTE_BLUES).not.toContain(out);
        // hue preserved: the darkened yellow keeps red,green >= blue channel
        const r = parseInt(out.slice(1, 3), 16);
        const g = parseInt(out.slice(3, 5), 16);
        const b = parseInt(out.slice(5, 7), 16);
        expect(r).toBeGreaterThanOrEqual(b);
        expect(g).toBeGreaterThanOrEqual(b);
    });

    it('darkens to a dark colour (so a bright node border delimits it on the dark page)', () => {
        const out = darkModeNodeFill('#ffffff');
        const brightness = (() => {
            const r = parseInt(out.slice(1, 3), 16);
            const g = parseInt(out.slice(3, 5), 16);
            const b = parseInt(out.slice(5, 7), 16);
            return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
        })();
        expect(brightness).toBeLessThan(0.5);
    });
});

// ---------------------------------------------------------------------------
// D-133  Label text re-themed against a darkened fill (both themes)
// ---------------------------------------------------------------------------
describe('D-133 label re-theme after darkening', () => {
    it('picks white text on a dark fill and black text on a light fill', () => {
        // dark fill -> white label
        expect(readableTextColorFor('#2e3440')).toBe('#ffffff');
        // light fill -> black label
        expect(readableTextColorFor('#ffffcc')).toBe('#000000');
    });

    function buildNode(textFill: string): { shape: Element; text: Element } {
        const g = document.createElement('g');
        const shape = document.createElement('ellipse');
        const text = document.createElement('text');
        text.setAttribute('fill', textFill);
        g.appendChild(shape);
        g.appendChild(text);
        return { shape, text };
    }

    it('flips stranded black author text to white on a now-dark fill (the D-133 bug)', () => {
        const { shape, text } = buildNode('#000000'); // author/default black text
        expect(text.getAttribute('fill')).toBe('#000000'); // direction: black-on-dark
        retintNodeLabelForFill(shape, darkModeNodeFill('#ffffcc')); // darkened fill
        expect(text.getAttribute('fill')).toBe('#ffffff');
    });

    it('BOTH-THEME parity: same darkened fill yields readable text regardless of the text it started with', () => {
        // The re-theme is decided by the NEW fill only, not the incoming text or
        // a raw isDarkMode flag — so a light-authored (#333333) and a
        // dark-authored (#000000) label both end up readable on the dark fill.
        const dark = darkModeNodeFill('#e0ffe0');
        const nodeA = buildNode('#333333');
        const nodeB = buildNode('#000000');
        retintNodeLabelForFill(nodeA.shape, dark);
        retintNodeLabelForFill(nodeB.shape, dark);
        expect(nodeA.text.getAttribute('fill')).toBe('#ffffff');
        expect(nodeB.text.getAttribute('fill')).toBe('#ffffff');
        // and if a fill were light, both would resolve to black (the other theme)
        const nodeC = buildNode('#ffffff');
        retintNodeLabelForFill(nodeC.shape, '#ffffcc');
        expect(nodeC.text.getAttribute('fill')).toBe('#000000');
    });
});
