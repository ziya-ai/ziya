/**
 * PrintRenderPage — Standalone page for headless WHOLE-CONVERSATION rendering.
 *
 * Mounted at `/print`.  This is the conversation-scale sibling of
 * `DiagramRenderPage` (`/render`): where that route renders a single diagram
 * through the real D3Renderer pipeline, this route renders an ENTIRE
 * conversation through the real `MarkdownRenderer` pipeline — the exact same
 * Prism / KaTeX / react-diff-view / D3 code the chat UI uses — so a headless
 * capture is pixel-faithful to what a user sees.
 *
 * SHARED INFRASTRUCTURE.  This route is consumed by:
 *   - `app/services/pdf_exporter.py`  → `capture_pdf()`   (Card I, PDF)
 *   - `app/services/pdf_exporter.py`  → `extract_html()`  (Card II, HTML)
 * so it MUST NOT bake in PDF-only / A4 / print-media assumptions.  It renders
 * a self-contained, light-themed DOM; the Python driver decides whether to
 * `page.pdf()` it or read its `outerHTML`.
 *
 * Payload channels (mirrors DiagramRenderPage):
 *   1. URL hash fragment (base64 JSON) — for small payloads / manual testing.
 *   2. `window.__renderConversation(jsonString)` — used by Playwright; the
 *      only viable channel for a long conversation, which blows past URL
 *      length limits.
 *
 * Payload shape:
 *   {
 *     title: string,
 *     messages: Array<{ role, content, ... }>,
 *     options: { roundLimit, includeHuman, includeCollapsed, includeFooter },
 *     footerHtml?: string,           // pre-rendered footer (matches other exports)
 *     renderTimeoutMs?: number,      // in-page safety timeout (default 60s)
 *   }
 *
 * Completion contract:
 *   Sets `data-render-status="complete"` on `#print-render-root` ONLY after
 *   every async renderer has settled — no diagram is still pending, no KaTeX
 *   node is unrendered, and all <img> have loaded (or errored).  Readiness is
 *   GENUINELY AWAITED (a MutationObserver quiescence gate + per-resource
 *   promises); the fixed `setTimeout` that makes the client-side export flaky
 *   is deliberately NOT the gate — a short debounce only confirms quiescence.
 */
import React, {
    useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
} from 'react';
import { useTheme } from '../context/ThemeContext';
import { ActiveChatProvider } from '../context/ActiveChatContext';
import { StreamingProvider } from '../context/StreamingContext';
import { lazyWithRetry } from '../utils/lazyWithRetry';
// Print-mode page-fragmentation + oversized-figure width rules (scoped to
// body.ziya-print-mode; see the file header for why not @media print).
import '../styles/print.css';
// Force any baked dark mermaid theme to light BEFORE the renderer runs, in this
// SHARED /print path, so PDF (capture_pdf) and HTML (extract_html) both get
// light diagrams (user defect #6 — dark diagram theme leaking onto the white
// page).  See utils/mermaidThemeNormalize for why CSS cannot fix a theme baked
// into the diagram's own <style>.
import { normalizeMermaidThemeToLight } from '../utils/mermaidThemeNormalize';

const MarkdownRenderer = lazyWithRetry(
    () => import('./MarkdownRenderer').then(m => ({ default: m.MarkdownRenderer }))
);

interface PrintMessage {
    role?: 'human' | 'assistant' | 'system' | string;
    content?: string;
    [k: string]: any;
}

interface PrintOptions {
    roundLimit?: number | null;
    includeHuman?: boolean;
    includeCollapsed?: boolean;
    includeFooter?: boolean;
}

interface ConversationSpec {
    /** 'conversation' (default) — transcript with per-message chrome;
     *  'document' — authored IR render: `sections` each on their own page,
     *  no message chrome (see app/utils/document_ir.py). */
    kind?: 'conversation' | 'document';
    title?: string;
    /** Document mode: front-matter author (report title block + PDF /Author). */
    author?: string;
    /** Document mode: 'report' (title block) | 'plain' (no chrome). */
    layout?: string;
    /** Document mode: pagebreak-split markdown bodies. */
    sections?: string[];
    messages?: PrintMessage[];
    options?: PrintOptions;
    footerHtml?: string;
    renderTimeoutMs?: number;
}

type RenderStatus = 'idle' | 'loading' | 'rendering' | 'complete' | 'error';

const DEFAULT_OPTIONS: Required<PrintOptions> = {
    roundLimit: null,
    includeHuman: true,
    includeCollapsed: true,
    includeFooter: true,
};

/**
 * Live-session UI-chrome patterns that are meaningful in the running app but
 * are noise in an exported document.  The "Auto-added N file(s) to context …
 * Remove via the A button in the Files panel." banner (MarkdownRenderer's
 * contextEnhancementOverlay) and comparable "checking context" spinners are
 * live affordances only.  They normally render from component state that never
 * fires under /print, but a banner that was PERSISTED into a message's content
 * would otherwise survive into the PDF/HTML — so we strip the whole CLASS of
 * chrome here in the shared option-filter path rather than string-matching one
 * banner, so a future affordance cannot silently reappear in exports.
 *
 * Each entry matches a full line (leading whitespace tolerated).  Kept as a
 * line-level strip so it removes the affordance without disturbing adjacent
 * real answer prose.
 */
