/**
 * The printable content box the /print page fits figures and tables into.
 *
 * Chromium paginates `page.pdf()` at the PAPER size minus the margins the
 * exporter passes, so "does this figure fit the page?" is only answerable
 * against that box. The two export paths use different margins:
 *
 *  - Conversation transcripts: `capture_pdf`'s defaults (A4, 12/16/10/10mm),
 *    which is what `DEFAULT_PRINT_CONTENT_BOX` encodes.
 *  - Authored documents: the front-matter `page.margin` (19mm sides by
 *    default), which yields a text column ~70px NARROWER than the transcript
 *    one. The exporter already sizes the viewport to this box and now sends
 *    it in the payload as `page.contentWidthPx` / `page.contentHeightPx`.
 *
 * Before the payload carried the box the page measured every export against
 * the transcript constants, so a document figure between ~650 and ~718px wide
 * passed the oversize check untouched and was then squeezed by the
 * `max-width: 100%` stylesheet rule instead of being fitted deliberately.
 */

export interface PrintContentBox {
    widthPx: number;
    heightPx: number;
}

/** Shape of the optional `page` block on the /print payload. */
export interface PrintPageSpec {
    contentWidthPx?: number;
    contentHeightPx?: number;
}

const MM_TO_PX = 96 / 25.4;

/**
 * Transcript export box: A4 (210x297mm) minus `capture_pdf`'s default margins
 * (top 12mm / bottom 16mm / left 10mm / right 10mm), in CSS px at 96dpi.
 *   width  = 190mm ~= 718px,  height = 269mm ~= 1016px
 */
export const DEFAULT_PRINT_CONTENT_BOX: Readonly<PrintContentBox> = Object.freeze({
    widthPx: (210 - 10 - 10) * MM_TO_PX,
    heightPx: (297 - 12 - 16) * MM_TO_PX,
});

const usable = (v: unknown): v is number =>
    typeof v === 'number' && Number.isFinite(v) && v > 0;

/**
 * Resolve the box from a payload's `page` block, per side, falling back to
 * the transcript defaults for anything missing or unusable. Never throws on
 * a malformed block; a transcript payload (no `page`) yields the defaults.
 */
export function resolvePrintContentBox(page: PrintPageSpec | null | undefined): PrintContentBox {
    return {
        widthPx: usable(page?.contentWidthPx)
            ? page!.contentWidthPx!
            : DEFAULT_PRINT_CONTENT_BOX.widthPx,
        heightPx: usable(page?.contentHeightPx)
            ? page!.contentHeightPx!
            : DEFAULT_PRINT_CONTENT_BOX.heightPx,
    };
}
