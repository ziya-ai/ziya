/**
 * G-0daf08 / D-153 (light-palette-not-canvas-aware) — mermaid light theme.
 *
 * mermaid.initialize's DARK branch carries an extensive high-contrast
 * themeVariables override; the LIGHT branch historically carried ONLY
 * buildPieThemeVariables(false). So mermaid's stock `default` palette — placed
 * without reference to the WHITE render canvas — rendered illegibly:
 *   - quadrantChart white point labels on near-white quadrants (~1.04:1, w1-13)
 *   - journey white task labels on pale-yellow bands (~1.02:1, w1-09)
 *   - gitGraph pale yellow/green branch strokes on white (~1.07:1, w1-10)
 *   - mindmap pale link ribbons (~1.05:1, w1-11)
 *   - xychart-beta near-white first bar on white (~1.15:1, w1-14)
 *
 * buildMermaidLightThemeVariables() supplies canvas-aware text/label colours and
 * the diagram-type palettes (quadrant*, journey task text, git0..git7 + labels,
 * lineColor, xyChart.plotColorPalette). Every value is verified against the
 * WHITE canvas here.
 *
 * DIRECTION: this suite fails against the pre-fix build, where the light branch
 * defined none of these keys (the exported builder did not exist). The
 * assertions below require each fixed entry to clear its contrast floor on
 * white, so an empty/missing light palette cannot satisfy them.
 *
 * BOTH-THEME obligation: D-153 is the LIGHT half of a per-theme split — the dark
 * branch already meets contrast on its dark canvas and is unchanged. This suite
 * discharges the light half (legible on #ffffff) and also asserts the builder
 * does NOT touch node-background keys, so the dark path and the already-legible
 * light flowchart/class/state/sequence diagrams are not disturbed.
 */

import { resolveStyleColorToRgb, contrastRatioRgb } from '../mermaidEnhancer';
import { buildMermaidLightThemeVariables } from '../mermaidPlugin';

const WHITE = { r: 255, g: 255, b: 255 };
const BLACK = { r: 0, g: 0, b: 0 };

const rgb = (c: string) => {
  const v = resolveStyleColorToRgb(c);
  if (!v) throw new Error(`unresolved colour: ${c}`);
  return v;
};
const crWhite = (c: string) => contrastRatioRgb(rgb(c), WHITE);

describe('D-153: light-theme mermaid palette is canvas-aware (white background)', () => {
  const vars = buildMermaidLightThemeVariables();

  it('quadrant + journey + generic text fills are dark and >= 4.5:1 on white', () => {
    const textKeys = [
      'textColor', 'titleColor', 'labelColor',
      'taskTextColor', 'taskTextDarkColor', 'taskTextLightColor',
      'taskTextOutsideColor', 'actorTextColor',
      'quadrantPointTextFill', 'quadrant1TextFill', 'quadrant2TextFill',
      'quadrant3TextFill', 'quadrant4TextFill', 'quadrantTitleFill',
      'quadrantXAxisTextFill', 'quadrantYAxisTextFill',
      'tagLabelColor',
    ];
    for (const k of textKeys) {
      expect(typeof vars[k]).toBe('string');
      // A white/near-white label on the white canvas (the pre-fix default) would
      // score ~1:1 here; every fixed text colour must clear the WCAG AA floor.
      expect(crWhite(vars[k] as string)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it('generic lineColor (mindmap ribbons / edges) is legible on white', () => {
    // Graphical strokes need >= 3:1; a dark line clears it comfortably.
    expect(crWhite(vars.lineColor as string)).toBeGreaterThanOrEqual(3.0);
  });

  it('gitGraph branch strokes clear 3:1 on white and each label reads on its branch', () => {
    for (let i = 0; i < 8; i++) {
      const branch = vars[`git${i}`] as string;
      const label = vars[`gitBranchLabel${i}`] as string;
      expect(typeof branch).toBe('string');
      // Branch stroke visible on the white canvas (graphical floor).
      expect(crWhite(branch)).toBeGreaterThanOrEqual(3.0);
      // Branch label text legible on its OWN branch colour.
      expect(contrastRatioRgb(rgb(label), rgb(branch))).toBeGreaterThanOrEqual(4.5);
    }
  });

  it('xychart plotColorPalette leads with white-legible bar/line colours', () => {
    const xy = vars.xyChart as { plotColorPalette: string };
    expect(typeof xy?.plotColorPalette).toBe('string');
    const palette = xy.plotColorPalette.split(',').map((s) => s.trim());
    // First two series (bar + line in w1-14) must be visible on white.
    expect(crWhite(palette[0])).toBeGreaterThanOrEqual(3.0);
    expect(crWhite(palette[1])).toBeGreaterThanOrEqual(3.0);
    // The pre-fix default led with #ECECFF (near-white) — assert it is gone.
    expect(palette[0].toUpperCase()).not.toBe('#ECECFF');
  });

  it('does NOT override node backgrounds, so flowchart/class/state light diagrams are untouched', () => {
    for (const k of ['mainBkg', 'nodeBkg', 'clusterBkg', 'secondBkg', 'background']) {
      expect(vars[k]).toBeUndefined();
    }
  });
});
