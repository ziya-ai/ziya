import { repairAuthorTextOnFill } from '../plotlyPreprocessor';
import { contrastRatio } from '../chartTheme';

/**
 * D-459 (author-text-on-fill-no-contrast-repair), gfx-sweep G-0934c9.
 *
 * Author-pinned textfont / insidetextfont colours drawn on author fills get no
 * contrast repair: #333 inside-text on a #1f2d3d bar = 1.11:1, #fff on #f7f7f7
 * bars = 1.07:1, #f8f8f8 pie labels on pale slices ~1.06:1. The failure is
 * theme-independent (both fill and text are author-pinned), so it must be
 * repaired for BOTH backgrounds. repairAuthorTextOnFill nudges each failing
 * text colour against its OWN fill until it clears 4.5:1 — scalar fill -> scalar
 * repair, per-element marker.colors -> matched array.
 */

const FLOOR = 4.5;

describe('repairAuthorTextOnFill (D-459)', () => {
  it('repairs a dark #333 inside-label on a dark author bar fill', () => {
    const data: any[] = [{
      type: 'bar',
      marker: { color: '#1f2d3d' },
      insidetextfont: { color: '#333333', size: 12 },
    }];
    expect(contrastRatio('#333333', '#1f2d3d')).toBeLessThan(FLOOR);
    const out = repairAuthorTextOnFill(data);
    const c = out[0].insidetextfont.color;
    expect(c).not.toBe('#333333');
    expect(contrastRatio(c, '#1f2d3d')).toBeGreaterThanOrEqual(FLOOR);
    // size preserved
    expect(out[0].insidetextfont.size).toBe(12);
  });

  it('repairs a white #fff on-bar label on a near-white author fill', () => {
    const data: any[] = [{ type: 'bar', marker: { color: '#f7f7f7' }, textfont: { color: '#ffffff' } }];
    expect(contrastRatio('#ffffff', '#f7f7f7')).toBeLessThan(FLOOR);
    const out = repairAuthorTextOnFill(data);
    expect(contrastRatio(out[0].textfont.color, '#f7f7f7')).toBeGreaterThanOrEqual(FLOOR);
  });

  it('emits a per-slice array for a pie with marker.colors and a scalar author label colour', () => {
    const data: any[] = [{
      type: 'pie',
      marker: { colors: ['#ffffff', '#111111', '#eeeeee'] },
      insidetextfont: { color: '#f8f8f8' },
    }];
    const out = repairAuthorTextOnFill(data);
    const colors = out[0].insidetextfont.color;
    expect(Array.isArray(colors)).toBe(true);
    expect(colors).toHaveLength(3);
    colors.forEach((c: string, i: number) => {
      expect(contrastRatio(c, data[0].marker.colors[i])).toBeGreaterThanOrEqual(FLOOR);
    });
    // The slice where #f8f8f8 already reads (#111111) keeps the author colour.
    expect(colors[1]).toBe('#f8f8f8');
  });

  it('leaves an already-legible author text colour byte-identical (reference-equal on no-op)', () => {
    const data: any[] = [{ type: 'bar', marker: { color: '#1f2d3d' }, insidetextfont: { color: '#ffffff' } }];
    expect(contrastRatio('#ffffff', '#1f2d3d')).toBeGreaterThanOrEqual(FLOOR);
    expect(repairAuthorTextOnFill(data)).toBe(data);
  });

  it('does not invent a text colour where the author set none (plotly auto-contrast still applies)', () => {
    const data: any[] = [{ type: 'bar', marker: { color: '#1f2d3d' } }];
    const out = repairAuthorTextOnFill(data);
    expect(out[0].insidetextfont).toBeUndefined();
    expect(out[0].textfont).toBeUndefined();
    expect(out).toBe(data);
  });
});
