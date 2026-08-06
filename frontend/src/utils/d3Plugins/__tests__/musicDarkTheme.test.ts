/**
 * @jest-environment jsdom
 *
 * Regression tests for dark-mode legibility of rendered music notation.
 *
 * The bug: VexFlow hardcodes its ink to black and has no theme hook.  It
 * writes fill="black" stroke="black" onto the ROOT <svg>, and virtually
 * every notehead, stem, clef, rest and glyph inherits from there rather
 * than carrying a colour of its own.  On the dark chat background
 * (#1f1f1f) that measures 1.27:1 contrast -- black ink on near-black.
 *
 * The tests are written against MEASURED CONTRAST rather than against
 * specific hex values wherever possible, so they assert the property that
 * actually matters (can a human read the staff) instead of freezing a
 * palette choice.  A future palette change that stays legible should not
 * break these; one that regresses legibility must.
 */
import {
  applyMusicDarkTheme,
  musicInkColor,
  renderMusicSpec,
  type MusicSpec,
} from '../musicPlugin';

/** Dark backgrounds the notation is actually composited against. */
const MESSAGE_BG = '#1f1f1f';   // .dark .message.human in index.css
const BODY_BG = '#141414';      // document.body in ThemeContext.tsx
const LIGHT_BG = '#ffffff';

