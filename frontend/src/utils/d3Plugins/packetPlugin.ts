/**
 * General-purpose packet / byte-field / protocol frame diagram renderer.
 *
 * NOT tied to any specific protocol.  Everything about the diagram —
 * field names, widths, colors, grouping brackets — comes from the
 * JSON spec the caller provides.
 *
 * Supports:
 *   - Configurable bit-width per row (8, 16, 32, …)
 *   - Named color themes OR explicit hex per field/section
 *   - Left and right nestable bracket annotations
 *   - Auto-generated deterministic colors when none are specified
 *   - Dark-mode aware rendering via colorUtils
 */

import {
  getOptimalTextColor,
  hexToRgb,
  luminance,
  calculateContrastRatio,
} from '../colorUtils';

// ── Built-in semantic color themes ──────────────────────────────────────────
// Users can reference these by name OR supply arbitrary hex.
// Each theme has light and dark variants; the renderer picks the right one.

export interface ColorTriple {
  bg: string;
  border: string;
  text: string;
}

const THEMES_LIGHT: Record<string, ColorTriple> = {
  header:    { bg: '#E5E7EB', border: '#9CA3AF', text: '#374151' },
  transport: { bg: '#B2E0F0', border: '#4BA3C7', text: '#1A5276' },
  security:  { bg: '#F9E79F', border: '#D4AC0D', text: '#7D6608' },
  control:   { bg: '#D5F5E3', border: '#82E0AA', text: '#1E8449' },
  payload:   { bg: '#F2F3F4', border: '#BDC3C7', text: '#5D6D7E' },
  metadata:  { bg: '#D6EAF8', border: '#5DADE2', text: '#1B4F72' },
  reserved:  { bg: '#D4A5C7', border: '#9B59B6', text: '#4A235A' },
  error:     { bg: '#FADBD8', border: '#E74C3C', text: '#922B21' },
  network:   { bg: '#D1F2EB', border: '#48C9B0', text: '#0E6655' },
  highlight: { bg: '#7EC8E3', border: '#2E86AB', text: '#1A5276' },
  accent:    { bg: '#FDEBD0', border: '#F0B27A', text: '#935116' },
  purple:    { bg: '#E8DAEF', border: '#AF7AC5', text: '#6C3483' },
  dark:      { bg: '#2E86AB', border: '#1A5276', text: '#FFFFFF' },
};

const THEMES_DARK: Record<string, ColorTriple> = {
  header:    { bg: '#374151', border: '#6B7280', text: '#E5E7EB' },
  transport: { bg: '#1A5276', border: '#4BA3C7', text: '#D6EAF8' },
  security:  { bg: '#7D6608', border: '#D4AC0D', text: '#FEF9E7' },
  control:   { bg: '#1E8449', border: '#82E0AA', text: '#D5F5E3' },
  payload:   { bg: '#2C3E50', border: '#5D6D7E', text: '#D5D8DC' },
  metadata:  { bg: '#1B4F72', border: '#5DADE2', text: '#D6EAF8' },
  reserved:  { bg: '#4A235A', border: '#9B59B6', text: '#E8DAEF' },
  error:     { bg: '#922B21', border: '#E74C3C', text: '#FADBD8' },
  network:   { bg: '#0E6655', border: '#48C9B0', text: '#D1F2EB' },
  highlight: { bg: '#2E86AB', border: '#7EC8E3', text: '#D6EAF8' },
  accent:    { bg: '#935116', border: '#F0B27A', text: '#FDEBD0' },
  purple:    { bg: '#6C3483', border: '#AF7AC5', text: '#E8DAEF' },
  dark:      { bg: '#1A5276', border: '#2E86AB', text: '#D6EAF8' },
};

// Deterministic palette for auto-assignment when no color is specified.
// Spread across hue space so adjacent sections don't clash.
// D-183: the auto-palette recycles by `autoIndex % len`, so a diagram with
// many sections lands multiple sections on this list. Two entries here were
// same-hue near-duplicates of earlier slots — index 5 (#D1F2EB, hue 167°) was
// a teal-green all-but-identical to index 2's green (#D5F5E3, 21° apart, fill
// contrast 1.02), and index 9 (#A9DFBF) was a THIRD green (hue 144°) — so at
// ~6+ sections two bands became indistinguishable. WCAG contrast is a poor
// distinctness metric for equal-lightness pastels (blue vs yellow reads 1.14
// yet is obviously distinct), so the fix restores HUE separation: the two
// duplicate slots are recoloured to fill the empty lime (~95°) and periwinkle
// (~242°) gaps, giving every slot >=37° hue separation from all others while
// staying pastel with dark, >=4.5:1 label text. Only the LATER duplicate slots
// (5, 9) changed, so any diagram with <=5 sections is byte-identical.
const AUTO_PALETTE_LIGHT: ColorTriple[] = [
  { bg: '#B2E0F0', border: '#4BA3C7', text: '#1A5276' },
  { bg: '#F9E79F', border: '#D4AC0D', text: '#7D6608' },
  { bg: '#D5F5E3', border: '#82E0AA', text: '#1E8449' },
  { bg: '#FADBD8', border: '#E74C3C', text: '#922B21' },
  { bg: '#E8DAEF', border: '#AF7AC5', text: '#6C3483' },
  { bg: '#D8F1C6', border: '#69AC39', text: '#3A631D' }, // lime (was #D1F2EB, a dup green of idx 2)
  { bg: '#FDEBD0', border: '#F0B27A', text: '#935116' },
  { bg: '#D6EAF8', border: '#5DADE2', text: '#1B4F72' },
  { bg: '#D4A5C7', border: '#9B59B6', text: '#4A235A' },
  { bg: '#D1D0F1', border: '#5C59C0', text: '#2C297A' }, // periwinkle (was #A9DFBF, a 3rd green)
];

// D-183 (dark side): index 7 (#1B4F72, hue 204°) was a near-identical blue to
// index 0 (#1A5276, 1° apart, fill contrast 1.04) and index 9 (#196F3D, hue
// 145°) was an identical-hue green to index 2 (#1E8449, 0° apart), so recycled
// dark bands blurred together. The two later duplicate slots are recoloured to
// the empty lime (~95°) and indigo (~243°) gaps — every slot now has >=40° hue
// separation from all others, staying deep with light, >=4.5:1 label text.
// Only slots 7 and 9 changed; diagrams with <=7 sections are byte-identical.
const AUTO_PALETTE_DARK: ColorTriple[] = [
  { bg: '#1A5276', border: '#4BA3C7', text: '#D6EAF8' },
  { bg: '#7D6608', border: '#D4AC0D', text: '#FEF9E7' },
  { bg: '#1E8449', border: '#82E0AA', text: '#D5F5E3' },
  { bg: '#922B21', border: '#E74C3C', text: '#FADBD8' },
  { bg: '#6C3483', border: '#AF7AC5', text: '#E8DAEF' },
  { bg: '#0E6655', border: '#48C9B0', text: '#D1F2EB' },
  { bg: '#935116', border: '#F0B27A', text: '#FDEBD0' },
  { bg: '#416F20', border: '#82CB4D', text: '#D9EBCB' }, // lime (was #1B4F72, a dup blue of idx 0)
  { bg: '#4A235A', border: '#9B59B6', text: '#E8DAEF' },
  { bg: '#312D76', border: '#7E79D2', text: '#D4D3EE' }, // indigo (was #196F3D, a dup green of idx 2)
];

