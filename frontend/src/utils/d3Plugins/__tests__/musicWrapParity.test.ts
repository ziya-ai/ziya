/**
 * @jest-environment jsdom
 *
 * REGRESSION: a score that does not wrap must keep its exact single-system
 * layout.
 *
 * System wrapping restructured the single `factory.System` into N stacked
 * systems and made every stave a per-system slice.  That is a large enough
 * change to the render core that feature tests passing is not evidence the
 * previously-correct output survived.
 *
 * During development this suite imported the original module alongside a staged
 * copy and compared the emitted SVG byte for byte; all 34 cases below matched
 * exactly (after normalising VexFlow's global id counter).  With the change
 * landed there is only one module, so that comparison would compare a render to
 * itself.  The cases are kept as SNAPSHOTS instead: they still fail if any
 * coordinate, path or glyph moves, which is the property the comparison was
 * really asserting.
 *
 * Every spec here fits one system, which is true of every spec in the older
 * suites too -- so this pins the layout those 194 assertions describe.
 */

// Polyfill structuredClone for jest's jsdom environment: vexflow 5.0.0 uses
// it in metrics.getFontInfo, and jest's jsdom global does not expose it on
// Node 20 (a plain-data font-metrics clone, so JSON round-trip suffices).
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) =>
    (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
}

import { renderMusicSpec, type MusicSpec } from '../musicPlugin';

/** Records overlay text so the d3-drawn layers are observable, not just VexFlow. */
const makeD3 = () => {
  const calls: string[] = [];
  const node = () => {
    const self: any = {
      append: (t: any) => { calls.push(`append:${t}`); return node(); },
      attr: (k: any, v: any) => { calls.push(`attr:${k}=${v}`); return self; },
      style: (k: any, v: any) => { calls.push(`style:${k}=${v}`); return self; },
      text: (v: any) => { calls.push(`text:${v}`); return self; },
    };
    return self;
  };
  return { d3: { select: () => node() }, calls };
};

/**
 * VexFlow numbers its elements from a GLOBAL monotonic counter, so the same
 * spec rendered twice in a row emits `id="vf-auto3424"` the first time and
 * `id="vf-auto3520"` the second.  Comparing raw markup therefore reports a
 * difference on every single element even when the geometry is byte-identical
 * -- which is exactly what happened: 31 of 33 parity cases "failed" purely on
 * counter drift.  The ids carry no layout meaning, so they are normalised out
 * and everything else (coordinates, path data, glyph codepoints, attribute
 * order) is compared verbatim.
 */
const stripVolatileIds = (markup: string): string =>
  markup.replace(/vf-auto\d+/g, 'vf-auto');

// Install a VexFlow text-measurement canvas.  Bare `npx jest` does not load
// CRA's setupTests.ts, so VexFlow's measurement canvas resolves to jsdom's
// unimplemented getContext and every glyph width is 0 -- which shifts every
// coordinate in the serialized SVG and so breaks these byte-exact layout
// snapshots (and throws in a trill's Vibrato constructor).  Provide the
// measurement canvas through VexFlow's own API so the geometry these snapshots
// pin is the faithful one, matching how the committed baseline was recorded.
beforeAll(() => {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { Element } = require('vexflow');
  const CH = 8;
  Element.setTextMeasurementCanvas({
    getContext: () => ({
      font: '',
      measureText: (t: string) => ({
        width: (t ?? '').length * CH,
        actualBoundingBoxAscent: CH,
        actualBoundingBoxDescent: 2,
        actualBoundingBoxLeft: 0,
        actualBoundingBoxRight: (t ?? '').length * CH,
        fontBoundingBoxAscent: CH,
        fontBoundingBoxDescent: 2,
      }),
    }),
  });
});

const renderWith = async (fn: any, spec: MusicSpec, dark = false) => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const { d3, calls } = makeD3();
  await fn(container, spec, dark, d3);
  const svg = container.querySelector('svg');
  return {
    // Full serialized SVG minus the volatile ids: the strongest available signal.
    svg: svg ? stripVolatileIds(svg.outerHTML) : '',
    width: svg?.getAttribute('width') ?? null,
    height: svg?.getAttribute('height') ?? null,
    overlay: calls.join('|'),
  };
};

/**
 * Pin a spec's rendered geometry.
 *
 * Snapshots the normalised SVG plus the canvas box and the overlay calls.  The
 * length guard keeps a silently-empty render from passing: an empty snapshot
 * would otherwise be accepted on first run and then "match" forever.
 */
