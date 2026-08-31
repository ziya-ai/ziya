/**
 * @jest-environment jsdom
 */
/**
 * G-63 — Plotly annotation arrow/text dark-theme parity (D-263).
 *
 * applyPlotlyTheme set a global dark font.color but never recoloured
 * layout.annotations, whose arrowcolor/font default to plotly's #444. On the
 * dark plot surface that pointer measures 1.71:1 and effectively vanishes
 * (w1-13 "SLO breach" arrow). themeDarkAnnotations resolves the annotation
 * colours from the dark theme ONLY when the author left them unset.
 *
 * This is a THEME defect, so BOTH themes are asserted: the dark case is now
 * readable AND the light case is left byte-identical (the constant-swap trap).
 */
import { applyPlotlyTheme, themeDarkAnnotations } from '../plotlyPlugin';

function luminance(hex: string): number {
  const h = hex.replace('#', '');
  const full = h.length === 3 ? h.split('').map(c => c + c).join('') : h;
  const ch = [0, 2, 4].map(i => {
    const c = parseInt(full.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
}
function contrast(a: string, b: string): number {
  const la = luminance(a), lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

const DARK_BG = '#1e1e1e';
const LIGHT_BG = '#ffffff';
const spec = () => ({ annotations: [{ text: 'SLO breach', showarrow: true, x: 1, y: 2 }] });

describe('D-263 themeDarkAnnotations — dark annotation arrow/text parity', () => {
  it('DIRECTION: plotly default arrow #444 is invisible on the dark plot surface', () => {
    expect(contrast('#444444', DARK_BG)).toBeLessThan(2); // 1.71 measured
    // ...and perfectly fine on the light surface, which is why light must NOT change.
    expect(contrast('#444444', LIGHT_BG)).toBeGreaterThan(4.5); // 9.74 measured
  });

  it('DARK: an unset annotation arrow/font is themed to the dark foreground', () => {
    const out = applyPlotlyTheme(spec(), true);
    expect(out.annotations[0].arrowcolor).toBe('#e0e0e0');
    expect(out.annotations[0].font.color).toBe('#e0e0e0');
    // themed arrow clears the graphical + text floor on the dark surface.
    expect(contrast('#e0e0e0', DARK_BG)).toBeGreaterThan(4.5); // 12.63
  });

  it('LIGHT: annotations are left byte-identical (arrow keeps its default, no #e0e0e0 leak)', () => {
    const out = applyPlotlyTheme(spec(), false);
    expect(out.annotations[0].arrowcolor).toBeUndefined();
    expect(out.annotations[0].font).toBeUndefined();
  });

  it('CONSERVATIVE: an explicit author arrowcolor/font wins even in dark', () => {
    const authored = { annotations: [{ text: 't', arrowcolor: '#ff0000', font: { color: '#00ff00' } }] };
    const out = applyPlotlyTheme(authored, true);
    expect(out.annotations[0].arrowcolor).toBe('#ff0000');
    expect(out.annotations[0].font.color).toBe('#00ff00');
  });

  it('fills only the missing side when the author set font.color but not the arrow', () => {
    const out = themeDarkAnnotations({ annotations: [{ text: 't', font: { color: '#abcdef' } }] }, true);
    expect(out.annotations[0].arrowcolor).toBe('#e0e0e0'); // themed (was unset)
    expect(out.annotations[0].font.color).toBe('#abcdef');  // author kept
  });

  it('is a reference-stable no-op with no annotations, and in light', () => {
    const layout = { annotations: [{ text: 't', arrowcolor: '#111', font: { color: '#222' } }] };
    expect(themeDarkAnnotations(layout, true)).toBe(layout); // nothing to fill
    const light = { annotations: [{ text: 't' }] };
    expect(themeDarkAnnotations(light, false)).toBe(light);  // light untouched
  });
});