// ── Public types ────────────────────────────────────────────────────────────

export interface PacketBracket {
  /** 0-based row index within this section where the bracket starts */
  start_row: number;
  /** 0-based row index within this section where the bracket ends (inclusive) */
  end_row: number;
  /** Short label displayed alongside the bracket */
  label: string;
  /** Which side of the grid: 'left' or 'right' (default 'right') */
  side?: 'left' | 'right';
  /** Nesting depth (0 = closest to grid).  Auto-computed when omitted. */
  depth?: number;
}

export interface PacketSection {
  /** Label shown to the left of this section.  Supports \n for 2-line labels. */
  label: string;
  /** Named theme key OR explicit {bg, border, text} triple */
  color?: string | ColorTriple;
  /** Rows of fields.  Each field: [name, bitWidth] or [name, bitWidth, colorOverride] */
  rows: Array<Array<[string, number] | [string, number, string | ColorTriple]>>;
  /** Optional bracket annotations */
  brackets?: PacketBracket[];
}

export interface PacketSpec {
  type: 'packet';
  /** Diagram title */
  title: string;
  /** Subtitle / description line */
  subtitle?: string;
  /** Bits per row.  Default 8.  Use 32 for classic RFC style. */
  bitWidth?: number;
  /** Ordered list of protocol layer sections */
  sections: PacketSection[];
}

// ── Layout constants (all in px, overridable via spec in future) ────────────

export interface LayoutConfig {
  BIT_W: number;
  ROW_H: number;
  LABEL_W: number;
  BRACKET_W: number;
  HEADER_H: number;
  SECTION_GAP: number;
  LEFT_PAD: number;
  TOP_PAD: number;
  TITLE_H: number;
  SUBTITLE_H: number;
}

/**
 * Maximum field bit-width the renderer will honour for geometry. A field's
 * pixel width is `bits * BIT_W`; SVG rejects negative `width`/`x` and any
 * non-finite value ("Infinity"). A single degenerate field bit-width (e.g.
 * -8 → negative rect width that silently fails to paint, or 1e308 →
 * `1e308 * BIT_W` overflows Number.MAX_VALUE to Infinity, corrupting the
 * shared bit-offset accumulator and bleeding sibling fields off-canvas)
 * therefore causes silent per-field data loss and layout corruption.
 * 65536 bits is far beyond any real protocol field yet keeps
 * `bits * BIT_W` (max 56) safely under Number.MAX_SAFE_INTEGER.
 */
export const PACKET_MAX_FIELD_BITS = 65536;

/**
 * Coerce a field bit-width to a value safe to feed SVG geometry arithmetic
 * (`fx = base + off * BIT_W`, `fw = bits * BIT_W`). Pure and DOM-free so it
 * is unit-testable in isolation.
 *
 * Rules (general, spec-agnostic — a no-op for every well-formed spec whose
 * field widths are small positive integers):
 *   - non-finite (NaN, Infinity, null→NaN) or non-number → 0 (renders as an
 *     invisible zero-width marker instead of an invalid attribute)
 *   - negative                                            → 0 (SVG forbids negative width)
 *   - > PACKET_MAX_FIELD_BITS                             → clamped to the cap
 *   - otherwise                                           → unchanged
 */
export function sanitizeFieldBits(bits: unknown): number {
  const n = typeof bits === 'number' ? bits : Number(bits);
  if (!Number.isFinite(n) || n <= 0) return 0;
  return Math.min(n, PACKET_MAX_FIELD_BITS);
}

/**
 * Default row bit-width when the spec omits (or supplies a degenerate)
 * `bitWidth`, and the maximum honoured. A packet diagram semantically assumes
 * an INTEGER bit width: the value drives the ruler tick loop
 * (`for (b = 0; b < bits; b++)` labelling `bits - 1 - b`) and the grid width
 * (`GRID_W = bits * BIT_W`).
 */
export const PACKET_DEFAULT_BIT_WIDTH = 8;
export const PACKET_MAX_BIT_WIDTH = 512;

/**
 * Coerce a top-level `bitWidth` to a POSITIVE INTEGER safe to feed the ruler
 * and grid geometry. Pure and DOM-free so it is unit-testable in isolation.
 *
 * A fractional `bitWidth` (e.g. 31.5) leaks straight into the ruler tick loop:
 * the loop runs `Math.ceil(bits)` times and prints `bits - 1 - b`, producing
 * nonsensical fractional, mutually-overlapping tick labels ("30.5 29.5 … 0.5
 * -0.5") — an illegible ruler plus a spurious negative final tick. Rounding to
 * an integer restores a clean 0..N-1 ruler.
 *
 * Rules (general, spec-agnostic — a no-op for every well-formed integer width):
 *   - non-finite (NaN, Infinity, null→NaN) or non-number → default (8)
 *   - < 1                                                → default (8)
 *   - fractional                                         → rounded to nearest int
 *   - > PACKET_MAX_BIT_WIDTH                             → clamped to the cap
 */
export function sanitizePacketBitWidth(bitWidth: unknown): number {
  const n = typeof bitWidth === 'number' ? bitWidth : Number(bitWidth);
  if (!Number.isFinite(n) || n < 1) return PACKET_DEFAULT_BIT_WIDTH;
  return Math.min(Math.round(n), PACKET_MAX_BIT_WIDTH);
}

export function defaultLayout(bitWidth: number): LayoutConfig {
  // Scale bit cell width so total grid stays reasonable
  const BIT_W = bitWidth <= 8 ? 56 : bitWidth <= 16 ? 36 : bitWidth <= 32 ? 24 : 16;
  return {
    BIT_W,
    ROW_H: 34,
    LABEL_W: 180,
    BRACKET_W: 44,
    HEADER_H: 22,
    SECTION_GAP: 3,
    LEFT_PAD: 10,
    TOP_PAD: 10,
    TITLE_H: 26,
    SUBTITLE_H: 16,
  };
}

// ── Color resolution ────────────────────────────────────────────────────────

/**
 * Theme page background the packet diagram is drawn on. A field whose fill is
 * `transparent`/`none` shows THIS colour through, so it is the surface the
 * label text actually sits on. Ziya's dark canvas is #1e1e1e; light is white.
 */
export const PACKET_PAGE_BG_LIGHT = '#ffffff';
export const PACKET_PAGE_BG_DARK = '#1e1e1e';

/**
 * True when a fill value paints nothing, so the themed canvas shows through:
 * the `transparent`/`none` keywords, an empty/absent value, or a zero-alpha
 * `rgba(...)`. Case- and whitespace-tolerant.
 */
export function isSeeThroughFill(bg: string | undefined | null): boolean {
  if (bg == null) return true;
  if (typeof bg !== 'string') return false;
  const v = bg.trim().toLowerCase();
  if (v === '' || v === 'transparent' || v === 'none') return true;
  // rgba(r,g,b,0) / rgba(r,g,b, 0.0) - a fully transparent explicit colour.
  const m = v.match(/^rgba?\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+\s*,\s*([\d.]+)\s*\)$/);
  if (m) return !(parseFloat(m[1]) > 0);
  return false;
}

