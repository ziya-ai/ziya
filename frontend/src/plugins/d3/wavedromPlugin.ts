/**
 * D3 render plugin for WaveDrom digital timing diagrams.
 *
 * Recognises specs with `type: 'wavedrom'`.  Rendering is delegated to the
 * wavedrom npm package (MIT), lazily imported so its ~700KB never lands in
 * the main bundle.  Parsing, validation, skin selection and the
 * style-scoping pass live in utils/d3Plugins/wavedromPlugin (pure,
 * unit-tested); this wrapper owns the dependency, mounting, and the error
 * surface — the same split the packet and railroad plugins use.
 */
import { D3RenderPlugin } from '../../types/d3';
import {
    parseWaveJson,
    validateWaveDromSpec,
    applySkin,
    scopeSvgStyles,
    themeSvgSurface,
} from '../../utils/d3Plugins/wavedromPlugin';
import { extractDefinition } from '../../utils/d3Plugins/specEnvelope';

/**
 * Monotonic render index.  WaveDrom bakes the index into the element ids it
 * generates, so two diagrams on one page must not share one or their DOM
 * ids collide.  It also seeds the per-render style scope id.
 */
let renderIndex = 0;

function renderError(container: HTMLElement, message: string, rawSpec: any,
                     isDarkMode: boolean): void {
    const specStr = typeof rawSpec === 'string' ? rawSpec
        : typeof rawSpec?.definition === 'string' ? rawSpec.definition
        : JSON.stringify(rawSpec, null, 2);
    const escape = (s: string) => s
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

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
      <strong>WaveDrom diagram error:</strong> ${escape(message)}
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
        "><code>${escape(specStr || '(empty)')}</code></pre>
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

async function render(container: HTMLElement, _d3: any, rawSpec: any,
                      isDarkMode: boolean): Promise<void> {
    const definition = extractDefinition(rawSpec);
    const source = typeof definition === 'string'
        ? parseWaveJson(definition) : definition;
    if (source === undefined) {
        renderError(container,
            'definition is not valid WaveJSON (JSON5 style — unquoted keys '
            + 'and single quotes — is fine, but the structure must close)',
            rawSpec, isDarkMode);
        return;
    }
    const problem = validateWaveDromSpec(source);
    if (problem) {
        renderError(container, problem, rawSpec, isDarkMode);
        return;
    }

    let wavedrom: any;
    let skins: Record<string, unknown>;
    try {
        const [wavedromMod, defSkin, darkSkin] = await Promise.all([
            import('wavedrom'),
            import('wavedrom/skins/default.js'),
            import('wavedrom/skins/dark.js'),
        ]);
        wavedrom = (wavedromMod as any).default ?? wavedromMod;
        // Each skin module exports its accumulated WaveSkin object
        // ({default: <onml>} / {dark: <onml>}).  The interop unwrap must
        // check Array.isArray: WaveSkin's own 'default' KEY holds an onml
        // ARRAY, so a bare truthiness test on .default would mistake an
        // already-unwrapped WaveSkin for a module namespace and spread an
        // array into numeric keys.
        const unwrap = (m: any) =>
            (m && m.default && !Array.isArray(m.default)) ? m.default : m;
        skins = { ...unwrap(defSkin), ...unwrap(darkSkin) };
    } catch (e: any) {
        renderError(container,
            `wavedrom failed to load: ${e?.message || e}`,
            rawSpec, isDarkMode);
        return;
    }

    const idx = renderIndex++;
    let svg: string;
    try {
        const themed = applySkin(source, isDarkMode, skins);
        const res = wavedrom.renderAny(idx, themed, skins);
        svg = wavedrom.onml.stringify(res);
    } catch (e: any) {
        renderError(container, e?.message || String(e), rawSpec, isDarkMode);
        return;
    }
    svg = scopeSvgStyles(svg, `ziya-wavedrom-${idx}`);
    // Dark mode needs the surface repaired after scoping: WaveDrom hardcodes
    // a white backdrop rect and black bitfield ink irrespective of skin.
    svg = themeSvgSurface(svg, isDarkMode);

    container.innerHTML = '';
    const wrap = document.createElement('div');
    wrap.style.overflowX = 'auto';
    wrap.style.padding = '8px 0';
    wrap.innerHTML = svg;
    const svgEl = wrap.firstElementChild as SVGElement | null;
    if (svgEl) {
        (svgEl as any).style.display = 'block';
        (svgEl as any).style.maxWidth = '100%';
        (svgEl as any).style.height = 'auto';
    }
    container.appendChild(wrap);
}

export const wavedromPlugin: D3RenderPlugin = {
    name: 'wavedrom-renderer',
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
    canHandle: (spec: any): boolean => spec?.type === 'wavedrom',
    // Streaming gate: a partial WaveJSON body parses to undefined, so the
    // renderer waits for the closed block instead of flashing error cards.
    isDefinitionComplete: (definition: string): boolean =>
        parseWaveJson(definition) !== undefined,
    render,
};
