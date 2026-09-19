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
 * REGRESSION (D-156 reopened): the empty-fill guard is necessary but not
 * sufficient. An ER cardinality marker is a STROKED outline glyph whose <path>
 * can carry a genuine SOLID fill (theme/CSS-derived, not none/transparent/
 * empty) left by mermaid or an earlier post-render pass. The dark pass then
 * flooded that fill with the teal line colour, re-creating the occluding blob.
 * The stroke-aware resolver resolveMermaidMarkerPaint() keeps every stroked or
 * hollow marker hollow and floods the fill ONLY for a fill-only solid arrowhead
 * (flowchart pointEnd). Both the helper and the inline dark guard are pinned to
 * that contract below; the stroked-outline case FAILS pre-fix (the old guard
 * flooded it) and passes post-fix.
 */
import * as fs from 'fs';
import * as path from 'path';
import { resolveMermaidMarkerColors, resolveMermaidMarkerPaint } from '../mermaidPlugin';

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

  describe('stroke-aware resolver keeps stroked outline markers hollow (D-156 regression)', () => {
    it('a STROKED outline glyph with a SOLID fill stays hollow (no teal blob)', () => {
      // ER crow's-foot / cardinality marker: it has a stroke and mermaid left a
      // solid fill on the path. It must NOT be flooded — fill resolves to null.
      expect(resolveMermaidMarkerPaint('#2e3440', LINE, LINE).fill).toBeNull();
      expect(resolveMermaidMarkerPaint('#000000', '#333333', LINE).fill).toBeNull();
      // stroke is always the theme line colour so the outline stays visible
      expect(resolveMermaidMarkerPaint('#2e3440', LINE, LINE).stroke).toBe(LINE);
    });

    it('a FILL-ONLY solid arrowhead (no stroke) is still recoloured', () => {
      expect(resolveMermaidMarkerPaint('#000000', 'none', LINE).fill).toBe(LINE);
      expect(resolveMermaidMarkerPaint('#000000', '', LINE).fill).toBe(LINE);
      expect(resolveMermaidMarkerPaint('#000000', null, LINE).fill).toBe(LINE);
    });

    it('a genuinely hollow marker stays hollow regardless of stroke', () => {
      expect(resolveMermaidMarkerPaint('none', LINE, LINE).fill).toBeNull();
      expect(resolveMermaidMarkerPaint('', LINE, LINE).fill).toBeNull();
      expect(resolveMermaidMarkerPaint('transparent', 'none', LINE).fill).toBeNull();
    });

    it('inline dark guard neutralises a stroked outline to fill:none', () => {
      const anchor = src.indexOf("querySelectorAll('defs marker path')");
      const region = src.slice(anchor, anchor + 3500);
      // The inline block must compute stroke presence and set fill:none for a
      // stroked outline instead of flooding it with the line colour.
      expect(region).toContain('strokedOutline');
      expect(region).toContain("el.style.setProperty('fill', 'none', 'important')");
    });
  });
});
