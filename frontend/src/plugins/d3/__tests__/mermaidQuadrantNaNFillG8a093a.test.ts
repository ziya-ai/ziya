/**
 * @jest-environment jsdom
 *
 * G-8a093a / D-153 (mermaid-w1-13): the quadrantChart LIGHT theme fill is
 * emitted by mermaid as a MALFORMED `hsl(240, 100%, NaN%)` (NaN lightness from
 * an upstream theme-derivation bug). The shared post-render contrast pass
 * (`colorUtils.enhanceSVGVisibility`) resolves that as the point label's
 * background, but it parses to null, so:
 *   - `calculateContrastRatio` collapses to a degenerate 1 (which DOES trip the
 *     fix path), and
 *   - `getOptimalTextColor` blindly returns its `#ffffff` default,
 * so a white quadrant point label was repainted WHITE — invisible on the white
 * canvas (~1.04:1). Adding the earlier `quadrantPointTextFill` themeVariable did
 * not help because this DOM pass runs afterward and overrode it back to white.
 *
 * The fix: when the resolved element background is unparseable, fall back to the
 * THEME page background (#ffffff light / #2e3440 dark) so the readable-text
 * decision stays correct on BOTH themes. This is theme-derived, not a constant
 * swap: light -> BLACK label (21:1 on the white canvas), dark -> WHITE label
 * (12.5:1 on the dark canvas).
 *
 * NON-VACUOUS: on the pre-fix code the LIGHT assertion fails because the label
 * stays '#ffffff' (getOptimalTextColor's unparseable default) rather than
 * flipping to '#000000'.
 */
import { enhanceSVGVisibility } from '../../../utils/colorUtils';

const SVGNS = 'http://www.w3.org/2000/svg';

/** Build a minimal quadrant-point group: a NaN-hsl background rect + a white label. */
function buildQuadrantPointSvg(): { svg: SVGElement; label: SVGTextElement } {
  const svg = document.createElementNS(SVGNS, 'svg') as SVGElement;
  const g = document.createElementNS(SVGNS, 'g');
  // The quadrant background fill mermaid actually emits in light mode.
  const rect = document.createElementNS(SVGNS, 'rect');
  rect.setAttribute('width', '200');
  rect.setAttribute('height', '200');
  rect.setAttribute('fill', 'hsl(240, 100%, NaN%)');
  const label = document.createElementNS(SVGNS, 'text') as SVGTextElement;
  label.setAttribute('fill', '#ffffff'); // mermaid default point-label colour
  label.textContent = 'Dark mode parity';
  g.appendChild(rect);
  g.appendChild(label);
  svg.appendChild(g);
  document.body.appendChild(svg);
  return { svg, label };
}

describe('G-8a093a / D-153: quadrant point label over a malformed hsl(NaN) fill', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('LIGHT: recolours a white label to BLACK when its bg is an unparseable hsl(NaN)', () => {
    const { svg, label } = buildQuadrantPointSvg();
    enhanceSVGVisibility(svg, /* isDarkMode */ false);
    // Pre-fix: getOptimalTextColor('hsl(...NaN%)') -> '#ffffff' -> label stays white.
    expect(label.getAttribute('fill')).toBe('#000000');
  });

  it('DARK: keeps the label WHITE (legible on the dark canvas), not black', () => {
    const { svg, label } = buildQuadrantPointSvg();
    enhanceSVGVisibility(svg, /* isDarkMode */ true);
    // Falls back to the dark page bg (#2e3440); white reads at ~12.5:1, so the
    // white label is retained rather than being flipped to an illegible black.
    expect(label.getAttribute('fill')).not.toBe('#000000');
  });

  it('does not disturb a label whose background parses normally (regression)', () => {
    const svg = document.createElementNS(SVGNS, 'svg') as SVGElement;
    const g = document.createElementNS(SVGNS, 'g');
    const rect = document.createElementNS(SVGNS, 'rect');
    rect.setAttribute('width', '200');
    rect.setAttribute('height', '200');
    rect.setAttribute('fill', '#1f77b4'); // a real, parseable dark-blue fill
    const label = document.createElementNS(SVGNS, 'text') as SVGTextElement;
    label.setAttribute('fill', '#ffffff'); // white on dark-blue = legible
    label.textContent = 'SSO';
    g.appendChild(rect);
    g.appendChild(label);
    svg.appendChild(g);
    document.body.appendChild(svg);
    enhanceSVGVisibility(svg, false);
    // #ffffff on #1f77b4 is ~4.8:1 (> 3), so the label is left as-is.
    expect(label.getAttribute('fill')).toBe('#ffffff');
  });
});
