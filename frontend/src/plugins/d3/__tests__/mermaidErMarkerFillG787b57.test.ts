/**
 * D-293 / G-787b57 (mermaid-w1-06): ER crow's-foot / cardinality markers are
 * drawn HOLLOW (fill:none) and stroked. The dark-theme post-render recolour
 * pass used to force BOTH stroke and fill of every `defs marker path` to the
 * theme line colour, which turned a hollow cardinality marker into a solid teal
 * blob that occluded the entity border and hid the glyph.
 *
 * resolveMermaidMarkerColors() is the pure predicate that the injected
 * applyMermaidTheme() dark override inlines: it always recolours the stroke
 * (so the marker stays visible) but only recolours the FILL when the marker was
 * actually filled — a hollow marker keeps fill:none.
 *
 * Pre-fix behaviour was equivalent to always returning fill === lineColor, so
 * the `fill === null` assertions below FAIL without the change and pass with it.
 */
import { resolveMermaidMarkerColors } from '../mermaidPlugin';

const LINE = '#88c0d0'; // dark-theme lineColor

describe('resolveMermaidMarkerColors — preserve hollow ER markers (D-293/w1-06)', () => {
  it('keeps a hollow (fill:none) cardinality marker hollow', () => {
    const r = resolveMermaidMarkerColors('none', LINE);
    expect(r.fill).toBeNull();          // NOT painted solid → glyph reads through
    expect(r.stroke).toBe(LINE);        // outline still recoloured → stays visible
  });

  it('keeps a transparent-filled marker hollow', () => {
    expect(resolveMermaidMarkerColors('transparent', LINE).fill).toBeNull();
  });

  it('treats an absent/empty fill as hollow (do not fabricate a fill)', () => {
    expect(resolveMermaidMarkerColors('', LINE).fill).toBeNull();
    expect(resolveMermaidMarkerColors(null, LINE).fill).toBeNull();
    expect(resolveMermaidMarkerColors(undefined, LINE).fill).toBeNull();
  });

  it('recolours a genuinely filled arrowhead so it stays visible', () => {
    const r = resolveMermaidMarkerColors('#000000', LINE);
    expect(r.fill).toBe(LINE);
    expect(r.stroke).toBe(LINE);
  });

  it('is case-insensitive on the fill keyword', () => {
    expect(resolveMermaidMarkerColors('NONE', LINE).fill).toBeNull();
    expect(resolveMermaidMarkerColors('Transparent', LINE).fill).toBeNull();
  });
});
