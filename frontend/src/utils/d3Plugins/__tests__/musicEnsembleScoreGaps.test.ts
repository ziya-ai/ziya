/**
 * @jest-environment jsdom
 *
 * Gaps found re-typesetting an ensemble harp score (Pratt, "Round",
 * mm. 19-52, Harp 1 grand staff) from a photograph.  Each test here asserts a
 * primitive or layout rule the source needed and the renderer lacked:
 *
 *   G1  crossStaffLines  -- the diagonal "l.h." hand-crossing line from a
 *       note on the upper staff to a note on the lower staff.
 *   G2  per-measure `section` -- a boxed rehearsal letter mid-score
 *       (spec-level `section` could only label the opening bar).
 *   G3  last-system justification -- a final line at least half full is
 *       stretched to the margin like the lines above it.
 *   G4  explicit systemBreaks define the lines -- the default width budget
 *       no longer auto-breaks one bar early and orphans a bar.
 *   G5  brackets: `hooks: "both"` two-hook phrase brackets, and automatic
 *       stacking of overlapping brackets onto separate bands.
 *   G7  placement against the notes: brackets clear ledger-line notes, a
 *       rehearsal mark clears a bracket on its bar, and the crossStaffLines
 *       label sits by the lower note.
 *   G6  an empty measure (`notes: []`) renders as an empty bar, documented.
 *
 * Each assertion names an element that did not exist before the fix, so the
 * test fails against the unpatched renderer.
 */
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) =>
    (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
}

import {
  renderMusicSpec, planSystemBreaks, explicitBreakBudget, placeBracketLines,
  tempoBracketLiftPx,
  type MusicSpec, type MusicBracket,
} from '../musicPlugin';

const SVG_NS = 'http://www.w3.org/2000/svg';
const makeSel = (node: any): any => {
  const sel: any = {};
  sel.append = (tag: string) => {
    const child = document.createElementNS(SVG_NS, tag);
    if (node && typeof node.appendChild === 'function') node.appendChild(child);
    return makeSel(child);
  };
  sel.attr = (k: string, v: any) => { if (node?.setAttribute) node.setAttribute(k, String(v)); return sel; };
  sel.text = (t: any) => { if (node) node.textContent = String(t); return sel; };
  // Record the style so assertions can read the font size an overlay was drawn at.
  sel.style = (k: string, v: any) => { if (v != null && node?.setAttribute) node.setAttribute('style', `${k}:${v}`); return sel; };
  sel.classed = () => sel; sel.html = () => sel;
  return sel;
};
const d3Stub = { select: (el: any) => makeSel(el) };

let warnSpy: jest.SpyInstance;
beforeEach(() => { warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {}); });
afterEach(() => { warnSpy.mockRestore(); });
beforeAll(() => {
  const { Element } = require('vexflow');
  const CH = 8;
  Element.setTextMeasurementCanvas({ getContext: () => ({ font: '', measureText: (t: string) => ({
    width: (t ?? '').length * CH, actualBoundingBoxAscent: CH, actualBoundingBoxDescent: 2,
    actualBoundingBoxLeft: 0, actualBoundingBoxRight: (t ?? '').length * CH,
    fontBoundingBoxAscent: CH, fontBoundingBoxDescent: 2 }) }) });
});

const draw = async (spec: MusicSpec) => {
  const c = document.createElement('div');
  document.body.appendChild(c);
  await renderMusicSpec(c, spec, false, d3Stub);
  return c;
};
const warnings = () => warnSpy.mock.calls.map((c) => c.join(' ')).join(' | ');
const glyphs = (c: HTMLElement) =>
  Array.from(c.querySelectorAll('text')).map((t) => t.textContent ?? '').join('');
const systemsDrawn = (c: HTMLElement) => (glyphs(c).match(/\ue050/g) ?? []).length;
const noteheads = (c: HTMLElement) => (glyphs(c).match(/[\ue0a2\ue0a3\ue0a4]/g) ?? []).length;
const heads = (c: HTMLElement) =>
  Array.from(c.querySelectorAll('.vf-notehead text'))
    .filter((t) => /^[\ue0a2\ue0a3\ue0a4]$/.test(t.textContent ?? ''))
    .map((t) => ({ x: Number(t.getAttribute('x')), y: Number(t.getAttribute('y')) }));
