/**
 * G-bb997d — three drawio recovery/theme defects sharing drawioPlugin.ts.
 *
 *  D-388 single-quoted-style-value-token-survives (w4-11): `fontSize='14';opacity='100'`
 *        inside a double-quoted style="..." bypasses every de-quote pass; opacity='100'
 *        parses to NaN and crushes the cell to transparent. dequoteSingleQuotedStyleValues
 *        strips it like the literal/entity forms.
 *  D-390 geometry-less/zero-size cell dropped (w4-12): a vertex with no width/height renders
 *        0x0 (invisible); drawioDefaultVertexSize supplies a default box.
 *  D-391 legacy-array-points-waypoint-blanks-canvas (w4-15): a single collinear-and-between
 *        authored waypoint makes the connector build a zero-length segment → NaN route →
 *        blank canvas. filterDegenerateWaypoints drops redundant points.
 *
 * Each block asserts the pre-fix behaviour (DIRECTION) then the fix. Recovery defects are
 * theme-independent (the preprocessor/importer has no theme input); both themes are
 * re-rendered at the shared verification stage.
 */

import {
    normalizeSingleQuotedAttributes,
    dequoteSingleQuotedStyleValues,
    normalizeDrawIOXml,
    drawioDefaultVertexSize,
    filterDegenerateWaypoints,
    reconcileCanvasLabelColor,
} from '../drawioPlugin';
import { calculateContrastRatio } from '../../../utils/colorUtils';

const DARK_CANVAS = '#1e1e1e';
const LIGHT_CANVAS = '#ffffff';
const TEXT_FLOOR = 4.5;

describe('D-387: trust-boundary titles meet the 4.5 text floor on BOTH themed canvases', () => {
    // drawio-w1-09: a red title tuned for light (#b85450 = 4.75 on white, 3.51 on dark)
    // and a blue title tuned for dark (#6c8ebf = 4.97 on dark, 3.36 on white). At the old
    // 3.0 floor both were kept and each failed its weak theme.
    const RED = '#b85450';
    const BLUE = '#6c8ebf';

    it('DIRECTION: each saturated author title fails 4.5 on ONE theme (the bug the 3.0 floor missed)', () => {
        expect(calculateContrastRatio(RED, DARK_CANVAS)).toBeLessThan(TEXT_FLOOR);   // 3.51
        expect(calculateContrastRatio(RED, LIGHT_CANVAS)).toBeGreaterThanOrEqual(TEXT_FLOOR); // 4.75
        expect(calculateContrastRatio(BLUE, LIGHT_CANVAS)).toBeLessThan(TEXT_FLOOR); // 3.36
        expect(calculateContrastRatio(BLUE, DARK_CANVAS)).toBeGreaterThanOrEqual(TEXT_FLOOR); // 4.97
    });

    it('DARK: the red title is lifted to the theme font; the legible blue is kept', () => {
        const red = reconcileCanvasLabelColor(RED, /*isVertex*/ true, /*hasFillColor(none)*/ true, /*dark*/ true);
        expect(red).toBe('#e0e0e0');
        expect(calculateContrastRatio(red, DARK_CANVAS)).toBeGreaterThanOrEqual(TEXT_FLOOR);
        const blue = reconcileCanvasLabelColor(BLUE, true, true, true);
        expect(blue).toBe(BLUE); // 4.97 ≥ 4.5 → kept
    });

    it('LIGHT: the blue title is lifted to the theme font; the legible red is kept', () => {
        const blue = reconcileCanvasLabelColor(BLUE, true, true, false);
        expect(blue).toBe('#000000');
        expect(calculateContrastRatio(blue, LIGHT_CANVAS)).toBeGreaterThanOrEqual(TEXT_FLOOR);
        const red = reconcileCanvasLabelColor(RED, true, true, false);
        expect(red).toBe(RED); // 4.75 ≥ 4.5 → kept
    });
});

