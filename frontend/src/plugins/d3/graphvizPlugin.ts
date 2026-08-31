import { D3RenderPlugin } from '../../types/d3';
import { isDiagramDefinitionComplete } from '../../utils/diagramUtils';
import { extractDefinitionFromYAML } from '../../utils/diagramUtils';
import { enhanceSVGVisibility, isLightBackground } from '../../utils/colorUtils';
import { zoomIn, zoomOut, resetZoom, storeOriginalViewBox } from '../../utils/zoomUtils';
import { downloadSvg } from '../../utils/svgUtils';
import { escapeHtml } from '../../utils/htmlSanitize';
import { getZoomScript, getDownloadSvgScript } from '../../utils/popupScriptUtils';

/**
 * D-125: selectors for text the universal SVG enhancer must NOT recolour.
 *
 * Graphviz edge LABELS are `<text>` inside `<g class="edge">`, and this plugin
 * already injects a theme-correct `fontcolor` for them (edge[] default:
 * `#000000` in light / `#ffffff` in dark — see `defaultTextColor`). So the
 * edge-label ink is authoritative before enhancement runs.
 *
 * The shared enhancer's `findElementBackground` (colorUtils.ts, out of this
 * plugin's tree) picks the FIRST filled shape in the parent `<g>` as the
 * label's background — but inside `<g class="edge">` that first shape is the
 * ARROWHEAD `<polygon>`, filled with the (dark, e.g. `#333333`) edge colour.
 * The enhancer therefore concludes the label sits on a dark surface and, in
 * LIGHT mode, rewrites the label fill to white → white-on-white (~1:1),
 * losing every edge label. In DARK the forced white merely re-affirms the
 * already-white injected ink, which is why dark looked fine.
 *
 * Rather than have the plugin second-guess a heuristic it does not own, we
 * tell the enhancer (via its documented `skipSelectors` option) to leave
 * edge-label text alone, preserving the per-theme `fontcolor` this plugin
 * already set. This is scoped to graphviz edge labels only; node/cluster/graph
 * text is still enhanced (D-133/D-136/D-137 machinery), and the shared enhancer
 * is unchanged for every other engine.
 */
export const GRAPHVIZ_ENHANCER_SKIP_SELECTORS = ['g.edge text'];

export interface GraphvizSpec {
    type: 'graphviz';
    isStreaming?: boolean;
    isMarkdownBlockClosed?: boolean;
    forceRender?: boolean;
    definition: string;
}

/**
 * STRESS-GUARD (Issue 33): minimum drawing size, in INCHES, below which a DOT
 * `size=` graph attribute is treated as degenerate and dropped.
 *
 * Graphviz honors `size` verbatim and only ever scales the whole drawing DOWN
 * to fit it (never up, unless a trailing `!` is present). A tiny value such as
 * `size="0.01,0.01"` (0.01in ≈ sub-pixel at any raster DPI) collapses the entire
 * graph to a sub-pixel canvas: the render "succeeds" with HTTP 200 but produces
 * a blank raster — catastrophic SILENT data loss. No legitimate diagram is
 * authored below half an inch, so a value under this floor is unambiguously a
 * footgun; dropping it lets the graph render at natural size.
 */
export const GRAPHVIZ_MIN_SIZE_INCHES = 0.5;

/**
 * Parse the numeric dimensions out of a DOT `size` value.
 * Accepts "W,H", "W", optional trailing "!" (force flag) and surrounding spaces.
 * Returns the finite, parseable dimensions (may be length 0/1/2).
 */
function parseGraphvizSizeDims(raw: string): number[] {
    return String(raw)
        .replace(/!/g, '')
        .split(',')
        .map((s) => parseFloat(s.trim()))
        .filter((n) => Number.isFinite(n));
}

/**
 * True when a DOT `size` value would collapse the drawing to a sub-threshold
 * canvas — i.e. it has at least one positive dimension and every positive
 * dimension is below `minInches`. An unparseable value, or one whose largest
 * dimension is >= the floor, is left untouched (returns false) so legitimate
 * small-but-sane specs pass through unchanged.
 */
export function isDegenerateGraphvizSize(raw: string, minInches: number = GRAPHVIZ_MIN_SIZE_INCHES): boolean {
    const dims = parseGraphvizSizeDims(raw);
    const positive = dims.filter((d) => d > 0);
    if (positive.length === 0) return false; // no positive dim (e.g. "0,0" or unparseable) -> leave alone
    // Degenerate only when EVERY positive dimension is below the floor; a spec
    // that is small in one axis but reasonable in the other is preserved.
    return positive.every((d) => d < minInches);
}

/**
 * Remove any degenerate `size=` graph attribute from a DOT string so a
 * sub-pixel `size` (with or without `ratio=fill`) can no longer produce a
 * blank raster. Pure and idempotent; well-formed / reasonably-sized specs are
 * returned byte-identical. Handles both quoted (`size="0.01,0.01"`) and
 * unquoted (`size=0.01`) forms, and does NOT touch `fontsize`/`POINT-SIZE`
 * (the negative lookbehind guards against the `-SIZE` HTML-label attribute).
 */
export function clampGraphvizSize(dot: string, minInches: number = GRAPHVIZ_MIN_SIZE_INCHES): string {
    if (typeof dot !== 'string' || dot.length === 0) return dot;
    let out = dot;
    // Quoted form: size="0.01,0.01" / size="6,6!" / size="0.01"
    out = out.replace(/(?<![-\w])size\s*=\s*"([^"]*)"/gi, (m, val) =>
        isDegenerateGraphvizSize(val, minInches) ? '' : m
    );
    // Unquoted form: size=0.01 / size=0.01,0.01 / size=6,6!
    out = out.replace(/(?<![-\w])size\s*=\s*([0-9]*\.?[0-9]+(?:\s*,\s*[0-9]*\.?[0-9]+)?!?)/gi, (m, val) =>
        isDegenerateGraphvizSize(val, minInches) ? '' : m
    );
    return out;
}

/* =====================================================================
 * VIEWPORT FIT (G-56: D-129 crop / no-upscale, D-130 & D-135 sub-pixel font)
 * ---------------------------------------------------------------------
 * Viz.js emits an SVG sized in ABSOLUTE points with a viewBox but no
 * responsive behaviour, and the plugin mounted it as-is. Three failures
 * share this one missing fit-to-viewport step:
 *   - D-129 (crop): a graph LARGER than the bounded capture window is sliced
 *     at the edge instead of being scaled to fit; its mirror is a small /
 *     `size="1.5,1.5!"`-forced graph that draws as a sub-pixel island with no
 *     upscale into the empty canvas.
 *   - D-130 / D-135 (sub-pixel labels): where the drawing IS shrunk to fit
 *     there is no minimum legible-font floor, so at high fan-out / density the
 *     labels dissolve below a readable size.
 *
 * `planGraphvizViewport` is the single shared lever. It is PURE and operates
 * in CSS px: given the drawing's natural size and the container width it
 * returns the target SVG width, the effective scale and whether the wrapper
 * must scroll. It intervenes ONLY in the two defect regimes — a small graph is
 * upscaled to fill, an over-wide graph past the min-font floor stops shrinking
 * and scrolls — and returns natural size for the comfortable middle range, so
 * a currently-fine render is byte-equivalent (no unrelated-output change).
 * ===================================================================== */

/** Points -> CSS px (1pt = 96/72 px). Graphviz SVG width/height attrs and the
 *  viewBox user units are both in points. */
export const GRAPHVIZ_PT_TO_PX = 96 / 72;

/**
 * Below this scale a shrink-to-fit pushes the graphviz default label (the
 * plugin injects fontsize~12-14pt ~ 16px) under ~8px — the legibility floor.
 * At or past it we stop shrinking and scroll instead of dissolving the text.
 */
export const GRAPHVIZ_MIN_FONT_SCALE = 0.5;

/**
 * A graph is only UPSCALED when it is at most ~2/3 of the container width
 * (fitScale >= 1.5). Between 2/3 and full width the render is already
 * comfortable and is left exactly as-is, so ordinary diagrams are untouched.
 */
export const GRAPHVIZ_UPSCALE_MIN_FITSCALE = 1.5;