const lines = (c: HTMLElement) => Array.from(c.querySelectorAll('line')).map((l) => ({
  x1: Number(l.getAttribute('x1')), x2: Number(l.getAttribute('x2')),
  y1: Number(l.getAttribute('y1')), y2: Number(l.getAttribute('y2')),
  dash: l.getAttribute('stroke-dasharray'),
}));
const linePaths = (c: HTMLElement) =>
  Array.from(c.querySelectorAll('path'))
    .map((p) => /^M([\d.]+) ([\d.]+)L([\d.]+) ([\d.]+)$/.exec((p.getAttribute('d') ?? '').replace(/\s+/g, ' ').trim()))
    .filter(Boolean)
    .map((m) => ({ x1: Number(m![1]), y1: Number(m![2]), x2: Number(m![3]), y2: Number(m![4]) }));

const bar = (...ks: Array<[string, string]>) => ({ notes: ks.map(([k, d]) => ({ keys: [k], duration: d })) });
const twoStaff = (extra: Partial<MusicSpec> = {}): MusicSpec => ({
  type: 'music', timeSignature: '3/4', keySignature: 'G',
  staves: [
    { clef: 'treble', measures: [bar(['d/6', 'h.']), bar(['g/5', 'h.'])] },
    { clef: 'treble', measures: [bar(['g/4', 'h.']), bar(['d/4', 'h.'])] },
  ],
  ...extra,
} as MusicSpec);

describe('G1 crossStaffLines', () => {
  it('draws a straight line from an upper-staff note down to a lower-staff note', async () => {
    const c = await draw(twoStaff({ crossStaffLines: [{ from: [0, 0], to: [1, 1], text: 'l.h.' }] }));
    const staffLines = linePaths(c).filter((l) => l.y1 === l.y2);
    // Two staves of 5 lines: the band between their y extents is the gap.
    const ys = Array.from(new Set(staffLines.map((l) => l.y1))).sort((a, b) => a - b);
    expect(ys.length).toBeGreaterThanOrEqual(10);
    const upperBottom = ys[4]; const lowerTop = ys[5];
    const diag = linePaths(c).find((l) => l.y1 < upperBottom && l.y2 > lowerTop && l.x2 > l.x1);
    expect(diag).toBeDefined();
    expect(glyphs(c)).toContain('l.h.');
    expect(warnings()).not.toMatch(/crossStaffLines/);
  });

  it('refuses an out-of-range endpoint and a single-staff spec with a report, not a throw', async () => {
    const c = await draw(twoStaff({ crossStaffLines: [{ from: [0, 0], to: [1, 9] }] }));
    expect(noteheads(c)).toBe(4);
    expect(warnings()).toMatch(/crossStaffLines endpoint \[1, 9\] is out of range/);
    await draw({ type: 'music', notes: [{ keys: ['c/4'], duration: 'q' }], crossStaffLines: [{ from: [0, 0], to: [0, 0] }] } as MusicSpec);
    expect(warnings()).toMatch(/two or more staves/);
  });
});

describe('G2 per-measure rehearsal mark', () => {
  it('boxes the label above the bar that carries `section`, not only the opening bar', async () => {
    const spec: MusicSpec = {
      type: 'music', timeSignature: '3/4', clef: 'treble',
      measures: [bar(['g/5', 'h.']), bar(['g/5', 'h.']), { ...bar(['g/5', 'h']), section: 'B' }, bar(['a/5', 'h.'])],
    } as MusicSpec;
    const c = await draw(spec);
    const label = Array.from(c.querySelectorAll('text')).find((t) => t.textContent === 'B');
    expect(label).toBeDefined();
    const rects = Array.from(c.querySelectorAll('rect')).map((r) => ({
      x: Number(r.getAttribute('x')), w: Number(r.getAttribute('width')), fill: r.getAttribute('fill'),
    }));
    const lx = Number(label!.getAttribute('x'));
    const box = rects.find((r) => r.fill === 'none' && r.x <= lx && r.x + r.w >= lx);
    expect(box).toBeDefined();
    // Third bar of four: the mark sits past the midpoint of the system, so it
    // is at its bar and not jammed at the stave's left edge like StaveSection.
    const width = Number(c.querySelector('svg')!.getAttribute('width'));
    expect(lx).toBeGreaterThan(width / 2);
  });

  it('is not drawn as a "-B" annotation artefact and survives the voices normaliser', async () => {
    const spec: MusicSpec = {
      type: 'music', timeSignature: '3/4', clef: 'treble',
      measures: [bar(['g/5', 'h.']), { section: 'B', voices: [{ notes: [{ keys: ['g/5'], duration: 'h.' }] }] } as any],
    } as MusicSpec;
    const c = await draw(spec);
    const texts = Array.from(c.querySelectorAll('text')).map((t) => t.textContent);
    expect(texts).toContain('B');
    expect(texts).not.toContain('-B');
  });
});

