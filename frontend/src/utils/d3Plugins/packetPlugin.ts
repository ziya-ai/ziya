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
const AUTO_PALETTE_LIGHT: ColorTriple[] = [
  { bg: '#B2E0F0', border: '#4BA3C7', text: '#1A5276' },
  { bg: '#F9E79F', border: '#D4AC0D', text: '#7D6608' },
  { bg: '#D5F5E3', border: '#82E0AA', text: '#1E8449' },
  { bg: '#FADBD8', border: '#E74C3C', text: '#922B21' },
  { bg: '#E8DAEF', border: '#AF7AC5', text: '#6C3483' },
  { bg: '#D1F2EB', border: '#48C9B0', text: '#0E6655' },
  { bg: '#FDEBD0', border: '#F0B27A', text: '#935116' },
  { bg: '#D6EAF8', border: '#5DADE2', text: '#1B4F72' },
  { bg: '#D4A5C7', border: '#9B59B6', text: '#4A235A' },
  { bg: '#A9DFBF', border: '#27AE60', text: '#1E8449' },
];

const AUTO_PALETTE_DARK: ColorTriple[] = [
  { bg: '#1A5276', border: '#4BA3C7', text: '#D6EAF8' },
  { bg: '#7D6608', border: '#D4AC0D', text: '#FEF9E7' },
  { bg: '#1E8449', border: '#82E0AA', text: '#D5F5E3' },
  { bg: '#922B21', border: '#E74C3C', text: '#FADBD8' },
  { bg: '#6C3483', border: '#AF7AC5', text: '#E8DAEF' },
  { bg: '#0E6655', border: '#48C9B0', text: '#D1F2EB' },
  { bg: '#935116', border: '#F0B27A', text: '#FDEBD0' },
  { bg: '#1B4F72', border: '#5DADE2', text: '#D6EAF8' },
  { bg: '#4A235A', border: '#9B59B6', text: '#E8DAEF' },
  { bg: '#196F3D', border: '#27AE60', text: '#A9DFBF' },
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
    // Explicit triple — adapt text color if needed for contrast
    return {
      bg: color.bg,
      border: color.border,
      text: color.text || getOptimalTextColor(color.bg),
    };
  }
  // Named theme
  const themes = isDarkMode ? THEMES_DARK : THEMES_LIGHT;
  if (themes[color]) return themes[color];
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

export function computeDimensions(spec: PacketSpec): { width: number; height: number; layout: LayoutConfig } {
  const bits = spec.bitWidth ?? 8;
  const L = defaultLayout(bits);

  const totalRows = spec.sections.reduce((n, s) => n + s.rows.length, 0);
  const numSections = spec.sections.length;

  // Compute max bracket nesting depth on each side to allocate space.
  // Must run the same auto-depth assignment that the renderer uses,
  // because input specs typically omit explicit depth values.
  let maxLeftDepth = 0;
  let maxRightDepth = 0;
  for (const sec of spec.sections) {
    const allBrackets = sec.brackets ?? [];
    const rightAssigned = assignBracketDepths(allBrackets, 'right');
    const leftAssigned  = assignBracketDepths(allBrackets, 'left');
    for (const br of rightAssigned) {
      maxRightDepth = Math.max(maxRightDepth, (br.depth ?? 0) + 1);
    }
    for (const br of leftAssigned) {
      maxLeftDepth = Math.max(maxLeftDepth, (br.depth ?? 0) + 1);
    }
  }
  // Add 14px padding per side to accommodate label overlap shifts.
  // The renderer shifts colliding labels outward by 14px each.
  const bracketLeftW = maxLeftDepth * L.BRACKET_W + (maxLeftDepth > 0 ? 14 : 0);
  const bracketRightW = Math.max(maxRightDepth, 1) * L.BRACKET_W + 14;

  const GRID_W = bits * L.BIT_W;
  const width = L.LEFT_PAD + bracketLeftW + L.LABEL_W + GRID_W + bracketRightW + L.LEFT_PAD;
  const subtitleH = spec.subtitle ? L.SUBTITLE_H + 6 : 6;
  const height =
    L.TOP_PAD + L.TITLE_H + subtitleH +
    L.HEADER_H +
    totalRows * L.ROW_H +
    Math.max(0, numSections - 1) * L.SECTION_GAP +
    L.HEADER_H + L.TOP_PAD;

  return { width, height, layout: L };
}

// ── Bracket depth auto-computation ──────────────────────────────────────────

/**
 * Assign nesting depths to brackets on one side so overlapping ranges
 * don't collide.  Innermost brackets get depth 0 (closest to grid).
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
    while (true) {
      const conflict = assigned.some(
        a => a.depth === depth &&
          a.start_row <= br.end_row &&
          a.end_row >= br.start_row
      );
      if (!conflict) break;
      depth++;
    }
    assigned.push({ ...br, depth, side });
  }

  return assigned;
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