/** Cap the blow-up of a tiny graph so a 2-node stub does not become grotesque. */
export const GRAPHVIZ_MAX_UPSCALE = 4;

export interface GraphvizViewportPlan {
    /** How the SVG is sized relative to natural size. */
    mode: 'natural' | 'upscale' | 'fit' | 'scroll';
    /** Target CSS width for the SVG element, in px (>= 1 for a sized graph). */
    svgWidthPx: number;
    /** svgWidthPx / naturalWpx — the on-screen scale applied to the drawing. */
    effectiveScale: number;
    /** Whether the wrapper must allow scroll/pan (content wider than the box). */
    scroll: boolean;
}

/**
 * Decide how to fit a laid-out graphviz drawing (natural size, in px) into the
 * container. Pure and side-effect-free; unit-testable without a browser.
 *
 * Regimes (naturalWpx vs containerWpx):
 *   - WIDER than container, shrink keeps labels >= floor -> 'fit' (width=container).
 *   - WIDER than container, shrink would go below the floor -> 'scroll'
 *     (clamp scale at the floor, SVG stays wider than the box, wrapper scrolls).
 *   - clearly SMALLER (fitScale >= upscaleMin) -> 'upscale' to fill, capped.
 *   - comfortable middle -> 'natural' (byte-equivalent to the old output).
 */
export function planGraphvizViewport(
    naturalWpx: number,
    naturalHpx: number,
    containerWpx: number,
    opts: { minFontScale?: number; upscaleMinFitScale?: number; maxUpscale?: number } = {}
): GraphvizViewportPlan {
    const minFontScale = opts.minFontScale ?? GRAPHVIZ_MIN_FONT_SCALE;
    const upscaleMin = opts.upscaleMinFitScale ?? GRAPHVIZ_UPSCALE_MIN_FITSCALE;
    const maxUpscale = opts.maxUpscale ?? GRAPHVIZ_MAX_UPSCALE;

    // Defensive: unusable measurements -> leave the SVG at natural size.
    if (!Number.isFinite(naturalWpx) || !Number.isFinite(containerWpx) ||
        naturalWpx <= 0 || containerWpx <= 0) {
        const w = naturalWpx > 0 ? naturalWpx : 0;
        return { mode: 'natural', svgWidthPx: w, effectiveScale: 1, scroll: false };
    }

    const fitScale = containerWpx / naturalWpx;

    if (naturalWpx > containerWpx) {
        // Graph WIDER than the container.
        if (fitScale >= minFontScale) {
            // Shrink-to-fit keeps labels above the floor (today's max-width:100%).
            return { mode: 'fit', svgWidthPx: containerWpx, effectiveScale: fitScale, scroll: false };
        }
        // Shrinking to fit would push labels below the legible floor: clamp the
        // downscale at the floor and SCROLL past that point instead of vanishing.
        return {
            mode: 'scroll',
            svgWidthPx: naturalWpx * minFontScale,
            effectiveScale: minFontScale,
            scroll: true,
        };
    }

    // Graph fits within the container (naturalWpx <= containerWpx).
    if (fitScale >= upscaleMin) {
        // Clearly small (<= ~2/3 width): upscale to fill so sub-pixel labels
        // (e.g. from a forced size="1.5,1.5!") become legible; cap the blow-up.
        const scale = Math.min(fitScale, maxUpscale);
        return { mode: 'upscale', svgWidthPx: naturalWpx * scale, effectiveScale: scale, scroll: false };
    }

    // Comfortably-sized graph: leave exactly as-is (no unrelated-output change).
    return { mode: 'natural', svgWidthPx: naturalWpx, effectiveScale: 1, scroll: false };
}

/**
 * Read a graphviz SVG's natural drawing size in CSS px, from its width/height
 * attributes (points) and falling back to the viewBox user units. Returns
 * {w:0,h:0} when nothing parseable is present (caller then leaves it alone).
 * Takes an attribute getter so it is testable without a live SVG element.
 */
export function readGraphvizNaturalSizePx(
    getAttr: (name: string) => string | null
): { w: number; h: number } {
    const parsePt = (v: string | null): number => {
        if (!v) return 0;
        const m = String(v).match(/-?[0-9]*\.?[0-9]+/);
        if (!m) return 0;
        const n = parseFloat(m[0]);
        return Number.isFinite(n) && n > 0 ? n * GRAPHVIZ_PT_TO_PX : 0;
    };
    let w = parsePt(getAttr('width'));
    let h = parsePt(getAttr('height'));
    if (!w || !h) {
        const vb = getAttr('viewBox');
        if (vb) {
            const parts = vb.trim().split(/[\s,]+/).map((s) => parseFloat(s));
            if (parts.length === 4 && Number.isFinite(parts[2]) && Number.isFinite(parts[3])) {
                if (!w && parts[2] > 0) w = parts[2] * GRAPHVIZ_PT_TO_PX;
                if (!h && parts[3] > 0) h = parts[3] * GRAPHVIZ_PT_TO_PX;
            }
        }
    }
    return { w, h };
}

/* =====================================================================
 * RECOVERY / COLOUR NORMALISATION (G-16: D-127, D-128)
 * ---------------------------------------------------------------------
 * The plugin previously had NO lexical/syntactic repair stage: a markdown
 * fence, a JSON envelope, smart quotes, single-quoted attribute values,
 * unbalanced braces, a graph/digraph edge-operator dialect mismatch, or an
 * invalid comma node-group each reached Viz.js as-is, threw a DOT parse
 * error, and — because the plugin's throw is delivered to the headless
 * harness as a silent 30s watchdog timeout — surfaced as total data loss
 * with no diagnostic (D-127). Separately, any colour Viz.js could not
 * resolve (rgb()/rgba(), a design token, a near-miss name) fell back to
 * solid #000000 — a black slab on the light page / a black hole on the
 * dark panel (D-128).
 *
 * All of the functions below are PURE and IDEMPOTENT: clean, well-formed
 * DOT is returned byte-identical, so they cannot alter a working spec.
 * ===================================================================== */

/** Mask double-quoted string literals so an edge-operator / comma rewrite can
 *  never corrupt text inside a label. Returns the masked string + the tokens. */
function maskDotStrings(s: string): { masked: string; tokens: string[] } {
    const tokens: string[] = [];
    const masked = s.replace(/"(?:\\.|[^"\\])*"/g, (m) => {
        tokens.push(m);
        return `\u0000${tokens.length - 1}\u0000`;
    });
    return { masked, tokens };
}

function unmaskDotStrings(s: string, tokens: string[]): string {
    return s.replace(/\u0000(\d+)\u0000/g, (_m, i) => tokens[parseInt(i, 10)] ?? '');
}

/** Strip a leading/trailing markdown code fence (```dot / ```graphviz / ```).
 *  Handles a properly closed fence AND an UNTERMINATED opening fence (a common
 *  truncated-output shape) by removing the opening ```lang line and any trailing
 *  fence when the body still looks like DOT. */
