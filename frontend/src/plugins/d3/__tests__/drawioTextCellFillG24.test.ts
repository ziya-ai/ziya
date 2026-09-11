/**
 * G-24 / D-026 — text-cell-gets-default-vertex-fill (drawio-w1-15).
 *
 * A drawio TEXT primitive (`text;html=1;…` → shape=text, or the bare `text`
 * flag) that declares no fillColor is still painted inside maxGraph's DEFAULT
 * light-blue vertex fill (#C3D9FF), so a title/legend caption grows a phantom
 * slab. In drawio a text cell is fill-less and stroke-less by design.
 *
 * These tests import the REAL exported helper `applyTextCellFillDefaults` from
 * drawioPlugin — it does NOT exist on the pre-fix tree, so every block that
 * calls it fails to resolve (non-vacuous / RED before the change). The
 * pre-fix-documenting blocks (which use only pre-existing symbols) stay GREEN
 * and pin the bug the fix removes.
 *
 * The theme obligation is discharged in BOTH themes: after the fill is cleared
 * the caption sits on the themed canvas and its font is reconciled there —
 * asserted readable in LIGHT and DARK — paired with the pre-fix guard showing
 * that WITHOUT the cleared fill the dark-canvas font choice collapses below the
 * 3:1 floor (the phantom-fill assumption the fix removes).
 */
import {
    applyTextCellFillDefaults,
    reconcileCanvasLabelColor,
    resolveFilllessFontColor,
    MAXGRAPH_DEFAULT_VERTEX_FILL,
} from '../drawioPlugin';
import { CHART_DARK_BG, CHART_LIGHT_BG } from '../chartTheme';
import { calculateContrastRatio, getOptimalTextColor } from '../../../utils/colorUtils';

const GRAPHICAL_FLOOR = 3.0;
const TEXT_FLOOR = 4.5;

// ─────────────── pre-fix: document the phantom slab (GREEN before the fix) ───────────────
describe('D-026 pre-fix — a fill-less text cell is painted on the default #C3D9FF slab', () => {
    it('getOptimalTextColor(#C3D9FF) picks black — the caption reads as black-on-lightblue box', () => {
        // maxGraph paints the default light-blue vertex fill; the label is tuned to
        // THAT fill (black), which is exactly the phantom slab w1-15 shows.
        expect(getOptimalTextColor(MAXGRAPH_DEFAULT_VERTEX_FILL)).toBe('#000000');
    });

    it('WITHOUT a cleared fill, the dark-canvas font choice is illegible on the dark canvas', () => {
        // resolveFilllessFontColor with hasFillColor=false assumes the #C3D9FF slab
        // and returns black; if the slab were absent that black caption would sit on
        // the dark canvas at < 3:1 — the reason the fill MUST be cleared to `none`.
        const preFixFont = resolveFilllessFontColor(true, /*hasFillColor*/ false, /*dark*/ true);
        expect(preFixFont).toBe('#000000');
        expect(calculateContrastRatio(preFixFont, CHART_DARK_BG)).toBeLessThan(GRAPHICAL_FLOOR);
    });
});

// ─────────────── the fix: clear fill/stroke for text primitives (RED before the fix) ───────────────
describe('D-026 fix — applyTextCellFillDefaults clears the phantom fill for text primitives', () => {
    it('defaults fillColor and strokeColor to `none` for a shape=text cell', () => {
        const styleObj: Record<string, any> = { shape: 'text', fontColor: '#333333', html: 1 };
        applyTextCellFillDefaults(styleObj);
        expect(styleObj['fillColor']).toBe('none');
        expect(styleObj['strokeColor']).toBe('none');
    });

    it('handles the bare `text` flag form (text;html=1 → styleObj.text = 1)', () => {
        const styleObj: Record<string, any> = { text: 1, html: 1 };
        applyTextCellFillDefaults(styleObj);
        expect(styleObj['fillColor']).toBe('none');
        expect(styleObj['strokeColor']).toBe('none');
    });

    it('preserves an author fillColor/strokeColor (defaults only — author wins)', () => {
        const styleObj: Record<string, any> = {
            shape: 'text', fillColor: '#ffe6cc', strokeColor: '#d79b00',
        };
        applyTextCellFillDefaults(styleObj);
        expect(styleObj['fillColor']).toBe('#ffe6cc');
        expect(styleObj['strokeColor']).toBe('#d79b00');
    });

    it('never touches a non-text vertex (a fill-less rectangle keeps the default-fill path)', () => {
        const styleObj: Record<string, any> = { shape: 'rectangle' };
        applyTextCellFillDefaults(styleObj);
        expect(styleObj['fillColor']).toBeUndefined();
        expect(styleObj['strokeColor']).toBeUndefined();
    });
});

// ─────────────── both-theme integration: caption on the themed canvas ───────────────
describe('D-026 both-theme — a cleared text cell reads on the themed canvas in LIGHT and DARK', () => {
    it('DARK (previously the phantom-slab / illegible direction): font is canvas-readable', () => {
        const styleObj: Record<string, any> = { shape: 'text', html: 1 };
        applyTextCellFillDefaults(styleObj);
        // fill is now 'none' → hasFillColor=true → reconciled against the DARK canvas.
        const font = reconcileCanvasLabelColor(
            styleObj['fontColor'], true, !!styleObj['fillColor'], /*dark*/ true,
        );
        expect(font).toBe('#e0e0e0');
        expect(calculateContrastRatio(font, CHART_DARK_BG)).toBeGreaterThanOrEqual(TEXT_FLOOR);
    });

    it('LIGHT (must stay correct): font is canvas-readable, no phantom box', () => {
        const styleObj: Record<string, any> = { shape: 'text', html: 1 };
        applyTextCellFillDefaults(styleObj);
        const font = reconcileCanvasLabelColor(
            styleObj['fontColor'], true, !!styleObj['fillColor'], /*dark*/ false,
        );
        expect(font).toBe('#000000');
        expect(calculateContrastRatio(font, CHART_LIGHT_BG)).toBeGreaterThanOrEqual(TEXT_FLOOR);
    });
});
