/**
 * Preprocessor for Plotly specs.
 *
 * LLMs routinely emit technically-valid Plotly specs that render poorly:
 * titles colliding with plot area, colorbars positioned outside paper
 * bounds, annotations overlapping subtitles, scene domains too close
 * together, etc.  Rather than instructing the model to avoid these
 * quirks (an unbounded game), we normalize the spec here.
 *
 * Each fix is conservative: it only activates when the quirk is present
 * AND the user hasn't made an explicit choice that would conflict.
 * Specs that were already well-constructed pass through unchanged.
 *
 * Exported individually for unit testing; composed into
 * `preprocessPlotlySpec` which is what the plugin calls.
 */

type PlotlySpec = {
  data?: any[];
  layout?: any;
  config?: any;
  [k: string]: any;
};

/**
 * Offset two-line titles so they don't collide with the plot area.
 *
 * Plotly's default title.y places the title at the very top of the paper,
 * and when `title.text` contains a `<br>` or `<sub>` tag the second line
 * pushes down into the plot. If the user hasn't set title.y explicitly,
 * nudge it down slightly and ensure margin.t is large enough to fit.
 */
export function fixMultilineTitle(layout: any): any {
  if (!layout?.title) return layout;
  const title = typeof layout.title === 'string' ? { text: layout.title } : layout.title;
  const text: string = title.text || '';
  const hasMultiline = text.includes('<br>') || text.includes('<sub>') || text.includes('\n');
  if (!hasMultiline) return layout;

  const newTitle = { ...title };
  if (newTitle.y === undefined) newTitle.y = 0.97;

  const newLayout = { ...layout, title: newTitle };
  const currentTop = layout.margin?.t;
  if (currentTop === undefined || currentTop < 80) {
    newLayout.margin = { ...(layout.margin || {}), t: 100 };
  }
  return newLayout;
}

/**
 * Clamp colorbars positioned beyond the paper boundary.
 *
 * Plotly colorbar.x is in paper coordinates; values > 1 place the bar
 * outside the visible area and it gets clipped. LLMs often emit x=1.15
 * or similar when trying to place multiple colorbars side-by-side.
 * Pull anything > 1.02 back to 0.99 and set xanchor=left so it sits
 * just inside the right edge.
 */
export function clampColorbars(data: any[]): any[] {
  if (!Array.isArray(data)) return data;
  return data.map(trace => {
    if (!trace?.marker?.colorbar && !trace?.colorbar) return trace;
    const patchBar = (bar: any): any => {
      if (!bar) return bar;
      const out = { ...bar };
      if (typeof out.x === 'number' && out.x > 1.02) {
        out.x = 0.99;
        if (out.xanchor === undefined) out.xanchor = 'left';
      }
      if (typeof out.y === 'number' && (out.y > 1.02 || out.y < -0.02)) {
        out.y = Math.max(0.05, Math.min(0.95, out.y));
      }
      return out;
    };
    const newTrace = { ...trace };
    if (newTrace.marker?.colorbar) {
      newTrace.marker = { ...newTrace.marker, colorbar: patchBar(newTrace.marker.colorbar) };
    }
    if (newTrace.colorbar) {
      newTrace.colorbar = patchBar(newTrace.colorbar);
    }
    return newTrace;
  });
}

/**
 * Shrink scene domains that consume the full vertical space when a
 * multi-line title is present.
 *
 * Scenes with `domain.y: [0, 1]` collide with titles at y=0.97.
 * When a multi-line title exists, cap scene.domain.y[1] at 0.88 to
 * leave room. Only applies when the user hasn't set a smaller upper
 * bound already.
 */
export function adjustSceneDomainsForTitle(layout: any): any {
  if (!layout?.title) return layout;
  const titleText = typeof layout.title === 'string' ? layout.title : layout.title?.text || '';
  const hasMultiline = titleText.includes('<br>') || titleText.includes('<sub>');
  if (!hasMultiline) return layout;

  const newLayout = { ...layout };
  for (const key of Object.keys(layout)) {
    if (!key.startsWith('scene')) continue;
    const scene = layout[key];
    if (!scene?.domain?.y) continue;
    const [low, high] = scene.domain.y;
    if (high > 0.9) {
      newLayout[key] = {
        ...scene,
        domain: { ...scene.domain, y: [low, Math.min(high, 0.88)] },
      };
    }
  }
  return newLayout;
}

/**
 * Enforce minimum gap between horizontally-adjacent scene domains.
 *
 * Scenes with `scene.domain.x: [0, 0.48]` and `scene2.domain.x: [0.52, 1]`
 * leave only 4% gap, and axis labels visually merge. If two scenes have
 * touching or near-touching x-domains, widen the gap to ≥ 6%.
 */
export function ensureSceneDomainGaps(layout: any): any {
  if (!layout) return layout;
  const scenes = Object.keys(layout).filter(k => k.startsWith('scene') && layout[k]?.domain?.x);
  if (scenes.length < 2) return layout;

  const sorted = scenes
    .map(k => ({ key: k, x: layout[k].domain.x as [number, number] }))
    .sort((a, b) => a.x[0] - b.x[0]);

  const newLayout = { ...layout };
  for (let i = 0; i < sorted.length - 1; i++) {
    const a = sorted[i];
    const b = sorted[i + 1];
    const gap = b.x[0] - a.x[1];
    if (gap < 0.06) {
      const shift = (0.06 - gap) / 2;
      const newA = Math.max(0, a.x[1] - shift);
      const newB = Math.min(1, b.x[0] + shift);
      newLayout[a.key] = { ...layout[a.key], domain: { ...layout[a.key].domain, x: [a.x[0], newA] } };
      newLayout[b.key] = { ...layout[b.key], domain: { ...layout[b.key].domain, x: [newB, b.x[1]] } };
    }
  }
  return newLayout;
}

/**
 * Adjust paper-referenced annotations that sit in the title zone.
 *
 * When a multi-line title is present and annotations use yref='paper'
 * with y > 0.92, they overlap the subtitle. Pull them down to 0.89
 * only in that specific case.
 */
export function adjustAnnotationsForTitle(layout: any): any {
  if (!layout?.annotations || !Array.isArray(layout.annotations)) return layout;
  const titleText = typeof layout.title === 'string' ? layout.title : layout.title?.text || '';
  const hasMultiline = titleText.includes('<br>') || titleText.includes('<sub>');
  if (!hasMultiline) return layout;

  const newAnnotations = layout.annotations.map((ann: any) => {
    if (ann?.yref === 'paper' && typeof ann.y === 'number' && ann.y > 0.92) {
      return { ...ann, y: 0.89 };
    }
    return ann;
  });
  return { ...layout, annotations: newAnnotations };
}

/** Compose all preprocessors. Order matters: title fix first so subsequent
 *  passes see the adjusted title state. */
export function preprocessPlotlySpec(spec: PlotlySpec): PlotlySpec {
  if (!spec || typeof spec !== 'object') return spec;
  let layout = spec.layout;
  layout = fixMultilineTitle(layout);
  layout = adjustSceneDomainsForTitle(layout);
  layout = ensureSceneDomainGaps(layout);
  layout = adjustAnnotationsForTitle(layout);
  const data = clampColorbars(spec.data || []);
  return { ...spec, data, layout };
}