export function stripGraphvizFence(input: string): string {
    if (typeof input !== 'string') return input;
    const fenced = input.match(/```[a-zA-Z0-9_-]*\s*\n?([\s\S]*?)\n?```/);
    if (fenced && /\b(?:strict\s+)?(?:di)?graph\b/i.test(fenced[1])) {
        return fenced[1].trim();
    }
    // Unterminated opening fence: ```lang\n<dot...>  (no closing fence)
    if (/^\s*```/.test(input)) {
        const stripped = input
            .replace(/^\s*```[a-zA-Z0-9_-]*[ \t]*\n?/, '')
            .replace(/\n?```\s*$/, '');
        if (/\b(?:strict\s+)?(?:di)?graph\b/i.test(stripped)) {
            return stripped.trim();
        }
    }
    return input;
}

/** Unwrap a JSON envelope such as {"type":"graphviz","definition":"digraph{...}"}.
 *  DOT never begins with a bare '{', so a parseable object carrying a string
 *  definition/dot/graph/src key is unambiguously an envelope. */
export function unwrapGraphvizJsonEnvelope(input: string): string {
    if (typeof input !== 'string') return input;
    const t = input.trim();
    if (t[0] !== '{') return input;
    try {
        const obj = JSON.parse(t);
        if (obj && typeof obj === 'object') {
            for (const k of ['definition', 'dot', 'graph', 'src', 'source']) {
                if (typeof obj[k] === 'string' && obj[k].trim().length > 0) {
                    return obj[k];
                }
            }
        }
    } catch {
        /* not a JSON envelope — leave untouched */
    }
    return input;
}

/** Normalise Unicode smart quotes to their ASCII equivalents. */
export function normalizeGraphvizSmartQuotes(input: string): string {
    if (typeof input !== 'string') return input;
    return input
        .replace(/[\u201C\u201D\u201E\u201F]/g, '"')
        .replace(/[\u2018\u2019\u201A\u201B]/g, "'");
}

/** Convert single-quoted attribute values (`label='x'`) to DOT-legal double
 *  quotes. Only fires after an `=` so an apostrophe inside a double-quoted
 *  label is untouched. */
export function normalizeGraphvizSingleQuotes(input: string): string {
    if (typeof input !== 'string') return input;
    return input.replace(/=\s*'([^'\n]*)'/g, (_m, v) => `="${v}"`);
}

/** Reconcile the edge operator with the graph keyword: a `digraph` must use
 *  `->`, an undirected `graph` must use `--`. A mismatch is a hard parse error
 *  in Viz.js. Runs on string-masked input so label text is never rewritten. */
export function repairGraphvizEdgeDialect(input: string): string {
    if (typeof input !== 'string') return input;
    const isDirected = /\b(?:strict\s+)?digraph\b/i.test(input);
    const isUndirected = !isDirected && /\b(?:strict\s+)?graph\b/i.test(input);
    if (!isDirected && !isUndirected) return input;
    const { masked, tokens } = maskDotStrings(input);
    let out = masked;
    if (isDirected) {
        // any `--` used as an edge operator -> `->`
        out = out.replace(/([\w\]}\u0000])\s*--\s*(?=[\w"{\u0000])/g, '$1 -> ');
    } else {
        // any `->` used as an edge operator -> `--`
        out = out.replace(/([\w\]}\u0000])\s*->\s*(?=[\w"{\u0000])/g, '$1 -- ');
    }
    return unmaskDotStrings(out, tokens);
}

/** Repair invalid comma node-groups (`{a, b, c}` / `{a,b,c,}`) — legal DOT
 *  separates grouped nodes with spaces/semicolons, not commas — and drop an
 *  empty `[,]` attribute list. Attribute-list trailing commas (`[a=1,]`) are
 *  LEGAL and left untouched: only pure identifier groups (no `=`/`;`/edge op)
 *  are rewritten. Runs on string-masked input. */
export function repairGraphvizNodeGroups(input: string): string {
    if (typeof input !== 'string') return input;
    const { masked, tokens } = maskDotStrings(input);
    let out = masked.replace(/\[\s*,\s*\]/g, '[]');
    out = out.replace(/\{([^{}]*)\}/g, (m, body) => {
        if (!body.includes(',')) return m;
        // Only a pure node group: identifiers/masked-strings separated by commas,
        // with no attribute assignment, statement separator or edge operator.
        if (/[=;]|->|--/.test(body)) return m;
        if (!/^[\s\w"\u0000,]+$/.test(body)) return m;
        const cleaned = body.replace(/\s*,\s*/g, ' ').replace(/\s+/g, ' ').trim();
        return `{${cleaned}}`;
    });
    return unmaskDotStrings(out, tokens);
}

/** Append missing closing braces so an unterminated body reaches layout. */
export function balanceGraphvizBraces(input: string): string {
    if (typeof input !== 'string') return input;
    const { masked } = maskDotStrings(input);
    const open = (masked.match(/\{/g) || []).length;
    const close = (masked.match(/\}/g) || []).length;
    if (open > close) return input + '\n' + '}'.repeat(open - close);
    return input;
}

/**
 * Rewrite the deprecated `setlinewidth(N)` style idiom to the modern
 * `penwidth=N` attribute (D-254). Pre-2011 DOT — which models were heavily
 * trained on — expresses stroke width as `style="setlinewidth(3),filled"`.
 * Modern graphviz silently DROPS the unrecognised `setlinewidth(...)` style
 * token, so every node/edge falls back to the default width and the authored
 * distinction (`Thick A` vs `Thin C`) is lost with no error. We lift the width
 * out of the style string into a sibling `penwidth=N` attribute and keep any
 * remaining style tokens (`filled`, `dashed`, ...). Pure and idempotent: a spec
 * with no `setlinewidth` is returned byte-identical. Operates on the raw
 * `style="..."` attribute (which maskDotStrings would otherwise hide), and only
 * on a `style=` attribute so label text is never touched.
 */
export function normalizeGraphvizSetlinewidth(input: string): string {
    if (typeof input !== 'string' || input.length === 0) return input;
    if (!/setlinewidth/i.test(input)) return input;
    return input.replace(/style\s*=\s*"([^"]*)"/gi, (match, body: string) => {
        const m = body.match(/setlinewidth\s*\(\s*([\d.]+)\s*\)/i);
        if (!m) return match;
        const width = m[1];
        const rest = body
            .replace(/setlinewidth\s*\(\s*[\d.]+\s*\)/i, '')
            .replace(/,\s*,/g, ',')
            .replace(/^\s*,\s*/, '')
            .replace(/\s*,\s*$/, '')
            .trim();
        return rest.length > 0
            ? `penwidth=${width} style="${rest}"`
            : `penwidth=${width}`;
    });
}

/**
 * Full lexical recovery pipeline (D-127). Ordered so masking is correct:
 * fence/envelope -> smart quotes -> single-quote attrs -> setlinewidth ->
 * dialect -> node groups -> brace balance. Idempotent; a no-op on clean DOT.
 */
export function repairGraphvizSource(input: string): string {
    if (typeof input !== 'string' || input.length === 0) return input;
    let out = input;
    out = stripGraphvizFence(out);
    out = unwrapGraphvizJsonEnvelope(out);
    out = normalizeGraphvizSmartQuotes(out);
    out = normalizeGraphvizSingleQuotes(out);
    out = normalizeGraphvizSetlinewidth(out);
    out = repairGraphvizEdgeDialect(out);
    out = repairGraphvizNodeGroups(out);
    out = balanceGraphvizBraces(out);
    return out;
}

/** Colour attributes graphviz understands; used to scope token-dropping and
 *  name-snapping so we never touch unrelated identifiers. */
const GRAPHVIZ_COLOR_ATTRS = 'fillcolor|bgcolor|color|fontcolor|pencolor|labelfontcolor';

/** Small near-miss -> canonical X11 name table (Levenshtein-1 cases that
 *  graphviz rejects and would otherwise fall back to black). */
const GRAPHVIZ_COLOR_NAME_FIX: Record<string, string> = {
    cornflower: 'cornflowerblue',
    dodger: 'dodgerblue',
    slategrey: 'slategray',
};

/**
 * Colour-form normaliser (D-128). Three targeted, deterministic steps — the
 * antidote to Viz.js's fallback-to-#000000:
 *   1. rgb()/rgba() FUNCTION -> #rrggbb (alpha dropped; xcolor/DOT have no
 *      alpha channel). Percentage forms are left untouched (rare, ambiguous).
 *   2. An unresolvable token in a colour attribute (var(--x), $token,
 *      theme.foo, currentColor) -> DROP the whole attribute so the node
 *      inherits the themed default, NEVER a literal black.
 *   3. A near-miss colour name in a colour attribute -> snap to canonical.
 * Pure and idempotent; resolvable colours (#hex, valid X11 names) pass through.
 */
export function normalizeGraphvizColors(input: string): string {
    if (typeof input !== 'string' || input.length === 0) return input;
    let out = input;

    // (1) rgb()/rgba() -> #rrggbb  (integer 0-255 components only)
    out = out.replace(
        /rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*[\d.]+\s*)?\)/gi,
        (m, r, g, b) => {
            const c = [r, g, b].map((n: string) => {
                const v = Math.max(0, Math.min(255, parseInt(n, 10)));
                return v.toString(16).padStart(2, '0');
            });
            return `#${c.join('')}`;
        }
    );

    // (2) unresolvable token in a colour attribute -> drop the attribute
    const tokenVal = '(?:var\\([^")\\]]*\\)|\\$[\\w-]+|theme\\.[\\w.]+|currentcolor)';
    const dropRe = new RegExp(
        `\\b(?:${GRAPHVIZ_COLOR_ATTRS})\\s*=\\s*("?)${tokenVal}\\1`,
        'gi'
    );
    out = out.replace(dropRe, '');
    // tidy attribute-list punctuation left dangling by a removed attribute
    out = out
        .replace(/\[\s*,/g, '[')
        .replace(/,\s*,/g, ',')
        .replace(/,\s*\]/g, ']')
        .replace(/\[\s*\]/g, '[]');

    // (3) near-miss colour name -> canonical X11 name (colour attributes only)
    const nameRe = new RegExp(
        `\\b(${GRAPHVIZ_COLOR_ATTRS})(\\s*=\\s*"?)([a-zA-Z]+)("?)`,
        'gi'
    );
    out = out.replace(nameRe, (m, attr, eq, val, q) => {
        const fixed = GRAPHVIZ_COLOR_NAME_FIX[val.toLowerCase()];
        return fixed ? `${attr}${eq}${fixed}${q}` : m;
    });

    return out;
}

