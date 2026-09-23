/**
 * @jest-environment jsdom
 *
 * Diagnostic: measure the real vertical extent of each system, including the
 * dynamics/hairpins that hang BELOW the lowest staff, so the inter-system
 * spacing can be set from measurement instead of guesswork.
 *
 * Temporary file.
 */
import { renderMusicSpec, type MusicSpec, type MusicMeasure } from './zzStaged';

const SVG_NS = 'http://www.w3.org/2000/svg';
const wrapNode = (el: Element | null): any => {
  const sel: any = {
    append: (tag: string) => {
      if (!el) return wrapNode(null);
      const c = document.createElementNS(SVG_NS, tag);
      el.appendChild(c);
      return wrapNode(c);
    },
    attr: (n: string, v: unknown) => { if (el && v != null) el.setAttribute(n, String(v)); return sel; },
    style: () => sel,
    text: (v: unknown) => { if (el) el.textContent = v == null ? '' : String(v); return sel; },
    node: () => el,
  };
  return sel;
};
const d3Stub = { select: (t: any) => wrapNode(typeof t === 'string' ? document.querySelector(t) : t ?? null) };

const draw = async (spec: MusicSpec) => {
  const c = document.createElement('div');
  document.body.appendChild(c);
  await renderMusicSpec(c, spec, false, d3Stub);
  return c;
};

const E8 = (): MusicMeasure => ({
  notes: Array.from({ length: 8 }, () => ({ keys: ['c/5'], duration: '8' })),
});

const staveTops = (c: HTMLElement): number[] => {
  const out: number[] = [];
  c.querySelectorAll('.vf-stave').forEach((g) => {
    const d = g.querySelector('path')?.getAttribute('d') ?? '';
    const m = /M[\d.]+ ([\d.]+)/.exec(d);
    if (m) out.push(Math.round(Number(m[1])));
  });
  return out.sort((a, b) => a - b);
};

/**
 * Lowest y touched by ANY drawn element — the true content bottom, which is
 * what the next system has to clear. Reads every y-bearing attribute plus
 * path/line coordinates.
 */
const lowestY = (c: HTMLElement): number => {
  let max = 0;
  const bump = (v: number) => { if (Number.isFinite(v) && v > max) max = v; };
  c.querySelectorAll('*').forEach((el) => {
    for (const a of ['y', 'y1', 'y2', 'cy']) {
      const raw = el.getAttribute(a);
      if (raw != null) bump(Number(raw));
    }
    const rectY = Number(el.getAttribute('y') ?? NaN);
    const rectH = Number(el.getAttribute('height') ?? NaN);
    if (Number.isFinite(rectY) && Number.isFinite(rectH)) bump(rectY + rectH);
    const d = el.getAttribute('d');
    if (d) {
      // Every "x y" pair in the path data; take the y of each.
      const nums = d.match(/-?[\d.]+/g) ?? [];
      for (let i = 1; i < nums.length; i += 2) bump(Number(nums[i]));
    }
  });
  return Math.round(max);
};

const report = async (label: string, spec: MusicSpec) => {
  const c = await draw(spec);
  const svg = c.querySelector('svg')!;
  const tops = staveTops(c);
  const h = Number(svg.getAttribute('height'));
  const bottom = lowestY(c);
  const nStaves = (spec.staves?.length ?? 1);
  const systems = tops.length / nStaves;
  // Gap between the LAST stave of system 1 and the FIRST stave of system 2.
  const interGap = tops.length > nStaves ? tops[nStaves] - tops[nStaves - 1] : null;
  const withinGap = nStaves > 1 ? tops[1] - tops[0] : null;
  // eslint-disable-next-line no-console
  console.log(
    `GEOM ${label}: canvas_h=${h} content_bottom=${bottom} slack=${h - bottom}`
    + ` systems=${systems} withinStaveGap=${withinGap} interSystemGap=${interGap}`
    + ` ratio=${interGap && withinGap ? (interGap / withinGap).toFixed(2) : 'n/a'}`,
  );
  return c;
};

it('GEOM: 3 staves, 6 dense bars, no dynamics', async () => {
  const bars = Array.from({ length: 6 }, E8);
  await report('3-staff bare', {
    type: 'music', timeSignature: '4/4', keySignature: 'G', autoBeam: true,
    staves: [
      { clef: 'treble', name: 'Flute', shortName: 'Fl.', measures: bars },
      { clef: 'bass', name: 'Cello', shortName: 'Vc.', measures: bars },
      { clef: 'treble', name: 'Harp', shortName: 'Hp.', measures: bars },
    ],
  });
  expect(true).toBe(true);
}, 30000);

it('GEOM: 3 staves with dynamics + hairpin under the lowest staff', async () => {
  const bars = Array.from({ length: 6 }, E8);
  const dyn = bars.map((b, i) => (i === 0
    ? { notes: b.notes.map((n, j) => (j === 0 ? { ...n, dynamic: 'pp' } : n)) }
    : b));
  await report('3-staff dynamics', {
    type: 'music', timeSignature: '4/4', keySignature: 'G', autoBeam: true,
    staves: [
      { clef: 'treble', name: 'Flute', shortName: 'Fl.', measures: bars },
      { clef: 'bass', name: 'Cello', shortName: 'Vc.', measures: bars },
      // Dynamics AND a hairpin on the LOWEST staff: the worst case for the
      // room a system needs below its last stave.
      { clef: 'treble', name: 'Harp', shortName: 'Hp.', measures: dyn,
        hairpins: [{ from: 0, to: 7, type: 'cresc' }] },
    ],
  });
  expect(true).toBe(true);
}, 30000);

it('GEOM: single staff, 3 dense bars', async () => {
  await report('1-staff', {
    type: 'music', timeSignature: '4/4', autoBeam: true,
    measures: Array.from({ length: 3 }, E8),
  });
  expect(true).toBe(true);
}, 30000);