const LIVE_SESSION_CHROME_PATTERNS: RegExp[] = [
    // "Auto-added N file(s) to context (…) — available for subsequent queries.
    //  Remove via the A button in the Files panel."  (may wrap across lines)
    /^[ \t>*-]*Auto-added\b[\s\S]*?Files panel\.?\s*$/gim,
    // Defensive: a standalone tail if the banner was split across paragraphs.
    /^[ \t>*-]*Remove via the A button in the Files panel\.?\s*$/gim,
    // "⚠️ This diff references files not in context: …" enhancement warning.
    /^[ \t>*-]*(?:⚠️\s*)?This diff references files not in context:[^\n]*$/gim,
    // "🔄 Checking context..." spinner text.
    /^[ \t>*-]*🔄?\s*Checking context\.\.\.\s*$/gim,
];

function stripLiveSessionChrome(content: string): string {
    if (!content) return content;
    let out = content;
    for (const re of LIVE_SESSION_CHROME_PATTERNS) {
        out = out.replace(re, '');
    }
    // Collapse the blank lines a removed banner may leave behind so it does not
    // become a whitespace band (feeds NEW-3 / defect #5 territory).
    out = out.replace(/\n{3,}/g, '\n\n');
    return out;
}

/**
 * Apply scope & content filters — the SAME semantics as the frontend
 * ExportConversationModal, kept here so the server pipeline shares one source
 * of truth (PDF, HTML, CLI all go through this route).
 */
function applyOptions(messages: PrintMessage[], options: PrintOptions): PrintMessage[] {
    const opts = { ...DEFAULT_OPTIONS, ...options };
    let msgs = [...messages];

    if (opts.roundLimit !== null && opts.roundLimit !== undefined && opts.roundLimit > 0) {
        const humanIndices = msgs.reduce<number[]>((acc, m, i) => {
            if (m.role === 'human') acc.push(i);
            return acc;
        }, []);
        const startFrom = humanIndices[Math.max(0, humanIndices.length - opts.roundLimit)];
        if (startFrom !== undefined) msgs = msgs.slice(startFrom);
    }

    if (!opts.includeHuman) {
        msgs = msgs.filter(m => m.role !== 'human');
    }

    if (!opts.includeCollapsed) {
        msgs = msgs.map(m => ({
            ...m,
            content: m.content
                ? m.content
                    .replace(/<details[\s\S]*?<\/details>/gi, '')
                    .replace(/```thinking:step-\d+\n[\s\S]*?```/g, '')
                : m.content,
        }));
    }

    // Force any baked dark mermaid theme to light so diagrams composite onto the
    // white page instead of leaving dark bands (user defect #6).  Done here in
    // the SHARED option-filter path so PDF and HTML exports both inherit it.
    msgs = msgs.map(m => ({
        ...m,
        content: m.content ? normalizeMermaidThemeToLight(m.content) : m.content,
    }));

    // Strip live-session UI chrome (auto-added-context banner, "Remove via the
    // A button" affordance, context spinners) that is meaningless in an export
    // (user-reported NEW-2b).  Shared path so PDF and HTML exports both inherit.
    msgs = msgs.map(m => ({
        ...m,
        content: m.content ? stripLiveSessionChrome(m.content) : m.content,
    }));

    return msgs;
}

function parseSpecFromHash(): ConversationSpec | null {
    const hash = window.location.hash.slice(1);
    if (!hash) return null;
    try {
        return JSON.parse(decodeURIComponent(escape(atob(hash))));
    } catch (e) {
        try { return JSON.parse(atob(hash)); } catch { /* fallthrough */ }
        console.error('PrintRenderPage: Failed to parse spec from hash:', e);
        return null;
    }
}

/**
 * Printable A4 content box, in CSS px at 96dpi, matching the capture margins
 * in `app/services/pdf_exporter.py` -> `capture_pdf` (A4, top 12mm / bottom
 * 16mm / left 10mm / right 10mm).  A4 is 210x297mm; 1mm = 96/25.4 px.
 *   width  = (210 - 10 - 10)mm = 190mm ~= 718px
 *   height = (297 - 12 - 16)mm = 269mm ~= 1016px
 * These are the bounds an atomic figure must fit within to avoid forcing an
 * absurd page break (defect #7) or leaving an empty band (defect #5).  A small
 * safety factor absorbs sub-pixel rounding and the diagram's own padding.
 */
const MM_TO_PX = 96 / 25.4;
const PRINT_CONTENT_WIDTH_PX = (210 - 10 - 10) * MM_TO_PX;   // ~718
const PRINT_CONTENT_HEIGHT_PX = (297 - 12 - 16) * MM_TO_PX;  // ~1016
const FIT_SAFETY = 0.96;

/**
 * Uniformly shrink any diagram/figure that is taller or wider than one
 * printable page so it fits WHOLE on a single page.
 *
 * WHY JS (not CSS).  Mermaid writes `width`/`height` as INLINE `!important`
 * declarations on its <svg> (e.g. `height: 2364px !important`).  An inline
 * `!important` beats any author-stylesheet rule, even one that is also
 * `!important`, so `styles/print.css` cannot resize it — only setting the
 * inline property (also with priority `important`) can.  The <svg> carries a
 * `viewBox` + `preserveAspectRatio="xMidYMid meet"`, so scaling width and
 * height by the SAME factor rescales the drawing proportionally with no
 * distortion and no clipping.
 *
 * This does NOT hide or clip anything: an element that is intrinsically larger
 * than a page is made to fit; everything remains visible.  Idempotent — a
 * second call is a no-op because the element already fits.
 *
 * SHARED note: the width cap also benefits Card II's HTML export (a diagram
 * wider than the container would otherwise overflow horizontally); it is left
 * here in the shared /print readiness path rather than in a PDF-only step.
 */
