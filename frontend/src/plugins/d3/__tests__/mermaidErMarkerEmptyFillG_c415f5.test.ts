/**
 * D-156 / G-c415f5 (mermaid-w1-06, dark): an ER crow's-foot / cardinality
 * marker is drawn hollow. When its hollowness comes from a CSS rule rather than
 * an inline `fill="none"`, the marker `<path>` has NO inline fill, so the dark
 * post-render pass reads `curFill === ''`.
 *
 * The exported predicate resolveMermaidMarkerColors() correctly treats an
 * empty/absent fill as hollow. The RUNTIME code, however, lives inline in the
 * injected applyMermaidTheme() template string, and previously guarded only
 * `curFill !== 'none' && curFill !== 'transparent'` — so an empty-fill ER marker
 * was painted a solid theme-line blob that occluded the entity border in dark
 * mode (light does not run this blob-first override, so light was unaffected).
 *
 * This test pins the inline guard to the same contract as the helper: it (1)
 * re-confirms the helper treats '' as hollow, and (2) locates the inline
 * `defs marker path` fill guard in the plugin source and asserts it excludes the
 * empty-fill case. Without the `curFill !== ''` term the source lacks that
 * exclusion, so this test FAILS pre-fix and passes post-fix.
 */
import * as fs from 'fs';
import * as path from 'path';
import { resolveMermaidMarkerColors } from '../mermaidPlugin';

const LINE = '#88c0d0'; // dark-theme lineColor

describe('inline dark marker guard treats empty fill as hollow (D-156)', () => {
  const src = fs.readFileSync(
    path.resolve(__dirname, '..', 'mermaidPlugin.ts'),
    'utf8',
  );

  it('helper treats every hollow form (none/transparent/empty) as hollow', () => {
    expect(resolveMermaidMarkerColors('none', LINE).fill).toBeNull();
    expect(resolveMermaidMarkerColors('transparent', LINE).fill).toBeNull();
    expect(resolveMermaidMarkerColors('', LINE).fill).toBeNull();
    // a genuinely filled arrowhead is still recoloured
    expect(resolveMermaidMarkerColors('#000000', LINE).fill).toBe(LINE);
  });

  it('inline `defs marker path` fill guard excludes the empty-fill case', () => {
    const anchor = src.indexOf("querySelectorAll('defs marker path')");
    expect(anchor).toBeGreaterThan(-1);
    const region = src.slice(anchor, anchor + 2500);

    // The guard must recolour fill only for genuinely filled markers, i.e. it
    // must reject 'none', 'transparent' AND '' (empty/absent).
    expect(region).toContain("curFill !== 'none'");
    expect(region).toContain("curFill !== 'transparent'");
    expect(region).toContain("curFill !== ''");
  });
});
