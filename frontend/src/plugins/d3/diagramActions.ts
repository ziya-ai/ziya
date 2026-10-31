/**
 * Shared Open / Source / Save controls for SVG-producing render plugins.
 *
 * mermaid, graphviz, vega(-lite), drawio and packet each carry an inline
 * copy of this cluster; wavedrom, railroad, flamegraph, chord, force-directed
 * and d2 had none.  This module is the one implementation the latter group
 * mounts, so a new SVG plugin gets the trio in one call.
 *
 * Deliberately NOT mounted on latex or music: their output is often a
 * single glyph or a short phrase, and three buttons would cover it.  The
 * same concern is enforced generically by the size guard below -- a render
 * narrower or shorter than the cluster itself gets no controls.
 */
import { getZoomScript, getDownloadSvgScript } from '../../utils/popupScriptUtils';

/** Below either of these (nominal SVG px) the buttons would sit on top of
 *  the drawing instead of beside it, so none are mounted. */
export const MIN_ACTIONS_WIDTH = 200;
export const MIN_ACTIONS_HEIGHT = 48;

export interface DiagramActionsOptions {
    /** The rendered view: hidden while the source is shown, and every <svg>
     *  inside it (or the element itself, if it is one) is what Open and Save
     *  export. */
    view: HTMLElement | SVGElement;
    /** Text shown by the Source toggle -- the author's original definition. */
    source: string;
    /** Popup window title, e.g. 'WaveDrom Diagram'. */
    title: string;
    /** Download name stem; a timestamp and .svg are appended. */
    filenameBase: string;
    isDarkMode: boolean;
    /** Plugin-specific buttons appended after the standard three. */
    extraButtons?: HTMLElement[];
}

/** The author's text for the Source view: the fence body when there is one,
 *  otherwise the spec object pretty-printed. */
export function diagramSourceText(rawSpec: any): string {
    if (typeof rawSpec === 'string') return rawSpec;
    if (rawSpec && 'definition' in rawSpec) {
        const d = rawSpec.definition;
        return typeof d === 'string' ? d : JSON.stringify(d, null, 2);
    }
    try {
        return JSON.stringify(rawSpec, null, 2);
    } catch {
        return String(rawSpec);
    }
}

/** Nominal size from width/height attributes, then viewBox, then layout.
 *  Attributes come first because the plugins set them and jsdom/detached
 *  containers have no layout to measure. */