describe('G3 last-system justification', () => {
  const bars = (n: number) => Array.from({ length: n }, () => bar(['g/4', 'h.']));
  // Six bars, then a last line of `lastCount` bars: 4 is a two-thirds-full
  // line (natural width BELOW the full line, so the rule is what decides),
  // 2 is a short tail.
  const mkSpec = (lastCount: number, last?: boolean): MusicSpec => ({
    type: 'music', timeSignature: '3/4', clef: 'treble',
    measures: [...bars(6), { ...bar(['g/4', 'h.']), systemBreak: true }, ...bars(lastCount - 1)],
    ...(last === undefined ? {} : { justifyLastSystem: last }),
  } as MusicSpec);
  const rightEdges = (c: HTMLElement): number[] => {
    // Rightmost end of each system's stave lines, top system first.  Only
    // long horizontals count, so a dot or a short rail cannot masquerade.
    const horiz = linePaths(c).filter((l) => l.y1 === l.y2 && l.x2 - l.x1 > 100);
    const bySystem = new Map<number, number>();
    for (const l of horiz) {
      const band = Math.round(l.y1 / 100);
      bySystem.set(band, Math.max(bySystem.get(band) ?? 0, l.x2));
    }
    return Array.from(bySystem.entries()).sort((p, q) => p[0] - q[0]).map(([, x]) => x);
  };

  it('stretches a last line that is at least half full to the same right margin by default', async () => {
    const c = await draw(mkSpec(4));
    expect(systemsDrawn(c)).toBe(2);
    const [a, b] = rightEdges(c);
    expect(Math.abs(a - b)).toBeLessThan(2);
  });

  it('`justifyLastSystem: false` keeps the natural width; a short tail is natural by default', async () => {
    const [a, b] = rightEdges(await draw(mkSpec(4, false)));
    expect(b).toBeLessThan(a - 20);
    const [ta, tb] = rightEdges(await draw(mkSpec(2)));
    expect(tb).toBeLessThan(ta - 20);
    // ...and `true` forces even the short tail to the margin.
    const [fa, fb] = rightEdges(await draw(mkSpec(2, true)));
    expect(Math.abs(fa - fb)).toBeLessThan(2);
  });
});

describe('G4 explicit breaks define the lines', () => {
  it('explicitBreakBudget raises the budget to the widest author line, capped at legibility', () => {
    const widths = [300, 300, 300, 300, 300, 300, 300, 300];
    const breaks = [false, false, false, false, true, false, false, false];
    const b = explicitBreakBudget(widths, breaks, 1200);
    expect(b).toBeGreaterThanOrEqual(110 + 300 * 4 + 24 * 3);
    expect(planSystemBreaks(widths, breaks, b)).toEqual([[0, 1, 2, 3], [4, 5, 6, 7]]);
    // Unchanged when the author placed no breaks.
    expect(explicitBreakBudget(widths, widths.map(() => false), 1200)).toBe(1200);
    // Never below the fallback, never above the legibility limit.
    expect(explicitBreakBudget([50, 50], [false, true], 1200)).toBe(1200);
    expect(explicitBreakBudget([9000, 9000], [false, true], 1200)).toBeLessThan(9000);
  });

  it('renders exactly the author-delimited systems with no orphaned bar', async () => {
    const six = () => Array.from({ length: 6 }, () => bar(['b/4', 'h'], ['a/4', 'q']));
    const spec: MusicSpec = {
      type: 'music', timeSignature: '3/4', clef: 'treble',
      measures: [...six(), { ...bar(['g/5', 'q'], ['f#/5', 'q'], ['e/5', 'q']), systemBreak: true }, ...six().slice(1),
        { ...bar(['d/6', 'h.']), systemBreak: true }, ...six().slice(1)],
    } as MusicSpec;
    const c = await draw(spec);
    expect(systemsDrawn(c)).toBe(3);
    expect(noteheads(c)).toBe(6 * 2 + 3 + 5 * 2 + 1 + 5 * 2);
  });
});