/** WCAG relative luminance. Mirrors colorUtils.luminance. */
function luminance(hex: string): number {
  let h = hex.replace('#', '');
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  const f = (c: number) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function contrast(a: string, b: string): number {
  const [la, lb] = [luminance(a), luminance(b)];
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/**
 * Build a minimal SVG shaped exactly like real VexFlow 5 output.
 *
 * Each element carries a `data-role` so assertions can address it by
 * identity.  Addressing by DOM position instead is a trap that was hit
 * while developing these tests: `querySelectorAll('path')[2]` pointed at
 * the StringNumber path rather than the ledger line, and the ledger
 * assertion then reported a comfortable pass while measuring the wrong
 * element entirely.
 */
function makeVexflowLikeSvg(): SVGElement {
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg') as SVGElement;
  // The root ink every glyph inherits -- this is the actual bug surface.
  svg.setAttribute('fill', 'black');
  svg.setAttribute('stroke', 'black');

  // A notehead: text with NO fill of its own, inheriting from the root.
  const notehead = document.createElementNS(NS, 'text');
  notehead.setAttribute('data-role', 'notehead');
  notehead.setAttribute('stroke', 'none');
  notehead.textContent = '\uE0A4';
  svg.appendChild(notehead);

  // A stem: stroke-only path, fill="none" is load-bearing.
  const stem = document.createElementNS(NS, 'path');
  stem.setAttribute('data-role', 'stem');
  stem.setAttribute('fill', 'none');
  stem.setAttribute('stroke-width', '1.5');
  svg.appendChild(stem);

  // Ledger line: explicit #444, survives root inheritance.
  const ledger = document.createElementNS(NS, 'path');
  ledger.setAttribute('data-role', 'ledger');
  ledger.setAttribute('fill', 'none');
  ledger.setAttribute('stroke', '#444');
  svg.appendChild(ledger);

  // StringNumber's dashed connector: explicit #000000.
  const stringNum = document.createElementNS(NS, 'path');
  stringNum.setAttribute('data-role', 'stringnumber');
  stringNum.setAttribute('stroke', '#000000');
  svg.appendChild(stringNum);

  // Stave lines: #999999, already legible on dark, must be left alone.
  const staveGroup = document.createElementNS(NS, 'g');
  staveGroup.setAttribute('class', 'vf-stave');
  staveGroup.setAttribute('stroke', '#999999');
  svg.appendChild(staveGroup);

  return svg;
}

/** Address a fixture element by role, never by DOM position. */
function role(svg: SVGElement, name: string): Element {
  const el = svg.querySelector(`[data-role="${name}"]`);
  if (!el) throw new Error(`fixture has no element with data-role="${name}"`);
  return el;
}

describe('applyMusicDarkTheme — root ink', () => {
  it('lifts the root fill off black, which nearly every glyph inherits', () => {
    const svg = makeVexflowLikeSvg();
    expect(svg.getAttribute('fill')).toBe('black');

    applyMusicDarkTheme(svg);

    const fill = svg.getAttribute('fill')!;
    expect(fill.toLowerCase()).not.toBe('black');
    expect(contrast(fill, MESSAGE_BG)).toBeGreaterThanOrEqual(7);
    expect(contrast(fill, BODY_BG)).toBeGreaterThanOrEqual(7);
  });

  it('lifts the root stroke off black, which stems and beams inherit', () => {
    const svg = makeVexflowLikeSvg();
    applyMusicDarkTheme(svg);

    const stroke = svg.getAttribute('stroke')!;
    expect(stroke.toLowerCase()).not.toBe('black');
    expect(contrast(stroke, MESSAGE_BG)).toBeGreaterThanOrEqual(7);
  });

  it('leaves inheriting glyphs without their own colour, so one root fixes all', () => {
    // A notehead must NOT gain its own fill: the whole approach depends on
    // inheritance, and per-element fills would defeat later restyling.
    const svg = makeVexflowLikeSvg();
    applyMusicDarkTheme(svg);

    expect(role(svg, 'notehead').getAttribute('fill')).toBeNull();
  });
});

describe('applyMusicDarkTheme — explicitly-coloured exceptions', () => {
  it('recolours ledger lines, which carry #444 and so ignore the root', () => {
    const svg = makeVexflowLikeSvg();
    applyMusicDarkTheme(svg);

    const stroke = role(svg, 'ledger').getAttribute('stroke')!;
    expect(stroke.toLowerCase()).not.toBe('#444');
    // 1.69:1 before the fix; must clear the 3:1 non-text minimum with margin.
    expect(contrast(stroke, MESSAGE_BG)).toBeGreaterThanOrEqual(4.5);
  });

  it('recolours StringNumber\'s explicit #000000 connector line', () => {
    const svg = makeVexflowLikeSvg();
    applyMusicDarkTheme(svg);

    const stroke = role(svg, 'stringnumber').getAttribute('stroke')!;
    expect(stroke.toLowerCase()).not.toBe('#000000');
    expect(contrast(stroke, MESSAGE_BG)).toBeGreaterThanOrEqual(4.5);
  });

  it('keeps ledger lines subordinate to note ink, as in light mode', () => {
    // VexFlow's light palette puts ledger lines (#444, 9.7:1) below note ink
    // (black, 21:1) so they position without competing.  Dark mode must
    // preserve that ordering rather than flattening everything to one value.
    const svg = makeVexflowLikeSvg();
    applyMusicDarkTheme(svg);

    const ink = svg.getAttribute('fill')!;
    const ledger = role(svg, 'ledger').getAttribute('stroke')!;
    expect(contrast(ledger, MESSAGE_BG)).toBeLessThan(contrast(ink, MESSAGE_BG));
  });

  it('leaves stave lines alone — #999999 is already legible on dark', () => {
    // Guards against over-correction: lightening the staff would make the
    // lines compete with the noteheads they exist to position.
    const svg = makeVexflowLikeSvg();
    expect(contrast('#999999', MESSAGE_BG)).toBeGreaterThan(4.5); // premise

    applyMusicDarkTheme(svg);

    expect(svg.querySelector('g.vf-stave')!.getAttribute('stroke')).toBe('#999999');
  });
});

describe('applyMusicDarkTheme — structural attributes must survive', () => {
  it('preserves fill="none" on stroke-only paths', () => {
    // Recolouring fill="none" would flood every stem and slur with ink.
    const svg = makeVexflowLikeSvg();
    applyMusicDarkTheme(svg);

    expect(role(svg, 'stem').getAttribute('fill')).toBe('none');
    expect(role(svg, 'ledger').getAttribute('fill')).toBe('none');
  });

  it('preserves stroke="none" on filled text', () => {
    // Recolouring stroke="none" would outline every notehead glyph.
    const svg = makeVexflowLikeSvg();
    applyMusicDarkTheme(svg);

    expect(role(svg, 'notehead').getAttribute('stroke')).toBe('none');
  });

  it('does not thicken hairline strokes', () => {
    // enhanceSVGVisibility force-sets stroke-width:2 on strokes it judges
    // invisible.  Stems are deliberately 1.5; thickening smears the staff.
    // This is why the shared enhancer is not reused here.
    const svg = makeVexflowLikeSvg();
    applyMusicDarkTheme(svg);

    expect(role(svg, 'stem').getAttribute('stroke-width')).toBe('1.5');
  });

  it('is a no-op on null rather than throwing', () => {
    expect(() => applyMusicDarkTheme(null)).not.toThrow();
  });

  it('is idempotent, so a re-render cannot compound the shift', () => {
    const svg = makeVexflowLikeSvg();
    applyMusicDarkTheme(svg);
    const once = svg.getAttribute('fill');
    const ledgerOnce = role(svg, 'ledger').getAttribute('stroke');

    applyMusicDarkTheme(svg);

    expect(svg.getAttribute('fill')).toBe(once);
    expect(role(svg, 'ledger').getAttribute('stroke')).toBe(ledgerOnce);
  });
});

describe('musicInkColor', () => {
  it('is legible on the dark chat background', () => {
    expect(contrast(musicInkColor(true), MESSAGE_BG)).toBeGreaterThanOrEqual(7);
  });

  it('is legible on a light background', () => {
    expect(contrast(musicInkColor(false), LIGHT_BG)).toBeGreaterThanOrEqual(7);
  });

  it('returns different ink per theme', () => {
    expect(musicInkColor(true)).not.toBe(musicInkColor(false));
  });
});

/**
 * End-to-end through real VexFlow.  jsdom has no 2D canvas context, so text
 * measurement degrades to empty metrics and layout is approximate -- but
 * drawing still completes and the emitted attributes are real, which is
 * exactly what these assertions inspect.
 */
describe('renderMusicSpec (integration) — dark mode', () => {
  const d3Stub = {
    select: () => ({ append: () => ({ attr: () => ({ attr: () => ({}) }) }) }),
  };

  const render = async (spec: MusicSpec, isDarkMode: boolean) => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    await renderMusicSpec(container, spec, isDarkMode, d3Stub);
    return container;
  };

  const spec: MusicSpec = {
    type: 'music',
    clef: 'treble',
    timeSignature: '4/4',
    notes: [
      { keys: ['c/5'], duration: 'q' },
      { keys: ['d/5'], duration: 'q' },
      { keys: ['e/5'], duration: 'q' },
      { keys: ['f/5'], duration: 'q' },
    ],
  };

  it('emits no black root ink when dark', async () => {
    const c = await render(spec, true);
    const svg = c.querySelector('svg')!;

    expect(svg.getAttribute('fill')?.toLowerCase()).not.toBe('black');
    expect(svg.getAttribute('stroke')?.toLowerCase()).not.toBe('black');
    expect(contrast(svg.getAttribute('fill')!, MESSAGE_BG)).toBeGreaterThanOrEqual(7);
  });

  it('leaves light mode exactly as VexFlow drew it', async () => {
    // The light path was never broken; the fix must not perturb it.
    const c = await render(spec, false);
    const svg = c.querySelector('svg')!;

    expect(svg.getAttribute('fill')).toBe('black');
    expect(svg.getAttribute('stroke')).toBe('black');
  });

  it('recolours ledger lines on notes outside the staff', async () => {
    // c/7 and c/3 sit far outside the treble staff, forcing ledger lines --
    // the real source of the #444 strokes.
    const c = await render({
      type: 'music',
      clef: 'treble',
      notes: [
        { keys: ['c/7'], duration: 'q' },
        { keys: ['c/3'], duration: 'q' },
      ],
    }, true);
    const svg = c.querySelector('svg')!;

    expect(svg.querySelectorAll('[stroke="#444"]').length).toBe(0);
  });

  it('still recolours elements drawn after the main factory pass', async () => {
    // Hairpins and beams are drawn after factory.draw(); a recolour done too
    // early would leave those additions black.
    const c = await render({
      type: 'music',
      clef: 'treble',
      notes: [
        { keys: ['c/5'], duration: '8' },
        { keys: ['d/5'], duration: '8' },
        { keys: ['e/5'], duration: '8' },
        { keys: ['f/5'], duration: '8' },
      ],
      autoBeam: true,
      hairpins: [{ from: 0, to: 3, type: 'cresc' }],
    }, true);
    const svg = c.querySelector('svg')!;

    for (const el of Array.from(svg.querySelectorAll('*'))) {
      for (const attr of ['fill', 'stroke']) {
        const v = el.getAttribute(attr);
        if (!v || v === 'none') continue;
        expect(['black', '#000', '#000000']).not.toContain(v.toLowerCase());
      }
    }
  });

  it('still produces a drawable staff when dark', async () => {
    // Guards against the recolour pass throwing and aborting the render.
    const c = await render(spec, true);
    expect(c.querySelector('svg')).not.toBeNull();
    expect(c.querySelectorAll('path').length).toBeGreaterThan(4);
  });
});