const expectStableLayout = async (spec: MusicSpec, dark = false) => {
  const r = await renderWith(renderMusicSpec, spec, dark);
  expect(r.svg.length).toBeGreaterThan(200);
  // A single system, which is the precondition these cases are about.
  expect((r.svg.match(/vf-stave"/g) ?? []).length)
    .toBe((spec.staves?.length ?? 1));
  expect({ width: r.width, height: r.height }).toMatchSnapshot('canvas');
  expect(r.overlay).toMatchSnapshot('overlay');
  expect(r.svg).toMatchSnapshot('svg');
};

const Q4 = [
  { keys: ['c/5'], duration: 'q' },
  { keys: ['d/5'], duration: 'q' },
  { keys: ['e/5'], duration: 'q' },
  { keys: ['f/5'], duration: 'q' },
];
const E8 = [
  { keys: ['c/5'], duration: '8' }, { keys: ['d/5'], duration: '8' },
  { keys: ['e/5'], duration: '8' }, { keys: ['f/5'], duration: '8' },
  { keys: ['g/5'], duration: '8' }, { keys: ['a/5'], duration: '8' },
  { keys: ['b/5'], duration: '8' }, { keys: ['c/6'], duration: '8' },
];

describe('unwrapped layout: single-measure specs', () => {
  it('a bare flat note list', async () => {
    await expectStableLayout({ type: 'music', timeSignature: '4/4', notes: Q4 });
  });

  it('rests', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      notes: [Q4[0], { rest: true, duration: 'q' }, Q4[2], { rest: true, duration: 'q' }],
    });
  });

  it('a key signature and a bass clef', async () => {
    await expectStableLayout({
      type: 'music', clef: 'bass', keySignature: 'Eb', timeSignature: '3/4',
      notes: Q4.slice(0, 3),
    });
  });

  it('articulations and ornaments', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      notes: [
        { ...Q4[0], articulations: ['staccato', 'accent'] },
        { ...Q4[1], ornaments: ['trill'] },
        { ...Q4[2], articulations: ['tenuto'] },
        { ...Q4[3], articulations: ['fermata-above'] },
      ],
    });
  });

  it('dynamics', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      notes: [{ ...Q4[0], dynamic: 'pp' }, Q4[1], { ...Q4[2], dynamic: 'ff' }, Q4[3]],
    });
  });

  it('slurs, ties and hairpins', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4', notes: Q4,
      slurs: [{ from: 0, to: 2 }],
      ties: [{ from: 2, to: 3 }],
      hairpins: [{ from: 0, to: 3, type: 'cresc' }],
    });
  });

  it('glissandos and trill lines', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4', notes: Q4,
      glissandos: [{ from: 0, to: 1, text: 'gliss.' }],
      trillLines: [{ from: 2, to: 3 }],
    });
  });

  it('brackets', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4', notes: Q4,
      brackets: [{ from: 0, to: 3, text: '8', superscript: 'va' }],
    });
  });

  it('tuplets', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      notes: [
        { keys: ['c/5'], duration: '8' }, { keys: ['d/5'], duration: '8' },
        { keys: ['e/5'], duration: '8' }, ...Q4.slice(1),
      ],
      tuplets: [{ from: 0, to: 2 }],
    });
  });

  it('auto-beaming', async () => {
    await expectStableLayout({ type: 'music', timeSignature: '4/4', autoBeam: true, notes: E8 });
  });

  it('explicit beam groups', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '6/8', autoBeam: true,
      beamGroups: [[3, 8]], notes: E8.slice(0, 6),
    });
  });

  it('grace notes', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      notes: [
        { ...Q4[0], graceNotes: [{ keys: ['b/4'], duration: '8', slash: true }] },
        ...Q4.slice(1),
      ],
    });
  });

  it('fingerings, string numbers and chord symbols', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      notes: [
        { ...Q4[0], fingering: '3' },
        { ...Q4[1], stringNumber: '2' },
        { ...Q4[2], chordSymbol: 'Cmaj7' },
        { ...Q4[3], chordSymbol: { text: 'V', position: 'below' } },
      ],
    });
  });

  it('lyrics', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      notes: Q4.map((n, i) => ({ ...n, lyric: `syl${i}` })),
    });
  });

  it('annotations', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      notes: [{ ...Q4[0], annotations: [{ text: 'dolce', position: 'above' }] }, ...Q4.slice(1)],
    });
  });

  it('a harp pedal diagram', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      notes: [{ ...Q4[0], harpPedal: '^v-|vv-^' }, ...Q4.slice(1)],
    });
  });

  it('chords', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      notes: [{ keys: ['c/5', 'e/5', 'g/5'], duration: 'w' }],
    });
  });
});

describe('unwrapped layout: system chrome', () => {
  it('tempo, mark, volta, measure number and section', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4', notes: Q4,
      tempo: { name: 'Allegro', duration: 'q', bpm: 120 },
      mark: 'segno', volta: { type: 'begin', label: '1.' },
      measureNumber: 5, section: 'A',
    });
  });

  it('a title block', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4', notes: Q4,
      title: 'Study', subtitle: 'for parity', composer: 'Anon.',
    });
  });

  it('outer barlines', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4', notes: Q4,
      beginBar: 'repeat-begin', endBar: 'repeat-end',
    });
  });

  it('an explicit width and height', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4', notes: Q4, width: 500, height: 200,
    });
  });

  it('dark mode', async () => {
    await expectStableLayout({ type: 'music', timeSignature: '4/4', notes: Q4 }, true);
  });
});

