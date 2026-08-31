/**
 * D3 render plugin for general-purpose packet / protocol frame diagrams.
 *
 * Recognises specs with `type: 'packet'`.  Renders bit-ruler, sections,
 * colored fields, and nestable bracket annotations.  Dark mode aware
 * via the shared colorUtils.
 */
import { D3RenderPlugin } from '../../types/d3';
import {
  type PacketSpec, type PacketSection, type PacketBracket,
  type LayoutConfig,
  computeDimensions, defaultLayout, resolveColor,
  assignBracketDepths, escapeXml, computeBracketGutters, fitFieldLabel,
  bracketLabelLayout,
  normalizePacketSpec, sanitizeFieldBits, sanitizeBrackets, sectionLabel,
  normalizeSectionRows, sanitizePacketBitWidth,
} from '../../utils/d3Plugins/packetPlugin';
import { getOptimalTextColor } from '../../utils/colorUtils';
import { getZoomScript, getDownloadSvgScript } from '../../utils/popupScriptUtils';
import JSON5 from 'json5';
import { extractDefinition } from '../../utils/d3Plugins/specEnvelope';

/**
 * Strip a markdown code fence from a definition string (D-215). Model output
 * frequently wraps a valid packet spec (JSON or `packet-beta` DSL) in a
 * ```json / ```mermaid fence; the raw JSON.parse choked on the leading
 * backticks (w4-04) and parsePacketBetaDsl's `/^packet/` sniff never fired on
 * the fenced DSL (w4-05). Handles a matched fence, an unmatched leading/trailing
 * fence, and a bare language tag. Pure / DOM-free / testable.
 */
export function stripPacketFence(raw: string): string {
  let t = String(raw).trim();
  const matched = /^```[a-zA-Z0-9_-]*\s*\n?([\s\S]*?)\n?```$/.exec(t);
  if (matched) return matched[1].trim();
  t = t.replace(/^```[a-zA-Z0-9_-]*\s*/, '').replace(/```\s*$/, '');
  return t.trim();
}

/**
 * Normalise smart / curly quotes to ASCII so a copy-pasted payload parses
 * (D-214 w4-12). json5 does NOT accept U+201C/U+201D/U+2018/U+2019, so this
 * must run before the json5 fallback. Pure / testable.
 */
export function normalizePacketSmartQuotes(raw: string): string {
  return String(raw)
    .replace(/[\u201C\u201D\u201E\u201F]/g, '"')
    .replace(/[\u2018\u2019\u201A\u201B]/g, "'");
}

/**
 * Lenient parse of a JSON-ish packet definition (D-214). Tries strict
 * JSON.parse first (fast path, byte-identical to the old behaviour for valid
 * input), then a json5 fallback after stripping a markdown fence, normalising
 * smart quotes, and slicing to the outermost {...} (drops leading prose /
 * trailing `;`). json5 recovers trailing commas (w4-01), unquoted keys (w4-02),
 * single quotes (w4-03), // comments + trailing `;` (w4-14); smart quotes
 * (w4-12) are handled by the pre-normalisation. Returns the parsed object, or
 * `undefined` when unrecoverable. Pure / DOM-free / testable.
 */
export function lenientParsePacketJson(raw: string): any {
  if (typeof raw !== 'string') return undefined;
  const cleaned = normalizePacketSmartQuotes(stripPacketFence(raw)).trim();
  if (!cleaned) return undefined;
  try { return JSON.parse(cleaned); } catch (_e) { /* fall through */ }
  const first = cleaned.indexOf('{');
  const last = cleaned.lastIndexOf('}');
  if (first === -1 || last === -1 || last < first) return undefined;
  const body = cleaned.slice(first, last + 1);
  try { return JSON.parse(body); } catch (_e2) { /* fall through to json5 */ }
  try { return JSON5.parse(body); } catch (_e3) { return undefined; }
}