describe('G5 brackets', () => {
  it('`hooks: "both"` draws a solid bracket with a hook at each end', async () => {
    const spec: MusicSpec = {
      type: 'music', timeSignature: '3/4', clef: 'treble',
      notes: [{ keys: ['g/5'], duration: 'q' }, { keys: ['a/5'], duration: 'q' }, { keys: ['b/5'], duration: 'q' }],
      brackets: [{ from: 0, to: 2, hooks: 'both' }],
    } as MusicSpec;
    const c = await draw(spec);
    const ls = lines(c);
    const rail = ls.find((l) => l.y1 === l.y2 && l.x2 - l.x1 > 30 && l.dash == null);
    expect(rail).toBeDefined();
    const hooks = ls.filter((l) => l.x1 === l.x2 && l.y1 === rail!.y1 && l.y2 === rail!.y1 + 8);
    expect(hooks.map((h) => h.x1).sort((a, b) => a - b)).toEqual([rail!.x1, rail!.x2]);
    // Above the top stave line.
    const topLine = Math.min(...linePaths(c).filter((l) => l.y1 === l.y2).map((l) => l.y1));
    expect(rail!.y1).toBeLessThan(topLine);
  });

  it('overlapping 8va brackets no longer share a y band', async () => {
    const notes = Array.from({ length: 5 }, () => ({ keys: ['g/5'], duration: 'q' }));
    const one = await draw({ type: 'music', clef: 'treble', notes, brackets: [{ from: 0, to: 2 }] } as MusicSpec);
    const two = await draw({ type: 'music', clef: 'treble', notes, brackets: [{ from: 0, to: 2 }, { from: 2, to: 4 }] } as MusicSpec);
    // The "8" label is drawn at the bracket's own y, so distinct label ys mean
    // distinct bands.  With one bracket there is one; two overlapping ones
    // used to share it.
    const labelYs = (c: HTMLElement) =>
      Array.from(new Set(Array.from(c.querySelectorAll('text'))
        .filter((t) => t.textContent === '8')
        .map((t) => Number(t.getAttribute('y')))));
    expect(labelYs(one)).toHaveLength(1);
    expect(labelYs(two)).toHaveLength(2);
  });
});

// Defects the first end-to-end render of the Round spec showed and
// the tests above did not catch: brackets and labels placed by band, with no
// regard for the notes under them.
const noteheadYs = (c: HTMLElement): number[] =>
  Array.from(c.querySelectorAll('.vf-notehead text')).map((t) => Number(t.getAttribute('y')));

describe('G7 placement against the notes', () => {
  it('placeBracketLines stacks overlaps by step and honours minimum and explicit lines', () => {
    const bs: MusicBracket[] = [
      { from: 0, to: 2 }, { from: 2, to: 4 }, { from: 5, to: 7 }, { from: 6, to: 8, line: 7 },
    ];
    // Same as the old level stacking when nothing is raised (level 1 -> line 3).
    expect(placeBracketLines(bs, [1, 1, 1, 1])).toEqual([1, 2, 1, 7]);
    // One stave line apart is already separate...
    expect(placeBracketLines([{ from: 0, to: 2 }, { from: 2, to: 4 }], [5, 4])).toEqual([5, 4]);
    // ...but the same line is pushed up one.
    expect(placeBracketLines([{ from: 0, to: 2 }, { from: 2, to: 4 }], [5, 5])).toEqual([5, 6]);
  });

  it('a two-hook bracket over ledger-line notes clears every notehead', async () => {
    const c = await draw({
      type: 'music', timeSignature: '3/4', clef: 'treble',
      notes: [{ keys: ['d/6'], duration: 'q' }, { keys: ['g/6'], duration: 'q' }, { keys: ['d/6'], duration: 'q' }],
      brackets: [{ from: 0, to: 2, hooks: 'both' }],
    } as MusicSpec);
    const rail = lines(c).find((l) => l.y1 === l.y2 && l.x2 - l.x1 > 30);
    expect(rail).toBeDefined();
    expect(noteheadYs(c).length).toBe(3);
    expect(rail!.y1).toBeLessThan(Math.min(...noteheadYs(c)) - 6);
  });

  it('an 8va over a ledger-line D6 is raised above the note', async () => {
    const c = await draw({
      type: 'music', timeSignature: '3/4', clef: 'treble',
      notes: [{ keys: ['d/6'], duration: 'h.' }],
      brackets: [{ from: 0, to: 0, text: '8', superscript: 'va' }],
    } as MusicSpec);
    const eight = Array.from(c.querySelectorAll('text')).find((t) => t.textContent === '8');
    expect(eight).toBeDefined();
    expect(Number(eight!.getAttribute('y'))).toBeLessThan(Math.min(...noteheadYs(c)) - 6);
  });

  it('a rehearsal mark mid-system ends left of its bar\'s first note, clear of the bracket that starts there', async () => {
    const c = await draw({
      type: 'music', timeSignature: '3/4', clef: 'treble',
      measures: [bar(['g/5', 'h.']), { ...bar(['g/5', 'h'], ['a/5', 'q']), section: 'B' }],
      brackets: [{ from: 1, to: 2, hooks: 'both' }],
    } as MusicSpec);
    const box = Array.from(c.querySelectorAll('rect')).find((r) => r.getAttribute('fill') === 'none');
    expect(box).toBeDefined();
    const right = Number(box!.getAttribute('x')) + Number(box!.getAttribute('width'));
    const rail = lines(c).find((l) => l.y1 === l.y2 && l.x2 - l.x1 > 20);
    expect(rail).toBeDefined();
    expect(right).toBeLessThan(rail!.x1);
  });
  it('the crossStaffLines label sits by the lower note, not above the upper staff', async () => {
    const c = await draw(twoStaff({ crossStaffLines: [{ from: [0, 0], to: [1, 1], text: 'l.h.' }] }));
    const ys = Array.from(new Set(linePaths(c).filter((l) => l.y1 === l.y2).map((l) => l.y1)))
      .sort((a, b) => a - b);
    const lowerTop = ys[5];
    const labels = Array.from(c.querySelectorAll('text')).filter((t) => t.textContent === 'l.h.');
    expect(labels).toHaveLength(1);
    expect(Number(labels[0].getAttribute('y'))).toBeGreaterThan(lowerTop);
  });
});