/**
 * Resolve the ACTUAL backdrop a field label sits on: for a see-through fill
 * (`transparent`/`none`/empty/zero-alpha) that is the themed `canvas`, not the
 * keyword; for any opaque fill it is the fill itself, returned unchanged.
 *
 * D-208 / D-227: the theme-blind colour helpers treat `transparent` as white
 * (namedColorToHex: transparent -> #ffffff), so a label derived directly from
 * the fill commits to black - correct on the white light canvas, invisible on
 * the #1e1e1e dark canvas (black-on-dark = 1.26:1). Routing the label backdrop
 * through the real themed canvas fixes dark WITHOUT touching light.
 */
export function effectiveCellBackdrop(fill: string | undefined | null, canvas: string): string {
  return isSeeThroughFill(fill) ? canvas : (fill as string);
}

/**
 * Pick a readable label colour for a given field fill under the current theme,
 * resolving a see-through fill against the theme page background first (D-208).
 */
export function textColorForFill(bg: string | undefined, isDarkMode: boolean): string {
  const canvas = isDarkMode ? PACKET_PAGE_BG_DARK : PACKET_PAGE_BG_LIGHT;
  return getOptimalTextColor(effectiveCellBackdrop(bg, canvas));
}

/** Resolve a color spec to a concrete triple for the current theme. */
export function resolveColor(
  color: string | ColorTriple | undefined,
  isDarkMode: boolean,
  autoIndex: number,
): ColorTriple {
  if (!color) {
    // Auto-assign from rotating palette
    const palette = isDarkMode ? AUTO_PALETTE_DARK : AUTO_PALETTE_LIGHT;
    return palette[autoIndex % palette.length];
  }
  if (typeof color === 'object') {
    // Explicit triple — adapt the BORDER for contrast against the ACTIVE theme
    // canvas (D-454). An author who hardcodes a near-white fill (#FAFAFA/#DDD
    // border) is legible on the light page but dissolves into the #1e1e1e dark
    // canvas — and a near-black palette (#1A1A1A/#333) is the mirror on dark.
    // The FILL is left as authored (their intent), but when the border fails
    // the 3:1 graphical-object floor BOTH against the themed canvas AND against
    // its own fill, the cell has no visible boundary in that theme, so its
    // extents/separators vanish. Substitute a theme outline that clears 3:1 on
    // the current canvas so cell geometry is always drawn. Per-theme and
    // asymmetric: the theme where the author border already clears 3:1 (light
    // for #333, dark for #DDD) is returned unchanged.
    const canvas = isDarkMode ? PACKET_PAGE_BG_DARK : PACKET_PAGE_BG_LIGHT;
    let border = color.border;
    if (border) {
      const vsCanvas = calculateContrastRatio(border, canvas);
      const vsFill = color.bg ? calculateContrastRatio(border, color.bg) : 0;
      if (!(vsCanvas >= 3 || vsFill >= 3)) {
        // #6B7280 clears 4.83:1 on white / 4.63:1 on #FAFAFA (light);
        // #a0a0a0 clears 6.38:1 on #1e1e1e / 6.66:1 on #1A1A1A (dark).
        border = isDarkMode ? '#a0a0a0' : '#6B7280';
      }
    }
    return {
      bg: color.bg,
      border,
      // D-208: a transparent/none fill shows the THEME page, not a white page —
      // resolve the label colour against the theme background so it stays
      // readable in dark as well as light.
      text: color.text || textColorForFill(color.bg, isDarkMode),
    };
  }
  // Named theme
  const themes = isDarkMode ? THEMES_DARK : THEMES_LIGHT;
  if (themes[color]) return themes[color];
  // A bare `transparent`/`none` fill keyword: keep the fill transparent so the
  // page shows through, but pick the label colour against the theme page.
  if (isSeeThroughFill(color)) {
    return {
      bg: color,
      border: '#888888',
      text: textColorForFill(color, isDarkMode),
    };
  }
  // Treat as a hex background color, derive the rest
  if (color.startsWith('#')) {
    return {
      bg: color,
      border: darkenHex(color, 0.3),
      text: getOptimalTextColor(color),
    };
  }
  // Unknown string → fall back to auto
  const palette = isDarkMode ? AUTO_PALETTE_DARK : AUTO_PALETTE_LIGHT;
  return palette[autoIndex % palette.length];
}

/** Darken a hex color by a factor (0–1). */
function darkenHex(hex: string, factor: number): string {
  const rgb = hexToRgb(hex);
  if (!rgb) return hex;
  const r = Math.round(rgb.r * (1 - factor));
  const g = Math.round(rgb.g * (1 - factor));
  const b = Math.round(rgb.b * (1 - factor));
  return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
}

// ── Dimension calculation ───────────────────────────────────────────────────

/**
 * Line height (px) used when stacking a multi-line section label. Shared by the
 * section-height math here and the renderer's label-draw loop so the computed
 * SVG height and the drawn label can never disagree (D-180).
 */
export const SECTION_LABEL_LINE_H = 14;

/**
 * Pixel height a section occupies: the GREATER of its row block
 * (`rowCount * ROW_H`) and the vertical room a multi-line section LABEL needs
 * (D-180). Pure and DOM-free so computeDimensions (SVG height) and the
 * renderer's per-section draw math consume ONE source of truth.
 *
 * A section label may carry `\n`-separated lines; the renderer stacks them at
 * SECTION_LABEL_LINE_H each, centered on the section. Sizing the section only
 * from `rowCount * ROW_H` (the old behaviour) let a tall label (e.g. 60 lines
 * on a 2-row section) overrun the computed viewBox bottom and overprint the
 * bottom ruler — silent content loss. Reserving `lines * LINE_H + pad` when the
 * label is taller than the rows fixes both.
 *
 * Strict no-op for the common case: a single-line label needs no extra room, so
 * the result is exactly `rowCount * ROW_H` and every well-formed spec is
 * byte-identical.
 */
export function sectionContentHeight(rowCount: number, labelLineCount: number, L: LayoutConfig): number {
  const rowsH = Math.max(0, rowCount) * L.ROW_H;
  const lines = Math.max(1, Math.floor(labelLineCount) || 1);
  // A single line always fits inside any row height, so only multi-line labels
  // can force the section taller. +8 keeps a small top/bottom margin.
  const labelH = lines > 1 ? lines * SECTION_LABEL_LINE_H + 8 : 0;
  return Math.max(rowsH, labelH);
}

/** Sum of every section's content height (row block vs. multi-line label). */
function sectionsBlockHeight(sections: PacketSection[], L: LayoutConfig): number {
  return sections.reduce(
    (h, s) => h + sectionContentHeight(
      s.rows?.length ?? 0, sectionLabel(s).split('\n').length, L),
    0);
}

/**
 * Widest ROW, in bits: the maximum over every row of the sum of its (sanitized)
 * field bit-widths. A row whose fields sum to MORE than the declared `bitWidth`
 * (a `packet-beta` field wider than the row, a JSON spec that omits bitWidth so
 * it defaults to 8 while its rows carry 16/32 bits) draws field rects out to
 * `sum * BIT_W` — past `bits * BIT_W`. Pure / DOM-free.
 */