function renderError(container: HTMLElement, message: string, rawSpec: any, isDarkMode: boolean): void {
  const specStr = typeof rawSpec === 'string' ? rawSpec
    : typeof rawSpec?.definition === 'string' ? rawSpec.definition
    : JSON.stringify(rawSpec, null, 2);
  const escaped = escapeXml(specStr || '(empty)');

  container.innerHTML = `
    <div style="
      padding: 16px;
      margin: 8px;
      background: ${isDarkMode ? '#2a1215' : '#fff1f0'};
      border: 1px solid ${isDarkMode ? '#5c2223' : '#ffa39e'};
      border-radius: 4px;
      color: ${isDarkMode ? '#ff4d4f' : '#cf1322'};
      font-family: monospace;
      font-size: 14px;
      line-height: 1.5;
    ">
      <strong>Packet diagram error:</strong> ${escapeXml(message)}
      <details style="margin-top: 8px; cursor: pointer;">
        <summary style="font-weight: bold;">Show Definition</summary>
        <pre style="
          max-height: 400px;
          overflow: auto;
          background: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
          padding: 12px;
          border-radius: 4px;
          margin: 8px 0 0 0;
          word-break: break-word;
          white-space: pre-wrap;
          color: ${isDarkMode ? '#e0e0e0' : '#24292e'};
        "><code>${escaped}</code></pre>
      </details>
    </div>
  `;

  // Tag the card so the headless harness (DiagramRenderPage) fails fast with
  // this message instead of polling for an svg/canvas/img the card never
  // contains and reporting a generic "svg:0" timeout. setAttribute, not
  // template interpolation: the message can contain quotes.
  const card = container.firstElementChild;
  if (card) card.setAttribute('data-diagram-error', message);
}

/**
 * Bridge the mermaid-style `packet-beta` textual DSL into a loose PacketSpec
 * (`{type,title,bitWidth,fields}`) that normalizePacketSpec understands.
 *
 * The `render_diagram` backend dispatches `type: 'packet'` straight to this
 * JSON PacketSpec renderer; the `packet-beta` DSL converters otherwise live
 * only on the mermaid path, so DSL text would previously fall through to
 * renderError (no <svg>) and hang the capture harness. This makes the packet
 * entry point accept BOTH representations. General across the whole class of
 * `packet-beta` specs, not a single-spec special case.
 *
 * Each `START-END: label` line becomes a field of width END-START+1. Inverted
 * (start > end), zero-width, non-finite and absurdly large ranges are clamped
 * to a width in [1, 512] so a bad range can never produce a 0/negative or
 * multi-million-pixel cell.
 */
export function parsePacketBetaDsl(text: string): { type: 'packet'; title: string; bitWidth: number; fields: Array<{ name: string; bits: number }> } | null {
  // De-fence defensively (D-215): callers pass de-fenced text, but a direct
  // caller (or a test) may hand a fenced DSL. Stripping here means the
  // `/^packet/` sniff below sees the real first token, not backticks.
  const trimmed = stripPacketFence(text);
  if (!/^packet(-beta)?/.test(trimmed)) return null;
  const lines = trimmed.split('\n');
  const titleLine = lines.find(l => l.trim().startsWith('title'));
  const title = titleLine
    ? titleLine.trim().replace(/^title\s+/, '').replace(/^"([\s\S]*)"$/, '$1')
    : 'Packet';
  const clampWidth = (w: number): number => {
    if (!Number.isFinite(w) || w < 1) return 1;
    return w > 512 ? 512 : w;
  };
  const unquote = (s: string): string => s.trim().replace(/^"([\s\S]*)"$/, '$1');
  const fields: Array<{ name: string; bits: number }> = [];
  for (const line of lines) {
    const t = line.trim();
    // Mermaid v11 RELATIVE width: `+N: label` — N bits appended after the
    // previous field (D-216). Checked first: a `+`-prefixed line can never
    // match the absolute START-END pattern below.
    let rel = t.match(/^\+\s*(\d+)\s*:\s*([\s\S]*)$/);
    if (rel) {
      fields.push({ name: unquote(rel[2]), bits: clampWidth(parseInt(rel[1], 10)) });
      continue;
    }
    // Absolute START-END range: width END-START+1. Inverted / zero-width /
    // non-finite / absurd ranges are clamped to [1, 512].
    const m = t.match(/^(-?\d+)\s*-\s*(-?\d+)\s*:\s*([\s\S]*)$/);
    if (!m) continue;
    let a = parseInt(m[1], 10);
    let b = parseInt(m[2], 10);
    const label = unquote(m[3]);
    if (!Number.isFinite(a)) a = 0;
    if (!Number.isFinite(b)) b = a;
    fields.push({ name: label, bits: clampWidth(b - a + 1) });
  }
  return fields.length > 0 ? { type: 'packet', title, bitWidth: 32, fields } : null;
}

