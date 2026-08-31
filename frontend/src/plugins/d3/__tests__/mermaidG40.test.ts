/**
 * @jest-environment jsdom
 *
 * G-40 mermaid theme + structural regression tests.
 *
 * D-160 (timeline-light-fill-light-text:dark): mermaid v11's `dark` theme derives
 *   timeline `cScaleLabelN` self-referentially, producing sections with a LIGHT
 *   fill AND a light label (~1.6:1) alongside near-black bands. The fix supplies
 *   an internally-consistent light-fill + dark-label palette for dark timelines.
 *
 * D-161 (linkstyle-stroke-override-dropped:dark): the dark visibility pass
 *   repaints every edge with the theme lineColor, discarding explicit
 *   `linkStyle ... stroke:` overrides. The fix re-applies them AFTER the pass and
 *   honour-then-lightens a dark-unfriendly stroke to clear 3:1.
 *
 * D-170 (gantt-gridlines-drawn-over-bars): the gantt grid group can paint after
 *   the task bars, slicing through them. The fix moves the grid behind the bars;
 *   a secondary pass recolours the under-contrast crit label to black.
 *
 * DIRECTION: each "fixed" assertion is paired with a check that the pre-fix state
 * genuinely needs the repair, so the test fails against the unpatched pipeline.
 * Both-theme: the D-161 dark repair is paired with a light no-op assertion.
 */

import {
  buildTimelineDarkThemeVariables,
  TIMELINE_DARK_SECTION_FILLS,
  TIMELINE_DARK_SECTION_LABEL,
  parseLinkStyleStrokes,
  reapplyLinkStyleStrokes,
  moveGanttGridBehind,
  recolorGanttCritLabels,
} from '../mermaidEnhancer';
import { contrastRatio } from '../chartTheme';

const SVGNS = 'http://www.w3.org/2000/svg';
function svgEl(tag: string, cls?: string): SVGElement {
  const e = document.createElementNS(SVGNS, tag) as SVGElement;
  if (cls) e.setAttribute('class', cls);
  return e;
}
function strokeOf(el: Element): string {
  return (el as SVGElement).style.getPropertyValue('stroke');
}

// ── D-160: timeline dark palette ─────────────────────────────────────────────
describe('D-160 timeline dark palette', () => {
  const vars = buildTimelineDarkThemeVariables();
  const DARK_PAGE = '#2e3440';

  it('defines cScaleN + cScaleLabelN for every index mermaid may reference (0..12)', () => {
    for (let i = 0; i <= 12; i++) {
      expect(typeof vars[`cScale${i}`]).toBe('string');
      expect(typeof vars[`cScaleLabel${i}`]).toBe('string');
    }
  });

  it('every section is internally consistent: dark label on a light fill, >= 4.5:1', () => {
    for (let i = 0; i <= 12; i++) {
      const fill = vars[`cScale${i}`];
      const label = vars[`cScaleLabel${i}`];
      // label sits ON the fill -> WCAG text floor
      expect(contrastRatio(label, fill)).toBeGreaterThanOrEqual(4.5);
      // the band boundary against the dark page -> graphical floor
      expect(contrastRatio(fill, DARK_PAGE)).toBeGreaterThanOrEqual(3);
    }
  });

  it('DIRECTION: labels are the dark ink, not the light fill mermaid would default to', () => {
    // Mermaid's dark theme sets cScaleLabel0 = cScale1 (a LIGHT fill) -> label
    // near-invisible on the light band. Our label is the dark ink instead.
    expect(vars.cScaleLabel0).toBe(TIMELINE_DARK_SECTION_LABEL);
    // ...and it is genuinely dark relative to its own (light) fill.
    expect(contrastRatio(vars.cScaleLabel0, vars.cScale0)).toBeGreaterThan(8);
    // the fills really are light (the failing mode was a light fill), so a light
    // label WOULD have failed:
    expect(contrastRatio('#eceff4', vars.cScale0)).toBeLessThan(3);
  });

  it('cycles the palette so >8 sections never fall back to the inconsistent default', () => {
    expect(vars.cScale8).toBe(TIMELINE_DARK_SECTION_FILLS[0]);
    expect(vars.cScale12).toBe(TIMELINE_DARK_SECTION_FILLS[12 % TIMELINE_DARK_SECTION_FILLS.length]);
  });
});

// ── D-161: linkStyle stroke parse + dark re-apply ────────────────────────────
describe('D-161 linkStyle parse', () => {
  it('captures indexed and default stroke overrides', () => {
    const def = [
      'flowchart TD',
      '  A --> B',
      '  A --> C',
      '  linkStyle 0 stroke:#ff8800,stroke-width:4px',
      '  linkStyle 1 stroke:#aa0000',
      '  linkStyle default stroke:#00f;',
    ].join('\n');
    const parsed = parseLinkStyleStrokes(def);
    expect(parsed).toEqual([
      { indices: [0], stroke: '#ff8800' },
      { indices: [1], stroke: '#aa0000' },
      { indices: 'default', stroke: '#00f' },
    ]);
  });

  it('captures multiple indices in one directive', () => {
    const parsed = parseLinkStyleStrokes('linkStyle 0,2 stroke:#f00');
    expect(parsed).toEqual([{ indices: [0, 2], stroke: '#f00' }]);
  });
});

