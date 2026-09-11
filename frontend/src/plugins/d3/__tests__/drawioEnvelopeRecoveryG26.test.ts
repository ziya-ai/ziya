/**
 * G-26 / D-028 — drawio XML envelope + colour recovery regression guards.
 *
 * SCOPE / HONESTY NOTE: every mechanism asserted here was already implemented
 * in source under prior work (D-114 measured-contrast font pick, D-115 fence
 * strip / <root> synthesis / post-</mxfile> truncation, D-116 bare angle-bracket
 * escape, D-117 single-quote + entity-escape de-quote, D-118 style-key separator
 * / vertex inference, D-119 unparseable-colour recovery). Stage-2 iteration 20
 * confirmed all nine D-028 spec shapes are handled and WIRED into
 * normalizeDrawIOXml / the render style pass, so NO new source change was made
 * for D-028.
 *
 * These are therefore REGRESSION GUARDS (green on the current tree), not proof
 * of a new fix. Their "fails-without-the-fix" direction is against the PRE-D-115/
 * D-116 tree: without the fence strip, <root> synthesis, post-</mxfile>
 * truncation and bare angle-bracket escape, each input below leaves
 * normalizeDrawIOXml output that the maxGraph decoder cannot reach (the 30s
 * empty-DOM hang the sweep recorded). The envelope-recovery cases (w4-01/05/13/
 * w4-15) had no direct normalizeDrawIOXml unit coverage before this file; the
 * colour cases (w4-08/w4-10) are additionally covered by drawioG60.test.ts and
 * re-pinned here only to tie the whole D-028 cluster together.
 *
 * Faithful inputs: each fixture is a reduced-but-representative slice of the
 * real failing spec at .ziya/gfx-sweep/specs/drawio/<id>.json.
 */
import {
    normalizeDrawIOXml,
    pickReadableFontColor,
    resolveUnparseableCellColors,
    MAXGRAPH_DEFAULT_VERTEX_FILL,
} from '../drawioPlugin';
import { calculateContrastRatio } from '../../../utils/colorUtils';

describe('D-028 / w4-01 — leading ```xml markdown fence is stripped', () => {
    const fenced =
        '```xml\n' +
        '<mxGraphModel dx="900" dy="600"><root>' +
        '<mxCell id="0"/><mxCell id="1" parent="0"/>' +
        '<mxCell id="a" value="Ingest" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#dae8fc;" vertex="1" parent="1">' +
        '<mxGeometry x="40" y="40" width="120" height="50" as="geometry"/></mxCell>' +
        '</root></mxGraphModel>\n' +
        '```';

    it('removes both fences and leaves a decodable model', () => {
        const out = normalizeDrawIOXml(fenced);
        // no fence survives (a fence line makes the parser never reach <mxGraphModel>)
        expect(out.includes('```')).toBe(false);
        // the actual model is preserved
        expect(out).toContain('<mxGraphModel');
        expect(out).toContain('value="Ingest"');
        // pre-fix direction: the RAW input begins with a non-XML fence line
        expect(fenced.trimStart().startsWith('```')).toBe(true);
    });
});

describe('D-028 / w4-13 — a missing <root> wrapper is synthesised', () => {
    // Real w4-13 emits base cells but NO <root>; maxGraph cannot decode it.
    const noRoot =
        '<mxGraphModel dx="900" dy="600" grid="0">' +
        '<mxCell id="d1" value="Flat A" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;" vertex="1" parent="1">' +
        '<mxGeometry x="40" y="50" width="150" height="60" as="geometry"/></mxCell>' +
        '</mxGraphModel>';

    it('inserts <root> + base cells 0/1 only when absent', () => {
        // pre-fix direction: no <root> element in the raw model
        expect(/<root\b/.test(noRoot)).toBe(false);
        const out = normalizeDrawIOXml(noRoot);
        expect(/<root\b/.test(out)).toBe(true);
        expect(out).toContain('<mxCell id="0"');
        expect(out).toContain('<mxCell id="1" parent="0"');
        expect(out).toContain('</root>');
        // the author cell survives the wrap
        expect(out).toContain('value="Flat A"');
    });

    it('leaves a well-formed model that already has <root> byte-identical w.r.t. the root wrapper', () => {
        const wellFormed =
            '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>' +
            '<mxCell id="x" value="A" style="fillColor=#fff" vertex="1" parent="1">' +
            '<mxGeometry x="0" y="0" width="80" height="40" as="geometry"/></mxCell>' +
            '</root></mxGraphModel>';
        const out = normalizeDrawIOXml(wellFormed);
        // exactly one root open tag — no duplicate synthesis
        expect((out.match(/<root\b/g) || []).length).toBe(1);
    });
});