function rasterizeCanvasesToImages(root: HTMLElement): void {
    const canvases = Array.from(root.querySelectorAll('canvas'));
    for (const canvas of canvases) {
        try {
            const rect = canvas.getBoundingClientRect();
            const cssW = rect.width || canvas.width || 0;
            const cssH = rect.height || canvas.height || 0;
            // Nothing drawn / not laid out — skip (no pixels to preserve).
            if ((canvas.width || 0) <= 0 || (canvas.height || 0) <= 0) continue;
            const dataUrl = canvas.toDataURL('image/png');
            if (!dataUrl || dataUrl === 'data:,') continue;
            const img = document.createElement('img');
            img.src = dataUrl;
            // Carry identity so author CSS keyed on the canvas still applies.
            if (canvas.id) img.id = canvas.id;
            if (canvas.className) img.className = canvas.className;
            img.setAttribute('data-print-rasterized-canvas', 'true');
            // Preserve the rendered box; cap width so the shared figure-fit rule
            // (fitOversizedFigures + print.css max-width:100%) still governs it.
            if (cssW > 0) img.style.width = `${Math.round(cssW)}px`;
            if (cssH > 0) img.style.height = `${Math.round(cssH)}px`;
            img.style.maxWidth = '100%';
            canvas.replaceWith(img);
        } catch (_e) {
            // Tainted / unreadable canvas: leave the original in place.
        }
    }
}

/**
 * Rasterize every live `<canvas>` in the render tree into an inline `<img>`
 * (PNG data-URL) so the pixels survive the headless capture.
 *
 * WHY THIS IS NEEDED (and SHARED).  A `<canvas>` is an imperative pixel surface,
 * not declarative DOM: its bitmap lives in the drawing context, not in the
 * serialized markup.  Two independent capture backends consume this shared
 * /print route and BOTH lose canvas pixels without this step:
 *   - `capture_pdf` → `page.pdf()`: a WebGL/2D canvas can rasterize BLANK in
 *     headless Chromium's print path if its backing store is not committed.
 *   - `extract_html` → `outerHTML` (Card II): a `<canvas>` serializes as an
 *     EMPTY `<canvas>` tag — its bitmap is gone from the standalone HTML file,
 *     so the figure vanishes when that HTML is later opened/printed.
 * Converting to `<img>` (a declarative element whose src carries the pixels)
 * fixes both.  This mirrors the retired client-side `pdfExport.ts`
 * (`convertCanvasElements`); doing it in the shared readiness path — BEFORE
 * `data-render-status="complete"`, so before either backend reads the DOM — is
 * the general seam both PDF and HTML exports reuse, not a PDF-only post-process.
 *
 * Faithful today, robust tomorrow.  Ziya's current renderers all emit inline
 * `<svg>` (mermaid/D3 via D3Renderer, VexFlow music via `Renderer.Backends.SVG`),
 * so a live conversation has no `<canvas>` to convert and this is a no-op.  It
 * is defensive infrastructure against any future renderer (or a user-authored
 * HTML block) that emits a `<canvas>`, which would otherwise silently blank on
 * BOTH export paths.  See `rasterizeCanvasesToImages` above for the mechanics.
 */
/* Flow-aware figure shrinking (user defect NEW-1).
 *
 * The floor for FLOW-driven shrinking.  The user ruled it acceptable to shrink
 * a figure to keep it near its introducing prose down to — but NEVER below —
 * 0.75x of its natural size.  This is distinct from the OVERSIZE path (a figure
 * intrinsically taller/wider than the page), which may legitimately scale below
 * 0.75x because otherwise it would not fit at all.  Both cases record
 * `data-print-fit-scale`; they are told apart by `data-print-fit-reason`
 * ('oversize' | 'flow'), so a check can assert the 0.75 floor for FLOW only.
 */
const FLOW_SHRINK_FLOOR = 0.75;
/* A figure whose box is nearly as tall as the whole printable page, wherever a
 * page break places it, occupies almost the entire page and leaves no room for
 * the prose that introduces (or follows) it — so it strands on its own
 * near-empty page.  Reserve a "companion band" of page height for that
 * neighbouring text; a figure too tall to leave the band is flow-hostile and is
 * shrunk (down to, never past, the floor) so text can share its page. */
const FLOW_COMPANION_BAND_FRACTION = 0.22;

/**
 * Set a figure <svg> to a fitted size and release the mermaid/D3 wrapper's
 * inline `min-height` reservation so the atomic figure box collapses to the
 * fitted diagram (otherwise the box stays page-tall and still forces the break
 * even after the inner <svg> shrinks).  Shared by the OVERSIZE and FLOW paths.
 */