describe('D-161 dark re-apply (both themes)', () => {
  function buildFlowSvg(): { svg: SVGElement; edges: SVGElement[] } {
    const svg = svgEl('svg');
    const edgePaths = svgEl('g', 'edgePaths');
    svg.appendChild(edgePaths);
    const edges = [0, 1, 2].map(() => {
      const p = svgEl('path', 'flowchart-link');
      edgePaths.appendChild(p);
      return p;
    });
    return { svg, edges };
  }
  const def = [
    'flowchart TD',
    '  A --> B',
    '  A --> C',
    '  A --> D',
    '  linkStyle 0 stroke:#ff8800',
    '  linkStyle 1 stroke:#aa0000',
  ].join('\n');

  it('DARK: restores dropped strokes; honours a good colour, lightens a dark-unfriendly one', () => {
    const { svg, edges } = buildFlowSvg();
    // Simulate the dark visibility pass having repainted every edge with lineColor.
    edges.forEach(e => e.style.setProperty('stroke', '#88c0d0', 'important'));
    // DIRECTION: pre-fix the user's colours are gone.
    expect(strokeOf(edges[0])).toBe('#88c0d0');
    expect(strokeOf(edges[1])).toBe('#88c0d0');

    const n = reapplyLinkStyleStrokes(svg, def, true);
    expect(n).toBe(2);

    // #ff8800 already clears 3:1 on #1e1e1e -> honoured verbatim.
    expect(strokeOf(edges[0]).toLowerCase()).toBe('#ff8800');
    // #aa0000 is 2.15:1 on #1e1e1e -> honour-then-lighten, identity kept-ish.
    expect(strokeOf(edges[1]).toLowerCase()).not.toBe('#aa0000');
    expect(strokeOf(edges[1]).toLowerCase()).not.toBe('#88c0d0');
    expect(contrastRatio(strokeOf(edges[1]), '#1e1e1e')).toBeGreaterThanOrEqual(3);
    // untargeted edge left as-is.
    expect(strokeOf(edges[2])).toBe('#88c0d0');
  });

  it('LIGHT: no-op — mermaid already honours the strokes, so nothing is touched', () => {
    const { svg, edges } = buildFlowSvg();
    edges.forEach(e => e.style.setProperty('stroke', '#ff8800', 'important'));
    const n = reapplyLinkStyleStrokes(svg, def, false);
    expect(n).toBe(0);
    expect(strokeOf(edges[0])).toBe('#ff8800');
    expect(strokeOf(edges[1])).toBe('#ff8800');
  });
});

// ── D-170: gantt grid z-order + crit label ───────────────────────────────────
describe('D-170 gantt grid z-order', () => {
  function buildGantt(order: string[]): { root: SVGElement; nodes: Record<string, SVGElement> } {
    const svg = svgEl('svg');
    const root = svgEl('g');
    svg.appendChild(root);
    const nodes: Record<string, SVGElement> = {};
    for (const kind of order) {
      if (kind === 'grid') {
        nodes.grid = svgEl('g', 'grid');
        root.appendChild(nodes.grid);
      } else if (kind === 'task') {
        nodes.task = svgEl('rect', 'task');
        root.appendChild(nodes.task);
      } else if (kind === 'section') {
        nodes.section = svgEl('rect', 'section section0');
        root.appendChild(nodes.section);
      }
    }
    return { root, nodes };
  }

  it('moves the grid behind the bars when it is painted on top', () => {
    const { root, nodes } = buildGantt(['section', 'task', 'grid']);
    const kids0 = Array.from(root.childNodes);
    // DIRECTION: grid starts AFTER the task bar (would slice it).
    expect(kids0.indexOf(nodes.grid)).toBeGreaterThan(kids0.indexOf(nodes.task));

    expect(moveGanttGridBehind(root)).toBe(true);

    const kids1 = Array.from(root.childNodes);
    expect(kids1.indexOf(nodes.grid)).toBeLessThan(kids1.indexOf(nodes.task));
  });

  it('is a no-op when the grid is already behind the bars', () => {
    const { root } = buildGantt(['grid', 'section', 'task']);
    expect(moveGanttGridBehind(root)).toBe(false);
  });

  it('recolours only crit-task labels to black', () => {
    const svg = svgEl('svg');
    const crit = svgEl('text', 'taskText crit');
    const normal = svgEl('text', 'taskText');
    svg.appendChild(crit);
    svg.appendChild(normal);
    const n = recolorGanttCritLabels(svg);
    expect(n).toBe(1);
    expect((crit as SVGElement).style.getPropertyValue('fill')).toBe('#000000');
    expect((normal as SVGElement).style.getPropertyValue('fill')).toBe('');
    // DIRECTION: black on the pure-red crit fill is 5.25:1 (>=4.5); white was 4.00.
    expect(contrastRatio('#000000', '#ff0000')).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio('#ffffff', '#ff0000')).toBeLessThan(4.5);
  });
});
