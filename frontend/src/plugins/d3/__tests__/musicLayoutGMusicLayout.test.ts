/**
 * @jest-environment jsdom
 *
 * Regression tests for group G-MUSIC-LAYOUT (defects D-138, D-139, D-140,
 * D-149) in musicPlugin.ts.  These import the REAL module and each is paired
 * with the pre-fix behaviour so it FAILS against unpatched source:
 *
 *   D-138  A flat top-level `notes[]` was one indivisible measure the wrapper
 *          could never break -> 120 notes on one ~9500px illegible system.
 *          synthesizeMeasures / synthesizeFlatNotesMeasures split it at meter
 *          boundaries so the proven measures[] path can wrap it; short runs are
 *          returned by reference (byte-identical, no barline added).
 *   D-140  An undersized author width/height deleted content rather than sizing
 *          the box to the music.  resolveAuthorCanvasDimension floors the
 *          canvas at the content's natural size while honouring a roomy pin.
 *   D-139  A wrapped score's canvas budgeted the per-system tail only once,
 *          clipping trailing systems off the bottom.  stackedCanvasHeight
 *          budgets the tail per system.
 *   D-149  VexFlow's #999999 stave lines are 2.85:1 on white (below the 3:1
 *          boundary floor) yet 5.79:1 on the dark surface; applyMusicLightTheme
 *          darkens ONLY those rules in light, dark leaves them untouched.
 *
 * These are pure/DOM helpers -- importing the module does NOT load VexFlow
 * (that import is dynamic, inside renderMusicSpec), matching the other
 * music*.test.ts suites.
 */
import {
  noteDurationInWholes,
  parseMeterCounts,
  synthesizeMeasures,
  synthesizeFlatNotesMeasures,
  resolveAuthorCanvasDimension,
  stackedCanvasHeight,
  applyMusicLightTheme,
  applyMusicDarkTheme,
} from '../../../utils/d3Plugins/musicPlugin';

/** WCAG relative luminance / contrast, computed locally (no import needed). */
function luminance(hex: string): number {
  const h = hex.replace('#', '');
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) / 255);
  const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