export function maxRowBits(sections: PacketSection[] | undefined): number {
  let m = 0;
  for (const sec of sections ?? []) {
    const rows = normalizeSectionRows(sec?.rows);
    for (const row of rows) {
      let sum = 0;
      for (const f of row) sum += sanitizeFieldBits((f as any)[1]);
      if (sum > m) m = sum;
    }
  }
  return m;
}

/**
 * Hard ceiling (px) on the packet GRID width. A grid wider than this is not
 * downscaled reliably by the headless element-screenshot path (a surface past
 * ~6000px, or a single multi-thousand-px field rect, returns a blank
 * viewport-wide strip — D-451). Capping the grid here keeps the whole diagram
 * inside a capturable, paintable surface WITHOUT relying on post-capture
 * downscale: the bit-cell width shrinks (to a fractional px if need be) so
 * `gridBits * BIT_W <= PACKET_MAX_GRID_PX`.
 */
export const PACKET_MAX_GRID_PX = 6000;

/**
 * Single source of truth for the packet grid geometry, shared by
 * computeDimensions (SVG/viewBox width) and the render plugin (field/ruler
 * placement) so the declared viewBox can NEVER be narrower than the drawn
 * content (the D-451 "content outside the viewBox → blank capture" family) and
 * can never exceed the capture ceiling (D-451 wide-surface blank strips).
 *
 * - `bits`     — the declared/sanitized row bit-width (drives the ruler labels).
 * - `gridBits` — max(bits, widest row): how many bit-cells the grid must span
 *                to enclose every field rect.
 * - `BIT_W`    — per-bit pixel width; shrunk (fractional allowed) only when
 *                `gridBits * baseBIT_W` would exceed PACKET_MAX_GRID_PX.
 * - `GRID_W`   — `gridBits * BIT_W`, always <= PACKET_MAX_GRID_PX.
 *
 * Pure / DOM-free. A strict no-op for well-formed specs whose rows fill exactly
 * `bitWidth` and whose grid is under the ceiling: gridBits === bits and BIT_W
 * is unchanged, so `GRID_W === bits * baseBIT_W` exactly as before.
 */
export function computeGridMetrics(spec: PacketSpec): { bits: number; gridBits: number; BIT_W: number; GRID_W: number } {
  const bits = sanitizePacketBitWidth(spec.bitWidth);
  const baseBIT_W = defaultLayout(bits).BIT_W;
  const gridBits = Math.max(1, Math.max(bits, maxRowBits(spec.sections)));
  let BIT_W = baseBIT_W;
  if (gridBits * BIT_W > PACKET_MAX_GRID_PX) {
    BIT_W = PACKET_MAX_GRID_PX / gridBits;
  }
  return { bits, gridBits, BIT_W, GRID_W: gridBits * BIT_W };
}

export function computeDimensions(spec: PacketSpec): { width: number; height: number; layout: LayoutConfig } {
  // Coerce to a positive integer so the grid width and the ruler agree with the
  // plugin (which sanitizes identically) — a fractional/degenerate bitWidth can
  // never drive geometry. The grid may be WIDER than bits*BIT_W (a row wider
  // than bitWidth) and its BIT_W may be shrunk to hold the capture ceiling; the
  // shared computeGridMetrics is the single source both consume.
  const { bits, GRID_W, BIT_W } = computeGridMetrics(spec);
  const L: LayoutConfig = { ...defaultLayout(bits), BIT_W };

  const sections = spec.sections ?? [];
  const numSections = sections.length;
  // Gutter widths on each side (shared with the renderer via a single helper
  // so layout sizing and drawing can never drift out of agreement).
  const { left: bracketLeftW, right: bracketRightW } = computeBracketGutters(sections, L);
  const width = L.LEFT_PAD + bracketLeftW + L.LABEL_W + GRID_W + bracketRightW + L.LEFT_PAD;
  const subtitleH = spec.subtitle ? L.SUBTITLE_H + 6 : 6;
  const height =
    L.TOP_PAD + L.TITLE_H + subtitleH +
    L.HEADER_H +
    sectionsBlockHeight(sections, L) +
    Math.max(0, numSections - 1) * L.SECTION_GAP +
    L.HEADER_H + L.TOP_PAD;

  return { width, height, layout: L };
}

// ── Bracket sanitization ────────────────────────────────────────────────────

/**
 * Maximum bracket nesting depth the renderer will reserve gutter space for.
 * Bracket depth is auto-assigned so that mutually-overlapping ranges get
 * distinct depths; N brackets that all share the SAME range therefore get N
 * DISTINCT depths (0..N-1). The left/right gutter width grows linearly with
 * max depth (`depth * BRACKET_W`), so an adversarial spec with 20+ overlapping
 * identical-range brackets balloons the gutter by hundreds of px, shoving the
 * packet grid to the far side of the canvas and stranding the bracket cluster
 * in a disjoint blob (the Issue-24 "layout explosion"). Capping the depth at a
 * value far beyond any real protocol annotation (deeper nesting than this is
 * visually indistinguishable anyway) bounds the gutter. No-op for any spec
 * whose real nesting is shallower than the cap.
 */
export const PACKET_MAX_BRACKET_DEPTH = 6;

/**
 * Maximum section-label width (px) the left-bracket gutter will reserve.
 * Left brackets are placed relative to the widest section label
 * (`bx = gridX - 16 - maxLabelW - offset`); an unbounded 300+ char label
 * therefore pushes them arbitrarily far left, off the content box. Capping the
 * contribution keeps the gutter bounded while still giving normal labels room.
 */
export const PACKET_MAX_LABEL_GUTTER_W = 320;

/**
 * Coerce a bracket's row indices, depth and side into values safe to feed the
 * layout/geometry math, clamped to a section that has `rowCount` rows. Pure and
 * DOM-free so it is unit-testable in isolation.
 *
 * A bracket's vertical extent is `secY + start_row*ROW_H` .. `secY +
 * (end_row+1)*ROW_H`. Unvalidated indices (`start_row:-5`/`end_row:999`,
 * `9999`/`10005`), inverted ranges (`start_row:2 > end_row:0`), a non-numeric
 * or negative `depth` ("3"/-7), and an invalid `side` ("top") all produce
 * bracket paths and gutter reservations far outside the diagram bounds.
 *
 * Rules (general, spec-agnostic — a no-op for every well-formed bracket):
 *   - `start_row`/`end_row`: coerced to integers, clamped to [0, rowCount-1]
 *     (or [0,0] when rowCount<=0, e.g. a section with no rows), then reordered
 *     so start <= end (an inverted range is silently corrected, not dropped).
 *   - `depth` (when present): non-finite/non-number → dropped (auto-assigned
 *     later); negative → 0; otherwise floored to an integer and capped at
 *     PACKET_MAX_BRACKET_DEPTH.
 *   - `side`: anything other than 'left'/'right' → 'right' (the documented
 *     default).
 *   - `label`: coerced to a string (a non-string label would crash `.length`).
 */