export function svgNominalSize(svg: SVGElement): { width: number; height: number } | null {
    const px = (v: string | null): number | null => {
        if (!v) return null;
        const n = parseFloat(v);
        return Number.isFinite(n) && !v.trim().endsWith('%') ? n : null;
    };
    const w = px(svg.getAttribute('width'));
    const h = px(svg.getAttribute('height'));
    if (w !== null && h !== null) return { width: w, height: h };
    const vb = (svg.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(Number);
    if (vb.length === 4 && vb.every(Number.isFinite)) {
        return { width: w ?? vb[2], height: h ?? vb[3] };
    }
    const r = svg.getBoundingClientRect?.();
    if (r && r.width > 0 && r.height > 0) return { width: r.width, height: r.height };
    return null;
}

/** True when no SVG in the list is big enough to carry the buttons. */
export function isTooSmallForActions(svgs: SVGElement[]): boolean {
    let maxW = 0, maxH = 0;
    for (const s of svgs) {
        const size = svgNominalSize(s);
        if (!size) continue;
        maxW = Math.max(maxW, size.width);
        maxH = Math.max(maxH, size.height);
    }
    return maxW < MIN_ACTIONS_WIDTH || maxH < MIN_ACTIONS_HEIGHT;
}

function collectSvgs(view: Element): SVGElement[] {
    if (view instanceof SVGElement && view.tagName.toLowerCase() === 'svg') return [view];
    return Array.from(view.querySelectorAll('svg'));
}

function serialize(svgs: SVGElement[]): string[] {
    const ser = new XMLSerializer();
    return svgs.map(s => ser.serializeToString(s));
}

function downloadBlob(blob: Blob, filename: string): void {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const SVG_DOCTYPE = '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
    + '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
    + '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n';

function openPopup(svgs: SVGElement[], title: string, filename: string, isDarkMode: boolean): void {
    const bodies = serialize(svgs);
    let w = 400, h = 300;
    for (const s of svgs) {
        const size = svgNominalSize(s);
        if (!size) continue;
        w = Math.max(w, size.width + 50);
        h += size.height;
    }
    const html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>${title.replace(/[<&]/g, '')}</title>
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
    .container { flex: 1; display: flex; flex-direction: column; justify-content: center;
      align-items: center; overflow: auto; padding: 20px; gap: 12px; }
    svg { max-width: 100%; height: auto; transform-origin: center top; }
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
  <div class="container" id="svg-container">${bodies.join('\n')}</div>
  <script>
    ${getZoomScript()}${getDownloadSvgScript(filename)}
  </script>
</body>
</html>`;
    const blob = new Blob([html], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    const popup = window.open(
        url, title.replace(/\W+/g, ''),
        `width=${Math.round(w)},height=${Math.round(Math.min(h + 100, 1200))},resizable=yes,scrollbars=yes,status=no,toolbar=no,menubar=no,location=no`,
    );
    if (popup) popup.focus();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/**
 * Mount the control cluster into `container`.  Returns the cluster, or null
 * when the view holds no SVG or is too small to carry the buttons -- in that
 * case nothing is added to the DOM.
 */
export function addDiagramActions(container: HTMLElement, opts: DiagramActionsOptions): HTMLDivElement | null {
    const { view, source, title, filenameBase, isDarkMode } = opts;
    const svgs = collectSvgs(view);
    if (svgs.length === 0 || isTooSmallForActions(svgs)) return null;

    const actions = document.createElement('div');
    actions.className = 'diagram-actions';
    const mkBtn = (label: string, cls: string): HTMLButtonElement => {
        const b = document.createElement('button');
        b.textContent = label;
        b.className = `diagram-action-button ${cls}`;
        return b;
    };

    const stamp = () => `${filenameBase}-${Date.now()}`;

    const openButton = mkBtn('↗️ Open', 'diagram-open-button');
    openButton.onclick = () => openPopup(svgs, title, `${stamp()}.svg`, isDarkMode);
    actions.appendChild(openButton);

    // Source toggle: swap the rendered view for the raw definition and back.
    const sourcePre = document.createElement('pre');
    sourcePre.className = 'diagram-source-view';
    sourcePre.style.cssText = `
        display: none; margin: 0; padding: 16px; width: 100%; box-sizing: border-box;
        background: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
        border: 1px solid ${isDarkMode ? '#303030' : '#e1e4e8'}; border-radius: 6px;
        color: ${isDarkMode ? '#e6e6e6' : '#24292e'};
        font: 13px Monaco, Menlo, "Ubuntu Mono", monospace; line-height: 1.45;
        white-space: pre-wrap; word-break: break-word; max-height: 500px; overflow: auto;
    `;
    const code = document.createElement('code');
    code.textContent = source;   // model-authored text: never innerHTML
    sourcePre.appendChild(code);
    container.appendChild(sourcePre);

    let showingSource = false;
    const sourceButton = mkBtn('📝 Source', 'diagram-source-button');
    sourceButton.onclick = () => {
        showingSource = !showingSource;
        sourceButton.textContent = showingSource ? '🎨 View' : '📝 Source';
        (view as HTMLElement).style.display = showingSource ? 'none' : '';
        sourcePre.style.display = showingSource ? 'block' : 'none';
    };
    actions.appendChild(sourceButton);

    // Save: one standalone .svg per rendered SVG (railroad stacks several).
    const saveButton = mkBtn('💾 Save', 'diagram-save-button');
    saveButton.onclick = () => {
        const bodies = serialize(svgs);
        const base = stamp();
        bodies.forEach((body, i) => {
            const name = bodies.length === 1 ? `${base}.svg` : `${base}-${i + 1}.svg`;
            downloadBlob(new Blob([SVG_DOCTYPE + body], { type: 'image/svg+xml' }), name);
        });
    };
    actions.appendChild(saveButton);

    for (const extra of opts.extraButtons || []) actions.appendChild(extra);

    container.appendChild(actions);
    return actions;
}