/**
 * Record/port syntax detector (D-131). A record/Mrecord label declares ports as
 * `<portname>` tokens and separates fields with `|`. The generic label->HTML-like
 * rewrite HTML-escapes `<`/`>`, so `<f0> left` was rendered as the literal text
 * `<f0> left` for every port (and roughly doubled each field's geometry at scale).
 * A double-quoted DOT label already displays correctly for BOTH record shapes
 * (ports parsed natively) and non-record shapes (the text shown literally), so the
 * safe, minimal fix is to LEAVE such labels as plain quoted strings rather than
 * force them through the HTML-like path. Trigger: a `<identifier>` port token.
 */
export function labelUsesRecordPortSyntax(labelContent: string): boolean {
    return /<\s*[A-Za-z_][\w]*\s*>/.test(labelContent);
}

/**
 * Convert DOT `label="..."` attributes to the more robust HTML-like `label=<...>`
 * form, HTML-escaping metacharacters and mapping graphviz justification escapes to
 * HTML-like <br> variants:
 *   \n -> <br/>                 (centre)      [pre-existing]
 *   \l -> <br align="left"/>    (D-132, left-justify)
 *   \r -> <br align="right"/>   (D-132, right-justify)
 * Record/port labels (D-131) are left as plain quoted strings so their `<port>`
 * tokens are consumed as port identifiers, not escaped into visible text. Pure and
 * idempotent; a label with no special content is byte-identical to the old output.
 */