export function sanitizeBracket(br: any, rowCount: number): PacketBracket {
  const maxRow = rowCount > 0 ? rowCount - 1 : 0;
  const clampRow = (v: any): number => {
    const n = Math.floor(Number(v));
    if (!Number.isFinite(n)) return 0;
    return Math.max(0, Math.min(maxRow, n));
  };
  let s = clampRow(br?.start_row);
  let e = clampRow(br?.end_row);
  if (s > e) { const t = s; s = e; e = t; }

  const out: PacketBracket = {
    start_row: s,
    end_row: e,
    label: typeof br?.label === 'string' ? br.label : String(br?.label ?? ''),
    side: br?.side === 'left' || br?.side === 'right' ? br.side : 'right',
  };

  if (br?.depth !== undefined && br?.depth !== null) {
    const d = Math.floor(Number(br.depth));
    if (Number.isFinite(d)) {
      out.depth = Math.max(0, Math.min(PACKET_MAX_BRACKET_DEPTH, d));
    }
  }
  return out;
}

/**
 * Sanitize every bracket in a section against that section's row count.
 * Convenience wrapper over `sanitizeBracket`.
 */
export function sanitizeBrackets(brackets: any[] | undefined, rowCount: number): PacketBracket[] {
  if (!Array.isArray(brackets)) return [];
  return brackets.map(b => sanitizeBracket(b, rowCount));
}

// ── Bracket depth auto-computation ──────────────────────────────────────────

/**
 * Assign nesting depths to brackets on one side so overlapping ranges
 * don't collide.  Innermost brackets get depth 0 (closest to grid).
 * Auto-assigned depths are capped at PACKET_MAX_BRACKET_DEPTH so a pile of
 * overlapping identical-range brackets cannot balloon the gutter without bound.
 */
export function assignBracketDepths(brackets: PacketBracket[], side: 'left' | 'right'): PacketBracket[] {
  const sideBrackets = brackets
    .filter(b => (b.side ?? 'right') === side)
    .sort((a, b) => {
      // Sort by span size ascending — smaller spans are innermost
      const spanA = a.end_row - a.start_row;
      const spanB = b.end_row - b.start_row;
      return spanA - spanB || a.start_row - b.start_row;
    });

  const assigned: Array<PacketBracket & { depth: number }> = [];

  for (const br of sideBrackets) {
    // Find the minimum depth that doesn't overlap any already-assigned bracket
    let depth = 0;
    while (depth < PACKET_MAX_BRACKET_DEPTH) {
      const conflict = assigned.some( // eslint-disable-line no-loop-func -- synchronous callback, depth is correct per-iteration
        a => a.depth === depth &&
          a.start_row <= br.end_row &&
          a.end_row >= br.start_row
      );
      if (!conflict) break;
      depth++;
    }
    // Cap the depth: beyond PACKET_MAX_BRACKET_DEPTH, additional overlapping
    // brackets share the deepest lane rather than pushing the gutter (and the
    // grid) ever further out. Label-overlap shifting still keeps their labels
    // legible; the point is that the GUTTER width stays bounded.
    assigned.push({ ...br, depth: Math.min(depth, PACKET_MAX_BRACKET_DEPTH), side });
  }

  return assigned;
}

// Approximate glyph widths for the section-label fonts (bold 13px main line,
// 10px sub lines) so bracket placement needs no DOM measurement.
const SECTION_MAIN_CHAR_W = 8;
const SECTION_SUB_CHAR_W = 6;

/**
 * Estimate the rendered pixel width of a section label (multi-line via \n;
 * the first line renders bold/larger than subsequent lines).
 */
export function estimateSectionLabelWidth(label: string): number {
  if (!label) return 0;
  return label.split('\n').reduce((w, ln, i) =>
    Math.max(w, ln.length * (i === 0 ? SECTION_MAIN_CHAR_W : SECTION_SUB_CHAR_W)), 0);
}

// ── Bracket label orientation ───────────────────────────────────────────────

/** Font size used for horizontal (unrotated) bracket labels. */
export const BRACKET_LABEL_FONT = 11;
/** Bold 11px monospace advance width, approximated (no DOM measurement). */
const BRACKET_LABEL_CHAR_W = 6.6;
/**
 * Widest horizontal bracket label allowed before falling back to rotation.
 * Rotated text is markedly harder to read and forces a smaller font, so
 * horizontal is the default; rotation is reserved for labels long enough that
 * laying them out horizontally would dominate the canvas.
 */
export const PACKET_MAX_HORIZ_BRACKET_LABEL_W = 220;

/** Rotated-label sizing (the fallback path): base/min font and glyph width. */
const ROT_BASE_FONT = 10;
const ROT_MIN_FONT = 6;
const ROT_CHAR_W = 6.5;

export interface BracketLabelLayout {
  /** True when the label is drawn unrotated, reading left-to-right. */
  horizontal: boolean;
  fontSize: number;
  /** Horizontal px the label text consumes; 0 when rotated. */
  width: number;
}

/**
 * Decide how one bracket label is drawn. Pure and DOM-free so the gutter
 * calculation and the renderer share a single decision and cannot drift.
 *
 * Horizontal whenever the label fits PACKET_MAX_HORIZ_BRACKET_LABEL_W — it
 * reads at a larger font and needs no head-turning. Only longer labels rotate,
 * and those keep the previous behaviour of scaling down to fit the bracket's
 * vertical span (`spanH`, px; pass 0 when unknown, which only affects the
 * rotated font size).
 */
export function bracketLabelLayout(label: unknown, spanH = 0): BracketLabelLayout {
  const text = typeof label === 'string' ? label : String(label ?? '');
  const w = text.length * BRACKET_LABEL_CHAR_W;
  if (w <= PACKET_MAX_HORIZ_BRACKET_LABEL_W) {
    return { horizontal: true, fontSize: BRACKET_LABEL_FONT, width: w };
  }
  const textAtBase = text.length * ROT_CHAR_W;
  let fontSize = ROT_BASE_FONT;
  if (spanH > 0 && textAtBase > spanH) {
    fontSize = Math.max(ROT_MIN_FONT, Math.floor(ROT_BASE_FONT * spanH / textAtBase));
  }
  return { horizontal: false, fontSize, width: 0 };
}

export interface BracketGutters {
  left: number;
  right: number;
  /** All left brackets render on the (free) right side instead. */
  flipLeftToRight: boolean;
  /** Widest estimated section label, for close-in left placement. */
  maxLabelW: number;
}

/**
 * Compute bracket gutter widths and the side-placement decision.
 *
 * Section labels always occupy the left column, so the right side is the
 * naturally free side: if no section uses right-side brackets, left brackets
 * flip to the right (all-or-nothing across sections so alignment is
 * preserved). When the right side is occupied, left brackets stay left but
 * hug the widest section label; extra width is reserved only for what
 * overflows the fixed label column. Runs assignBracketDepths so nested
 * depth is counted correctly. Shared by computeDimensions and the render
 * plugin so sizing and drawing can never disagree.
 */
