/**
 * D3 render plugin for interactive flame graphs.
 *
 * Recognises specs with `type: 'flamegraph'`.  Rendering is delegated to
 * d3-flame-graph (Apache-2.0), lazily imported so it stays out of the main
 * bundle.  Input handling, validation and the scoped theme CSS live in
 * utils/d3Plugins/flamegraphPlugin (pure, unit-tested); this wrapper owns
 * the dependency, mounting and the error surface.
 *
 * Unlike the other diagram plugins this one is genuinely interactive:
 * clicking a frame zooms to it, clicking the root resets.  It still
 * renders a real <svg>, so visualizationCapture's querySelector('svg')
 * path captures it for export like any other diagram.
 */
import { D3RenderPlugin } from '../../types/d3';
import {
    parseFlamegraphInput,
    validateFlamegraphNode,
    flamegraphCss,
} from '../../utils/d3Plugins/flamegraphPlugin';
import { extractDefinition } from '../../utils/d3Plugins/specEnvelope';

/** Monotonic index, so two charts on a page get distinct style scopes. */
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
      <strong>Flame graph error:</strong> ${escape(message)}
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
}

async function render(container: HTMLElement, d3: any, rawSpec: any,
                      isDarkMode: boolean): Promise<void | (() => void)> {
    const definition = extractDefinition(rawSpec);

    let parsed;
    try {
        parsed = parseFlamegraphInput(definition);
    } catch (e: any) {
        renderError(container, e?.message || String(e), rawSpec, isDarkMode);
        return;
    }
    const problem = validateFlamegraphNode(parsed.root);
    if (problem) {
        renderError(container, problem, rawSpec, isDarkMode);
        return;
    }

    let flamegraph: any;
    let select: any;
    try {
        const mod: any = await import('d3-flame-graph');
        flamegraph = mod.default ?? mod.flamegraph ?? mod;
        // D3Renderer passes the full d3 namespace; fall back to importing
        // it rather than reaching for the transitive d3-selection package.
        select = typeof d3?.select === 'function'
            ? d3.select
            : (await import('d3')).select;
    } catch (e: any) {
        renderError(container,
            `d3-flame-graph failed to load: ${e?.message || e}`,
            rawSpec, isDarkMode);
        return;
    }

    const idx = renderIndex++;
    const scopeId = `ziya-flamegraph-${idx}`;
    // The container D3Renderer passes is DETACHED at render time (it is
    // attached only after a successful render), so clientWidth reads 0
    // here.  The measured width arrives on the spec instead.
    const width = Math.max(320, Math.min(Number(rawSpec?.width) || 800, 2400));

    container.innerHTML = '';
    const wrap = document.createElement('div');
    // Height is left unset so the library derives it from stack depth.
    // The scroller covers a chart wider than a narrow viewport; the SVG
    // carries no viewBox (the library does not emit one, and it would go
    // stale on zoom), so it is not scaled down.
    wrap.style.overflowX = 'auto';
    wrap.style.padding = '8px 0';
    container.appendChild(wrap);

    let chart: any;
    try {
        chart = flamegraph()
            .width(width)
            .cellHeight(18)
            .minFrameSize(1)
            .transitionDuration(250)
            .sort(true)
            .title('');
        select(wrap).datum(parsed.root).call(chart);
    } catch (e: any) {
        renderError(container, e?.message || String(e), rawSpec, isDarkMode);
        return;
    }

    const svgEl = wrap.querySelector('svg');
    if (svgEl) {
        svgEl.setAttribute('id', scopeId);
        const style = document.createElementNS(
            'http://www.w3.org/2000/svg', 'style');
        style.textContent = flamegraphCss(scopeId, isDarkMode);
        // Prepended, and not inside a <g>, so the library's own
        // selectAll('g').data(...).exit().remove() on zoom leaves it be.
        svgEl.insertBefore(style, svgEl.firstChild);
        (svgEl as any).style.display = 'block';
    }

    // Returned for correctness, but note D3Renderer currently discards the
    // cleanup function on the plugin path (it only captures one from the
    // inline-render path), so this does not run today.
    return () => {
        try { chart.destroy(); } catch { /* already torn down */ }
    };
}

export const flamegraphPlugin: D3RenderPlugin = {
    name: 'flamegraph-renderer',
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
    canHandle: (spec: any): boolean => spec?.type === 'flamegraph',
    // Streaming gate.  A partial trailing collapsed-stack line (or an
    // unclosed JSON body) fails to parse, so the renderer waits instead of
    // flashing error cards.  D3Renderer stops consulting this once the
    // markdown block closes, so a genuinely malformed profile still gets
    // an error card rather than rendering nothing forever.
    isDefinitionComplete: (definition: string): boolean => {
        try {
            parseFlamegraphInput(definition);
            return true;
        } catch {
            return false;
        }
    },
    render,
};
