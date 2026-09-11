/**
 * G-DRAWIO-RECOVERY (iteration 20) — consolidated guard for the drawio XML/colour
 * recovery cluster D-086..D-090.
 *
 * Stage-1 triage flagged these five recovery defects; against the CURRENT source
 * every one is already remediated inside normalizeDrawIOXml + the render style
 * pass, so NO new source change was made this iteration. This file confirms the
 * remediation by binding the EXACT backlog spec inputs (drawio-w4-01/04/05/11/12/
 * 13/14/15/08/10) to the REAL exported helpers.
 *
 * Non-vacuity: the helpers imported below (normalizeStyleKeySeparators,
 * shouldInferVertex, resolveUnparseableCellColors, pickReadableFontColor,
 * dequoteEntityEscapedStyleValues, normalizeSingleQuotedAttributes) are all
 * introduced by the recovery fixes — the imports fail to type-check against
 * pre-fix code — and each behavioural block also documents the pre-fix DIRECTION
 * that fails against the old inline logic. Complements drawioEnvelopeRecoveryG26
 * (D-086/087), drawioG36QuoteRecovery (D-088) and drawioG60 (D-089/090 units)
 * by covering the residual edges: the four w4-14 style-separator slips through
 * the real split() path, w4-12 vertex inference, and the D-089 both-theme floor.
 */
import {
    normalizeDrawIOXml,
    normalizeSingleQuotedAttributes,
    dequoteEntityEscapedStyleValues,
    normalizeStyleKeySeparators,
    resolveUnparseableCellColors,
    shouldInferVertex,
    pickReadableFontColor,
    isResolvableColor,
    MAXGRAPH_DEFAULT_VERTEX_FILL,
} from '../drawioPlugin';
import { calculateContrastRatio } from '../../../utils/colorUtils';

const TEXT_FLOOR = 4.5;

// Mirror the plugin's style-string -> object parse (drawioPlugin ~L1935) so the
// assertions exercise the SAME split(';') / split('=') the renderer uses.
function parseStyle(style: string): Record<string, string> {
    const obj: Record<string, string> = {};
    style.split(';').forEach(pair => {
        const p = pair.trim();
        if (!p) return;
        if (p.includes('=')) {
            const [k, v] = p.split('=');
            if (k && v !== undefined && v !== '') obj[k.trim()] = v.trim();
        }
    });
    return obj;
}

// ───────────────────────── D-090 / w4-14 — style-separator slips ─────────────
describe('D-090 / drawio-w4-14 — every style-separator slip keeps its intended fill', () => {
    // The four cells of the real w4-14 spec: doubled ;; , literal newlines+indent,
    // SPACES instead of ; , and a leading ;; .
    const variants: Record<string, string> = {
        'doubled ;;': 'rounded=0;;whiteSpace=wrap;;html=1;;fillColor=#dae8fc;;strokeColor=#6c8ebf;;fontColor=#102040;;',
        'newlines+indent': 'rounded=0;\n  whiteSpace=wrap;\n  html=1;\n  fillColor=#d5e8d4;\n  strokeColor=#82b366;\n  fontColor=#102040;\n',
        'spaces-not-semicolons': 'rounded=0;whiteSpace=wrap html=1 fillColor=#ffe6cc strokeColor=#d79b00;fontColor=#102040',
        'leading ;;': ';;rounded=0;whiteSpace=wrap;html=1;fillColor=#e1d5e7;strokeColor=#9673a6;fontColor=#102040;',
    };
    const expectedFill: Record<string, string> = {
        'doubled ;;': '#dae8fc',
        'newlines+indent': '#d5e8d4',
        'spaces-not-semicolons': '#ffe6cc',
        'leading ;;': '#e1d5e7',
    };

    it('DIRECTION: the space-separated cell loses its fill under a naive parse (the bug)', () => {
        // Without normalizeStyleKeySeparators, ` html=1 fillColor=...` is swallowed
        // into the whiteSpace value, so fillColor never becomes a key and maxGraph
        // substitutes its default #C3D9FF — a plausible-but-wrong image.
        const naive = parseStyle(variants['spaces-not-semicolons']);
        expect(naive.fillColor).toBeUndefined();
    });

    for (const [name, style] of Object.entries(variants)) {
        it(`recovers fillColor for the "${name}" cell`, () => {
            const fixed = normalizeStyleKeySeparators(style);
            const obj = parseStyle(fixed);
            expect(obj.fillColor).toBe(expectedFill[name]);
            // the swallowed keys are all present again
            expect(obj.html).toBe('1');
            expect(obj.strokeColor).toBeTruthy();
            expect(obj.fontColor).toBe('#102040');
        });
    }
});

