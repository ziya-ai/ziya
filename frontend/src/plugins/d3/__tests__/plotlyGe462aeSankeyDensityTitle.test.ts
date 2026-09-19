/**
 * G-e462ae — two structural plotly defects, one file (plotlyPreprocessor.ts):
 *
 *  D-461 (sankey-node-labels-collide): the dense-sankey capture-height grow
 *  already existed but was TOO SMALL. The sweep measured label overprint
 *  beginning ~16 nodes/column at ~680px (~42px per node), yet at 26px/node a
 *  19-node column (plotly-w2-07) grew to only 19*26+120=614px — below the onset
 *  — so labels still smeared. The fix raises PLOTLY_SANKEY_NODE_ROW_PX so the
 *  grown height clears the empirical onset.
 *
 *  D-462 (wrapped-yaxis-title-clipped-at-top-edge): a rotated y-axis title's
 *  wrapped-line CHARACTER LENGTH maps to the plot HEIGHT, not its width, so the
 *  shared 60-char budget produced a ~58-char (~460px) line taller than the plot
 *  area and its top clipped. The fix gives rotated y-titles a shorter per-line
 *  budget (40) and a larger line cap (6, absorbed horizontally by automargin).
 *
 * Both are pure geometry / theme-independent; the both-theme obligation is
 * discharged at the shared render stage. Each block carries an explicit
 * DIRECTION check: the assertion describes a value the pre-fix tree could not
 * produce (26px/node -> <42px room; y-title wrapped at 60 -> a >40-char line).
 */
import {
  sankeyAwareRenderHeightPx,
  wrapLongTitles,
  PLOTLY_SANKEY_NODE_ROW_PX,
  PLOTLY_YAXIS_TITLE_WRAP_MAXCHARS,
  PLOTLY_YAXIS_TITLE_WRAP_MAXLINES,
  PLOTLY_TITLE_WRAP_MAXCHARS,
} from '../plotlyPreprocessor';

/** A layered sankey: `stages` columns of `perCol` nodes, fully bipartite between
 *  adjacent columns (busiest column == perCol). */
function layeredSankey(stages: number, perCol: number): any {
  const label: string[] = [];
  for (let s = 0; s < stages; s++)
    for (let i = 0; i < perCol; i++) label.push(`stage${s}/node${i}`);
  const source: number[] = [];
  const target: number[] = [];
  for (let s = 0; s < stages - 1; s++)
    for (let a = 0; a < perCol; a++)
      for (let b = 0; b < perCol; b++) {
        source.push(s * perCol + a);
        target.push((s + 1) * perCol + b);
      }
  return { type: 'sankey', node: { label, pad: 3, thickness: 8 }, link: { source, target } };
}

describe('D-461 dense-sankey grow clears the empirical collision onset', () => {
  // The sweep's measured onset: ~16 nodes/column overprint at ~680px, i.e. the
  // grown height must give at least ~42px of vertical room per node.
  const ONSET_PX_PER_NODE = 42;

  it('DIRECTION: a 19-node column (plotly-w2-07) is grown past the onset', () => {
    const px = sankeyAwareRenderHeightPx([layeredSankey(6, 19)]) as number;
    expect(px).not.toBeNull();
    const roomPerNode = (px - 120) / 19;
    // 26px/node gave 26 room/node (614px total) -> BELOW onset -> pre-fix FAIL.
    expect(roomPerNode).toBeGreaterThanOrEqual(ONSET_PX_PER_NODE);
    expect(px).toBeGreaterThan(680);
  });

  it('the per-node row budget itself clears the onset', () => {
    expect(PLOTLY_SANKEY_NODE_ROW_PX).toBeGreaterThanOrEqual(ONSET_PX_PER_NODE);
  });

  it('a 16-node column (the measured onset itself) is also grown past 680px', () => {
    const px = sankeyAwareRenderHeightPx([layeredSankey(3, 16)]) as number;
    expect(px).toBeGreaterThan(680);
  });
});

const LONG =
  'Aggregate p99 end-to-end request latency measured at the edge termination ' +
  'layer for authenticated traffic excluding health-check probes and synthetic canaries';

describe('D-462 rotated y-axis title wraps to a shorter per-line budget', () => {
  const linesOf = (t: any): string[] => String(t.text).split('<br>');

  it('DIRECTION: every y-title line fits the tighter rotated budget', () => {
    const out = wrapLongTitles({ yaxis: { title: { text: LONG } } });
    const lines = linesOf(out.yaxis.title);
    expect(lines.length).toBeGreaterThan(1); // it wrapped
    const longest = Math.max(...lines.map(l => l.length));
    // pre-fix: y wrapped at 60 -> a 58-char line (~460px rotated) that clipped.
    expect(longest).toBeLessThanOrEqual(PLOTLY_YAXIS_TITLE_WRAP_MAXCHARS);
    expect(PLOTLY_YAXIS_TITLE_WRAP_MAXCHARS).toBeLessThan(PLOTLY_TITLE_WRAP_MAXCHARS);
  });

  it('no y-title text is lost to ellipsis (larger line cap absorbs the extra lines)', () => {
    const out = wrapLongTitles({ yaxis: { title: { text: LONG } } });
    const lines = linesOf(out.yaxis.title);
    expect(lines.length).toBeLessThanOrEqual(PLOTLY_YAXIS_TITLE_WRAP_MAXLINES);
    expect(out.yaxis.title.text).not.toContain('\u2026'); // no ellipsis
    // reconstructing words from the wrapped lines recovers the original title
    expect(lines.join(' ').replace(/\s+/g, ' ').trim()).toBe(LONG);
  });

  it('the main and x-axis titles keep the wider horizontal budget (unchanged)', () => {
    const out = wrapLongTitles({
      title: { text: LONG },
      xaxis: { title: { text: LONG } },
    });
    const xLongest = Math.max(...linesOf(out.xaxis.title).map(l => l.length));
    // x/main map line-length to the wider plot WIDTH, so they still use 60 —
    // a line longer than the tight y budget confirms the branch differentiates.
    expect(xLongest).toBeGreaterThan(PLOTLY_YAXIS_TITLE_WRAP_MAXCHARS);
    expect(xLongest).toBeLessThanOrEqual(PLOTLY_TITLE_WRAP_MAXCHARS);
  });
});