// End to end: the score that motivated this file, mm. 19-52 of Harp 1, as a
// fixture.  Each assertion is one of the defects the first full render
// showed, checked on the rendered SVG rather than on an intermediate.
describe('Round Harp 1 (fixture)', () => {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const spec: MusicSpec = require('./fixtures/roundHarp1.json');
  const heads = (c: HTMLElement) =>
    // The notehead group also holds augmentation dots and modifiers; keep
    // only the notehead glyphs (whole / half / black).
    Array.from(c.querySelectorAll('.vf-notehead text'))
      .filter((t) => /^[\ue0a2\ue0a3\ue0a4]$/.test(t.textContent ?? ''))
      .map((t) => ({ x: Number(t.getAttribute('x')), y: Number(t.getAttribute('y')) }));
  const overlayRails = (c: HTMLElement) =>
    lines(c).filter((l) => l.y1 === l.y2 && l.x2 - l.x1 > 20);

  it('draws six systems of two staves, every note, with no renderer warnings', async () => {
    const c = await draw(spec);
    expect(systemsDrawn(c)).toBe(12);
    const expected = spec.staves!.reduce((n, s) =>
      n + (s.measures ?? []).reduce((m, bar) => m + bar.notes.filter((x: any) => !x.rest)
        .reduce((k: number, x: any) => k + x.keys.length, 0), 0), 0);
    expect(heads(c).length).toBe(expected);
    expect(warnings()).toBe('');
  });

  it('no phrase-bracket rail passes through a notehead beneath it', async () => {
    const c = await draw(spec);
    const hs = heads(c);
    const rails = overlayRails(c);
    expect(rails.length).toBeGreaterThanOrEqual(16);
    for (const r of rails) {
      const under = hs.filter((h) => h.x >= r.x1 - 2 && h.x <= r.x2 && h.y > r.y1 - 6 && h.y < r.y1 + 60);
      for (const h of under) expect(h.y).toBeGreaterThan(r.y1 + 4);
    }
  });

  it('each 8va sits above the note it covers', async () => {
    const c = await draw(spec);
    const hs = heads(c);
    const eights = Array.from(c.querySelectorAll('text')).filter((t) => t.textContent === '8');
    expect(eights).toHaveLength(3);
    for (const e of eights) {
      const x = Number(e.getAttribute('x')); const y = Number(e.getAttribute('y'));
      const near = hs.filter((h) => Math.abs(h.x - x) < 20 && h.y > y - 10 && h.y < y + 60);
      expect(near.length).toBeGreaterThan(0);
      for (const h of near) expect(h.y).toBeGreaterThan(y + 6);
    }
  });

  it('the rehearsal box sits before the barline of its bar and crosses no bracket rail', async () => {
    const c = await draw(spec);
    const box = Array.from(c.querySelectorAll('rect')).find((r) => r.getAttribute('fill') === 'none');
    expect(box).toBeDefined();
    const bx = Number(box!.getAttribute('x')); const by = Number(box!.getAttribute('y'));
    const bw = Number(box!.getAttribute('width')); const bh = Number(box!.getAttribute('height'));
    for (const r of overlayRails(c)) {
      const overlapsX = r.x1 < bx + bw && r.x2 > bx;
      const insideY = r.y1 > by - 1 && r.y1 < by + bh + 1;
      expect(overlapsX && insideY).toBe(false);
    }
    // ...and it ends left of the m33 first note (the source puts it by the barline).
    const noteAfter = heads(c).filter((h) => h.x > bx + bw).sort((a, b) => a.x - b.x)[0];
    expect(noteAfter.x - (bx + bw)).toBeGreaterThan(15);
  });
  it('each "l.h." label sits beside the lower-staff note it names', async () => {
    const c = await draw(spec);
    const hs = heads(c);
    const labels = Array.from(c.querySelectorAll('text')).filter((t) => t.textContent === 'l.h.');
    expect(labels).toHaveLength(2);
    for (const l of labels) {
      const x = Number(l.getAttribute('x')); const y = Number(l.getAttribute('y'));
      // A notehead just above-right of the label: the lower endpoint.
      expect(hs.some((h) => h.x >= x && h.x - x < 30 && y - h.y > 0 && y - h.y < 30)).toBe(true);
    }
  });
});

