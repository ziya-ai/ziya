/**
 * G-14 / D-111 regression tests: drawio default-fill vs theme-fontColor (dark).
 *
 * Root cause (confirmed against source): the plugin's defaultVertexStyle sets
 * only fontColor/fontSize, so maxGraph paints its built-in default vertex fill
 * (#C3D9FF, a light blue) for any styled vertex that specifies NO fillColor.
 * The old "no fill color" branch chose fontColor = isDarkMode ? '#e0e0e0' :
 * '#000000', so in DARK mode a light #e0e0e0 label landed on that light-blue
 * fill at 1.08:1 (invisible). Light already used #000000 (dark text) and was
 * fine.
 *
 * The fix resolves the label against the fill maxGraph will ACTUALLY paint
 * (theme-independent #C3D9FF) for a fill-less vertex, while a fill-less edge
 * label or a cell with fillColor:'none' (fill explicitly cleared) keeps the
 * themed-canvas colour.
 *
 * Imports the REAL shipped helpers (not a re-implementation) so it detects drift.
 * Every assertion is paired: the broken theme (dark) is now correct AND the
 * other theme (light) still is.
 */

import { resolveFilllessFontColor, MAXGRAPH_DEFAULT_VERTEX_FILL } from '../drawioPlugin';
import { calculateContrastRatio } from '../../../utils/colorUtils';

const TEXT_FLOOR = 4.5; // WCAG AA for normal-size text
const DARK_CANVAS = '#1e1e1e';
const LIGHT_CANVAS = '#ffffff';

describe('G-14 / D-111 — resolveFilllessFontColor', () => {
  it('the default vertex fill is the light-blue maxGraph builtin', () => {
    expect(MAXGRAPH_DEFAULT_VERTEX_FILL.toUpperCase()).toBe('#C3D9FF');
  });

  it('DIRECTION: the OLD dark output (#e0e0e0) was illegible on the default fill', () => {
    // This is what the pre-fix branch produced for a fill-less vertex in dark
    // mode. It sits on the #C3D9FF default fill, not the dark canvas.
    const oldDark = calculateContrastRatio('#e0e0e0', MAXGRAPH_DEFAULT_VERTEX_FILL);
    expect(oldDark).toBeLessThan(3.0); // ~1.08 — below even the 3:1 graphical floor
  });

  describe('fill-less VERTEX: label reconciled against the ACTUAL painted fill (#C3D9FF)', () => {
    it('DARK theme (was broken) now clears the text floor on the default fill', () => {
      const c = resolveFilllessFontColor(true, false, true);
      expect(c).not.toBe('#e0e0e0'); // no longer the ghost value
      expect(calculateContrastRatio(c, MAXGRAPH_DEFAULT_VERTEX_FILL))
        .toBeGreaterThanOrEqual(TEXT_FLOOR);
    });

    it('LIGHT theme (was fine) still clears the text floor on the default fill', () => {
      const c = resolveFilllessFontColor(true, false, false);
      expect(calculateContrastRatio(c, MAXGRAPH_DEFAULT_VERTEX_FILL))
        .toBeGreaterThanOrEqual(TEXT_FLOOR);
    });

    it('resolves to the SAME (theme-independent) colour in both themes — the fill is theme-independent', () => {
      expect(resolveFilllessFontColor(true, false, true))
        .toBe(resolveFilllessFontColor(true, false, false));
    });
  });

  describe('fill-less EDGE / canvas label: keeps the themed-canvas colour', () => {
    it('DARK keeps a light label for the dark canvas', () => {
      const c = resolveFilllessFontColor(false, false, true);
      expect(c).toBe('#e0e0e0');
      expect(calculateContrastRatio(c, DARK_CANVAS)).toBeGreaterThanOrEqual(TEXT_FLOOR);
    });

    it('LIGHT keeps a dark label for the light canvas', () => {
      const c = resolveFilllessFontColor(false, false, false);
      expect(c).toBe('#000000');
      expect(calculateContrastRatio(c, LIGHT_CANVAS)).toBeGreaterThanOrEqual(TEXT_FLOOR);
    });
  });

  describe('explicit fillColor:none (fill cleared) — no default fill painted, sits on canvas', () => {
    // The call site passes hasFillColor = !!styleObj['fillColor'], which is
    // TRUE for the string 'none'. So a vertex with fillColor:'none' must keep
    // the themed-canvas colour, NOT the #C3D9FF reconciliation.
    it('DARK vertex with cleared fill uses the dark-canvas label', () => {
      expect(resolveFilllessFontColor(true, true, true)).toBe('#e0e0e0');
    });
    it('LIGHT vertex with cleared fill uses the light-canvas label', () => {
      expect(resolveFilllessFontColor(true, true, false)).toBe('#000000');
    });
  });
});