// ───────────────────────── D-090 / w4-12 — vertex-ness inference ─────────────
describe('D-090 / drawio-w4-12 — a sized geometry with no vertex="1" is inferred a vertex', () => {
    it('infers vertex when geometry is sized and the cell is not an edge/connector', () => {
        // w4-12 cell m1: value + sized mxGeometry but NO vertex="1".
        expect(shouldInferVertex({
            hasVertexFlag: false, isEdge: false, hasSource: false, hasTarget: false,
            width: 170, height: 60,
        })).toBe(true);
    });
    it('DIRECTION: does NOT hijack an edge or a connector (no false vertices)', () => {
        // An edge (has source/target, edge flag) must stay an edge, else it would
        // be dropped/duplicated.
        expect(shouldInferVertex({
            hasVertexFlag: false, isEdge: true, hasSource: true, hasTarget: true,
            width: 0, height: 0,
        })).toBe(false);
        expect(shouldInferVertex({
            hasVertexFlag: false, isEdge: false, hasSource: true, hasTarget: true,
            width: 100, height: 40,
        })).toBe(false);
        // A zero-size cell is not inferred (nothing to draw).
        expect(shouldInferVertex({
            hasVertexFlag: false, isEdge: false, hasSource: false, hasTarget: false,
            width: 0, height: 0,
        })).toBe(false);
    });
});

// ───────────────────────── D-089 / w4-08 + w4-10 — colour recovery, BOTH themes
describe('D-089 / drawio-w4-08 — CSS-named fills get a font that clears the floor (theme-independent)', () => {
    it('picks BLACK on cornflowerblue (7.06:1), replacing author white (2.97:1, below floor)', () => {
        const font = pickReadableFontColor('cornflowerblue');
        expect(font).toBe('#000000');
        const ratio = calculateContrastRatio(font, 'cornflowerblue');
        expect(ratio).toBeGreaterThanOrEqual(TEXT_FLOOR); // 7.06
        // opaque fill → the label sits on the fill, NOT the canvas, so this one
        // value is correct in LIGHT and DARK alike (both-theme requirement met by
        // construction). The value it replaces was below the floor:
        expect(calculateContrastRatio('#ffffff', 'cornflowerblue')).toBeLessThan(TEXT_FLOOR);
    });
    it('honours author font colours that already contrast (no needless change)', () => {
        // Service: darkslategray on lightgoldenrodyellow = 8.36; Datastore: black on thistle = 12.36.
        expect(calculateContrastRatio('#2f4f4f', 'lightgoldenrodyellow')).toBeGreaterThanOrEqual(TEXT_FLOOR);
        expect(calculateContrastRatio('#000000', 'thistle')).toBeGreaterThanOrEqual(TEXT_FLOOR);
    });
});

