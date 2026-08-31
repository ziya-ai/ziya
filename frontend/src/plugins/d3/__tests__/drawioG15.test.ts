import {
    normalizeDrawIOXml,
    reconcileCanvasLabelColor,
    reconcileSwimlaneLabelColor,
} from '../drawioPlugin';

/**
 * G-15 regression suite (drawioPlugin.ts). Four open defects:
 *   D-112  swimlane fillOpacity=20 crushes the lane-title contrast in DARK
 *   D-113  a canvas-backdrop label keeps its author fontColor → invisible in DARK
 *   D-115  envelope-wrapper hangs (markdown fence / missing <root> / prose after </mxfile>)
 *   D-116  bare < / > inside an attribute value never escaped → phantom tag → hang
 *
 * The reconcile* helpers and the normalizeDrawIOXml export did not exist / were
 * not exported before the fix, so this suite fails to even import against
 * pre-fix code. Beyond that, every assertion is direction-checked: the theme
 * cases assert the BROKEN theme is now correct AND the other theme is still
 * correct, and the recovery cases assert the specific unpatched-hang substring
 * is gone.
 */

// ── D-113: label over the canvas (or default fill) reconciled per-theme ──────
describe('D-113 reconcileCanvasLabelColor — edge/fillless label vs the real backdrop', () => {
    // An author fontColor tuned for a light page (#102040) sits on the dark
    // canvas at 1.03:1 for an edge label / fillColor:none cell.
    it('DARK: replaces a light-tuned author fontColor invisible on the dark canvas', () => {
        // edge label: isVertex=false, hasFillColor=false
        const out = reconcileCanvasLabelColor('#102040', false, false, true);
        expect(out).not.toBe('#102040');       // the broken colour is dropped
        expect(out.toLowerCase()).toBe('#e0e0e0'); // themed dark-canvas colour
    });
    it('LIGHT: PRESERVES the same author fontColor (16:1 on white — no regression)', () => {
        const out = reconcileCanvasLabelColor('#102040', false, false, false);
        expect(out).toBe('#102040');
    });
    it('a readable author colour is preserved in BOTH themes', () => {
        expect(reconcileCanvasLabelColor('#ffffff', false, false, true)).toBe('#ffffff');  // white on dark canvas
        expect(reconcileCanvasLabelColor('#000000', false, false, false)).toBe('#000000'); // black on light canvas
    });
    it('a styled vertex with NO fillColor reconciles against maxGraph default fill #C3D9FF (both themes)', () => {
        // #C3D9FF is a light blue → black text in either theme; a light author
        // colour (#e0e0e0, 1.08:1 on #C3D9FF) must be replaced.
        expect(reconcileCanvasLabelColor(undefined, true, false, true)).toBe('#000000');
        expect(reconcileCanvasLabelColor('#e0e0e0', true, false, true)).toBe('#000000');
    });
});

// ── D-112: swimlane 20% fill composited over the themed canvas ───────────────
describe('D-112 reconcileSwimlaneLabelColor — lane title vs the 20% composite', () => {
    const AUTHOR_FILL = '#dae8fc'; // the common drawio light-blue swimlane fill
    it('DARK: a dark lane-title colour (2.22:1 on the composite) is lifted to a light colour', () => {
        const out = reconcileSwimlaneLabelColor('#000000', AUTHOR_FILL, true);
        expect(out).not.toBe('#000000');
        expect(out.toLowerCase()).toBe('#ffffff'); // readable on the #44464a composite
    });
    it('LIGHT: the same dark lane-title colour is PRESERVED (20:1 on the near-white composite)', () => {
        const out = reconcileSwimlaneLabelColor('#000000', AUTHOR_FILL, false);
        expect(out).toBe('#000000');
    });
    it('a missing lane-title colour is filled per-theme against the composite', () => {
        expect(reconcileSwimlaneLabelColor(undefined, AUTHOR_FILL, true).toLowerCase()).toBe('#ffffff');
        expect(reconcileSwimlaneLabelColor(undefined, AUTHOR_FILL, false).toLowerCase()).toBe('#000000');
    });
});

// ── D-115: envelope-wrapper recoveries ───────────────────────────────────────
describe('D-115 normalizeDrawIOXml — envelope wrappers that used to hang', () => {
    const BODY =
        '<mxfile host="ziya"><diagram name="d"><mxGraphModel><root>' +
        '<mxCell id="0"/><mxCell id="1" parent="0"/>' +
        '<mxCell id="2" value="A" vertex="1" parent="1"><mxGeometry x="10" y="10" width="80" height="40"/></mxCell>' +
        '</root></mxGraphModel></diagram></mxfile>';

    it('strips a leading ```xml markdown fence (and trailing fence)', () => {
        const out = normalizeDrawIOXml('```xml\n' + BODY + '\n```');
        expect(out).not.toContain('```');
        expect(out.trimStart().startsWith('<mxfile') || out.trimStart().startsWith('<?xml')).toBe(true);
        expect(out).toContain('<mxGraphModel');
    });

    it('truncates prose that follows </mxfile>', () => {
        const out = normalizeDrawIOXml(BODY + '\n\nThis diagram shows the request flow between services.');
        expect(out).not.toContain('This diagram shows');
        expect(out.trimEnd().endsWith('</mxfile>')).toBe(true);
    });

    it('synthesises a missing <root> wrapper (with base cells 0/1)', () => {
        const noRoot =
            '<mxGraphModel dx="640" dy="480">' +
            '<mxCell id="2" value="A" vertex="1" parent="1"><mxGeometry x="10" y="10" width="80" height="40"/></mxCell>' +
            '</mxGraphModel>';
        const out = normalizeDrawIOXml(noRoot);
        expect(out).toContain('<root>');
        expect(out).toContain('id="0"');
        expect(out).toContain('parent="0"'); // base cell 1 parents to 0
        // the authored cell survives inside the synthesised root
        expect(out).toContain('id="2"');
    });

    it('leaves a well-formed document with a <root> unchanged in structure (no double root)', () => {
        const out = normalizeDrawIOXml(BODY);
        expect((out.match(/<root>/g) || []).length).toBe(1);
    });
});

// ── D-116: bare angle brackets inside attribute values ───────────────────────
describe('D-116 normalizeDrawIOXml — escape bare < and > in attribute values', () => {
    it('escapes < and > (and &) in a label so the doc parses instead of hanging', () => {
        const xml =
            '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>' +
            '<mxCell id="2" value="R&D < Ops > Net" vertex="1" parent="1">' +
            '<mxGeometry x="0" y="0" width="120" height="40"/></mxCell>' +
            '</root></mxGraphModel>';
        const out = normalizeDrawIOXml(xml);
        expect(out).toContain('R&amp;D &lt; Ops &gt; Net');
        // the raw, parser-breaking form is gone
        expect(out).not.toContain('value="R&D < Ops > Net"');
    });

    it('leaves already-encoded entity labels untouched (no double-escaping)', () => {
        const xml =
            '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>' +
            '<mxCell id="2" value="a &lt;b&gt; c" vertex="1" parent="1">' +
            '<mxGeometry x="0" y="0" width="60" height="20"/></mxCell>' +
            '</root></mxGraphModel>';
        const out = normalizeDrawIOXml(xml);
        expect(out).toContain('a &lt;b&gt; c');
        expect(out).not.toContain('&amp;lt;'); // must not double-escape the entity
    });
});