// The two limits left open by G7: below-staff brackets and the tempo band.
describe('G8 below-staff clearance and tempo over a raised bracket', () => {
  it('an 8vb under ledger-line low notes sits below every notehead', async () => {
    const c = await draw({
      type: 'music', timeSignature: '3/4', clef: 'treble',
      notes: [{ keys: ['d/3'], duration: 'h.' }],
      brackets: [{ from: 0, to: 0, text: '8', superscript: 'vb', position: 'below' }],
    } as MusicSpec);
    const eight = Array.from(c.querySelectorAll('text')).find((t) => t.textContent === '8');
    expect(eight).toBeDefined();
    expect(Number(eight!.getAttribute('y'))).toBeGreaterThan(Math.max(...noteheadYs(c)) + 6);
  });

  it('tempoBracketLiftPx is 0 in the tempo band and grows a line per line above it', () => {
    const notes = [{ keys: ['d/6'], duration: 'q' }, { keys: ['g/6'], duration: 'q' }] as any;
    // D6 needs line 3, which is the tempo band itself.
    expect(tempoBracketLiftPx([{ from: 0, to: 0 }], notes, 'treble', 2)).toBe(0);
    // G6 needs line 4: one line (10px) above the band.
    expect(tempoBracketLiftPx([{ from: 1, to: 1 }], notes, 'treble', 2)).toBe(10);
    // ...but not when that bracket is on a later system, or below the staff.
    expect(tempoBracketLiftPx([{ from: 1, to: 1 }], notes, 'treble', 1)).toBe(0);
    expect(tempoBracketLiftPx([{ from: 1, to: 1, position: 'below' }], notes, 'treble', 2)).toBe(0);
  });

  it('a tempo mark stays above an 8va raised over a G6', async () => {
    const c = await draw({
      type: 'music', timeSignature: '3/4', clef: 'treble',
      tempo: { name: 'Allegro', duration: 'q', bpm: 120 },
      notes: [{ keys: ['g/6'], duration: 'h.' }],
      brackets: [{ from: 0, to: 0, text: '8', superscript: 'va' }],
    } as MusicSpec);
    const texts = Array.from(c.querySelectorAll('text'));
    const tempo = texts.find((t) => t.textContent === 'Allegro');
    const eight = texts.find((t) => t.textContent === '8');
    expect(tempo).toBeDefined();
    expect(eight).toBeDefined();
    expect(Number(tempo!.getAttribute('y'))).toBeLessThan(Number(eight!.getAttribute('y')) - 6);
    // ...and nothing was pushed off the canvas top to make room.
    expect(Number(tempo!.getAttribute('y'))).toBeGreaterThan(0);
  });
});

describe('G9 whole-bar rest in a short meter', () => {
  it('keeps the staves aligned: the bar after a 3/4 whole rest starts at the same x on both staves', async () => {
    const c = await draw({
      type: 'music', timeSignature: '3/4', clef: 'treble',
      staves: [
        { clef: 'treble', measures: [bar(['g/5', 'h.']), bar(['a/5', 'h.']), bar(['b/5', 'h.'])] },
        { clef: 'treble', measures: [{ notes: [{ rest: true, duration: 'w' }] }, bar(['d/4', 'h.']), bar(['e/4', 'h.'])] },
      ],
    } as MusicSpec);
    const heads = Array.from(c.querySelectorAll('.vf-notehead text'))
      .filter((t) => /^[\ue0a2\ue0a3\ue0a4]$/.test(t.textContent ?? ''))
      .map((t) => Number(t.getAttribute('x')));
    // upper: g5 a5 b5 ; lower: d4 e4 (the rest is not a notehead glyph)
    expect(heads).toHaveLength(5);
    const [, upperA, upperB, lowerD, lowerE] = heads;
    expect(Math.abs(upperA - lowerD)).toBeLessThan(2);
    expect(Math.abs(upperB - lowerE)).toBeLessThan(2);
  });
});

