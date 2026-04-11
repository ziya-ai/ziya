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
  assignBracketDepths, escapeXml,
  normalizePacketSpec,
} from '../../utils/d3Plugins/packetPlugin';
import { getOptimalTextColor } from '../../utils/colorUtils';

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
}

function render(container: HTMLElement, d3: any, rawSpec: any, isDarkMode: boolean): void {
  // Accept either a direct PacketSpec or { definition: jsonString }
  let pkt: PacketSpec;
  if (typeof rawSpec.definition === 'string') {
    try { pkt = JSON.parse(rawSpec.definition); }
    catch { renderError(container, 'Invalid JSON in definition', rawSpec, isDarkMode); return; }
  } else {
    pkt = rawSpec as PacketSpec;
  }

  // Normalize common alternate formats (flat fields, array wrapper, name/width aliases)
  const normalized = normalizePacketSpec(pkt);
  if (normalized) pkt = normalized;

  // Validate required fields before attempting to render
  if (!pkt.sections || !Array.isArray(pkt.sections) || pkt.sections.length === 0) {
    renderError(container, 'Requires a "sections" array with at least one section', rawSpec, isDarkMode);
    return;
  }

  const bits = pkt.bitWidth ?? 8;
  const { width, height, layout: L } = computeDimensions(pkt);
  const GRID_W = bits * L.BIT_W;

  // Compute bracket space on each side
  let maxLeftDepth = 0, maxRightDepth = 0;
  for (const sec of pkt.sections) {
    for (const br of sec.brackets ?? []) {
      const side = br.side ?? 'right';
      const d = (br.depth ?? 0) + 1;
      if (side === 'left') maxLeftDepth = Math.max(maxLeftDepth, d);
      else maxRightDepth = Math.max(maxRightDepth, d);
    }
  }
  const bracketLeftW = maxLeftDepth * L.BRACKET_W;
  const gridX = L.LEFT_PAD + bracketLeftW + L.LABEL_W;

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
    const secRows = sec.rows ?? [];
    const secH = secRows.length * L.ROW_H;
    const sectionColor = resolveColor(sec.color, isDarkMode, sectionIdx);

    // Section label (left column, vertically centered)
    const lines = sec.label.split('\n');
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

    // Brackets — auto-assign depths per side, then render
    const allBrackets = sec.brackets ?? [];
    const rightBrackets = assignBracketDepths(allBrackets, 'right');
    const leftBrackets  = assignBracketDepths(allBrackets, 'left');

    const renderBrackets = (brs: PacketBracket[], side: 'left' | 'right') => {
      // Monospace char width at 10px bold ≈ 6.5px
      const CHAR_W = 6.5;
      const BASE_FONT_SIZE = 10;
      const MIN_FONT_SIZE = 6;
      const LABEL_PAD = 4;

      // Pre-compute label geometry for overlap detection
      const labelInfos = brs.map(br => {
        const by1 = secY + br.start_row * L.ROW_H;
        const by2 = secY + (br.end_row + 1) * L.ROW_H;
        const labelY = (by1 + by2) / 2;
        const halfTextW = (br.label.length * CHAR_W) / 2;
        return { br, by1, by2, labelY, yMin: labelY - halfTextW, yMax: labelY + halfTextW };
      });

      // Assign label offsets: shift labels outward when they would
      // overlap vertically with another label at the same bracket depth.
      const labelOffsets = new Map<PacketBracket, number>();
      for (let i = 0; i < labelInfos.length; i++) {
        let extraShift = 0;
        const a = labelInfos[i];
        for (let j = 0; j < i; j++) {
          const b = labelInfos[j];
          if ((a.br.depth ?? 0) !== (b.br.depth ?? 0)) continue;
          // Check vertical overlap of rotated text extents
          const prevShift = labelOffsets.get(b.br) ?? 0;
          if (a.yMin < b.yMax + LABEL_PAD && a.yMax > b.yMin - LABEL_PAD) {
            extraShift = Math.max(extraShift, prevShift + 14);
          }
        }
        labelOffsets.set(a.br, extraShift);
      }

      labelInfos.forEach(({ br, by1, by2, labelY }) => {
        const depth = br.depth ?? 0;
        const offset = 4 + depth * 30;

        let bx: number;
        if (side === 'right') {
          bx = gridX + GRID_W + offset;
        } else {
          bx = gridX - L.LABEL_W + L.LABEL_W - offset - 12;
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

        // Bracket label (rotated), shifted outward if overlapping
        const extraShift = labelOffsets.get(br) ?? 0;
        const labelX = bx + tickDir * (20 + extraShift);

        // Scale font to fit within bracket span if label is too long
        const spanH = by2 - by1 - 4;  // usable vertical space
        const textAtBase = br.label.length * CHAR_W;
        let fontSize = BASE_FONT_SIZE;
        if (textAtBase > spanH && spanH > 0) {
          fontSize = Math.max(MIN_FONT_SIZE, Math.floor(BASE_FONT_SIZE * spanH / textAtBase));
        }

        svg.append('text')
          .attr('x', labelX).attr('y', labelY)
          .attr('transform', `rotate(${side === 'right' ? 90 : -90}, ${labelX}, ${labelY})`)
          .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
          .attr('fill', textFill)
          .style('font', `bold ${fontSize}px "Consolas", "Courier New", monospace`)
          .text(br.label);
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
        const fbits = field[1] as number;
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
          // Use getOptimalTextColor against actual bg for accessibility
          const labelColor = getOptimalTextColor(c.bg);
          g.append('text')
            .attr('x', fx + fw / 2).attr('y', ry + L.ROW_H / 2)
            .attr('text-anchor', 'middle').attr('dominant-baseline', 'central')
            .attr('fill', labelColor)
            .style('font', 'bold 11px "Segoe UI", Arial, sans-serif')
            .text(name);
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

    y += secH + L.SECTION_GAP;
  });

  // Bottom ruler
  drawRuler(y);
}

export const packetPlugin: D3RenderPlugin = {
  name: 'packet-renderer',
  priority: 6,
  sizingConfig: {
    sizingStrategy: 'content-driven',
    needsDynamicHeight: true,
    needsOverflowVisible: false,
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