export function computeBracketGutters(
  sections: PacketSection[],
  L: LayoutConfig,
): BracketGutters {
  let hasLeft = false;
  let hasRight = false;
  for (const sec of sections) {
    for (const br of sec.brackets ?? []) {
      if ((br.side ?? 'right') === 'left') hasLeft = true;
      else hasRight = true;
    }
  }
  const flipLeftToRight = hasLeft && !hasRight;

  // Bound the section-label contribution to the left-bracket gutter. Left
  // brackets are placed at `gridX - 16 - maxLabelW - offset`, so an unbounded
  // 300+ char label would push them arbitrarily far left, stranding them in a
  // disjoint cluster far from the grid (the Issue-24 split). The cap keeps the
  // gutter — and the returned maxLabelW the renderer uses for placement —
  // bounded; a no-op for normal-length labels.
  const maxLabelW = Math.min(
    PACKET_MAX_LABEL_GUTTER_W,
    sections.reduce(
      (w, s) => Math.max(w, estimateSectionLabelWidth(s.label ?? '')), 0));

  let maxLeftDepth = 0;
  let maxRightDepth = 0;
  let maxLeftHorizW = 0;
  let maxRightHorizW = 0;
  for (const sec of sections) {
    const allBrackets = (sec.brackets ?? []).map(b =>
      flipLeftToRight ? { ...b, side: 'right' as const } : b);
    for (const br of assignBracketDepths(allBrackets, 'right')) {
      maxRightDepth = Math.max(maxRightDepth, (br.depth ?? 0) + 1);
      maxRightHorizW = Math.max(maxRightHorizW, bracketLabelLayout(br.label).width);
    }
    for (const br of assignBracketDepths(allBrackets, 'left')) {
      maxLeftDepth = Math.max(maxLeftDepth, (br.depth ?? 0) + 1);
      maxLeftHorizW = Math.max(maxLeftHorizW, bracketLabelLayout(br.label).width);
    }
  }

  // A horizontal label starts 10px outside the outermost bracket stem (which
  // sits at 4 + (depth-1)*30) and runs outward, so it widens the SVG only by
  // what overruns the depth lanes reserved below (BRACKET_W per level plus 14px
  // for a rotated label's height): max(0, w - 14*depth - 24). Zero for short
  // labels and for rotated ones (width 0), so existing specs reserve exactly
  // what they did before.
  const horizOverflow = (w: number, depth: number): number =>
    Math.max(0, w - 14 * Math.max(depth, 1) - 24);

  // Left brackets sit just left of the widest section label (labels end at
  // gridX - 8): each depth level costs BRACKET_W, plus 14px for label-overlap
  // shifts. Only the overflow past the fixed label column widens the SVG.
  const leftNeeded = maxLeftDepth > 0
    ? maxLabelW + 8 + maxLeftDepth * L.BRACKET_W + 14
      + horizOverflow(maxLeftHorizW, maxLeftDepth)
    : 0;
  // Reserve left gutter for a section label WIDER than the fixed LABEL_W column
  // even when NO left brackets exist (D-177). The renderer right-anchors the
  // label at `gridX - 8` (gridX = LEFT_PAD + left + LABEL_W), so its text runs
  // from `gridX - 8 - maxLabelW` leftward; without this a label longer than
  // ~LABEL_W ran off the viewBox left edge and was clipped (e.g. a `packet-beta`
  // DSL title copied into the sole section's label). The condition to keep the
  // label's left edge at >= LEFT_PAD is `left >= maxLabelW + 8 - LABEL_W`.
  // Bounded because maxLabelW is already capped at PACKET_MAX_LABEL_GUTTER_W.
  // Strict no-op for labels that fit the 180px column (term <= 0) and no left
  // brackets, so normal specs are byte-identical.
  const labelOverflow = Math.max(0, maxLabelW + 8 - L.LABEL_W);
  return {
    left: Math.max(Math.max(0, leftNeeded - L.LABEL_W), labelOverflow),
    right: Math.max(maxRightDepth, 1) * L.BRACKET_W + 14
      + horizOverflow(maxRightHorizW, maxRightDepth),
    flipLeftToRight,
    maxLabelW,
  };
}

// ── Field-label fitting ─────────────────────────────────────────────────────

// Average glyph width as a fraction of font size for the bold sans-serif
// field-label font — an approximation so fitting needs no DOM measurement.
const FIELD_CHAR_W_RATIO = 0.6;
const FIELD_BASE_FONT = 11;
const FIELD_MIN_FONT = 7;

/**
 * Fit a field label into a cell of the given pixel width: scale the font
 * down (mirroring bracket-label scaling) and, only if even the minimum
 * size cannot fit, truncate with an ellipsis. The renderer's cell tooltip
 * always carries the full name, so truncation loses no information.
 */
export function fitFieldLabel(
  name: string,
  cellWidth: number,
): { fontSize: number; label: string } {
  const usableW = cellWidth - 6; // horizontal padding inside the cell
  let fontSize = FIELD_BASE_FONT;
  let label = name;
  const textW = name.length * FIELD_CHAR_W_RATIO * FIELD_BASE_FONT;
  if (textW > usableW && usableW > 0) {
    fontSize = Math.max(FIELD_MIN_FONT, Math.floor(FIELD_BASE_FONT * usableW / textW));
    const fitChars = Math.floor(usableW / (FIELD_CHAR_W_RATIO * fontSize));
    if (name.length > fitChars && fitChars > 1) {
      label = name.slice(0, fitChars - 1) + '…';
    }
  }
  return { fontSize, label };
}

// ── XML escaping ────────────────────────────────────────────────────────────

export function escapeXml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Re-export the theme maps so the plugin can use them
export { THEMES_LIGHT, THEMES_DARK, AUTO_PALETTE_LIGHT, AUTO_PALETTE_DARK };

// ── Section shape normalization ──

/**
 * Resolve a section's display label, accepting the common `name`/`title`
 * aliases in addition to the canonical `label`. Pure and DOM-free.
 *
 * The renderer draws the label via `sec.label.split('\n')` UNCONDITIONALLY, so
 * a section keyed with `name` (a very common/plausible alias, mirroring the
 * field-level `name`/`label` aliasing already present) left `sec.label`
 * `undefined` and crashed the whole render (blank canvas, total data loss).
 * Always returns a string so `.split` can never throw.
 */
export function sectionLabel(sec: any): string {
  if (!sec || typeof sec !== 'object') return '';
  const raw = sec.label ?? sec.name ?? sec.title ?? '';
  return typeof raw === 'string' ? raw : String(raw);
}

/**
 * Coerce a section's `rows` into the tuple-array shape the renderer's draw
 * loop consumes (`row.forEach(field => field[0]=name, field[1]=bits, ...)`).
 * Pure and DOM-free.
 *
 * Accepts BOTH:
 *   - the canonical tuple-array row  `[[name, bits, color?], ...]` (unchanged)
 *   - the object-shape row  `{ fields: [{name, bits, color}, ...] }` (or
 *     `{ cells: [...] }`) that LLMs frequently emit — each field object is
 *     mapped to a `[name, bits, color?]` tuple with the usual name/label and
 *     bits/width/size aliases. Without this, an object-shape row makes the
 *     draw loop iterate the object's keys (or throw) instead of fields.
 * Anything unrecognisable becomes an empty row rather than crashing.
 */
/**
 * Coerce a single field into the `[name, bits, color?]` tuple the renderer's
 * draw loop indexes (`field[0]`, `field[1]`, `field[2]`). Pure and DOM-free.
 *
 * A field can arrive as the canonical tuple (`["Ver", 4]`, returned unchanged)
 * OR as a field OBJECT (`{name:"Ver", bits:4, color?}`) — the shape LLMs emit
 * most often. On an object, resolves the usual name/label and bits/width/size
 * aliases; a color is carried through only when present.
 */
export function fieldToTuple(f: any): [string, number] | [string, number, string] {
  if (Array.isArray(f)) return f as [string, number] | [string, number, string];
  const name = f?.name ?? f?.label ?? '';
  const bits = f?.bits ?? f?.width ?? f?.size ?? 0;
  return (f?.color !== undefined && f?.color !== null
    ? [name, bits, f.color]
    : [name, bits]) as [string, number] | [string, number, string];
}