export function convertLabelsToHtmlLike(dot: string): string {
    if (typeof dot !== 'string' || dot.length === 0) return dot;
    return dot.replace(/label\s*=\s*"((?:\\"|[^"])*)"/g, (match, content) => {
        // First, unescape any `\"` in the original content string.
        const unescapedContent = content.replace(/\\"/g, '"');

        // D-131: a record/port label must stay a plain quoted string so graphviz
        // parses its `<port>` tokens instead of us escaping them into text.
        if (labelUsesRecordPortSyntax(unescapedContent)) {
            return match;
        }

        // Escape for HTML-like label format, THEN map the justification escapes.
        // Order matters: the `<br .../>` markup is inserted after the </>/" escaping
        // steps so its own angle brackets and quotes survive intact.
        const escapedForHtml = unescapedContent
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/\\n/g, '<br/>').replace(/\n/g, '<br/>')
            .replace(/\\l/g, '<br align="left"/>')
            .replace(/\\r/g, '<br align="right"/>');

        return `label=<${escapedForHtml}>`;
    });
}

// D-136: light-theme border palette. The previous clusterBorder '#cccccc'
// (1.07:1 on the authored lightgrey #d3d3d3 cluster fill, 1.61:1 on white) and
// nodeBorder '#999999' (2.85:1 on white) both fell below the 3:1 graphical-
// boundary floor, so nested clusters collapsed into one flat box in light while
// dark (cyan #4cc9f0) rendered them perfectly. Darkened to clear 3:1 on the white
// page, the injected cluster/node fills AND the authored lightgrey cluster fill
// (#6e6e6e: 5.10 on #ffffff, 3.41 on #d3d3d3, 4.47 on #f0f0f0, 4.68 on #f5f5f5).
// Dark-theme borders are a separate palette entry and are UNCHANGED.
export const GRAPHVIZ_LIGHT_CLUSTER_BORDER = '#6e6e6e';
export const GRAPHVIZ_LIGHT_NODE_BORDER = '#6e6e6e';

const isGraphvizSpec = (spec: any): spec is GraphvizSpec => {
    // Handle JSON-wrapped graphviz specs
    if (typeof spec === 'object' && spec !== null && spec.type === 'graphviz' && spec.definition) {
        return typeof spec.definition === 'string' && spec.definition.trim().length > 0;
    }
    
    // Handle direct graphviz spec objects
    return (
        typeof spec === 'object' &&
        spec !== null &&
        spec.type === 'graphviz' &&
        typeof spec.definition === 'string' &&
        spec.definition.trim().length > 0
    );
};

// Store the current theme for each container to detect changes
const containerThemes = new WeakMap<HTMLElement, boolean>();

export const graphvizPlugin: D3RenderPlugin = {
    name: 'graphviz-renderer',
    priority: 5,
    sizingConfig: {
        sizingStrategy: 'auto-expand',
        needsDynamicHeight: true,
        needsOverflowVisible: true,
        observeResize: true,
        containerStyles: {
            width: '100%',
            height: 'auto',
            overflow: 'visible'
        }
    },
    
    canHandle: (spec: any): boolean => {
        // Handle JSON-wrapped graphviz specs like {"type": "graphviz", "definition": "..."}
        if (typeof spec === 'object' && spec !== null && spec.type === 'graphviz' && spec.definition) {
            return typeof spec.definition === 'string' && spec.definition.trim().length > 0;
        }
        
        // Handle direct graphviz spec objects
        if (isGraphvizSpec(spec)) {
            return true;
        }
        
        return false;
    },

    // Helper to check if a graphviz definition is complete
    isDefinitionComplete: (definition: string): boolean => {
        if (!definition || definition.trim().length === 0) return false;

        // Check for balanced braces which is a good indicator of completeness
        const openBraces = definition.split('{').length - 1;
        const closeBraces = definition.split('}').length - 1;

        // A complete definition should have balanced braces and end with a closing brace
        return openBraces === closeBraces && openBraces > 0 && definition.includes('}');
    },
    render: async (container: HTMLElement, d3: any, spec: GraphvizSpec, isDarkMode: boolean) => {
        try {
            // Lazy load Viz.js
            const Viz = await import('@viz-js/viz');
            
            // Handle JSON-wrapped specs vs direct definition strings
            let rawDefinition: string;
            
            // COMPREHENSIVE DEBUG: Log everything about the spec
            console.log('=== GRAPHVIZ DEBUG START ===');
            console.log('Spec type:', typeof spec);
            console.log('Spec is null:', spec === null);
            console.log('Spec keys:', spec ? Object.keys(spec) : 'N/A');
            console.log('Spec stringified:', JSON.stringify(spec, null, 2));
            console.log('Spec.definition exists:', !!(spec && typeof spec === 'object' && 'definition' in spec));
            console.log('Spec.definition type:', spec && typeof spec === 'object' && 'definition' in spec ? typeof spec.definition : 'N/A');
            console.log('Spec.definition value (first 200):', spec && typeof spec === 'object' && spec.definition ? spec.definition.substring(0, 200) : 'N/A');
            console.log('=== GRAPHVIZ DEBUG END ===');
            
            console.log('Graphviz render called with spec:', typeof spec, spec);
            
            if (typeof spec === 'object' && spec !== null && spec.definition) {
                // Use the definition directly if it exists in the spec object
                let def = spec.definition;
                
                // Handle double-wrapped JSON definitions
                if (typeof def === 'string' && def.trim().startsWith('{')) {
                    try {
                        const parsed = JSON.parse(def);
                        if (parsed.type === 'graphviz' && parsed.definition) {
                            rawDefinition = parsed.definition;
                            console.log('Extracted definition from double-wrapped JSON');
                        } else {
                            rawDefinition = def;
                            console.log('Using definition string as-is');
                        }
                    } catch {
                        rawDefinition = def;
                        console.log('JSON parse failed, using definition string as-is');
                    }
                } else {
                    rawDefinition = def;
                    console.log('Using definition directly from spec object');
                }
            } else if (typeof spec === 'string') {
                console.log('Processing string spec');
                // Try to parse as JSON first
                try {
                    const parsed = JSON.parse(spec);
                    if (parsed.definition) {
                        rawDefinition = parsed.definition;
                        console.log('Extracted definition from JSON string');
                    } else {
                        rawDefinition = extractDefinitionFromYAML(spec, 'graphviz');
                        console.log('Used YAML extraction from string');
                    }
                } catch {
                    rawDefinition = extractDefinitionFromYAML(spec, 'graphviz');
                    console.log('Used YAML extraction fallback');
                }
            } else {
                console.error('Invalid spec format:', spec);
                throw new Error('Invalid graphviz spec: no definition found');
            }
            
            console.log('Raw definition (first 200 chars):', rawDefinition.substring(0, 200));
            
            const hasExistingContent = container.querySelector('svg') !== null;
            // Show loading spinner immediately
            const loadingSpinner = document.createElement('div');
            loadingSpinner.innerHTML = `
                <div style="
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    padding: 2em;
                    width: 100%;
                    height: 100%;
                    min-height: 150px;
                ">
                    <div style="
                        border: 4px solid rgba(0, 0, 0, 0.1);
                        border-top: 4px solid ${isDarkMode ? '#4cc9f0' : '#3498db'};
                        border-radius: 50%;
                        width: 40px;
                        height: 40px;
                        animation: graphviz-spin 1s linear infinite;
                        margin-bottom: 15px;
                    "></div>
                    <div style="
                        font-family: system-ui, -apple-system, sans-serif;
                        color: ${isDarkMode ? '#eceff4' : '#333333'};
                    ">Rendering Visualization...</div>
                </div>
                <style>
                    @keyframes graphviz-spin {
                        0% { transform: rotate(0deg); }
                        100% { transform: rotate(360deg); }
                    }
            `;
            container.innerHTML = loadingSpinner.innerHTML;

            // Conservative streaming approach - only render when markdown block is closed
            // This allows the content to display as highlighted code during streaming
            if (!spec.isMarkdownBlockClosed && !spec.forceRender) {
                // If we already have a rendered diagram, preserve it — a late
                // re-render with stale flags must not destroy finished work.
                if (container.querySelector('svg') || container.querySelector('.diagram-actions')) {
                    return;
                }
                console.log('Graphviz: Markdown block still open, letting content display as code');
                container.innerHTML = '';
                return; // Exit early - let markdown renderer handle the streaming content
            }

            // Only proceed with rendering when we have a complete definition
            if (!rawDefinition || rawDefinition.trim().length < 10) {
                console.log('Graphviz: Definition too short, waiting for more content');
                return; // Exit early and wait for complete definition
            }

            // If we already have content and we're streaming, don't show errors
            if (hasExistingContent && spec.isStreaming) {
                return; // Keep existing content during streaming if definition is incomplete
            }
            console.log(`Rendering Graphviz diagram with ${rawDefinition.length} chars`);

            // Store the current theme for this container
            containerThemes.set(container, isDarkMode);

            // Enhanced theme colors with better contrast
            const themeColors = {
                light: {
                    text: '#333333',                // Darker text for better contrast
                    stroke: '#555555',              // Darker stroke
                    nodeFill: '#f5f5f5',            // Light gray node fill
                    nodeBorder: GRAPHVIZ_LIGHT_NODE_BORDER, // D-136: 3:1-safe node border
                    edgeColor: '#333333',           // Dark edge color for better visibility
                    background: 'transparent',
                    labelText: '#333333',           // Dark label text
                    clusterBg: '#f0f0f0',           // Cluster background
                    clusterBorder: GRAPHVIZ_LIGHT_CLUSTER_BORDER // D-136: 3:1-safe cluster border
                },
                dark: {
                    // Bright, happy colors for dark mode
                    text: '#ffffff',                // White text for high contrast
                    stroke: '#00b7ff',              // Bright cyan stroke
                    nodeFill: '#2a3990',            // Rich blue node fill
                    nodeBorder: '#4cc9f0',          // Bright blue node border
                    edgeColor: '#f72585',           // Vibrant pink edge color for high visibility
                    background: 'transparent',
                    labelText: '#ffffff',           // White text for labels
                    clusterBg: '#1a1a2e',           // Dark cluster background
                    clusterBorder: '#4cc9f0',       // Bright cluster border

                    // Alternative node colors for variety
                    nodeColors: [
                        '#4361ee',                  // Royal blue
                        '#3a0ca3',                  // Deep purple
                        '#7209b7',                  // Vibrant purple
                        '#f72585',                  // Hot pink
                        '#4cc9f0',                  // Bright cyan
                        '#06d6a0',                  // Mint green
                        '#118ab2',                  // Teal
                    ]
                }
            };

            const colors = isDarkMode ? themeColors.dark : themeColors.light;

            const vizInstance = await Viz.instance();

            // Extract actual content from YAML wrapper if present
            let processedDefinition = rawDefinition;

            // RECOVERY (G-16 D-127/D-128): lexical repair + colour normalisation
            // BEFORE any layout/theme work. Both are pure and idempotent, so a
            // clean spec is returned byte-identical; a lexically-broken spec
            // (fence / JSON envelope / smart or single quotes / unbalanced braces
            // / graph<->digraph edge mismatch / comma node-group) is recovered
            // rather than delivered as a silent 30s timeout, and an rgb()/token/
            // near-miss colour is normalised rather than collapsing to #000000.
            processedDefinition = repairGraphvizSource(processedDefinition);
            processedDefinition = normalizeGraphvizColors(processedDefinition);

            console.log('Starting with rawDefinition:', rawDefinition.substring(0, 100));
            console.log('processedDefinition initialized as:', processedDefinition.substring(0, 100));

            // STRESS-GUARD (Issue 5): clamp degenerate layout-magnitude attributes before
            // they reach the Viz.js/dot layout engine. `dot` inserts ONE virtual node per
            // rank an edge spans, and `minlen` sets that rank count -- so minlen=1000000
            // forces ~1e6 virtual nodes for a single edge and the (synchronous, watchdog-less)
            // WASM layout hangs unboundedly (no image, no error -> total data loss). `weight`
            // (network-simplex pull) and `peripheries` (nested border polygons per node) blow
            // up similarly. Clamping to sane maxima is visually indistinguishable for
            // legitimate specs (values within bounds pass through unchanged) and turns a
            // total-data-loss hang into a graceful render. General across every graphviz spec.
            // `width`/`height` are node dimensions in INCHES; with fixedsize=true a value like
            // width=10000 is a 10000-inch (=720000pt) box that blows up SVG/PNG rasterization
            // and hangs the render the same way. Clamp them (decimal-aware) to a sane maximum.
            processedDefinition = processedDefinition
                .replace(/(\bminlen\s*=\s*)(\d+)/gi, (_m, p, n) => p + Math.min(parseInt(n, 10), 50))
                .replace(/(\bweight\s*=\s*)(\d+)/gi, (_m, p, n) => p + Math.min(parseInt(n, 10), 1000))
                .replace(/(\bperipheries\s*=\s*)(\d+)/gi, (_m, p, n) => p + Math.min(parseInt(n, 10), 10))
                .replace(/(\b(?:width|height)\s*=\s*)(\d+(?:\.\d+)?)/gi, (_m, p, n) => p + Math.min(parseFloat(n), 100));

            // STRESS-GUARD (Issue 33): drop a degenerate `size=` graph attribute that
            // would scale the whole drawing to a sub-pixel canvas. Graphviz honors DOT
            // `size` verbatim and only scales DOWN to fit; `size="0.01,0.01"` (0.01in,
            // sub-pixel) + `ratio=fill` collapses everything to nothing -> a "successful"
            // render that produces a BLANK raster (silent data loss). Dropping the
            // sub-threshold size lets the graph render at natural size. Reasonable sizes
            // (>= GRAPHVIZ_MIN_SIZE_INCHES in either axis) pass through untouched.
            processedDefinition = clampGraphvizSize(processedDefinition);

            // Fix invalid arrow syntax and edge label format
            processedDefinition = processedDefinition.replace(
                /(\w+)\s*-\.->\s*(\w+)\s*\[([^\]]+)\]/g,
                '$1 -> $2 [$3]'
            );
            
            // Also fix any remaining -.-> arrows without attributes
            processedDefinition = processedDefinition.replace(/(\w+)\s*-\.->\s*(\w+)/g, '$1 -> $2');

            // Convert standard string labels to the more robust HTML-like label
            // format. Record/port labels are left as plain quoted strings (D-131)
            // and \l / \r justification escapes are mapped (D-132). See
            // convertLabelsToHtmlLike for the full contract.
            processedDefinition = convertLabelsToHtmlLike(processedDefinition);

            // Add theme attributes to dot with more styling options
            let themedDot = processedDefinition;
            
            console.log('themedDot before theme application:', themedDot.substring(0, 100));

            // Only add theme attributes if the graph has a proper structure
            if (processedDefinition.match(/^(\s*(?:di)?graph\s+[^{]*{)/)) {
                // Set default text color based on page mode
                const defaultTextColor = isDarkMode ? '#ffffff' : '#000000';

                themedDot = processedDefinition.replace(
                    /^(\s*(?:di)?graph\s+[^{]*{)/,
                    `$1
                    bgcolor="transparent";
                    node [color="${colors.nodeBorder}", style="filled", fillcolor="${colors.nodeFill}", penwidth=1.5];
                    edge [color="${colors.edgeColor}", fontcolor="${defaultTextColor}", penwidth=1.5];
                    graph [fontcolor="${defaultTextColor}", color="${colors.clusterBorder}", fontname="Arial"];`
                );
                // Handle graph label if present
                const labelMatch = spec.definition.match(/^\s*label\s*=\s*"([^"]+)"/m);
                if (labelMatch) {
                    const originalLabel = labelMatch[1];
                    themedDot = themedDot.replace(
                        /^\s*label\s*=\s*"([^"]+)"/m,
                        ` label=<<font color="${defaultTextColor}">${originalLabel}</font>>`
                    );
                }
            }

            console.log('Final themedDot being sent to Viz.js:', themedDot.substring(0, 100));
            console.log('themedDot full length:', themedDot.length);
            
            const element = await vizInstance.renderSVGElement(themedDot);

            // Apply theme to SVG elements with more specific styling
            const elements = element.getElementsByTagName('*');

            // First pass: Apply colors to nodes and collect background colors
            const nodeBackgroundColors = new Map(); // Map to store node background colors
            const clusterBackgroundColors = new Map(); // Map to store cluster background colors

            // First identify all clusters and their background colors
            for (let i = 0; i < elements.length; i++) {
                const el = elements[i];

                // Identify cluster backgrounds
                if (el.tagName === 'polygon' && el.parentElement && el.parentElement.classList.contains('cluster')) {
                    const originalFill = el.getAttribute('fill');
                    if (originalFill) {
                        clusterBackgroundColors.set(el.parentElement, originalFill);

                        // In dark mode, override light cluster backgrounds
                        if (isDarkMode) {
                            // Check if this is a light color that needs to be darkened
                            if (isLightBackground(originalFill)) {
                                // Use a darker color based on the original hue
                                const darkColor = getDarkVersionOfColor(originalFill);
                                el.setAttribute('fill', darkColor);
                                el.setAttribute('stroke', colors.clusterBorder);

                                // Store the fact that we changed this color
                                el.setAttribute('data-original-fill', originalFill);
                                el.setAttribute('data-darkened', 'true');
                            }
                        }
                    }
                }
            }

            // Then process nodes
            for (let i = 0; i < elements.length; i++) {
                const el = elements[i];

                if (el.tagName === 'ellipse' || el.tagName === 'polygon') {
                    // Node shapes
                    if (el.getAttribute('fill') !== 'none') {
                        // Store the original fill color before we modify it
                        const originalFill = el.getAttribute('fill');
                        if (originalFill) {
                            // Store the element and its original fill color
                            nodeBackgroundColors.set(el, originalFill);
                        }

                        // In dark mode, handle node colors
                        if (isDarkMode) {
                            // Check if this is a light color that needs to be darkened
                            if (originalFill && isLightBackground(originalFill)) {
                                // D-126: previously white / very-light fills were
                                // replaced by nodeColors[nodeIndex % 7] — POSITIONAL
                                // palette cycling with no notion of author intent.
                                // That turned a deliberate #ffffcc "warn" node into
                                // royal blue and, because an HTML-like table renders
                                // each cell as its own <polygon>, cycled a different
                                // colour through every cell (205 identical fills ->
                                // confetti). Since the plugin injects its own dark
                                // default fill, EVERY fill that reaches this branch is
                                // author-chosen, so we now darken it the SAME way as
                                // every other light fill: a pure, hue-preserving
                                // function of the input colour. Identical author fills
                                // therefore map to identical results (no confetti) and
                                // the hue is preserved (no arbitrary blue).
                                const darkColor = darkModeNodeFill(originalFill);
                                el.setAttribute('fill', darkColor);

                                // Store the fact that we changed this color
                                el.setAttribute('data-original-fill', originalFill);
                                el.setAttribute('data-darkened', 'true');

                                // D-133: the fill we just darkened may now sit under
                                // the author's (or the injected default) light-on-dark
                                // or dark-on-light label text. Re-theme this node's
                                // OWN <text> against the NEW fill so black author text
                                // is not stranded invisibly on a now-dark fill. Scoped
                                // to fills WE darkened, applied uniformly so siblings
                                // sharing a default get the same treatment.
                                retintNodeLabelForFill(el, darkColor);
                            }

                            // Set border color
                            el.setAttribute('stroke', colors.nodeBorder);
                            el.setAttribute('stroke-width', '1.5');
                        }
                    } else if (isDarkMode) {
                        // D-137: an unfilled node (fill="none") has NO fill to
                        // darken, so the dark branch above never re-themed its
                        // text. An authored #000000 fontcolor (or the graphviz
                        // node-text default, which is black) then sits on the
                        // ~#1e1e1e panel at 1.26:1. Re-theme this node's own
                        // <text> against the EFFECTIVE (panel) background so it
                        // is legible; text already light enough for the panel is
                        // left untouched (a deliberate readable choice is kept).
                        retintUnfilledNodeTextForDark(el);
                    }
                }
            }
            
            // Apply universal visibility enhancement
            setTimeout(() => {
                // D-125: skip edge-label text so the enhancer's arrowhead-as-
                // background misfire cannot repaint the (already theme-correct)
                // edge fontcolor to white-on-white in light mode.
                const result = enhanceSVGVisibility(element, isDarkMode, {
                    debug: true,
                    skipSelectors: GRAPHVIZ_ENHANCER_SKIP_SELECTORS,
                });
                console.log(`✅ Graphviz visibility enhanced:`, result);
            }, 300);
            
            // Apply edge and path styling
            for (let i = 0; i < elements.length; i++) {
                const el = elements[i];
                
                if (el.tagName === 'path') {
                    // Edge paths
                    if (!el.getAttribute('fill') || el.getAttribute('fill') === 'none') {
                        // Make sure edges are visible with high contrast color
                        el.setAttribute('stroke', colors.edgeColor);
                        el.setAttribute('stroke-width', '1.5');
                    }
                } else if (el.tagName === 'polygon' && el.classList.contains('arrow')) {
                    // This is an arrowhead
                    el.setAttribute('fill', colors.edgeColor);
                    el.setAttribute('stroke', colors.edgeColor);
                }
            }

            // Clear container and append SVG
            container.innerHTML = '';

            // Create wrapper div similar to mermaid plugin
            const wrapper = document.createElement('div');
            wrapper.className = 'graphviz-wrapper';
            wrapper.style.cssText = `
                width: 100%;
                max-width: 100%;
                overflow: auto;
                padding: 1em;
                display: flex;
                justify-content: center;
            `;

            // Add the SVG to the wrapper
            wrapper.appendChild(element);

            // G-56 (D-129/D-130/D-135): fit the laid-out drawing to the viewport.
            // Viz.js sizes the SVG in absolute points with no responsive
            // behaviour, so a large graph is CROPPED by the bounded capture
            // window and a small / size!-forced graph draws as a sub-pixel island
            // with no upscale; where it IS shrunk to fit there is no minimum
            // legible-font floor. Give the SVG a viewBox + preserveAspectRatio and
            // apply the shared fit plan: upscale a small graph to fill, shrink a
            // wider one to fit, and past the min-font floor stop shrinking and
            // SCROLL instead of dissolving the labels. The comfortable middle
            // range resolves to natural size (byte-equivalent to the old output).
            try {
                const svgEl = element as unknown as SVGSVGElement;
                const nat = readGraphvizNaturalSizePx((n) => svgEl.getAttribute(n));
                if (nat.w > 0 && nat.h > 0) {
                    if (!svgEl.getAttribute('viewBox')) {
                        svgEl.setAttribute(
                            'viewBox',
                            `0 0 ${nat.w / GRAPHVIZ_PT_TO_PX} ${nat.h / GRAPHVIZ_PT_TO_PX}`
                        );
                    }
                    svgEl.setAttribute('preserveAspectRatio', 'xMidYMid meet');
                    const containerW = container.clientWidth || 1280;
                    const plan = planGraphvizViewport(nat.w, nat.h, containerW);
                    // Make the SVG fluid: drop the fixed pt width/height and drive
                    // size via CSS so preserveAspectRatio scales the content.
                    svgEl.removeAttribute('width');
                    svgEl.removeAttribute('height');
                    svgEl.style.maxWidth = 'none';
                    svgEl.style.width = Math.max(1, Math.round(plan.svgWidthPx)) + 'px';
                    svgEl.style.height = 'auto';
                    if (plan.scroll) {
                        // Over-wide past the min-font floor: keep it legible and let
                        // the wrapper scroll rather than shrink the labels away.
                        wrapper.style.justifyContent = 'flex-start';
                    }
                }
            } catch (fitErr) {
                console.warn('Graphviz viewport fit skipped:', fitErr);
            }

            // Add wrapper to container
            container.appendChild(wrapper);

            // Add action buttons container
            const actionsContainer = document.createElement('div');
            actionsContainer.className = 'diagram-actions';

            // Add Open button
            const openButton = document.createElement('button');
            openButton.innerHTML = '↗️ Open';
            openButton.className = 'diagram-action-button graphviz-open-button';
            openButton.onclick = () => {
                // Get the SVG dimensions
                const svgGraphics = element as unknown as SVGGraphicsElement;
                let width = 600;
                let height = 400;

                try {
                    // Try to get the bounding box
                    const bbox = svgGraphics.getBBox();
                    width = Math.max(bbox.width + 50, 400); // Add padding, minimum 400px
                    height = Math.max(bbox.height + 100, 300); // Add padding, minimum 300px
                } catch (e) {
                    console.warn('Could not get SVG dimensions, using defaults', e);
                }

                // Create a new SVG with proper XML declaration and doctype
                const svgData = new XMLSerializer().serializeToString(element);

                // Create an HTML document that will display the SVG responsively
                const htmlContent = `
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <title>Graphviz Diagram</title>
                    <style>
                        body {
                            margin: 0;
                            padding: 0;
                            display: flex;
                            flex-direction: column;
                            height: 100vh;
                            background-color: #f8f9fa;
                            font-family: system-ui, -apple-system, sans-serif;
                        }
                        .toolbar {
                            background-color: #f1f3f5;
                            border-bottom: 1px solid #dee2e6;
                            padding: 8px;
                            display: flex;
                            justify-content: space-between;
                            align-items: center;
                        }
                        .toolbar button {
                            background-color: #4361ee;
                            color: white;
                            border: none;
                            border-radius: 4px;
                            padding: 6px 12px;
                            cursor: pointer;
                            margin-right: 8px;
                            font-size: 14px;
                        }
                        .toolbar button:hover {
                            background-color: #3a0ca3;
                        }
                        .container {
                            flex: 1;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            overflow: auto;
                            padding: 20px;
                        }
                        svg {
                            max-width: 100%;
                            max-height: 100%;
                            height: auto;
                            width: auto;
                        }
                        @media (prefers-color-scheme: dark) {
                            body {
                                background-color: #212529;
                                color: #f8f9fa;
                            }
                            .toolbar {
                                background-color: #343a40;
                                border-bottom: 1px solid #495057;
                            }
                        }
                    </style>
                </head>
                <body>
                    <div class="toolbar">
                        <div>
                            <button onclick="zoomIn()">Zoom In</button>
                            <button onclick="zoomOut()">Zoom Out</button>
                            <button onclick="resetZoom()">Reset</button>
                        </div>
                        <div>
                            <button onclick="downloadSvg()">Download SVG</button>
                        </div>
                    </div>
                    <div class="container" id="svg-container">
                        ${svgData}
                    </div>
                    <script>
                        // Make sure SVG is responsive
                        const svgEl = document.querySelector('svg');
                        svgEl.setAttribute('width', '100%');
                        svgEl.setAttribute('height', '100%');
                        svgEl.style.maxWidth = '100%';
                        svgEl.style.maxHeight = '100%';
                        svgEl.setAttribute('preserveAspectRatio', 'xMidYMid meet');
                        ${getZoomScript()}${getDownloadSvgScript('graphviz-diagram.svg')}
                    </script>
                </body>
                </html>
                `;

                // Create a blob with the HTML content
                const blob = new Blob([htmlContent], { type: 'text/html' });
                const url = URL.createObjectURL(blob);

                // Open in a new window with specific dimensions
                const popupWindow = window.open(
                    url,
                    'GraphvizDiagram',
                    `width=${width},height=${height},resizable=yes,scrollbars=yes,status=no,toolbar=no,menubar=no,location=no`
                );

                // Focus the new window
                if (popupWindow) {
                    popupWindow.focus();
                }

                // Clean up the URL object after a delay
                setTimeout(() => URL.revokeObjectURL(url), 10000);
            };
            actionsContainer.appendChild(openButton);

            // Add Save button
            const saveButton = document.createElement('button');
            saveButton.innerHTML = '💾 Save';
            saveButton.className = 'diagram-action-button graphviz-save-button';
            saveButton.onclick = () => {
                // Create a new SVG with proper XML declaration and doctype
                const svgData = new XMLSerializer().serializeToString(element);

                // Create a properly formatted SVG document with XML declaration
                const svgDoc = `<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
${svgData}`;

                // Create a blob with the SVG content
                const blob = new Blob([svgDoc], { type: 'image/svg+xml' });
                const url = URL.createObjectURL(blob);

                // Create a download link
                const link = document.createElement('a');
                link.href = url;
                link.download = `graphviz-diagram-${Date.now()}.svg`;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);

                // Clean up the URL object after a delay
                setTimeout(() => URL.revokeObjectURL(url), 1000);
            };
            actionsContainer.appendChild(saveButton);

            // Add Source button with toggle functionality like in mermaid plugin
            let showingSource = false;
            const originalContent = wrapper.innerHTML;
            const sourceButton = document.createElement('button');
            sourceButton.innerHTML = showingSource ? '🎨 View' : '📝 Source';
            sourceButton.className = 'diagram-action-button graphviz-source-button';
            sourceButton.onclick = () => {
                showingSource = !showingSource;
                sourceButton.innerHTML = showingSource ? '🎨 View' : '📝 Source';

                if (showingSource) {
                    wrapper.innerHTML = `
                        <div style="
                            background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
                            border: 1px solid ${isDarkMode ? '#303030' : '#e1e4e8'};
                            border-radius: 6px;
                            padding: 16px;
                            margin: 16px 0;
                        ">
                            <div style="
                                font-size: 12px;
                                color: ${isDarkMode ? '#8b949e' : '#586069'};
                                margin-bottom: 8px;
                                font-weight: bold;
                            ">
                                🔗 Graphviz Source:
                            </div>
                            <pre style="
                                margin: 0;
                                color: ${isDarkMode ? '#e6e6e6' : '#24292e'};
                                font-size: 13px;
                                line-height: 1.45;
                                white-space: pre-wrap;
                                word-break: break-word;
                                font-family: Monaco, Menlo, 'Ubuntu Mono', monospace;
                                max-height: 500px;
                                overflow: auto;
                            "><code>${escapeHtml(spec.definition ?? '')}</code></pre>
                        </div>
                    `;
                } else {
                    wrapper.innerHTML = originalContent;
                }
            };
            actionsContainer.appendChild(sourceButton);

            // Add actions container before the wrapper
            container.insertBefore(actionsContainer, wrapper);

            // Add a theme button to manually re-render with the opposite theme
            const themeButton = document.createElement('button');
            themeButton.innerHTML = isDarkMode ? '☀️ Light' : '🌙 Dark';
            themeButton.className = 'diagram-action-button graphviz-theme-button';
            themeButton.onclick = () => {
                // Re-render with the opposite theme
                graphvizPlugin.render(container, d3, spec, !isDarkMode);
            };
            actionsContainer.appendChild(themeButton);
        } catch (error) {
            console.error('Graphviz rendering error:', error);

            // Only show error if we're not streaming or if we have no existing content
            if (!spec.isStreaming || !container.querySelector('svg')) {
                container.innerHTML = `
                <div class="graphviz-error">
                    <strong>Graphviz Error:</strong>
                    <pre>${escapeHtml(error instanceof Error ? error.message : 'Unknown error')}</pre>
                    <details>
                        <summary>Show Definition</summary>
                        <pre><code>${escapeHtml(spec.definition ?? '')}</code></pre>
                    </details>
                </div>
            `;
            }
        }
    }
};

