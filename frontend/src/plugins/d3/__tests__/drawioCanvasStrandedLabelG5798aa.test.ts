/**
 * @jest-environment jsdom
 *
 * G-5798aa / D-101 — label-stranded-on-dark-canvas-keeps-author-fontcolor.
 *
 * A drawio label that does NOT sit on an opaque authored fill keeps the
 * author's own (often dark) fontColor directly on the themed canvas and is
 * invisible in dark. This is the class the earlier vertex+transparent suite
 * (drawioTransparentFillG25) did NOT exercise:
 *
 *   - drawio-w4-02: an EDGE label ("HTTP"), isVertex=false, no fill -> sits on
 *     the themed canvas. Author fontColor #1a1a1a is 1.04:1 on the dark canvas
 *     (#1e1e1e) and must be re-themed, while its 17.4:1 on the light canvas
 *     (#ffffff) must be preserved (no light regression).
 *   - drawio-w4-12: a boxless TEXT primitive whose fill was dropped for missing
 *     width/height. applyTextCellFillDefaults defaults its fill to 'none', so
 *     drawioFillPaintsOpaque() is false and it routes to the canvas-reconcile
 *     branch rather than being measured against a phantom fill.
 *   - drawio-w2-06: overflow text — applyLabelFittingDefaults wraps/clips a
 *     boxed cell so the label no longer prints onto the bare dark canvas.
 *
 * DIRECTION: reconcileCanvasLabelColor / resolveFilllessFontColor /
 * drawioFillPaintsOpaque do not exist on the unpatched tree, so importing them
 * makes this suite RED before the fix. Theme defect => BOTH themes asserted.
 */
import {
    reconcileCanvasLabelColor,
    resolveFilllessFontColor,
    drawioFillPaintsOpaque,
    applyTextCellFillDefaults,
    applyLabelFittingDefaults,
} from '../drawioPlugin';
import { calculateContrastRatio } from '../../../utils/colorUtils';

const DARK_CANVAS = '#1e1e1e';
const LIGHT_CANVAS = '#ffffff';
const GRAPHICAL_FLOOR = 3.0;
// A light-tuned author fontColor that is legible on white but invisible on the
// dark canvas (1.04:1) — the exact w4-02 "HTTP" failure mode.
const AUTHOR_DARK_FONT = '#1a1a1a';

describe('D-101 — edge label stranded on the themed canvas (drawio-w4-02)', () => {
    it('DARK: re-themes an author dark fontColor invisible on the dark canvas', () => {
        // PRE-FIX: the author #1a1a1a was kept -> 1.04:1 on #1e1e1e (invisible).
        expect(calculateContrastRatio(AUTHOR_DARK_FONT, DARK_CANVAS)).toBeLessThan(GRAPHICAL_FLOOR);
        const out = reconcileCanvasLabelColor(
            AUTHOR_DARK_FONT,
            /* isVertex */ false, // edge label
            /* hasFillColor */ false,
            /* isDarkMode */ true,
        );
        expect(out).toBe('#e0e0e0');
        expect(calculateContrastRatio(out, DARK_CANVAS)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
    });

    it('LIGHT: preserves the same author fontColor (already 17.4:1 on white)', () => {
        const out = reconcileCanvasLabelColor(AUTHOR_DARK_FONT, false, false, false);
        // A readable author colour is kept — the fix must not regress light.
        expect(out).toBe(AUTHOR_DARK_FONT);
        expect(calculateContrastRatio(out, LIGHT_CANVAS)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
    });

    it('leaves an already theme-correct edge label untouched in both themes', () => {
        expect(reconcileCanvasLabelColor('#ffffff', false, false, true)).toBe('#ffffff');
        expect(reconcileCanvasLabelColor('#000000', false, false, false)).toBe('#000000');
    });
});

describe('D-101 — boxless text primitive routes to the canvas branch (drawio-w4-12)', () => {
    it('defaults a fill-less text primitive to fillColor:none (not the phantom #C3D9FF box)', () => {
        const styleObj: Record<string, any> = { shape: 'text', text: 1, fontColor: AUTHOR_DARK_FONT };
        applyTextCellFillDefaults(styleObj);
        expect(styleObj['fillColor']).toBe('none');
        // 'none' paints nothing, so the label sits on the themed canvas.
        expect(drawioFillPaintsOpaque(styleObj['fillColor'])).toBe(false);
    });

    it('DARK: the boxless label is reconciled against the canvas (was stranded dark)', () => {
        const styleObj: Record<string, any> = { shape: 'text', text: 1, fontColor: AUTHOR_DARK_FONT };
        applyTextCellFillDefaults(styleObj);
        // fillColor:none -> hasFillColor true (the literal keyword), isVertex false.
        const out = reconcileCanvasLabelColor(styleObj['fontColor'], false, true, true);
        expect(out).toBe('#e0e0e0');
        expect(calculateContrastRatio(out, DARK_CANVAS)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
    });

    it('LIGHT: the same boxless label keeps its legible author colour', () => {
        const styleObj: Record<string, any> = { shape: 'text', text: 1, fontColor: AUTHOR_DARK_FONT };
        applyTextCellFillDefaults(styleObj);
        const out = reconcileCanvasLabelColor(styleObj['fontColor'], false, true, false);
        expect(out).toBe(AUTHOR_DARK_FONT);
        expect(calculateContrastRatio(out, LIGHT_CANVAS)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
    });
});

describe('D-101 — styled vertex with no fill reconciles against the default box', () => {
    it('theme-independent: label on the #C3D9FF default vertex fill needs dark text', () => {
        // resolveFilllessFontColor returns the same colour in both themes because
        // maxGraph paints the SAME default fill in both — a per-fill decision.
        expect(resolveFilllessFontColor(true, false, true)).toBe('#000000');
        expect(resolveFilllessFontColor(true, false, false)).toBe('#000000');
    });
});

describe('D-101 — overflow label fitting (drawio-w2-06)', () => {
    it('wraps and clips a boxed cell so overflow text stops printing onto the canvas', () => {
        const styleObj: Record<string, any> = {};
        applyLabelFittingDefaults(styleObj, { isEdge: false });
        expect(styleObj['whiteSpace']).toBe('wrap');
        expect(styleObj['overflow']).toBe('hidden');
    });

    it('wraps but does NOT clip an edge label (it has no box to clip to)', () => {
        const styleObj: Record<string, any> = {};
        applyLabelFittingDefaults(styleObj, { isEdge: true });
        expect(styleObj['whiteSpace']).toBe('wrap');
        expect(styleObj['overflow']).toBeUndefined();
    });
});
