/**
 * @jest-environment jsdom
 *
 * Grace notes (appoggiatura / acciaccatura / ornamental runs).
 *
 * Two things are under test:
 *   1. `toStaveNoteKey` -- grace notes are built through StaveNote's own
 *      constructor, which parses the SLASH key form ("b/4"), NOT the
 *      slash-less EasyScore form ("B4").  Feeding it the EasyScore key was the
 *      bug behind a 30s render hang: StaveNote cannot parse "B4", the grace
 *      note gets a NaN y, and GraceNoteGroup's pre-format loop never converges.
 *      The slashless natural ("cn5") was the one form both converters missed.
 *   2. A render smoke test: a spec carrying graceNotes renders to an SVG
 *      without hanging (the positive outcome the key-form guard produces; it
 *      would time out if the guard regressed).
 */

// Polyfill structuredClone for jest's jsdom environment: vexflow 5.0.0 uses
// it in metrics.getFontInfo, and jest's jsdom global does not expose it on
// Node 20 (a plain-data font-metrics clone, so JSON round-trip suffices).
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) =>
    (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
}

import { toStaveNoteKey, renderMusicSpec, type MusicSpec } from '../musicPlugin';

const makeChain = () => {
  const chain: any = {};
  for (const m of ['attr', 'style', 'text', 'append', 'classed', 'html']) chain[m] = () => chain;
  return chain;
};
const d3Stub = { select: () => makeChain() };
const draw = async (spec: MusicSpec) => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  await renderMusicSpec(container, spec, false, d3Stub);
  return container;
};

describe('toStaveNoteKey (grace-note key form)', () => {
  it('passes a slash key through unchanged', () => {
    expect(toStaveNoteKey('b/4')).toBe('b/4');
    expect(toStaveNoteKey('c#/5')).toBe('c#/5');
    expect(toStaveNoteKey('bb/3')).toBe('bb/3');
  });

  it('inserts the slash into an EasyScore-form key', () => {
    expect(toStaveNoteKey('B4')).toBe('B/4');
    expect(toStaveNoteKey('C#5')).toBe('C#/5');
  });

  it('handles an explicit natural in both forms (the form both converters missed)', () => {
    expect(toStaveNoteKey('cn/5')).toBe('cn/5');
    expect(toStaveNoteKey('cn5')).toBe('cn/5');
  });

  it('clamps an out-of-range octave so it cannot hang the ledger-line loop', () => {
    expect(toStaveNoteKey('c/999')).toBe('c/9');
    expect(toStaveNoteKey('C999')).toBe('C/9');
  });
});

describe('grace-note render smoke', () => {
  it('renders a note carrying grace notes to an SVG without hanging', async () => {
    const c = await draw({
      type: 'music',
      notes: [
        {
          keys: ['c/5'], duration: 'q',
          graceNotes: [
            { keys: ['b/4'], duration: '8' },
            { keys: ['a/4'], duration: '8', slash: true },
          ],
        },
        { keys: ['d/5'], duration: 'q' },
      ],
    });
    expect(c.querySelector('svg')).not.toBeNull();
    // main notes + grace notes all become stavenote groups; at least the mains.
    expect(c.querySelectorAll('g.vf-stavenote').length).toBeGreaterThanOrEqual(2);
  });

  it('renders a chord grace note without hanging', async () => {
    const c = await draw({
      type: 'music',
      notes: [
        { keys: ['g/4'], duration: 'h', graceNotes: [{ keys: ['e/5', 'g/5'], duration: '16' }] },
      ],
    });
    expect(c.querySelector('svg')).not.toBeNull();
  });
});