/**
 * Resolve the backdrop a field label actually reads against (D-227).
 *
 * A field/section fill of `transparent`/`none` (or a zero-alpha rgba/hsla)
 * does NOT paint a surface — the themed canvas shows THROUGH it. The shared
 * colour helpers are theme-blind: `namedColorToHex` maps `transparent`→`#ffffff`
 * on the written assumption of a white page, so `getOptimalTextColor` commits
 * to black. That is correct on the light canvas (#ffffff) but wrong on the dark
 * canvas (#1e1e1e), where black label text lands on the dark surface at 1.26:1.
 *
 * The plugin is the only place that knows BOTH that the fill is transparent AND
 * which themed canvas is behind it, so the theme-correct fix lives here, not in
 * the theme-blind colour table: when the fill is see-through, resolve the label
 * against the real canvas background (`canvasBg`, itself derived from the active
 * theme) instead of the white-assuming constant. Any opaque fill is returned
 * unchanged, so non-transparent output is byte-identical to before.
 *
 * Pure / DOM-free / testable.
 */
export function effectiveCellBackdrop(fillBg: string | undefined, canvasBg: string): string {
  if (fillBg == null) return canvasBg;
  const norm = String(fillBg).trim().toLowerCase();
  if (norm === 'transparent' || norm === 'none' || norm === '') return canvasBg;
  // Zero-alpha rgba()/hsla() also paints nothing — the canvas shows through.
  const alpha = norm.match(/^(?:rgba|hsla)\([^)]*[,/]\s*(0|0?\.0+|0%)\s*\)$/);
  if (alpha) return canvasBg;
  return fillBg;
}

