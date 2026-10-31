/**
 * @jest-environment jsdom
 *
 * VexFlow's default entry embeds Bravura and starts loading it at import time
 * without exposing the promise, so `await import('vexflow')` can resolve while
 * the font is still loading.  Glyphs measured then use a fallback font:
 * noteheads come out ~2.4x too wide, stems detach from heads and ledger lines
 * overshoot.  A headless capture is always a cold page, so it always hit this;
 * jest never could, because the other music suites stub the measurement
 * canvas.  renderMusicSpec must wait for the music fonts before drawing.
 */
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) =>
    (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
}

import { renderMusicSpec, ensureMusicFonts } from '../musicPlugin';

const fixture = require('./fixtures/roundHarp1.json');

const SVG_NS = 'http://www.w3.org/2000/svg';
const makeSel = (node: any): any => {
  const sel: any = {};
  sel.append = (tag: string) => {
    const child = document.createElementNS(SVG_NS, tag);
    node?.appendChild?.(child);
    return makeSel(child);
  };
  sel.attr = (k: string, v: any) => { node?.setAttribute?.(k, String(v)); return sel; };
  sel.text = (t: any) => { if (node) node.textContent = String(t); return sel; };
  sel.style = () => sel; sel.classed = () => sel; sel.html = () => sel;
  return sel;
};
const d3Stub = { select: (el: any) => makeSel(el) };

describe('music font readiness', () => {
  afterEach(() => { delete (document as any).fonts; });

  it('ensureMusicFonts waits for Bravura and Academico to load', async () => {
    const loaded: string[] = [];
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    (document as any).fonts = {
      load: jest.fn((spec: string) => gate.then(() => { loaded.push(spec); return []; })),
      ready: Promise.resolve(),
    };
    let done = false;
    const p = ensureMusicFonts().then(() => { done = true; });
    await Promise.resolve();
    expect(done).toBe(false);          // still waiting on the font
    release();
    await p;
    expect(done).toBe(true);
    expect(loaded.join(' ')).toMatch(/Bravura/);
    expect(loaded.join(' ')).toMatch(/Academico/);
  });

  it('renderMusicSpec does not draw until the fonts have loaded', async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    (document as any).fonts = { load: jest.fn(() => gate.then(() => [])), ready: gate };
    const c = document.createElement('div');
    const render = renderMusicSpec(c, fixture, false, d3Stub);
    await new Promise((r) => setTimeout(r, 50));
    expect(c.querySelector('svg')).toBeNull();   // nothing drawn while fonts load
    release();
    await render;
    expect(c.querySelector('svg')).not.toBeNull();
  });

  it('a missing or failing FontFaceSet never blocks rendering', async () => {
    (document as any).fonts = { load: () => Promise.reject(new Error('offline')), ready: Promise.resolve() };
    await expect(ensureMusicFonts()).resolves.toBeUndefined();
    delete (document as any).fonts;
    await expect(ensureMusicFonts()).resolves.toBeUndefined();
  });
});

describe('Round fixture bars', () => {
  // Both staves share one VexFlow system and align by tick position, so one
  // short bar shifts every later note of that staff a beat early.
  const whole = (d: string) => {
    const dots = (d.match(/\./g) ?? []).length;
    const base = ({ w: 1, h: 0.5, q: 0.25, '8': 0.125 } as any)[d.replace(/\./g, '')];
    return base * (2 - Math.pow(0.5, dots));
  };
  it('every bar of every staff sums to the 3/4 meter', () => {
    for (const [si, staff] of fixture.staves.entries()) {
      staff.measures.forEach((m: any, mi: number) => {
        if (m.notes.length === 0) return;                 // deliberate empty bars
        // A lone whole rest is the whole-bar rest in any meter (the renderer
        // gives it the bar's length), so it counts as the full 3/4.
        if (m.notes.length === 1 && m.notes[0].rest && m.notes[0].duration === 'w') return;
        const sum = m.notes.reduce((s: number, n: any) => s + whole(n.duration), 0);
        expect({ staff: si, bar: 19 + mi, sum }).toEqual({ staff: si, bar: 19 + mi, sum: 0.75 });
      });
    }
  });
});