function contrast(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

const q = (n: number) => Array.from({ length: n }, () => ({ keys: ['c/4'], duration: 'q' }));
const e = (n: number) => Array.from({ length: n }, () => ({ keys: ['c/4'], duration: '8' }));

describe('D-138 note-duration and measure synthesis', () => {
  it('noteDurationInWholes maps bases and dots correctly', () => {
    expect(noteDurationInWholes('w')).toBeCloseTo(1);
    expect(noteDurationInWholes('h')).toBeCloseTo(0.5);
    expect(noteDurationInWholes('q')).toBeCloseTo(0.25);
    expect(noteDurationInWholes('8')).toBeCloseTo(0.125);
    // A dot adds half the base length.
    expect(noteDurationInWholes('q.')).toBeCloseTo(0.375);
    expect(noteDurationInWholes('h..')).toBeCloseTo(0.875);
    // Unknown base falls back to a quarter, never NaN.
    expect(noteDurationInWholes('bogus')).toBeCloseTo(0.25);
  });

  it('parseMeterCounts parses and defaults to 4/4', () => {
    expect(parseMeterCounts('3/4')).toEqual([3, 4]);
    expect(parseMeterCounts('6/8')).toEqual([6, 8]);
    expect(parseMeterCounts(undefined)).toEqual([4, 4]);
    expect(parseMeterCounts('garbage')).toEqual([4, 4]);
  });

  it('synthesizeMeasures bars a flat run at the meter boundary', () => {
    // 120 eighths in 4/4: 8 eighths fill a bar exactly -> 15 measures.
    const m8 = synthesizeMeasures(e(120), 4, 4);
    expect(m8).toHaveLength(15);
    expect(m8.every((m) => m.notes.length === 8)).toBe(true);
    // 120 quarters in 4/4: 4 per bar -> 30 measures.
    const mq = synthesizeMeasures(q(120), 4, 4);
    expect(mq).toHaveLength(30);
    expect(mq.every((m) => m.notes.length === 4)).toBe(true);
    // 3/4 packs three quarters per bar.
    const m34 = synthesizeMeasures(q(12), 3, 4);
    expect(m34).toHaveLength(4);
    // No note is lost or duplicated.
    expect(mq.flatMap((m) => m.notes)).toHaveLength(120);
  });

  it('synthesizeFlatNotesMeasures reshapes only an illegibly-wide flat staff', () => {
    // 120 eighths (~9500px natural) -> synthesised measures the wrapper can break.
    const wide = synthesizeFlatNotesMeasures({ notes: e(120) } as any, 4, 4);
    expect(Array.isArray(wide.measures)).toBe(true);
    expect((wide.measures ?? []).length).toBeGreaterThan(1);
    expect(wide.notes).toBeUndefined();

    // A SHORT flat run that already fits one legible system is returned BY
    // REFERENCE -- byte-identical, no barline added (pre-fix parity).
    const shortStaff = { notes: q(4) } as any;
    expect(synthesizeFlatNotesMeasures(shortStaff, 4, 4)).toBe(shortStaff);

    // A staff that already has measures/voices is never reshaped.
    const measured = { measures: [{ notes: q(4) }] } as any;
    expect(synthesizeFlatNotesMeasures(measured, 4, 4)).toBe(measured);
    const voiced = { voices: [{ notes: q(4) }] } as any;
    expect(synthesizeFlatNotesMeasures(voiced, 4, 4)).toBe(voiced);
  });
});

describe('D-140 content-aware canvas floor', () => {
  it('honours a roomy author dimension but floors an undersized one', () => {
    // Absent -> content size.
    expect(resolveAuthorCanvasDimension(undefined, 640)).toBe(640);
    // Roomy pin honoured verbatim (byte-identical to the old `author ?? content`).
    expect(resolveAuthorCanvasDimension(2000, 640)).toBe(2000);
    expect(resolveAuthorCanvasDimension(640, 640)).toBe(640);
    // Undersized pin grows to the content floor rather than deleting content
    // (w2-06 width:120 for 40 notes; w2-13 height:60 for 12 staves).
    expect(resolveAuthorCanvasDimension(120, 3200)).toBe(3200);
    expect(resolveAuthorCanvasDimension(60, 2000)).toBe(2000);
  });
});

describe('D-139 per-system tail budgeting', () => {
  it('budgets the system tail once PER system, not once total', () => {
    const staves = 1;
    const tail = 90;
    const adv = 120;
    const spacing = 36;
    const h1 = stackedCanvasHeight(staves, 1, adv, tail, spacing, 0, 0);
    const h3 = stackedCanvasHeight(staves, 3, adv, tail, spacing, 0, 0);
    // Three systems must reserve three tails; the pre-fix formula reserved one
    // and clipped the trailing systems.  Assert the extra two tails are present.
    expect(h3 - h1).toBeGreaterThanOrEqual(2 * tail);
    // A single system is unchanged from a bare per-system stack (parity).
    expect(h1).toBe(adv * staves + tail);
    // Monotonic in system count (no truncation).
    expect(stackedCanvasHeight(staves, 4, adv, tail, spacing, 0, 0))
      .toBeGreaterThan(h3);
  });
});

describe('D-149 light-theme stave-line contrast (BOTH themes)', () => {
  function svgWith(strokeColor: string): SVGElement {
    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    const line = document.createElementNS(NS, 'path');
    line.setAttribute('stroke', strokeColor);
    const notehead = document.createElementNS(NS, 'path');
    notehead.setAttribute('fill', '#000000');
    svg.appendChild(line);
    svg.appendChild(notehead);
    return svg as unknown as SVGElement;
  }

  it('BROKEN THEME (light): the #999999 stave rule is darkened to clear 3:1 on white', () => {
    const svg = svgWith('#999999');
    // Pre-fix the rule stayed #999999 = 2.85:1 on white, below the 3:1 floor.
    expect(contrast('#999999', '#ffffff')).toBeLessThan(3);
    applyMusicLightTheme(svg);
    const rule = svg.querySelector('path')!.getAttribute('stroke')!;
    expect(rule.toLowerCase()).toBe('#6b6b6b');
    expect(contrast(rule, '#ffffff')).toBeGreaterThanOrEqual(3);
    // Black noteheads are left untouched (still 21:1 on white).
    expect(svg.querySelectorAll('path')[1].getAttribute('fill')).toBe('#000000');
  });

  it('OTHER THEME (dark): the #999999 rule is left as-is and is still >= 3:1 on the dark surface', () => {
    const svg = svgWith('#999999');
    applyMusicDarkTheme(svg);
    // Dark deliberately does NOT remap #999999 (5.79:1 on #1f1f1f already).
    expect(svg.querySelector('path')!.getAttribute('stroke')!.toLowerCase())
      .toBe('#999999');
    expect(contrast('#999999', '#1f1f1f')).toBeGreaterThanOrEqual(3);
    // And the light-theme value would also be legible on the dark surface,
    // proving the darkening is a genuine resolve, not a value that only fixes
    // one background: #6b6b6b vs #1f1f1f.
    expect(contrast('#6b6b6b', '#1f1f1f')).toBeGreaterThanOrEqual(3);
  });
});