export function normalizeSectionRows(rows: any): PacketSection['rows'] {
  if (!Array.isArray(rows)) return [];
  return rows.map((row: any): PacketSection['rows'][number] => {
    if (Array.isArray(row)) {
      // An array row's ELEMENTS may themselves be canonical tuples
      // (`[name, bits]`) OR field objects (`{name, bits}`). The latter is a
      // very common LLM shape: the row is an array, but each field is an
      // object, so the draw loop's `field[0]`/`field[1]` index to `undefined`
      // → every cell collapses to a zero-width, unnamed rect (silent
      // per-field data loss across the WHOLE diagram). Coerce any object
      // element to a tuple. When every element is already an array (the
      // well-formed case) the row is returned BY REFERENCE, so canonical specs
      // are byte-identical — this is a normalization gap fill, not a catch-all.
      // The cast is required because TS 5.5+ infers a type predicate for the
      // `every` callback, narrowing `row` to `any[][]`, which is not assignable
      // to the fixed-length tuple union even though every element is a tuple.
      if (row.every((f: any) => Array.isArray(f))) {
        // Row nested one level too DEEP (D-173 / w4-13): each element is
        // itself an array OF arrays (a wrapped row-of-tuples), not a
        // `[name, bits]` tuple whose first element is a scalar. Such a row
        // passes `every(Array.isArray)` yet hands the draw loop an array as
        // field[0]/field[1], writing invalid SVG (30s timeout). Flatten one
        // level so the loop sees real field tuples. A canonical row (elements
        // are `[name,bits]` tuples, so each element's OWN first element is a
        // scalar) is NOT over-nested and is returned by reference below —
        // byte-identical; this is a targeted gap fill, not a catch-all.
        const overNested = row.length > 0 && row.every(
          (f: any) => Array.isArray(f) && f.length > 0 && f.every((g: any) => Array.isArray(g)));
        if (overNested) {
          const flat = ([] as any[]).concat(...row);
          return flat.map(fieldToTuple);
        }
        return row as PacketSection['rows'][number];
      }
      // A row that is itself a bare field tuple `[name, bits(, color)]` —
      // nested one level too SHALLOW (D-173 / w4-13). Without this it fell to
      // `.map(fieldToTuple)`, turning each scalar element into an empty
      // `['', 0]` cell. Wrap it as a single-field row instead.
      if (typeof row[0] === 'string' && typeof row[1] === 'number') {
        return [fieldToTuple(row)] as PacketSection['rows'][number];
      }
      return row.map(fieldToTuple);
    }
    const fields = Array.isArray(row?.fields) ? row.fields
      : Array.isArray(row?.cells) ? row.cells : null;
    if (fields) return fields.map(fieldToTuple);
    return [];
  });
}

/**
 * Normalize a single section object into a valid PacketSection: resolve its
 * label (name/title aliases), coerce its rows into tuple arrays, alias
 * `theme` -> `color`, and synthesize a placeholder row when the section has no
 * usable rows/fields (so a bracket-only section can't produce a zero-row draw
 * that looks empty). Pure and DOM-free. A no-op for well-formed sections whose
 * `label` and tuple-array `rows` are already correct.
 */
export function normalizeSection(sec: any, bitWidth: number): PacketSection {
  const label = sectionLabel(sec);
  let rows = normalizeSectionRows(sec?.rows);
  // A flat field list may arrive under `fields` OR `cells` at the SECTION
  // level (D-174 / w4-15). `cells` was honoured as a ROW key but not a SECTION
  // key, so a section keyed with `cells` silently lost every field to the
  // placeholder row. Both aliases resolve to the same flat-field wrapping.
  const flatFields = Array.isArray(sec?.fields) && sec.fields.length > 0 ? sec.fields
    : Array.isArray(sec?.cells) && sec.cells.length > 0 ? sec.cells
    : null;
  if (rows.length === 0 && flatFields) {
    rows = flatFieldsToRows(flatFields, bitWidth);
  }
  if (rows.length === 0) {
    rows = [[[label || 'Section', bitWidth] as [string, number]]];
  }
  const out: PacketSection = { ...(sec && typeof sec === 'object' ? sec : {}), label, rows };
  if ((out.color === undefined || out.color === null) && typeof sec?.theme === 'string') {
    out.color = sec.theme;
  }
  return out;
}

// ── Input normalization ─────────────────────────────────────────────────────
// Accept common "close but wrong" spec formats and coerce them into a valid
// PacketSpec.  This handles schemas that LLMs frequently hallucinate.

interface FlatField {
  name: string;
  bits: number;
  color?: string;
  description?: string;
}

interface LooseSpec {
  name?: string;
  title?: string;
  subtitle?: string;
  width?: number;
  bitWidth?: number;
  bits_per_row?: number;
  fields?: FlatField[];
  sections?: any[];
  brackets?: any[];
  type?: string;
}

/**
 * Convert flat-field objects `{name, bits, color}` into row tuples
 * `[[name, bits, color?]]`, auto-wrapping rows at the given bitWidth.
 */
function flatFieldsToRows(fields: FlatField[], bitWidth: number): PacketSection['rows'] {
  const rows: PacketSection['rows'] = [];
  let currentRow: Array<[string, number] | [string, number, string]> = [];
  let rowBits = 0;

  for (const raw of fields) {
    // Resolve the same name/bits/color aliases fieldToTuple uses, so a flat
    // field list arriving under `cells` (D-174) or a mix of {label,width} /
    // {name,size} field objects wraps correctly instead of leaving bits
    // undefined → NaN. A canonical {name, bits(, color)} field is unchanged.
    const f = raw as any;
    const name: string = f?.name ?? f?.label ?? '';
    const bits: number = f?.bits ?? f?.width ?? f?.size ?? 0;
    const color: string | undefined = f?.color;
    if (rowBits + bits > bitWidth && currentRow.length > 0) {
      rows.push(currentRow);
      currentRow = [];
      rowBits = 0;
    }
    const tuple: [string, number] | [string, number, string] = color
      ? [name, bits, color]
      : [name, bits];
    currentRow.push(tuple);
    rowBits += bits;
    if (rowBits >= bitWidth) {
      rows.push(currentRow);
      currentRow = [];
      rowBits = 0;
    }
  }
  if (currentRow.length > 0) rows.push(currentRow);
  return rows;
}

/**
 * Normalize a loosely-typed input into a valid PacketSpec.
 * Returns null if the input is not recognizably packet-like.
 */
