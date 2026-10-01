/**
 * The /print page must fit figures and tables against the printable box of
 * THIS export's margins, not a constant baked for the transcript export.
 *
 * Observed defect (document PDF export, 2026-09-30): `PrintRenderPage` used
 * PRINT_CONTENT_WIDTH_PX = 718 (A4 minus 10mm sides, the transcript default)
 * while `export_document_pdf` paginates with the front-matter margins (19mm
 * sides by default → ~650px). A document figure between ~650 and ~718px wide
 * passed the oversize check untouched and was then squeezed by the
 * `max-width: 100%` stylesheet rule instead of being fitted deliberately, so
 * its height reservation and flow shrink were computed for the wrong width.
 *
 * DIRECTION: `resolvePrintContentBox` does not exist before the fix, so this
 * file fails to compile on the unpatched tree. The seam tests additionally
 * fail if the helper exists but the page still measures against constants,
 * or if the exporter never puts the box on the payload.
 */
import * as fs from 'fs';
import * as path from 'path';
import { DEFAULT_PRINT_CONTENT_BOX, resolvePrintContentBox } from '../printContentBox';

// A4 (210x297mm) at 96dpi minus the DOCUMENT default margins
// (top 16 / bottom 18 / left 19 / right 19 mm), as page_content_box_px yields.
const MM = 96 / 25.4;
const DOC_W = Math.round((210 - 19 - 19) * MM); // 650
const DOC_H = Math.round((297 - 16 - 18) * MM); // 994

describe('DEFAULT_PRINT_CONTENT_BOX — the transcript export geometry', () => {
  it('is A4 minus capture_pdf defaults (12/16/10/10mm): 190mm x 269mm', () => {
    // 190mm = 718.1px, 269mm = 1016.7px — the same rounding page_content_box_px applies.
    expect(Math.round(DEFAULT_PRINT_CONTENT_BOX.widthPx)).toBe(718);
    expect(Math.round(DEFAULT_PRINT_CONTENT_BOX.heightPx)).toBe(1017);
  });
});

describe('resolvePrintContentBox', () => {
  it('uses the document box from the payload (the reported case: 650 wide, not 718)', () => {
    const box = resolvePrintContentBox({ contentWidthPx: DOC_W, contentHeightPx: DOC_H });
    expect(box).toEqual({ widthPx: DOC_W, heightPx: DOC_H });
    expect(box.widthPx).toBeLessThan(DEFAULT_PRINT_CONTENT_BOX.widthPx - 60);
  });

  it('falls back to the transcript defaults when the payload has no page block', () => {
    expect(resolvePrintContentBox(undefined)).toEqual({
      widthPx: DEFAULT_PRINT_CONTENT_BOX.widthPx,
      heightPx: DEFAULT_PRINT_CONTENT_BOX.heightPx,
    });
    expect(resolvePrintContentBox(null)).toEqual(resolvePrintContentBox(undefined));
    expect(resolvePrintContentBox({})).toEqual(resolvePrintContentBox(undefined));
  });

  it('falls back PER SIDE for unusable values and never throws on garbage', () => {
    expect(resolvePrintContentBox({ contentWidthPx: DOC_W })).toEqual({
      widthPx: DOC_W, heightPx: DEFAULT_PRINT_CONTENT_BOX.heightPx,
    });
    for (const bad of [0, -1, NaN, Infinity, '650' as any, null as any]) {
      const box = resolvePrintContentBox({ contentWidthPx: bad, contentHeightPx: DOC_H });
      expect(box.widthPx).toBe(DEFAULT_PRINT_CONTENT_BOX.widthPx);
      expect(box.heightPx).toBe(DOC_H);
    }
  });
});

describe('seam — PrintRenderPage measures against the resolved box, not constants', () => {
  const src = fs.readFileSync(
    path.join(__dirname, '..', '..', 'components', 'PrintRenderPage.tsx'), 'utf8',
  );

  it('imports the resolver and declares page on the payload type', () => {
    expect(src).toMatch(/resolvePrintContentBox[^\n]*from '\.\.\/utils\/printContentBox'/);
    expect(src).toMatch(/page\?: PrintPageSpec/);
  });

  it('no longer carries the 10mm constants', () => {
    expect(src).not.toContain('PRINT_CONTENT_WIDTH_PX');
    expect(src).not.toContain('PRINT_CONTENT_HEIGHT_PX');
  });

  it('resolves the box from spec.page and hands it to BOTH fit passes', () => {
    expect(src).toContain('resolvePrintContentBox(spec?.page)');
    expect(src).toMatch(/function fitOversizedFigures\(root: HTMLElement, box: PrintContentBox\)/);
    expect(src).toMatch(/function fitOverwideTables\(root: HTMLElement, box: PrintContentBox\)/);
    // Both call sites pass the same resolved box.
    expect(src).toMatch(/fitOversizedFigures\(node, contentBox\)/);
    expect(src).toMatch(/fitOverwideTables\(node, contentBox\)/);
  });
});

describe('seam — the document exporter puts the box on the payload', () => {
  const src = fs.readFileSync(
    path.join(__dirname, '..', '..', '..', '..', 'app', 'services', 'pdf_exporter.py'), 'utf8',
  );

  it('build_document_print_payload emits page.contentWidthPx / contentHeightPx from page_box', () => {
    expect(src).toContain('"contentWidthPx": int(page_box[0])');
    expect(src).toContain('"contentHeightPx": int(page_box[1])');
  });

  it('export_document_pdf passes the same box it uses for the viewport', () => {
    expect(src).toContain('page_box=(content_w, content_h)');
    expect(src).toContain('viewport=(content_w, content_h)');
  });
});