describe('unwrapped layout: multi-measure specs that fit one system', () => {
  it('two measures', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      measures: [{ notes: Q4 }, { notes: Q4 }],
    });
  });

  it('two measures with an inner repeat', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      measures: [{ notes: Q4, endBar: 'repeat-end' }, { notes: Q4 }],
    });
  });

  it('a slur crossing a barline', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      measures: [{ notes: Q4 }, { notes: Q4 }],
      slurs: [{ from: 2, to: 5 }],
    });
  });

  it('dynamics in a later measure', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      measures: [
        { notes: [{ ...Q4[0], dynamic: 'pp' }, ...Q4.slice(1)] },
        { notes: [{ ...Q4[0], dynamic: 'ff' }, ...Q4.slice(1)] },
      ],
    });
  });

  it('auto-beaming per measure', async () => {
    // One bar of eighths plus one of quarters is 12 notes -- inside the
    // legibility budget, so this must still render as a single system and match
    // the original byte for byte.  (Two bars of straight eighths is 16 notes /
    // 1382px, which legitimately wraps; see the case below.)
    await expectStableLayout({
      type: 'music', timeSignature: '4/4', autoBeam: true,
      measures: [{ notes: E8 }, { notes: Q4 }],
    });
  });

  it('wraps once a score exceeds the legibility budget', async () => {
    // The one intended behaviour change: two bars of eighths computed a 1382px
    // canvas, which rendered at ~54% scale in a normal column.  The new code
    // wraps it instead.  Pinned as an explicit expectation so this shows up as
    // the feature working rather than as unexplained parity drift.
    const spec: MusicSpec = {
      type: 'music', timeSignature: '4/4', autoBeam: true,
      measures: [{ notes: E8 }, { notes: E8 }],
    };
    // Wrapped: several systems, none of them the old 1382px ribbon.
    const wrapped = await renderWith(renderMusicSpec, spec);
    // Opting out reproduces the pre-wrapping geometry, which is the thing the
    // old ribbon width can still be asserted against.
    const flat = await renderWith(renderMusicSpec, { ...spec, maxSystemWidth: 99999 });
    expect(Number(flat.width)).toBeGreaterThan(1300);          // the old ribbon
    expect(Number(wrapped.width)).toBeLessThan(Number(flat.width));
    expect(Number(wrapped.height)).toBeGreaterThan(Number(flat.height));
    // Two systems where the flat render had one.
    expect((wrapped.svg.match(/vf-stave"/g) ?? []).length)
      .toBeGreaterThan((flat.svg.match(/vf-stave"/g) ?? []).length);
  });
});

describe('unwrapped layout: multi-staff specs', () => {
  it('a grand staff', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      staves: [
        { clef: 'treble', notes: Q4 },
        { clef: 'bass', notes: Q4 },
      ],
    });
  });

  it('named staves', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      staves: [
        { clef: 'treble', name: 'Flute', notes: Q4 },
        { clef: 'bass', name: 'Cello', notes: Q4 },
      ],
    });
  });

  it('three staves with per-staff keys and spans', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      staves: [
        { clef: 'treble', name: 'Fl.', notes: Q4, slurs: [{ from: 0, to: 3 }] },
        { clef: 'bass', name: 'Vc.', notes: Q4, keySignature: 'F' },
        { clef: 'treble', name: 'Hp.', notes: Q4, hairpins: [{ from: 0, to: 3, type: 'dim' }] },
      ],
    });
  });

  it('a grand staff over two measures', async () => {
    await expectStableLayout({
      type: 'music', timeSignature: '4/4',
      staves: [
        { clef: 'treble', measures: [{ notes: Q4 }, { notes: Q4 }] },
        { clef: 'bass', measures: [{ notes: Q4 }, { notes: Q4 }] },
      ],
    });
  });
});

describe('unwrapped layout: malformed specs still fail the same way', () => {
  it('an unparseable pitch throws in both', async () => {
    const spec = {
      type: 'music', timeSignature: '4/4',
      notes: [{ keys: ['not-a-pitch'], duration: 'q' }],
    } as unknown as MusicSpec;
    const message = await renderWith(renderMusicSpec, spec).catch((e) => e.message);
    expect(typeof message).toBe('string');
    // The error must still name the real cause rather than surfacing as an
    // unrelated voice complaint from downstream.
    expect(message).toMatch(/Could not parse/);
  });

  it('an out-of-range span warns in both', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const spec: MusicSpec = {
      type: 'music', timeSignature: '4/4', notes: Q4, slurs: [{ from: 0, to: 99 }],
    };
    await renderWith(renderMusicSpec, spec);
    const messages = warn.mock.calls.map((c) => c.join(' ')).join('\n');
    warn.mockRestore();
    expect(messages).toMatch(/out of range/);
  });
});