function render(container: HTMLElement, d3: any, rawSpec: any, isDarkMode: boolean): void {
  // Accept either a direct PacketSpec or { definition: string }. The string
  // may be: packet-beta DSL text (bridged), fenced content (D-215), or
  // JSON with the common LLM slips — trailing commas, unquoted keys, single
  // quotes, smart quotes, comments (D-214). rawSpec is left UNCHANGED so the
  // Source toggle still shows the author's original definition.
  // extractDefinition also covers the envelope whose definition is ALREADY an
  // object (a 
  let pkt: PacketSpec;
  if (typeof rawSpec?.definition === 'string') {
    // De-fence + smart-quote normalise once, then try the DSL bridge (its
    // `/^packet/` sniff needs the fence gone). parsePacketBetaDsl handles both
    // absolute ranges and v11 `+N:` relative widths.
    const cleaned = normalizePacketSmartQuotes(stripPacketFence(rawSpec.definition));
    const dsl = parsePacketBetaDsl(cleaned);
    if (dsl) {
      pkt = dsl as unknown as PacketSpec;
    } else {
      // Not DSL — lenient JSON parse (strict first, json5 fallback). Only when
      // even that fails do we surface the error card (no more silent 30s hang
      // on a recoverable slip).
      const parsed = lenientParsePacketJson(def);
      if (parsed === undefined) {
        renderError(container, 'Invalid JSON in definition', rawSpec, isDarkMode);
        return;
      }
      pkt = parsed as PacketSpec;
    }
  } else {
    pkt = def as PacketSpec;
  }

  // Normalize common alternate formats (flat fields, array wrapper, name/width aliases)
  const normalized = normalizePacketSpec(pkt);
  if (normalized) pkt = normalized;

  // Validate required fields before attempting to render
  if (!pkt.sections || !Array.isArray(pkt.sections) || pkt.sections.length === 0) {
    renderError(container, 'Requires a "sections" array with at least one section', rawSpec, isDarkMode);
    return;
  }

  // Sanitize bracket geometry BEFORE any layout math. Clamps each bracket's
  // start_row/end_row into its section's row range, corrects inverted ranges,
  // coerces bad depth, and validates side — so out-of-range/inverted/degenerate
  // brackets cannot produce paths or gutters far outside the diagram bounds
  // (the Issue-24 "layout explosion"). Runs on the SAME normalized spec that
  // computeDimensions and the draw loop consume, so sizing and drawing agree.
  pkt.sections = pkt.sections.map((sec: PacketSection) => {
    if (!sec.brackets) return sec;
    const rowCount = Array.isArray(sec.rows) ? sec.rows.length : 0;
    return { ...sec, brackets: sanitizeBrackets(sec.brackets, rowCount) };
  });

  // Coerce to a positive integer (round fractional, floor >=1, default on
  // NaN/Infinity) so the ruler tick loop below and the grid width match
  // computeDimensions, which sanitizes identically. A fractional bitWidth
  // (e.g. 31.5) otherwise leaks into the ruler producing "30.5 29.5 … -0.5".
  const bits = sanitizePacketBitWidth(pkt.bitWidth);
  const { width, height, layout: L } = computeDimensions(pkt);
  const GRID_W = bits * L.BIT_W;

  // Bracket gutters + side-placement decision — shared with computeDimensions
  // so gridX and the SVG width agree. When no section uses right-side
  // brackets, left brackets flip to the free right side (all-or-nothing
  // across sections so alignment is preserved); otherwise they stay left,
  // hugging the widest section label instead of the outer gutter edge.
  const gutters = computeBracketGutters(pkt.sections, L);
  const gridX = L.LEFT_PAD + gutters.left + L.LABEL_W;

  container.innerHTML = '';
  const svg = d3.select(container).append('svg')
    .attr('xmlns', 'http://www.w3.org/2000/svg')
    .attr('viewBox', `0 0 ${width} ${height}`)
    .attr('width', width).attr('height', height)
    .style('max-width', '100%').style('height', 'auto')
    .style('font-family', '"Segoe UI", Arial, sans-serif');

  const bgColor = isDarkMode ? '#1e1e1e' : '#ffffff';
  const textFill = isDarkMode ? '#e0e0e0' : '#1F2937';
  const dimFill  = isDarkMode ? '#a0a0a0' : '#6B7280';
  const bracketStroke = isDarkMode ? '#888888' : '#6B7280';

  svg.append('rect').attr('width', width).attr('height', height)
    .attr('fill', bgColor).attr('rx', 4);

  let y = L.TOP_PAD;

  // Title
  svg.append('text').attr('x', L.LEFT_PAD + 4).attr('y', y + 16)
    .attr('fill', textFill)
    .style('font', 'bold 16px "Segoe UI", Arial, sans-serif')
    .text(pkt.title);
  y += L.TITLE_H;

  // Subtitle
  if (pkt.subtitle) {
    svg.append('text').attr('x', L.LEFT_PAD + 4).attr('y', y + 10)
      .attr('fill', dimFill)
      .style('font', 'italic 11px "Segoe UI", Arial, sans-serif')
      .text(pkt.subtitle);
    y += L.SUBTITLE_H + 6;
  } else {
    y += 6;
  }

  // ── Bit ruler ──────────────────────────────────────────────────────────
  const drawRuler = (ry: number) => {
    for (let b = 0; b < bits; b++) {
      svg.append('text')
        .attr('x', gridX + b * L.BIT_W + L.BIT_W / 2)
        .attr('y', ry + 14)
        .attr('text-anchor', 'middle').attr('fill', dimFill)
        .style('font', 'bold 12px "Consolas", "Courier New", monospace')
        .text(bits - 1 - b);
    }
  };
  drawRuler(y);
  y += L.HEADER_H;

  // ── Sections ───────────────────────────────────────────────────────────
  pkt.sections.forEach((sec: PacketSection, sectionIdx: number) => {
    const secY = y;
    // Defense-in-depth: coerce object-shape rows ({fields:[...]}) to tuple
    // arrays here too, so a row that reached the renderer un-normalized draws
    // its fields instead of iterating object keys. Row COUNT is preserved, so
    // this stays in agreement with computeDimensions' height math.
    const secRows = normalizeSectionRows(sec.rows);
    const secH = secRows.length * L.ROW_H;
    const sectionColor = resolveColor(sec.color, isDarkMode, sectionIdx);

    // Section label (left column, vertically centered).
    // Defense-in-depth: resolve via sectionLabel() so a section that reached
    // the renderer with `name`/`title` (not `label`) or a non-string label
    // can never make `.split` throw and blank the whole canvas.
    const lines = sectionLabel(sec).split('\n');
    const midY = secY + secH / 2;
    lines.forEach((ln, li) => {
      const isMain = li === 0;
      const lineY = lines.length === 1
        ? midY + 4
        : midY + (li === 0 ? -4 : li * 14 - 4);
      svg.append('text')
        .attr('x', gridX - 8).attr('y', lineY)
        .attr('text-anchor', 'end')
        .attr('fill', isMain ? textFill : dimFill)
        .style('font', isMain
          ? 'bold 13px "Segoe UI", Arial, sans-serif'
          : '10px "Segoe UI", Arial, sans-serif')
        .text(ln);
    });

    // Brackets — flip left brackets to the free right side when the layout
    // decision says so, then auto-assign depths per side and render.
    const allBrackets = (sec.brackets ?? []).map(b =>
      gutters.flipLeftToRight ? { ...b, side: 'right' as const } : b);
    const rightBrackets = assignBracketDepths(allBrackets, 'right');
    const leftBrackets  = assignBracketDepths(allBrackets, 'left');

    const renderBrackets = (brs: PacketBracket[], side: 'left' | 'right') => {
      // Monospace char width at 10px bold ≈ 6.5px (rotated-label extent)
      const CHAR_W = 6.5;
      const LABEL_PAD = 4;

      // Pre-compute label geometry for overlap detection. Orientation comes
      // from the shared helper so the gutter computeBracketGutters reserved and
      // the label actually drawn can never disagree. A rotated label's extent
      // along the vertical axis is its text length; a horizontal one's is its
      // line height.
      const labelInfos = brs.map(br => {
        const by1 = secY + br.start_row * L.ROW_H;
        const by2 = secY + (br.end_row + 1) * L.ROW_H;
        const labelY = (by1 + by2) / 2;
        const lay = bracketLabelLayout(br.label, by2 - by1 - 4);
        const halfExtent = lay.horizontal
          ? (lay.fontSize + 2) / 2
          : (br.label.length * CHAR_W) / 2;
        return { br, by1, by2, labelY, lay,
                 yMin: labelY - halfExtent, yMax: labelY + halfExtent };
      });

      // Assign label offsets: shift labels outward when they would
      // overlap vertically with another label at the same bracket depth.
      // Clearing a horizontal neighbour costs its full text width; clearing a
      // rotated one costs only its line height.
      const labelOffsets = new Map<PacketBracket, number>();
      for (let i = 0; i < labelInfos.length; i++) {
        let extraShift = 0;
        const a = labelInfos[i];
        for (let j = 0; j < i; j++) {
          const b = labelInfos[j];
          if ((a.br.depth ?? 0) !== (b.br.depth ?? 0)) continue;
          const prevShift = labelOffsets.get(b.br) ?? 0;
          if (a.yMin < b.yMax + LABEL_PAD && a.yMax > b.yMin - LABEL_PAD) {
            const clear = b.lay.horizontal ? b.lay.width + 8 : 14;
            extraShift = Math.max(extraShift, prevShift + clear);
          }
        }
        labelOffsets.set(a.br, extraShift);
      }

      labelInfos.forEach(({ br, by1, by2, labelY, lay }) => {
        const depth = br.depth ?? 0;
        const offset = 4 + depth * 30;

        let bx: number;
        if (side === 'right') {
          bx = gridX + GRID_W + offset;
        } else {
          // Hug the widest section label (labels end at gridX - 8) rather
          // than sitting at the outer edge of the reserved gutter.
          bx = gridX - 16 - gutters.maxLabelW - offset;
        }

        // Bracket line
        const tickDir = side === 'right' ? 1 : -1;
        svg.append('path')
          .attr('d', [
            `M ${bx} ${by1 + 2}`,
            `L ${bx + tickDir * 6} ${by1 + 2}`,
            `L ${bx + tickDir * 6} ${by2 - 2}`,
            `L ${bx} ${by2 - 2}`,
          ].join(' '))
          .attr('fill', 'none').attr('stroke', bracketStroke)
          .attr('stroke-width', 1.2);

        // Bracket label: horizontal (larger, more readable font) whenever it
        // fits, rotated only when it is too long to sit horizontally without
        // dominating the canvas. Shifted outward if it overlaps a neighbour.
        const extraShift = labelOffsets.get(br) ?? 0;
        const labelX = bx + tickDir * ((lay.horizontal ? 10 : 20) + extraShift);

        const label = svg.append('text')
          .attr('x', labelX).attr('y', labelY)
          .attr('dominant-baseline', 'central')
          .attr('fill', textFill)
          .style('font', `bold ${lay.fontSize}px "Consolas", "Courier New", monospace`)
          .text(br.label);
        if (lay.horizontal) {
          label.attr('text-anchor', side === 'right' ? 'start' : 'end');
        } else {
          label.attr('text-anchor', 'middle')
            .attr('transform', `rotate(${side === 'right' ? 90 : -90}, ${labelX}, ${labelY})`);
        }
      });
    };

    renderBrackets(rightBrackets, 'right');
    renderBrackets(leftBrackets, 'left');

    // Field rows
    secRows.forEach((row, ri) => {
      const ry = y + ri * L.ROW_H;
      let bitOff = 0;

      row.forEach((field, fi) => {
        const name  = field[0] as string;
        const rawBits = field[1] as number;
        // Clamp degenerate/overflowing bit-widths BEFORE they reach SVG
        // geometry: negative → 0 (SVG forbids negative width), non-finite
        // (NaN/Infinity, incl. huge values that overflow bits*BIT_W to
        // Infinity) → 0, absurdly large → capped. Prevents invalid-attribute
        // errors and stops the shared bitOff accumulator from inheriting a
        // non-finite value that bleeds sibling fields off-canvas.
        const fbits = sanitizeFieldBits(rawBits);
        const fieldColorSpec = field.length > 2 ? field[2] as string | undefined : undefined;

        // Resolve: field override → section color → auto
        const c = fieldColorSpec
          ? resolveColor(fieldColorSpec, isDarkMode, sectionIdx * 100 + fi)
          : sectionColor;

        const fx = gridX + bitOff * L.BIT_W;
        const fw = fbits * L.BIT_W;

        const g = svg.append('g').style('cursor', 'default');

        g.append('rect')
          .attr('x', fx).attr('y', ry)
          .attr('width', fw).attr('height', L.ROW_H)
          .attr('fill', c.bg).attr('stroke', c.border)
          .attr('stroke-width', 1);

        // Field label — only if cell is wide enough
        if (fw >= 20 && name) {
          // Use getOptimalTextColor against the backdrop the label actually
          // reads against (D-227): a transparent/none fill lets the themed
          // canvas show through, so resolve against bgColor, not the
          // white-assuming resolved fill.
          const labelColor = getOptimalTextColor(effectiveCellBackdrop(c.bg, bgColor));
          // Scale the font to fit the cell (mirrors bracket-label scaling);
          // truncate with an ellipsis only when even the minimum size cannot
          // fit. The tooltip below always carries the full name.
          const { fontSize, label } = fitFieldLabel(name, fw);
          g.append('text')
            .attr('x', fx + fw / 2).attr('y', ry + L.ROW_H / 2)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('fill', labelColor)
            .style('font', 'bold ' + fontSize + 'px "Segoe UI", Arial, sans-serif')
            .text(label);
        }

        // Tooltip
        const highBit = (bits - 1) - bitOff;
        const lowBit  = bits - bitOff - fbits;
        g.append('title').text(
          `${name || '(unnamed)'}  [${fbits} bit${fbits > 1 ? 's' : ''}]  bits ${highBit}${lowBit !== highBit ? ':' + lowBit : ''}`
        );

        bitOff += fbits;
      });
    });

    // No gap after the final section — computeDimensions reserves only
    // (numSections - 1) gaps, so a trailing one misplaced the bottom ruler.
    y += secH + (sectionIdx < pkt.sections.length - 1 ? L.SECTION_GAP : 0);
  });

  // Bottom ruler
  drawRuler(y);

  // ── Interactive controls (source toggle + export), matching other plugins ──
  const svgNode = svg.node() as SVGSVGElement;
  const sourceStr = typeof rawSpec === 'string' ? rawSpec
    : typeof rawSpec?.definition === 'string' ? rawSpec.definition
    : JSON.stringify(rawSpec, null, 2);

  const actionsContainer = document.createElement('div');
  actionsContainer.className = 'diagram-actions';

  // Open: pop the diagram out into a standalone, zoomable window.
  const openButton = document.createElement('button');
  openButton.innerHTML = '↗️ Open';
  openButton.className = 'diagram-action-button packet-open-button';
  openButton.onclick = () => {
    const svgData = new XMLSerializer().serializeToString(svgNode);
    const winW = Math.max(width + 50, 400);
    const winH = Math.max(height + 100, 300);
    const htmlContent = `
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>Packet Diagram</title>
  <style>
    body { margin: 0; padding: 0; display: flex; flex-direction: column; height: 100vh;
      background-color: ${isDarkMode ? '#1e1e1e' : '#f8f9fa'};
      font-family: system-ui, -apple-system, sans-serif; }
    .toolbar { background-color: ${isDarkMode ? '#343a40' : '#f1f3f5'};
      border-bottom: 1px solid ${isDarkMode ? '#495057' : '#dee2e6'};
      padding: 8px; display: flex; justify-content: space-between; align-items: center; }
    .toolbar button { background-color: #4361ee; color: white; border: none; border-radius: 4px;
      padding: 6px 12px; cursor: pointer; margin-right: 8px; font-size: 14px; }
    .toolbar button:hover { background-color: #3a0ca3; }
    .container { flex: 1; display: flex; justify-content: center; align-items: center;
      overflow: auto; padding: 20px; }
    svg { max-width: 100%; max-height: 100%; height: auto; width: auto; }
  </style>
</head>
<body>
  <div class="toolbar">
    <div>
      <button onclick="zoomIn()">Zoom In</button>
      <button onclick="zoomOut()">Zoom Out</button>
      <button onclick="resetZoom()">Reset</button>
    </div>
    <div><button onclick="downloadSvg()">Download SVG</button></div>
  </div>
  <div class="container" id="svg-container">${svgData}</div>
  <script>
    document.querySelector('svg').setAttribute('preserveAspectRatio', 'xMidYMid meet');
    ${getZoomScript()}${getDownloadSvgScript(`packet-diagram-${Date.now()}.svg`)}
  </script>
</body>
</html>`;
    const blob = new Blob([htmlContent], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    const popupWindow = window.open(
      url, 'PacketDiagram',
      `width=${winW},height=${winH},resizable=yes,scrollbars=yes,status=no,toolbar=no,menubar=no,location=no`
    );
    if (popupWindow) popupWindow.focus();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  };
  actionsContainer.appendChild(openButton);

  // Source toggle: swap the rendered SVG for its raw definition and back.
  let showingSource = false;
  const sourcePre = document.createElement('pre');
  sourcePre.style.cssText = `
    display: none;
    margin: 0;
    padding: 16px;
    background: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
    border: 1px solid ${isDarkMode ? '#303030' : '#e1e4e8'};
    border-radius: 6px;
    color: ${isDarkMode ? '#e6e6e6' : '#24292e'};
    font: 13px Monaco, Menlo, "Ubuntu Mono", monospace;
    line-height: 1.45;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 500px;
    overflow: auto;
    width: 100%;
    box-sizing: border-box;
  `;
  sourcePre.innerHTML = `<code>${escapeXml(sourceStr)}</code>`;
  container.appendChild(sourcePre);

  const sourceButton = document.createElement('button');
  sourceButton.innerHTML = '📝 Source';
  sourceButton.className = 'diagram-action-button packet-source-button';
  sourceButton.onclick = () => {
    showingSource = !showingSource;
    sourceButton.innerHTML = showingSource ? '🎨 View' : '📝 Source';
    svgNode.style.display = showingSource ? 'none' : '';
    sourcePre.style.display = showingSource ? 'block' : 'none';
  };
  actionsContainer.appendChild(sourceButton);

  // Save / export the rendered diagram as a standalone SVG file.
  const saveButton = document.createElement('button');
  saveButton.innerHTML = '💾 Save';
  saveButton.className = 'diagram-action-button packet-save-button';
  saveButton.onclick = () => {
    const svgData = new XMLSerializer().serializeToString(svgNode);
    const svgDoc = `<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
${svgData}`;
    const blob = new Blob([svgDoc], { type: 'image/svg+xml' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `packet-diagram-${Date.now()}.svg`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  actionsContainer.appendChild(saveButton);

  // Theme: re-render the diagram with the opposite light/dark mode.
  const themeButton = document.createElement('button');
  themeButton.innerHTML = isDarkMode ? '☀️ Light' : '🌙 Dark';
  themeButton.className = 'diagram-action-button packet-theme-button';
  themeButton.onclick = () => {
    render(container, d3, rawSpec, !isDarkMode);
  };
  actionsContainer.appendChild(themeButton);

  container.appendChild(actionsContainer);
}

export const packetPlugin: D3RenderPlugin = {
  name: 'packet-renderer',
  priority: 6,
  sizingConfig: {
    sizingStrategy: 'content-driven',
    needsDynamicHeight: true,
    needsOverflowVisible: true,
    observeResize: false,
    containerStyles: {
      width: '100%',
      height: 'auto',
      minHeight: 'unset',
      overflow: 'visible',
    },
  },
  canHandle: (spec: any): boolean => {
    if (spec?.type === 'packet') return true;
    // Detect JSON definition string containing packet structure
    if (typeof spec?.definition === 'string') {
      try {
        const parsed = JSON.parse(spec.definition);
        return !!(parsed?.type === 'packet' || normalizePacketSpec(parsed));
      } catch { return false; }
    }
    return false;
  },
  isDefinitionComplete: (definition: string): boolean => {
    try {
      const parsed = JSON.parse(definition);
      const spec = normalizePacketSpec(parsed);
      return !!(spec && spec.title && spec.sections?.length > 0);
    } catch { return false; }
  },
  render,
};