describe('G10 empty bars, grand-staff barlines, single-note 8va', () => {
  it('an empty bar on one staff keeps the next bar aligned with the other staff', async () => {
    const c = await draw({
      type: 'music', timeSignature: '3/4', clef: 'treble',
      staves: [
        { clef: 'treble', measures: [bar(['g/5', 'h.']), bar(['a/5', 'h.']), bar(['b/5', 'h.'])] },
        { clef: 'treble', measures: [bar(['d/4', 'h.']), { notes: [] }, bar(['e/4', 'h.'])] },
      ],
    } as MusicSpec);
    const xs = Array.from(c.querySelectorAll('.vf-notehead text'))
      .filter((t) => /^[\ue0a2\ue0a3\ue0a4]$/.test(t.textContent ?? ''))
      .map((t) => Number(t.getAttribute('x')));
    expect(xs).toHaveLength(5);
    expect(Math.abs(xs[2] - xs[4])).toBeLessThan(2);
  });

  it('interior barlines are joined across the gap between the two staves', async () => {
    const c = await draw({
      type: 'music', timeSignature: '3/4', clef: 'treble',
      staves: [
        { clef: 'treble', measures: [bar(['g/5', 'h.']), bar(['g/5', 'h.'])] },
        { clef: 'treble', measures: [bar(['d/4', 'h.']), bar(['d/4', 'h.'])] },
      ],
    } as MusicSpec);
    // The only <line> overlay in this spec is the join: it spans the inter-staff gap.
    const joins = lines(c).filter((l) => l.x1 === l.x2 && l.y2 - l.y1 > 40);
    expect(joins.length).toBeGreaterThanOrEqual(1);
  });

  it('a single-note 8va is "8va" + dashes + a hook down, not a bare stub', async () => {
    const c = await draw({
      type: 'music', timeSignature: '3/4', clef: 'treble',
      notes: [{ keys: ['d/6'], duration: 'h.' }],
      brackets: [{ from: 0, to: 0, text: '8', superscript: 'va' }],
    } as MusicSpec);
    const texts = Array.from(c.querySelectorAll('text')).map((t) => t.textContent);
    expect(texts).toContain('8'); expect(texts).toContain('va');
    const rail = lines(c).find((l) => l.y1 === l.y2 && l.dash && l.x2 - l.x1 >= 20);
    expect(rail).toBeDefined();
    expect(lines(c).some((l) => l.x1 === l.x2 && l.x1 === rail!.x2 && l.y2 > l.y1)).toBe(true);
  });
});

