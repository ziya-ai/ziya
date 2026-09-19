/**
 * @jest-environment jsdom
 */
/**
 * G-961e2c / D-424 + D-425: sequenceDiagram autonumber legibility and
 * unbounded stress-input clamping.
 *
 * D-424 (sequence-autonumber-digits-invisible): mermaid paints the autonumber
 * step numeral at essentially the same value as its disc, so the numbers are
 * unreadable in BOTH themes. enhanceSequenceAutonumberLegibility repaints the
 * disc + numeral as a matched, theme-resolved high-contrast pair.
 *
 * D-425 (single-label-40k-chars-node-fills-8000px-cap-text-subpixel):
 * clampMermaidNodeLabels bounds a pathologically long quoted label so the node
 * cannot inflate past the render px cap and drive the text sub-pixel.
 *
 * These are structural/theme-independent (D-425) or explicitly both-theme
 * (D-424) defects; the assertions below cover both themes and FAIL without the
 * fix (raw mermaid leaves the numeral ~= disc; an un-clamped 40k label passes
 * through unchanged).
 */

import {
  enhanceSequenceAutonumberLegibility,
  clampMermaidNodeLabels,
} from '../mermaidPlugin';

// --- helpers -------------------------------------------------------------

const SVG_NS = 'http://www.w3.org/2000/svg';

function relLum(hex: string): number {
  const h = hex.replace('#', '');
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  const f = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
function contrast(a: string, b: string): number {
  const [hi, lo] = [relLum(a), relLum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

function buildAutonumberSvg(): SVGElement {
  const svg = document.createElementNS(SVG_NS, 'svg') as SVGElement;
  // mermaid renders the disc as a <marker id$="-sequencenumber"> containing a
  // <circle>, and the number as <text class="sequenceNumber">. Reproduce that.
  const defs = document.createElementNS(SVG_NS, 'defs');
  const marker = document.createElementNS(SVG_NS, 'marker');
  marker.setAttribute('id', 'mermaid-123-sequencenumber');
  const circle = document.createElementNS(SVG_NS, 'circle');
  // low-contrast against the numeral it will carry (the defect state)
  circle.setAttribute('fill', '#000000');
  marker.appendChild(circle);
  defs.appendChild(marker);
  svg.appendChild(defs);

  for (let i = 1; i <= 3; i++) {
    const t = document.createElementNS(SVG_NS, 'text');
    t.setAttribute('class', 'sequenceNumber');
    t.setAttribute('fill', '#111111'); // nearly matches the disc -> invisible
    t.textContent = String(i);
    svg.appendChild(t);
  }
  return svg;
}

// --- D-424: autonumber legibility ---------------------------------------

describe('sequence autonumber legibility (D-424)', () => {
  for (const dark of [false, true] as const) {
    const theme = dark ? 'dark' : 'light';

    it(`repaints numeral/disc to a legible pair in ${theme}`, () => {
      const svg = buildAutonumberSvg();

      const fixed = enhanceSequenceAutonumberLegibility(svg, dark);
      expect(fixed).toBe(3);

      const numeral = svg.querySelector('text.sequenceNumber') as SVGElement;
      const disc = svg.querySelector('[id$="-sequencenumber"] circle') as SVGElement;
      const numeralColor = numeral.style.fill || numeral.getAttribute('fill')!;
      const discColor = disc.style.fill || disc.getAttribute('fill')!;

      // Numeral must be clearly readable on its disc in this theme.
      expect(contrast(numeralColor, discColor)).toBeGreaterThanOrEqual(4.5);
    });
  }

  it('is a no-op when there are no autonumber numerals', () => {
    const svg = document.createElementNS(SVG_NS, 'svg') as SVGElement;
    const t = document.createElementNS(SVG_NS, 'text');
    t.textContent = 'not a number';
    svg.appendChild(t);
    expect(enhanceSequenceAutonumberLegibility(svg, false)).toBe(0);
    expect(enhanceSequenceAutonumberLegibility(svg, true)).toBe(0);
  });
});

// --- D-425: unbounded label clamp ---------------------------------------

describe('mermaid node label clamp (D-425)', () => {
  const bigLabel = 'Lorem ipsum dolor sit amet '.repeat(1500); // ~40k chars
  const def = `flowchart TD\n  big["${bigLabel}"]\n  big --> tail[End]`;

  for (const theme of ['light', 'dark'] as const) {
    it(`bounds a 40k-char label to a legible length (${theme}, theme-independent)`, () => {
      // preprocessing is theme-independent; assert the same for both.
      const out = clampMermaidNodeLabels(def);
      const m = out.match(/big\["([^"]*)"\]/);
      expect(m).not.toBeNull();
      const clamped = m![1];
      expect(clamped.length).toBeLessThan(bigLabel.length);
      expect(clamped.length).toBeLessThanOrEqual(820);
      expect(clamped.endsWith('[…]')).toBe(true);
      // the short label is untouched
      expect(out).toContain('tail[End]');
    });
  }

  it('leaves ordinary definitions byte-unchanged', () => {
    const normal = 'flowchart TD\n  a["A short label"] --> b["Another"]';
    expect(clampMermaidNodeLabels(normal)).toBe(normal);
  });
});