describe('D-028 / w4-15 — prose after </mxfile> is truncated', () => {
    const withTrailingProse =
        '<mxfile host="Electron" version="14.6.13"><diagram id="abc123" name="Page-1">' +
        '<mxGraphModel dx="900" dy="600"><root>' +
        '<mxCell id="0"/><mxCell id="1" parent="0"/>' +
        '<mxCell id="o1" value="Legacy" style="fillColor=#dae8fc;" vertex="1" parent="1">' +
        '<mxGeometry x="10" y="10" width="90" height="40" as="geometry"/></mxCell>' +
        '</root></mxGraphModel></diagram></mxfile>\n' +
        'This diagram shows the legacy flow described above.';

    it('drops everything past the final </mxfile>', () => {
        // pre-fix direction: the raw input carries trailing prose that hangs the parser
        expect(withTrailingProse.includes('This diagram shows')).toBe(true);
        const out = normalizeDrawIOXml(withTrailingProse);
        expect(out.trimEnd().endsWith('</mxfile>')).toBe(true);
        expect(out.includes('This diagram shows')).toBe(false);
        expect(out).toContain('value="Legacy"');
    });
});

describe('D-028 / w4-05 — bare < and > in an attribute value are escaped, entities preserved', () => {
    const bareAngles =
        '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>' +
        '<mxCell id="m1" value="R&D < Ops > Net" style="rounded=0;fillColor=#dae8fc;" vertex="1" parent="1">' +
        '<mxGeometry x="40" y="40" width="180" height="50" as="geometry"/></mxCell>' +
        '<mxCell id="m2" value="Auth &amp; Authz" style="rounded=0;fillColor=#dae8fc;" vertex="1" parent="1">' +
        '<mxGeometry x="280" y="40" width="180" height="50" as="geometry"/></mxCell>' +
        '</root></mxGraphModel>';

    it('escapes the raw angle brackets and bare ampersand that would open a phantom tag', () => {
        const out = normalizeDrawIOXml(bareAngles);
        expect(out).toContain('R&amp;D &lt; Ops &gt; Net');
        // no raw " < " / " > " left inside the label region
        expect(out).not.toContain('value="R&D < Ops > Net"');
    });

    it('does NOT double-escape an already-encoded &amp; entity (guard)', () => {
        const out = normalizeDrawIOXml(bareAngles);
        expect(out).toContain('Auth &amp; Authz');
        expect(out.includes('&amp;amp;')).toBe(false);
    });
});

describe('D-028 / w4-08 + w4-10 — colour recovery (cross-referenced with drawioG60)', () => {
    it('w4-08: picks readable BLACK on the CSS-named fill cornflowerblue (measured, not luminance)', () => {
        // cornflowerblue now resolves to #6495ed via the shared named-colour table,
        // so the contrast pass can MEASURE it: white = 2.97:1 (below floor), black = 7.06:1.
        const font = pickReadableFontColor('cornflowerblue');
        expect(font).toBe('#000000');
        // opaque fill → the label sits on the fill, identical contrast in BOTH themes
        expect(calculateContrastRatio(font, 'cornflowerblue')).toBeGreaterThanOrEqual(4.5);
        // direction: the value it displaces (white) is below the text floor
        expect(calculateContrastRatio('#ffffff', 'cornflowerblue')).toBeLessThan(4.5);
    });

    it('w4-10: an unparseable theme token is recovered to the default node fill, not solid black', () => {
        const style: Record<string, any> = { fillColor: 'var(--ziya-node-bg)', value: 'Themed A' };
        resolveUnparseableCellColors(style);
        expect(style.fillColor).toBe(MAXGRAPH_DEFAULT_VERTEX_FILL);
        // no longer the identical #000000 slab whose border vanished on the dark canvas
        expect(style.fillColor.toLowerCase()).not.toBe('#000000');
    });
});