export function normalizePacketSpec(raw: unknown): PacketSpec | null {
  // Unwrap arrays: [{...}] → {...}
  let obj: LooseSpec = raw as LooseSpec;
  if (Array.isArray(raw)) {
    if (raw.length === 0) return null;
    // Check if this is a bare array of field-like objects: [{name, bits}, ...]
    // LLMs frequently produce this format instead of a proper spec wrapper.
    const looksLikeFieldArray = raw.length > 1 &&
      raw.every((item: any) => item && typeof item === 'object' && 'bits' in item && ('name' in item || 'label' in item));
    if (looksLikeFieldArray) {
      // Synthesize a spec from the flat field array
      const fields = raw.map((f: any) => ({
        name: f.name || f.label || '',
        bits: f.bits || f.width || f.size || 1,
        color: f.color,
      }));
      const bitWidth = fields.reduce((sum: number, f: FlatField) => sum + f.bits, 0);
      // Pick the smallest standard row width that fits the widest field
      const rowWidth = bitWidth <= 8 ? 8 : bitWidth <= 16 ? 16 : bitWidth <= 32 ? 32 : bitWidth;
      const rows = flatFieldsToRows(fields, rowWidth);
      return {
        type: 'packet',
        title: 'Packet Frame',
        bitWidth: rowWidth,
        sections: [{ label: 'Frame', rows }],
      };
    }
    obj = raw[0] as LooseSpec;
  }
  if (!obj || typeof obj !== 'object') return null;

  const hasSections = Array.isArray(obj.sections) && obj.sections.length > 0;
  // Only bail for a missing title when there is nothing else to render. When a
  // valid non-empty `sections` array is present, synthesize a default title
  // rather than returning null — otherwise the whole (renderable) spec would
  // fall through to the renderer UNNORMALIZED, so section `name`→`label` and
  // object-shape-row aliasing never runs and `sec.label.split` crashes.
  const title = obj.title || obj.name || (hasSections ? 'Packet' : undefined);
  if (!title) return null;

  // Coerce to a positive integer once here so every downstream consumer
  // (computeDimensions, the ruler, flatFieldsToRows wrapping) sees the same
  // clean integer width — a fractional 31.5 can never reach the ruler tick loop.
  const bitWidth = sanitizePacketBitWidth(
    obj.bitWidth || obj.bits_per_row || obj.width || PACKET_DEFAULT_BIT_WIDTH);

  // Already has sections — accept as-is with aliased fields patched
  if (obj.sections && Array.isArray(obj.sections) && obj.sections.length > 0) {
    // Detect index-based sections: {label, start, end} pointing into a
    // fields array.  LLMs frequently generate this instead of the row-based
    // format the renderer expects.
    const isIndexBased = obj.sections.every(
      (s: any) => typeof s.start === 'number' && typeof s.end === 'number' && s.label
    );

    if (isIndexBased && obj.fields && Array.isArray(obj.fields) && obj.fields.length > 0) {
      // Build rows from the flat fields, grouped by the index-based sections
      const allRows = flatFieldsToRows(obj.fields, bitWidth);

      // Map field indices to the row that contains them.
      // Walk the rows and build a cumulative field-index → row-index map.
      const fieldToRow: number[] = [];
      let fieldIdx = 0;
      for (let rowIdx = 0; rowIdx < allRows.length; rowIdx++) {
        for (const _ of allRows[rowIdx]) {
          fieldToRow.push(rowIdx);
          fieldIdx++;
        }
      }

      // Convert top-level brackets from field-index-based to row-index-based.
      // The renderer expects {start_row, end_row} within a section, but the
      // input has {start, end} as global field indices.  We convert to global
      // row indices here; per-section offsetting happens below.
      const globalBrackets: Array<{
        label: string; startRow: number; endRow: number; side: 'left' | 'right';
      }> = [];
      if (obj.brackets && Array.isArray(obj.brackets)) {
        for (const br of obj.brackets) {
          const startFieldIdx = Math.min(br.start ?? 0, obj.fields.length - 1);
          const endFieldIdx = Math.min((br.end ?? br.start ?? 0), obj.fields.length - 1);
          globalBrackets.push({
            label: br.label || '',
            startRow: fieldToRow[startFieldIdx] ?? 0,
            endRow: fieldToRow[endFieldIdx] ?? (allRows.length - 1),
            side: br.side ?? 'right',
          });
        }
      }

      const packetSections: PacketSection[] = obj.sections.map((sec: any) => {
        const startFieldIdx = Math.min(sec.start ?? 0, obj.fields!.length - 1);
        const endFieldIdx = Math.min((sec.end ?? sec.start ?? 0), obj.fields!.length - 1);
        const startRow = fieldToRow[startFieldIdx] ?? 0;
        const endRow = fieldToRow[endFieldIdx] ?? (allRows.length - 1);

        const sectionRows = allRows.slice(startRow, endRow + 1);

        // Attach brackets whose row-span overlaps this section's rows,
        // converting global row indices to section-local row indices.
        const sectionBrackets: PacketBracket[] = [];
        for (const gb of globalBrackets) {
          if (gb.endRow >= startRow && gb.startRow <= endRow) {
            sectionBrackets.push({
              start_row: Math.max(0, gb.startRow - startRow),
              end_row: Math.min(sectionRows.length - 1, gb.endRow - startRow),
              label: gb.label,
              side: gb.side,
            });
          }
        }

        return {
          label: sec.label,
          rows: sectionRows.length > 0 ? sectionRows : [[[sec.label, bitWidth] as [string, number]]],
          ...(sectionBrackets.length > 0 ? { brackets: sectionBrackets } : {}),
        };
      });

      return {
        type: 'packet',
        title,
        subtitle: obj.subtitle,
        bitWidth,
        sections: packetSections,
      };
    }

    // Sections already in (approximately) the correct {label, rows} format.
    // Route every section through normalizeSection so BOTH shape mismatches
    // that reach this branch are repaired uniformly: (a) a section keyed with
    // `name`/`title` instead of `label` (its label is resolved so the
    // renderer's unconditional `sec.label.split` can never see undefined), and
    // (b) object-shape rows `{fields:[{name,bits,color}]}` instead of tuple
    // arrays (coerced to `[name,bits,color?]`). Missing/empty rows fall back to
    // fields, then to a placeholder row. General across the whole class of
    // name-keyed / object-row sections; a no-op for already-correct sections.
    const validatedSections = obj.sections.map((sec: any) =>
      normalizeSection(sec, bitWidth));

    return {
      type: 'packet',
      title,
      subtitle: obj.subtitle,
      bitWidth,
      sections: validatedSections,
    };
  }

  // Flat fields array → single section with auto-wrapped rows
  if (obj.fields && Array.isArray(obj.fields) && obj.fields.length > 0) {
    const rows = flatFieldsToRows(obj.fields, bitWidth);

    // Convert top-level brackets (field-index-based) to row-based
    let brackets: PacketBracket[] | undefined;
    if (obj.brackets && Array.isArray(obj.brackets) && obj.brackets.length > 0) {
      const fieldToRow: number[] = [];
      let fi = 0;
      for (let ri = 0; ri < rows.length; ri++) {
        for (const _ of rows[ri]) {
          fieldToRow.push(ri);
          fi++;
        }
      }
      brackets = obj.brackets.map((br: any) => {
        const sf = Math.min(br.start ?? 0, obj.fields!.length - 1);
        const ef = Math.min(br.end ?? br.start ?? 0, obj.fields!.length - 1);
        return {
          start_row: fieldToRow[sf] ?? 0,
          end_row: fieldToRow[ef] ?? (rows.length - 1),
          label: br.label || '',
          side: (br.side as 'left' | 'right') ?? 'right',
        };
      });
    }

    return {
      type: 'packet',
      title,
      subtitle: obj.subtitle,
      bitWidth,
      sections: [{ label: title, rows, ...(brackets ? { brackets } : {}) }],
    };
  }

  return null;
}