function applyFigureShrink(
    svg: SVGSVGElement, w: number, h: number, scale: number, reason: 'oversize' | 'flow',
): void {
    const newW = Math.floor(w * scale);
    const newH = Math.floor(h * scale);
    // Override the inline !important width/height with our own !important.
    svg.style.setProperty('width', `${newW}px`, 'important');
    svg.style.setProperty('height', `${newH}px`, 'important');
    svg.style.setProperty('max-width', '100%', 'important');
    svg.style.setProperty('max-height', 'none', 'important');
    svg.setAttribute('data-print-fit-scale', scale.toFixed(4));
    svg.setAttribute('data-print-fit-reason', reason);

    // The mermaid renderer sizes the wrapping .mermaid-wrapper /
    // .mermaid-container to the diagram's ORIGINAL height via an inline
    // `min-height` (e.g. min-height: 2384px).  That reservation survives us
    // shrinking the inner <svg>, so the atomic figure box stays page-tall and
    // still forces the absurd break (leaving the shrunk diagram marooned on a
    // mostly-empty page and pushing following content down).  Release the
    // ancestors' reserved height so the box collapses to the fitted diagram.
    // We walk up to the message boundary, only touching the known diagram
    // wrappers.
    let anc: HTMLElement | null = svg.parentElement;
    for (let depth = 0; anc && depth < 4; depth++, anc = anc.parentElement) {
        const cls = anc.className || '';
        const isWrapper = typeof cls === 'string' && (
            cls.includes('mermaid-wrapper') ||
            cls.includes('mermaid-container') ||
            cls.includes('d3-container')
        );
        if (!isWrapper) continue;
        // Clear the height reservation; let the box shrink-wrap the diagram.
        anc.style.setProperty('min-height', '0', 'important');
        anc.style.setProperty('height', 'auto', 'important');
        anc.style.setProperty('max-height', 'none', 'important');
    }
}

function fitOversizedFigures(root: HTMLElement): void {
    const svgs = Array.from(
        root.querySelectorAll<SVGSVGElement>(
            '.mermaid-container svg, .mermaid-wrapper svg, .d3-container svg, figure svg',
        ),
    );
    const maxW = PRINT_CONTENT_WIDTH_PX * FIT_SAFETY;
    const maxH = PRINT_CONTENT_HEIGHT_PX * FIT_SAFETY;
    // A figure box may be at most this tall before it can no longer leave a
    // companion band of prose on its page (the flow-hostile threshold).
    const flowMaxBoxH = maxH - PRINT_CONTENT_HEIGHT_PX * FLOW_COMPANION_BAND_FRACTION;
    for (const svg of svgs) {
        // Measured rendered size (falls back to the inline px if layout is 0).
        const rect = svg.getBoundingClientRect();
        const w = rect.width || parseFloat((svg.style.width || '0')) || 0;
        const h = rect.height || parseFloat((svg.style.height || '0')) || 0;
        if (w <= 0 || h <= 0) continue;
        const scale = Math.min(maxW / w, maxH / h, 1);
        if (scale < 1) {
            // OVERSIZE: intrinsically larger than a page — shrink to fit whole.
            // May go below 0.75x (it must, or it would not fit at all); this is
            // NOT flow-driven, so the 0.75 floor does not apply.
            applyFigureShrink(svg, w, h, scale, 'oversize');
            continue;
        }
        // FLOW-AWARE (NEW-1): the figure fits a page on its own, but if its box
        // is nearly as tall as a whole page it will monopolise whatever page a
        // break lands it on, stranding it away from its prose on a near-empty
        // page.  Shrink JUST enough to leave a companion band for neighbouring
        // text — but never past the 0.75 floor (the user's explicit ceiling on
        // flow-driven shrinkage; if the floor is not enough we accept it rather
        // than distort the figure further).
        if (h > flowMaxBoxH) {
            const flowScale = Math.max(FLOW_SHRINK_FLOOR, flowMaxBoxH / h);
            if (flowScale < 1) {
                applyFigureShrink(svg, w, h, flowScale, 'flow');
            }
        }
    }
}

/**
 * Only tables whose intrinsic (min-content) width exceeds the printable width by
 * at least this factor are fit-scaled.  A markdown table is routinely a little
 * wider than one page (its cells wrap, so only its rightmost column or two spill
 * a bit); scaling those would needlessly shrink legible content AND repaginate
 * the whole document (Card I saw `table-layout:fixed` push a 4-page export to 6
 * by crushing a merely-slightly-wide table).  The DEFECT case (PDF-09b) is a
 * table so wide its cells CANNOT wrap enough to fit — right-hand columns fall
 * off the content margin and vanish from the PDF entirely.  The canonical
 * fixture's 10-col table has a min-content overflow of ~1.9x (left untouched);
 * the 20-col adversarial table overflows ~5.4x (its rightmost cell is clipped
 * away) and is the one that must be scaled.  The 3.0x threshold cleanly
 * separates them and matches the user's ">~15 columns" framing.
 */
const OVERWIDE_TABLE_OVERFLOW_RATIO = 3.0;