// Move existing helper functions to the end and keep the ones that are still used
// Helper function to get a dark version of a color
function getDarkVersionOfColor(color: string): string {
    // For named colors, map to dark equivalents
    const colorMap: Record<string, string> = {
        'white': '#2e3440',
        'lightblue': '#5e81ac',
        'lightgreen': '#8fbcbb',
        'lightgrey': '#4c566a',
        'lightgray': '#4c566a',
        'pink': '#b48ead'
    };

    // Check if we have a direct mapping
    if (colorMap[color.toLowerCase()]) {
        return colorMap[color.toLowerCase()];
    }

    // Otherwise, try to darken the color
    try {
        let r, g, b;

        if (color.startsWith('#')) {
            // Handle hex colors
            const hex = color.substring(1);
            if (hex.length === 3) {
                r = parseInt(hex[0] + hex[0], 16);
                g = parseInt(hex[1] + hex[1], 16);
                b = parseInt(hex[2] + hex[2], 16);
            } else {
                r = parseInt(hex.substring(0, 2), 16);
                g = parseInt(hex.substring(2, 4), 16);
                b = parseInt(hex.substring(4, 6), 16);
            }
        } else if (color.startsWith('rgb')) {
            // Handle rgb/rgba colors
            const match = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*[\d.]+)?\)/);
            if (match) {
                r = parseInt(match[1], 10);
                g = parseInt(match[2], 10);
                b = parseInt(match[3], 10);
            } else {
                return '#2e3440'; // Default dark color
            }
        } else {
            return '#2e3440'; // Default dark color
        }

        // Darken the color by reducing each component by 60%
        r = Math.max(Math.floor(r * 0.4), 0);
        g = Math.max(Math.floor(g * 0.4), 0);
        b = Math.max(Math.floor(b * 0.4), 0);

        // Convert back to hex
        return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
    } catch (e) {
        return '#2e3440'; // Default dark color if parsing fails
    }
}