describe('G11 between-staff dynamics and run-out brackets', () => {
  const dyn = (c: HTMLElement, t: string) =>
    Array.from(c.querySelectorAll('text')).find((e) => e.textContent === t)!;
  const grand = (extra: any = {}) => ({
    type: 'music', timeSignature: '3/4', clef: 'treble',
    staves: [
      { clef: 'treble', measures: [bar(['g/5', 'h.']), { notes: [{ keys: ['a/5'], duration: 'h.', dynamic: 'mp', dynamicPlacement: 'between' }] }] },
      { clef: 'treble', measures: [bar(['d/4', 'h.']), bar(['e/4', 'h.'])] },
    ], ...extra } as MusicSpec);

  it('a between-staves dynamic sits in the gap, large, and ends before its bar (not first of system)', async () => {
    const c = await draw(grand());
    const m = dyn(c, 'mp');
    expect(m).toBeDefined();
    // Full-width stave lines only: the A5 here sits on a ledger line, which is a
    // short horizontal and would otherwise shift the stave-line indices by one.
    const ys = Array.from(new Set(linePaths(c).filter((l) => l.y1 === l.y2 && l.x2 - l.x1 > 100).map((l) => l.y1))).sort((a, b) => a - b);
    const y = Number(m.getAttribute('y'));
    expect(y).toBeGreaterThan(ys[4]); expect(y).toBeLessThan(ys[5]);
    expect(m.getAttribute('style') ?? '').toMatch(/2\dpx/);
    expect(m.getAttribute('text-anchor')).toBe('end');
    const noteX = heads(c).filter((h) => h.y < ys[4]).sort((a, b) => a.x - b.x)[1].x;
    expect(Number(m.getAttribute('x'))).toBeLessThan(noteX - 15);
  });

  it('a between-staves dynamic on the first note of a system is centred on it', async () => {
    const c = await draw({
      type: 'music', timeSignature: '3/4', clef: 'treble',
      staves: [
        { clef: 'treble', measures: [{ notes: [{ keys: ['a/5'], duration: 'h.', dynamic: 'f', dynamicPlacement: 'between' }] }] },
        { clef: 'treble', measures: [bar(['e/4', 'h.'])] },
      ],
    } as MusicSpec);
    const m = dyn(c, 'f');
    expect(m.getAttribute('text-anchor')).toBe('middle');
  });

  it('`hooks: "start"` hooks the left end only and runs the rail to the end of the system', async () => {
    const c = await draw({
      type: 'music', timeSignature: '3/4', clef: 'treble',
      notes: [{ keys: ['g/5'], duration: 'q' }, { keys: ['a/5'], duration: 'q' }, { keys: ['b/5'], duration: 'q' }],
      brackets: [{ from: 0, to: 2, hooks: 'start' }],
    } as MusicSpec);
    const ls = lines(c);
    const rail = ls.find((l) => l.y1 === l.y2 && l.x2 - l.x1 > 30 && l.dash == null)!;
    expect(rail).toBeDefined();
    const lastHead = Math.max(...heads(c).map((h) => h.x));
    expect(rail.x2).toBeGreaterThan(lastHead + 30);
    expect(ls.filter((l) => l.x1 === l.x2 && l.y1 === rail.y1 && l.y2 > l.y1).map((l) => l.x1)).toEqual([rail.x1]);
  });

  it('a lower-staff bracket below the staff hooks upward', async () => {
    const c = await draw(grand({}));
    const c2 = await draw({
      type: 'music', timeSignature: '3/4', clef: 'treble',
      notes: [{ keys: ['d/4'], duration: 'q' }, { keys: ['e/4'], duration: 'q' }, { keys: ['f/4'], duration: 'q' }],
      brackets: [{ from: 0, to: 2, hooks: 'both', position: 'below' }],
    } as MusicSpec);
    const bottom = Math.max(...linePaths(c2).filter((l) => l.y1 === l.y2).map((l) => l.y1));
    const rail = lines(c2).find((l) => l.y1 === l.y2 && l.x2 - l.x1 > 30 && l.y1 > bottom)!;
    expect(rail).toBeDefined();
    expect(lines(c2).some((l) => l.x1 === l.x2 && l.y1 === rail.y1 && l.y2 < rail.y1)).toBe(true);
    expect(c).toBeDefined();
  });
});

describe('G12 print layout: staffGap and systemWidth', () => {
  const grand = (extra: any = {}) => ({
    type: 'music', timeSignature: '3/4', clef: 'treble',
    staves: [
      { clef: 'treble', measures: Array.from({ length: 6 }, () => bar(['g/5', 'h.'])) },
      { clef: 'treble', measures: Array.from({ length: 6 }, () => bar(['d/4', 'h.'])) },
    ], ...extra } as MusicSpec);
  const topLines = (c: HTMLElement) => {
    const ys = Array.from(new Set(linePaths(c).filter((l) => l.y1 === l.y2 && l.x2 - l.x1 > 100)
      .map((l) => l.y1))).sort((a, b) => a - b);
    return [ys[0], ys[5]];
  };
  it('defaults to 8 empty spaces between staves (120px top line to top line)', async () => {
    const [a, b] = topLines(await draw(grand()));
    expect(b - a).toBe(120);
  });
  it('staffGap tightens the grand staff and shrinks the canvas', async () => {
    const wide = await draw(grand());
    const tight = await draw(grand({ staffGap: 4 }));
    const [a, b] = topLines(tight);
    expect(b - a).toBe(80);
    const h = (c: HTMLElement) => Number(c.querySelector('svg')!.getAttribute('height'));
    // The canvas budgets one stave advance per staff: 2 staves x 40px.
    expect(h(tight)).toBe(h(wide) - 80);
  });
  it('systemWidth sets the justified width without turning wrapping off', async () => {
    const spec = grand({ systemWidth: 700 });
    spec.staves![0].measures![3].systemBreak = true;
    spec.staves![1].measures![3].systemBreak = true;
    const c = await draw(spec);
    expect(Number(c.querySelector('svg')!.getAttribute('width'))).toBe(700);
    expect(systemsDrawn(c)).toBe(4);      // two systems x two staves
    expect(noteheads(c)).toBe(12);
  });
});

describe('G6 empty measure', () => {
  it('renders an empty bar with its barlines and no notes', async () => {
    const c = await draw({
      type: 'music', timeSignature: '3/4', clef: 'treble',
      measures: [bar(['g/4', 'h.']), { notes: [] }, bar(['g/4', 'h.'])],
    } as MusicSpec);
    expect(noteheads(c)).toBe(2);
    expect(warnings()).toBe('');
  });
});