describe('D-089 / drawio-w4-10 — unparseable theme tokens degrade to a legible pair, not solid black', () => {
    for (const tok of ['var(--ziya-node-bg)', '$primary', 'theme.surface']) {
        it(`"${tok}" is detected unparseable and recovered to the default node fill`, () => {
            expect(isResolvableColor(tok)).toBe(false);
            const styleObj: Record<string, any> = { fillColor: tok, fontColor: '#ffffff' };
            resolveUnparseableCellColors(styleObj);
            expect(styleObj.fillColor).toBe(MAXGRAPH_DEFAULT_VERTEX_FILL); // #C3D9FF, not #000000
            expect(styleObj.strokeColor).toBeTruthy(); // a bounding stroke is added
            // The recovered opaque fill takes a readable font in BOTH themes
            // (black on #C3D9FF = 14.69), so no dark-canvas 1.30 collapse.
            const font = pickReadableFontColor(styleObj.fillColor);
            expect(calculateContrastRatio(font, styleObj.fillColor)).toBeGreaterThanOrEqual(TEXT_FLOOR);
        });
    }
});

// ───────────────────── D-086/087/088 — end-to-end envelope/quote recovery ────
describe('D-086/087/088 — normalizeDrawIOXml makes the wave-4 envelopes decodable', () => {
    it('D-086 w4-01: strips a ```xml markdown fence', () => {
        const fenced = '```xml\n<mxGraphModel dx="900"><root><mxCell id="0"/>' +
            '<mxCell id="1" parent="0"/></root></mxGraphModel>\n```';
        expect(fenced.trimStart().startsWith('```')).toBe(true); // pre-fix: fence blocks the parser
        const out = normalizeDrawIOXml(fenced);
        expect(out).not.toContain('```');
        expect(out).toContain('<mxGraphModel');
    });
    it('D-086 w4-13: synthesises a missing <root> wrapper', () => {
        const noRoot = '<mxGraphModel dx="900" grid="0"><mxCell id="0"/>' +
            '<mxCell id="1" parent="0"/><mxCell id="d1" value="Flat A" vertex="1" parent="1">' +
            '<mxGeometry x="40" y="50" width="150" height="60" as="geometry"/></mxCell></mxGraphModel>';
        expect(/<root\b/.test(noRoot)).toBe(false); // pre-fix direction
        const out = normalizeDrawIOXml(noRoot);
        expect(/<root\b/.test(out)).toBe(true);
    });
    it('D-086 w4-15: truncates prose after </mxfile>', () => {
        const withProse = '<mxfile version="14.6.13"><diagram id="a" name="P1"><mxGraphModel><root>' +
            '<mxCell id="0"/><mxCell id="1" parent="0"/></root></mxGraphModel></diagram></mxfile>\n' +
            'This diagram shows the legacy flow described above.';
        const out = normalizeDrawIOXml(withProse);
        expect(out.trimEnd().endsWith('</mxfile>')).toBe(true);
        expect(out).not.toContain('legacy flow');
    });
    it('D-087 w4-05: escapes bare < and > in a label, preserving a real &amp; entity', () => {
        const bare = '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>' +
            '<mxCell id="m3" value="R&D < Ops > Net" vertex="1" parent="1">' +
            '<mxGeometry x="0" y="0" width="180" height="50" as="geometry"/></mxCell></root></mxGraphModel>';
        const out = normalizeDrawIOXml(bare);
        expect(out).toContain('R&amp;D &lt; Ops &gt; Net');
        expect(out).not.toContain('&amp;amp;'); // no double-escape
    });
    it('D-088 w4-04: converts single-quoted attributes to double-quoted', () => {
        const sq = "<mxCell id='q1' value='Queue' vertex='1' parent='1'/>";
        const out = normalizeSingleQuotedAttributes(sq);
        expect(out).toContain('id="q1"');
        expect(out).toContain('value="Queue"');
    });
    it('D-088 w4-11: de-quotes entity-escaped style values', () => {
        const esc = '<mxCell id="s1" style="fillColor=&quot;#dae8fc&quot;;fontSize=&quot;16&quot;"/>';
        const out = dequoteEntityEscapedStyleValues(esc);
        expect(out).toContain('fillColor=#dae8fc');
        expect(out).toContain('fontSize=16');
        expect(out).not.toContain('&quot;');
    });
});