/**
 * Deterministic dark-mode fill for an author-chosen light node fill (D-126).
 * A PURE function of the input colour — identical author fills map to identical
 * results, so a table's many same-coloured cells no longer become confetti and
 * a warm fill is never swapped for an arbitrary palette blue. (Replaces the old
 * positional `nodeColors[nodeIndex % 7]` cycling.)
 */
export function darkModeNodeFill(originalFill: string): string {
    return getDarkVersionOfColor(originalFill);
}

/**
 * Choose a label colour readable on `fillHex` (D-133). White text on a dark
 * fill, black text on a light fill — decided by perceived brightness, so the
 * choice tracks the ACTUAL fill rather than the raw isDarkMode flag.
 */
export function readableTextColorFor(fillHex: string): string {
    return getBrightness(fillHex) < 0.5 ? '#ffffff' : '#000000';
}

/**
 * Re-theme a node's own <text> children against a fill the plugin just changed
 * (D-133). Without this, when the dark loop darkens an author light fill, the
 * author's (or the injected default's) black label text is stranded invisibly
 * on the now-dark fill. Applied uniformly to every darkened node so siblings
 * sharing one default fontcolor are treated consistently.
 */
export function retintNodeLabelForFill(shapeEl: Element, fillHex: string): void {
    const group = shapeEl.parentElement;
    if (!group) return;
    const desired = readableTextColorFor(fillHex);
    const texts = group.getElementsByTagName('text');
    for (let i = 0; i < texts.length; i++) {
        texts[i].setAttribute('fill', desired);
    }
}