describe('D-388: single-quoted style-value tokens are de-quoted', () => {
    // drawio-w4-11 s2: numeric style values single-quoted INSIDE a double-quoted style.
    const s2 = `<mxCell id="s2" value="Quoted Nums" style="rounded=0;fillColor=#d5e8d4;fontColor=#102040;fontSize='14';opacity='100';arcSize='8';" vertex="1" parent="1"><mxGeometry x="300" y="50" width="180" height="70" as="geometry"/></mxCell>`;

    it('DIRECTION: normalizeSingleQuotedAttributes leaves the in-style single quotes intact (the bug)', () => {
        // The style="..." attribute is masked as already double-quoted, so its inner
        // single quotes are treated as content and survive → invalid style tokens.
        const out = normalizeSingleQuotedAttributes(s2);
        expect(out).toContain("opacity='100'");
        expect(out).toContain("fontSize='14'");
    });

    it('dequoteSingleQuotedStyleValues strips single-quote over-quoting on numeric style keys', () => {
        const out = dequoteSingleQuotedStyleValues(s2);
        expect(out).toContain('fontSize=14');
        expect(out).toContain('opacity=100');
        expect(out).toContain('arcSize=8');
        expect(out).not.toContain("opacity='100'");
        expect(out).not.toContain("fontSize='14'");
    });

    it('end-to-end: normalizeDrawIOXml removes the single-quote over-quoting so opacity is a valid number', () => {
        const out = normalizeDrawIOXml(s2);
        expect(out).toContain('opacity=100');
        expect(out).toContain('fontSize=14');
        expect(out).not.toContain("opacity='100'");
    });

    it('de-quotes single-quoted style colours too', () => {
        const out = dequoteSingleQuotedStyleValues(`style="fillColor='#dae8fc';strokeColor='#6c8ebf';"`);
        expect(out).toContain('fillColor=#dae8fc');
        expect(out).toContain('strokeColor=#6c8ebf');
    });

    it('does NOT touch a legitimately single-quoted attribute value that is not a style token', () => {
        // A tag-level single-quoted value with no colour/numeric style key before it.
        const xml = `<mxCell value='hello world'/>`;
        expect(dequoteSingleQuotedStyleValues(xml)).toBe(xml);
    });
});

describe('D-390: geometry-less / zero-size vertices get a default box', () => {
    it('DIRECTION + fix: a 0x0 (or missing) vertex size becomes the default box', () => {
        // Pre-fix the parser used the raw 0x0 (invisible). The helper supplies 120x60.
        expect(drawioDefaultVertexSize(0, 0)).toEqual({ width: 120, height: 60 });
        expect(drawioDefaultVertexSize(NaN, NaN)).toEqual({ width: 120, height: 60 });
        expect(drawioDefaultVertexSize(0, 60)).toEqual({ width: 120, height: 60 });
    });

    it('leaves a valid author box untouched (no regression)', () => {
        expect(drawioDefaultVertexSize(170, 60)).toEqual({ width: 170, height: 60 });
    });
});

describe('D-391: degenerate authored waypoints are dropped', () => {
    // drawio-w4-15: box centre (125,80) → waypoint (255,80) → diamond centre (390,80).
    const src = { cx: 125, cy: 80 };
    const tgt = { cx: 390, cy: 80 };

    it('DIRECTION: a lone collinear-and-between waypoint is redundant and must be removed', () => {
        const out = filterDegenerateWaypoints([{ x: 255, y: 80 }], src, tgt);
        expect(out).toEqual([]); // no zero-length segment fed to the connector
    });

    it('keeps a genuine bend that is OFF the source→target line', () => {
        const out = filterDegenerateWaypoints([{ x: 255, y: 200 }], src, tgt);
        expect(out).toEqual([{ x: 255, y: 200 }]);
    });

    it('keeps a point that is collinear but OUTSIDE the segment (a real detour)', () => {
        const out = filterDegenerateWaypoints([{ x: 600, y: 80 }], src, tgt);
        expect(out).toEqual([{ x: 600, y: 80 }]);
    });

    it('drops duplicate consecutive points and terminal-coincident points', () => {
        expect(filterDegenerateWaypoints([{ x: 125, y: 80 }], src, tgt)).toEqual([]);
        expect(filterDegenerateWaypoints(
            [{ x: 200, y: 200 }, { x: 200, y: 200 }], src, tgt
        )).toEqual([{ x: 200, y: 200 }]);
    });

    it('with unknown terminals, keeps points (only de-duplicates)', () => {
        expect(filterDegenerateWaypoints([{ x: 255, y: 80 }], null, null))
            .toEqual([{ x: 255, y: 80 }]);
    });
});