/**
 * Fit-scale any markdown content table that is SO much wider than the printable
 * page that its right-hand columns would be clipped off the content margin and
 * dropped from the exported PDF entirely (user defect PDF-09b).
 *
 * WHY JS (mirrors `fitOversizedFigures`).  A `<table>` wider than the page is
 * clipped by the print layout — headless Chromium's `page.pdf()` does NOT scroll
 * or scale it, so the overflowing columns simply do not appear in the captured
 * pages (verified: `WIDECELL_19`, the rightmost cell of a 20-column table, is
 * absent from the extracted PDF text).  No author stylesheet fixes this without
 * regressing narrow tables: `table-layout:fixed` crushes narrow tables (and
 * repaginates the document), and cell `word-break` alone still overflows.  The
 * robust remedy is to uniformly down-scale ONLY the genuinely over-wide table
 * with CSS `zoom`, which reflows its layout at the smaller size (so every column
 * lands within the margin and survives the capture) while leaving all other
 * content — and the document's pagination — untouched.
 *
 * We measure the table's intrinsic min-content width via `scrollWidth` (the
 * width its unbreakable cell content demands, independent of the current
 * viewport) and only act when it exceeds the printable width by
 * `OVERWIDE_TABLE_OVERFLOW_RATIO`.  Diff tables (`table.diff-table`, incl. the
 * `.diff-table-hunk` variant) are EXCLUDED: they carry their own horizontal
 * scroll/flow handling and NEW-3 lets them page-flow, so they must not be
 * zoom-scaled.  Idempotent — a rescaled table already fits, so a second call
 * is a no-op.  Shared with Card II's HTML export (a clipped-in-HTML wide table
 * benefits identically).
 */
function fitOverwideTables(root: HTMLElement): void {
    const maxW = PRINT_CONTENT_WIDTH_PX * FIT_SAFETY;
    const tables = Array.from(root.querySelectorAll<HTMLTableElement>('table'));
    for (const table of tables) {
        // Skip diff tables — they flow/scroll on their own terms (NEW-3).
        if (table.classList.contains('diff-table')) continue;
        if (table.closest('.diff-view, .diff-container')) continue;
        // Already scaled? (idempotent)
        if (table.getAttribute('data-print-table-fit-scale')) continue;
        // Intrinsic (min-content) width the table's unbreakable content demands.
        const natural = table.scrollWidth
            || table.getBoundingClientRect().width || 0;
        if (natural <= 0) continue;
        if (natural <= maxW * OVERWIDE_TABLE_OVERFLOW_RATIO) continue;
        const scale = maxW / natural;
        // `zoom` reflows the table's layout at the reduced size (unlike a CSS
        // transform, which would scale the painted box but keep the original
        // layout width and still clip in the PDF).
        table.style.setProperty('zoom', String(scale));
        table.setAttribute('data-print-table-fit-scale', scale.toFixed(4));
    }
}