/**
 * Effective dark panel/page background the graphviz SVG composites over (the
 * plugin sets bgcolor=transparent, so nodes sit directly on the ~#1e1e1e panel).
 */
export const GRAPHVIZ_DARK_PANEL_BG = '#1e1e1e';

/**
 * Whether a node-<text> fill is too dark to read on the dark panel and should be
 * rescued (D-137). Missing fill (inherits black), the literal `black`, or a
 * parseable dark hex/rgb colour qualify. Other named colours are assumed to be a
 * deliberate, visible author choice and left alone.
 */
export function textNeedsDarkPanelRescue(cur: string | null): boolean {
    if (!cur) return true;
    const c = cur.trim().toLowerCase();
    if (c === 'black') return true;
    if (c.startsWith('#') || c.startsWith('rgb')) return getBrightness(cur) < 0.5;
    return false;
}

/**
 * Re-theme an UNFILLED node's <text> against the dark panel (D-137). The dark
 * node loop only re-themes text when it darkens a fill; a fill="none" node is
 * skipped, so an authored dark (e.g. #000000) fontcolor — or the graphviz
 * node-text default — is stranded on the ~#1e1e1e panel at 1.26:1. Only text
 * that is itself too dark to read on the panel is rescued to a readable colour
 * (white, 16.67:1); already-light author text is left as-is so a deliberate
 * readable choice is not clobbered. Dark-mode only (the caller gates on it).
 */
export function retintUnfilledNodeTextForDark(shapeEl: Element): void {
    const group = shapeEl.parentElement;
    if (!group) return;
    const readable = readableTextColorFor(GRAPHVIZ_DARK_PANEL_BG); // '#ffffff'
    const texts = group.getElementsByTagName('text');
    for (let i = 0; i < texts.length; i++) {
        if (textNeedsDarkPanelRescue(texts[i].getAttribute('fill'))) {
            texts[i].setAttribute('fill', readable);
        }
    }
}

// Helper function to calculate brightness of a color (needed for getDarkVersionOfColor compatibility)
function getBrightness(color: string): number {
    // Convert hex or named colors to RGB
    let r, g, b;

    if (color.startsWith('#')) {
        // Handle hex colors
        const hex = color.substring(1);
        if (hex.length === 3) {
            r = parseInt(hex[0] + hex[0], 16);
            g = parseInt(hex[1] + hex[1], 16);
            b = parseInt(hex[2] + hex[2], 16);
        } else {
            r = parseInt(hex.substring(0, 2), 16);
            g = parseInt(hex.substring(2, 4), 16);
            b = parseInt(hex.substring(4, 6), 16);
        }
    } else if (color.startsWith('rgb')) {
        // Handle rgb/rgba colors
        const match = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*[\d.]+)?\)/);
        if (match) {
            r = parseInt(match[1], 10);
            g = parseInt(match[2], 10);
            b = parseInt(match[3], 10);
        } else {
            // Can't parse, assume dark
            return 0;
        }
    } else {
        // Can't parse, assume dark
        return 0;
    }

    // Calculate perceived brightness using the formula:
    // (0.299*R + 0.587*G + 0.114*B)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
}