export const PrintRenderPage: React.FC = () => {
    const [spec, setSpec] = useState<ConversationSpec | null>(null);
    const [status, setStatus] = useState<RenderStatus>('idle');
    const [errorMessage, setErrorMessage] = useState<string>('');
    const [diag, setDiag] = useState<{ elapsedMs: number; lastEvent: string }>({
        elapsedMs: 0, lastEvent: 'init',
    });
    // Diagrams that failed to render, reported NON-FATALLY: one bad diagram
    // in a 50-page export must not fail the whole document, but the caller
    // should not have to eyeball the PDF to discover it. Unlike the /render
    // harness, this page never stalled on an error card — its readiness gate
    // accepts `pre`, which every error card contains — so the defect here was
    // silence, not a hang.
    const [diagramErrors, setDiagramErrors] = useState<string[]>([]);
    const contentRef = useRef<HTMLDivElement | null>(null);
    const observerRef = useRef<MutationObserver | null>(null);
    const safetyTimerRef = useRef<ReturnType<typeof setTimeout>>();

    // ThemeContext is the real provider (mounted above in index.tsx). Force
    // light DETERMINISTICALLY rather than hoping a body-class removal sticks.
    const { setTheme, isDarkMode } = useTheme();

    const applySpec = useCallback((incoming: ConversationSpec) => {
        setSpec(incoming);
        setStatus('loading');
        setErrorMessage('');
    }, []);

    // Deterministic light theme: drive the real ThemeContext to 'light' AND
    // scrub the dark affordances the chat leaves on <body>/<html>, AND stamp a
    // data attribute the capture asserts on. This is the fix for defect #6
    // (dark-mode content composited onto a white page).
    //
    // useLayoutEffect (NOT useEffect): this runs SYNCHRONOUSLY after the DOM is
    // mutated but BEFORE the browser paints, so a user whose persisted theme is
    // dark (localStorage / OS prefers-color-scheme feeds ThemeContext's initial
    // isDarkMode=true) never produces a capturable dark frame.  With a passive
    // post-paint useEffect the first paint could bake dark theme-derived inline
    // colors into a snapshot; page.pdf() captures the final settled state so the
    // PDF path was already safe, but Card II's extract_html()/screenshot
    // consumer can capture earlier — forcing light pre-paint makes light
    // deterministic for EVERY shared-route consumer, not just page.pdf().
    useLayoutEffect(() => {
        setTheme('light');
        document.body.classList.remove('dark');
        document.documentElement.classList.remove('dark');
        document.documentElement.setAttribute('data-ziya-print-theme', 'light');
        document.body.style.backgroundColor = '#ffffff';
        document.documentElement.style.backgroundColor = '#ffffff';
        // Also set a global the chat renderer reads for code-apply gating.
        (window as any).enableCodeApply = 'false';

        // ── Release the single-viewport app clamp (defect: content clipped to
        // ONE page) ────────────────────────────────────────────────────────
        // The base `body` rule pins the whole document to one viewport
        // (overflow:hidden; height:100vh; position:fixed) so the app chrome
        // never scrolls.  For a WHOLE-CONVERSATION render that must flow to its
        // natural height and paginate, that clamp makes Chromium's page.pdf()
        // (and any fixed-viewport screenshot) capture only the first A4 box and
        // silently drop everything below the fold.  We opt this route out via
        // the shared `ziya-print-mode` class (see index.css) AND set the
        // properties inline as a belt-and-suspenders guard against stylesheet
        // load-order surprises in the prebuilt CSS.  SHARED with Card II's
        // extract_html() consumer: the DOM is full-height for both.
        document.body.classList.add('ziya-print-mode');
        // `allow-scroll` too, so any existing selector keyed on it also applies.
        document.body.classList.add('allow-scroll');
        const priorBody = {
            overflow: document.body.style.overflow,
            position: document.body.style.position,
            height: document.body.style.height,
        };
        const priorHtml = {
            overflow: document.documentElement.style.overflow,
            height: document.documentElement.style.height,
        };
        document.body.style.overflow = 'visible';
        document.body.style.position = 'static';
        document.body.style.height = 'auto';
        document.documentElement.style.overflow = 'visible';
        document.documentElement.style.height = 'auto';

        return () => {
            document.body.classList.remove('ziya-print-mode');
            document.body.classList.remove('allow-scroll');
            document.body.style.overflow = priorBody.overflow;
            document.body.style.position = priorBody.position;
            document.body.style.height = priorBody.height;
            document.documentElement.style.overflow = priorHtml.overflow;
            document.documentElement.style.height = priorHtml.height;
        };
    }, [setTheme]);

    // Accept spec via postMessage (same-origin only, mirrors DiagramRenderPage)
    useEffect(() => {
        const handleMessage = (event: MessageEvent) => {
            if (event.origin !== window.location.origin) return;
            if (event.data?.type === 'render-conversation' && event.data.spec) {
                applySpec(event.data.spec as ConversationSpec);
            }
        };
        window.addEventListener('message', handleMessage);
        return () => window.removeEventListener('message', handleMessage);
    }, [applySpec]);

    // URL hash channel (small payloads / manual testing)
    useEffect(() => {
        const hashSpec = parseSpecFromHash();
        if (hashSpec) applySpec(hashSpec);
    }, [applySpec]);

    // Imperative API for Playwright's page.evaluate() — the large-payload path.
    useEffect(() => {
        (window as any).__renderConversation = (specJson: string) => {
            try {
                applySpec(JSON.parse(specJson) as ConversationSpec);
                return true;
            } catch (e) {
                setErrorMessage(String(e));
                setStatus('error');
                return false;
            }
        };
        return () => { delete (window as any).__renderConversation; };
    }, [applySpec]);

    const options = useMemo(() => ({ ...DEFAULT_OPTIONS, ...(spec?.options || {}) }), [spec]);
    const filteredMessages = useMemo(
        () => (spec ? applyOptions(spec.messages || [], options) : []),
        [spec, options],
    );

    // Document mode (authored IR render): pagebreak-split sections, no message
    // chrome.  A document payload has no `messages`, so filteredMessages is []
    // and the transcript map below renders nothing — the two modes coexist
    // without touching the conversation path.  Mermaid theme normalization
    // still applies (a dark baked theme would band on the white page).
    const isDocument = spec?.kind === 'document';
    const docSections = useMemo(() => {
        if (!spec || spec.kind !== 'document') return [];
        return (spec.sections || []).map(s => normalizeMermaidThemeToLight(s || ''));
    }, [spec]);

    // ── Readiness detection ─────────────────────────────────────────────
    // Genuinely await async renderers. We consider the page COMPLETE when:
    //   (a) the DOM has stopped mutating for a short debounce window
    //       (diagrams/prism/katex have finished injecting nodes), AND
    //   (b) there are no unrendered KaTeX placeholders, AND
    //   (c) every <img> has loaded or errored.
    // The debounce only CONFIRMS quiescence; it is not itself the deadline.
    const finalizeReadiness = useCallback(async (node: HTMLDivElement, startedAt: number) => {
        // Rasterize any <canvas> to an inline <img> FIRST — before we gather and
        // await images below — so a canvas-backed renderer's pixels survive BOTH
        // capture backends (page.pdf() blanks an uncommitted canvas; extract_html
        // drops a canvas bitmap entirely) AND the resulting data-URL <img> is
        // included in the load-await gate.  No-op for today's all-SVG renderers;
        // shared with Card II's HTML export.
        rasterizeCanvasesToImages(node);
        // (c) await images
        const imgs = Array.from(node.querySelectorAll('img'));
        await Promise.all(imgs.map(img => {
            if (img.complete) return Promise.resolve();
            return new Promise<void>(resolve => {
                img.addEventListener('load', () => resolve(), { once: true });
                img.addEventListener('error', () => resolve(), { once: true });
            });
        }));
        // Shrink any diagram/figure taller or wider than the printable page
        // area so it fits WHOLE on one page (user defect #7 nonsensical breaks,
        // and #5 the big empty band left when an un-splittable tall figure is
        // bumped to its own page).  Done in JS because mermaid bakes
        // `width/height ... !important` inline on its <svg>, which outranks any
        // author stylesheet (see fitOversizedFigures + styles/print.css).
        // (Canvas rasterization already ran at the top of finalizeReadiness so
        // its data-URL <img> was awaited alongside the other images.)
        fitOversizedFigures(node);
        // Fit-scale any content table so much wider than the page that its
        // right-hand columns would be clipped off the margin and dropped from
        // the captured PDF entirely (user defect PDF-09b).  Narrow / mildly-wide
        // tables are left untouched; see fitOverwideTables.  Runs after images
        // settle so measured cell widths are final.
        fitOverwideTables(node);
        // Collect any plugin error cards so the caller learns which diagrams
        // failed without parsing the rendered output.
        const failed = Array.from(node.querySelectorAll('[data-diagram-error]'))
            .map(el => el.getAttribute('data-diagram-error') || 'unknown')
            .filter(Boolean);
        setDiagramErrors(failed);

        setDiag({ elapsedMs: Date.now() - startedAt, lastEvent: 'images-settled' });
        setStatus('complete');
    }, []);

    const onContentReady = useCallback((node: HTMLDivElement | null) => {
        contentRef.current = node;
        if (!node || status !== 'loading') return;

        setStatus('rendering');
        const startedAt = Date.now();
        setDiag({ elapsedMs: 0, lastEvent: 'observer-attached' });

        const safetyTimeoutMs = Math.max(2000, spec?.renderTimeoutMs ?? 60000);
        let quietTimer: ReturnType<typeof setTimeout> | undefined;
        const QUIET_MS = 600; // debounce that CONFIRMS quiescence (not the gate)

        const isSettled = () => {
            // No diagram still marked pending, no unrendered katex placeholder.
            const pendingDiagram = node.querySelector(
                '[data-render-status="rendering"], [data-render-status="loading"], .diagram-loading',
            );
            // MarkdownRenderer renders math to `.katex`; a leftover raw `$$`
            // math source node would indicate katex hasn't run yet.
            const hasContent = node.querySelector(
                'span.token, .katex, .diff-line, svg, img, p, pre, code',
            );
            return !pendingDiagram && !!hasContent;
        };

        const scheduleQuiet = () => {
            if (quietTimer) clearTimeout(quietTimer);
            quietTimer = setTimeout(async () => {
                if (isSettled()) {
                    observer.disconnect();
                    observerRef.current = null;
                    if (safetyTimerRef.current) clearTimeout(safetyTimerRef.current);
                    setDiag({ elapsedMs: Date.now() - startedAt, lastEvent: 'dom-quiescent' });
                    await finalizeReadiness(node, startedAt);
                }
            }, QUIET_MS);
        };

        const observer = new MutationObserver(() => {
            setDiag(prev => ({ elapsedMs: Date.now() - startedAt, lastEvent: prev.lastEvent }));
            scheduleQuiet();
        });
        observerRef.current = observer;
        observer.observe(node, { childList: true, subtree: true, attributes: true });

        // Kick off the first quiescence check in case content is already static.
        scheduleQuiet();

        // Safety net: if the page never quiesces, complete-with-content or
        // fail-with-diagnostics rather than hang the harness forever.
        safetyTimerRef.current = setTimeout(async () => {
            if (quietTimer) clearTimeout(quietTimer);
            observer.disconnect();
            observerRef.current = null;
            if (isSettled() || node.querySelector('p, pre, span.token, .katex, svg, img')) {
                setDiag({ elapsedMs: Date.now() - startedAt, lastEvent: 'timeout-with-content' });
                await finalizeReadiness(node, startedAt);
            } else {
                const counts = {
                    svg: node.querySelectorAll('svg').length,
                    img: node.querySelectorAll('img').length,
                    tokens: node.querySelectorAll('span.token').length,
                    htmlLen: node.innerHTML.length,
                };
                setErrorMessage(
                    `Print render timeout after ${safetyTimeoutMs}ms. ` +
                    `DOM snapshot: ${JSON.stringify(counts)}`,
                );
                setDiag({ elapsedMs: Date.now() - startedAt, lastEvent: 'timeout-no-content' });
                setStatus('error');
            }
        }, safetyTimeoutMs);
    }, [status, spec?.renderTimeoutMs, finalizeReadiness]);

    useEffect(() => () => {
        if (safetyTimerRef.current) clearTimeout(safetyTimerRef.current);
        if (observerRef.current) { observerRef.current.disconnect(); observerRef.current = null; }
    }, []);

    // Stub the two remaining coupling contexts (Project/Folder/SendPayload) at
    // module level is not possible; instead we wrap in the real ActiveChat and
    // Streaming providers (prop-driven) and rely on ProjectProvider/FolderProvider
    // being mounted above in index.tsx. This keeps the render faithful without
    // the app's live chat state.
    const activeChatValue = useMemo(() => ({
        // Fields a static read-only render dereferences (see Stage 1 stub_shape)
        reasoningContentMap: new Map(),
        currentConversationId: 'print-export',
        currentMessages: [],
        throttlingRecoveryData: new Map(),
        // interactive-only no-ops:
        addStreamingConversation: () => {},
        setThrottlingRecoveryData: () => {},
    } as any), []);

    return (
        <div
            id="print-render-root"
            data-render-status={status}
            data-error={errorMessage || undefined}
            data-diagram-errors={diagramErrors.length ? String(diagramErrors.length) : undefined}
            data-diagram-error-list={diagramErrors.length ? diagramErrors.join(' | ') : undefined}
            data-elapsed-ms={diag.elapsedMs}
            data-last-event={diag.lastEvent}
            data-theme={isDarkMode ? 'dark' : 'light'}
            style={{
                background: '#ffffff',
                color: '#1a1a1a',
                minHeight: '100vh',
                width: '100%',
                margin: 0,
                padding: 0,
            }}
        >
            {status === 'idle' && (
                <div style={{ color: '#888', fontSize: 14, padding: 40, textAlign: 'center' }}>
                    Waiting for conversation…
                    <br />
                    <code style={{ fontSize: 11 }}>window.__renderConversation(json)</code>
                </div>
            )}

            {status === 'error' && (
                <div style={{ color: '#cf1322', padding: 20 }}>
                    <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>
                        Print Render Error
                    </div>
                    <div style={{ fontSize: 13 }}>{errorMessage}</div>
                </div>
            )}

            {spec && status !== 'error' && (
                <ActiveChatProvider {...activeChatValue}>
                    <StreamingProvider
                        isStreaming={false}
                        isStreamingAny={false}
                        currentConversationId={'print-export'}
                        streamingConversations={new Set<string>()}
                    >
                        {/* The conversation container. `conversation-messages-container`
                            matches the chat UI class so CSS selectors used by the
                            renderer (and by Card II's HTML extraction) apply. */}
                        <div
                            ref={onContentReady}
                            id="print-render-content"
                            className="conversation-messages-container ziya-print"
                            style={{
                                background: '#ffffff',
                                color: '#1a1a1a',
                                padding: '24px 28px',
                                maxWidth: '100%',
                            }}
                        >
                            {!isDocument && spec.title && (
                                <h1 style={{ fontSize: 22, marginBottom: 16 }}>{spec.title}</h1>
                            )}
                            {isDocument && spec.layout === 'report' && (spec.title || spec.author) && (
                                <header className="print-doc-titleblock" style={{ marginBottom: 28 }}>
                                    {spec.title && (
                                        <h1 style={{ fontSize: 26, marginBottom: 6 }}>{spec.title}</h1>
                                    )}
                                    {spec.author && (
                                        <div className="print-doc-author" style={{ fontSize: 13, color: '#57606a' }}>
                                            {spec.author}
                                        </div>
                                    )}
                                    <div className="print-doc-date" style={{ fontSize: 12, color: '#8c8c8c' }}>
                                        {new Date().toLocaleDateString()}
                                    </div>
                                </header>
                            )}
                            <React.Suspense
                                fallback={<div style={{ padding: 20, color: '#888' }}>Loading renderer…</div>}
                            >
                                {isDocument && docSections.map((body, i) => (
                                    <div
                                        key={i}
                                        className="print-doc-section"
                                        data-doc-section={i}
                                        style={i > 0 ? { breakBefore: 'page' } : undefined}
                                    >
                                        <MarkdownRenderer
                                            markdown={body}
                                            enableCodeApply={false}
                                            isStreaming={false}
                                            forceRender={true}
                                        />
                                    </div>
                                ))}
                                {filteredMessages.map((msg, i) => {
                                    // PDF-07 (Card IV): the per-message separator
                                    // (bottom border + margin + padding) is a
                                    // BETWEEN-messages divider.  On the FINAL message
                                    // nothing follows it but the footer (or the document
                                    // end), so that trailing border/space is pure chrome:
                                    // a stray horizontal rule at the document tail and a
                                    // small band of trailing whitespace.  Drop it on the
                                    // last message so the transcript ends cleanly.  These
                                    // are inline styles (they outrank the stylesheet), so
                                    // the fix must live here, not in print.css.
                                    const isLastMessage = i === filteredMessages.length - 1;
                                    return (
                                    <div
                                        key={i}
                                        className={`print-message print-message-${msg.role || 'unknown'}`}
                                        data-role={msg.role}
                                        data-last-message={isLastMessage ? 'true' : undefined}
                                        style={{
                                            marginBottom: isLastMessage ? 0 : 20,
                                            paddingBottom: isLastMessage ? 0 : 12,
                                            borderBottom: isLastMessage ? undefined : '1px solid #eaecef',
                                        }}
                                    >
                                        <div
                                            className="print-message-role"
                                            style={{
                                                fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
                                                letterSpacing: 0.5, color: '#57606a', marginBottom: 6,
                                            }}
                                        >
                                            {msg.role === 'human' ? 'You'
                                                : msg.role === 'assistant' ? 'Ziya'
                                                    : (msg.role || '')}
                                        </div>
                                        <MarkdownRenderer
                                            markdown={msg.content || ''}
                                            enableCodeApply={false}
                                            isStreaming={false}
                                            forceRender={true}
                                            role={msg.role as any}
                                        />
                                    </div>
                                    );
                                })}
                            </React.Suspense>

                            {options.includeFooter && spec.footerHtml && (
                                <div
                                    className="print-footer"
                                    // Footer HTML is produced by the trusted server-side
                                    // `_create_footer` (version/model/provider), NOT model
                                    // output, so this is not an injection surface.
                                    dangerouslySetInnerHTML={{ __html: spec.footerHtml }}
                                />
                            )}
                        </div>
                    </StreamingProvider>
                </ActiveChatProvider>
            )}
        </div>
    );
};

export default PrintRenderPage;
