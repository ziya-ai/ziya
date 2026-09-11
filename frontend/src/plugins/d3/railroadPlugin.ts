/**
 * D3 render plugin for railroad (syntax) diagrams.
 *
 * Recognises specs with `type: 'railroad'`.  All layout and SVG generation
 * live in utils/d3Plugins/railroadPlugin (pure, unit-tested); this wrapper
 * owns mounting, the title/rule-name headings, the error surface, and theme
 * selection — the same split the packet plugin uses.
 */
import { D3RenderPlugin } from '../../types/d3';
import {
    renderRailroadSvg,
    lenientJsonParse,
} from '../../utils/d3Plugins/railroadPlugin';
import {
    extractDefinition,
    isStreamingIncomplete,
} from '../../utils/d3Plugins/specEnvelope';

/** A railroad body is renderable once it lenient-parses; a truncated one
 *  yields undefined.  Shared by the streaming guard in render() and by the
 *  plugin's isDefinitionComplete so the two can never disagree. */
const railroadDefinitionComplete = (definition: string): boolean =>
    lenientJsonParse(definition) !== undefined;

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
      <strong>Railroad diagram error:</strong> ${escape(message)}
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

    // Tag the card so the headless render harness can fail fast with this
    // message. Without the marker its completion poll waits for an
    // svg/canvas/img that an error card never contains, so the render burns
    // the full safety timeout and then reports a generic "svg:0" snapshot in
    // place of the message above.
    //
    // Set via setAttribute rather than interpolated into the template: the
    // message embeds model-authored JSON keys, so a quoted attribute value
    // would need attribute-level escaping (escape() above covers &, <, > but
    // not ") to avoid breaking out of the attribute.
    const card = container.firstElementChild;
    if (card) card.setAttribute('data-diagram-error', message);
}

function render(container: HTMLElement, _d3: any, rawSpec: any,
                isDarkMode: boolean): void {
    const definition = extractDefinition(rawSpec);

    // Mid-stream the body arrives truncated, and canHandle claims the spec by
    // TYPE, so render() runs on every chunk.  Painting an error card here
    // flickers a red card until the fence closes, and it goes directly into the
    // container, so D3Renderer's isStreaming error suppression cannot hide it.
    // Skip silently and retry on the next chunk.  A body that PARSES but is
    // invalid falls through: that error is real and must still be shown.
    if (isStreamingIncomplete(rawSpec, railroadDefinitionComplete)) return;

    let result;
    try {
        result = renderRailroadSvg(definition, isDarkMode);
    } catch (e: any) {
        renderError(container, e?.message || String(e), rawSpec, isDarkMode);
        return;
    }

    container.innerHTML = '';
    const wrap = document.createElement('div');
    wrap.style.overflowX = 'auto';
    wrap.style.padding = '8px 0';

    // Title and rule names go through textContent, never innerHTML: they are
    // model-authored strings.  The SVG itself is safe by construction — every
    // label passes through escapeXml in the layout engine.
    if (result.title) {
        const h = document.createElement('div');
        h.textContent = result.title;
        h.style.cssText =
            'font: bold 15px monospace; margin: 4px 0 10px 4px;';
        wrap.appendChild(h);
    }
    for (const rule of result.rules) {
        if (rule.name) {
            const name = document.createElement('div');
            name.textContent = `${rule.name}:`;
            name.style.cssText =
                'font: bold 13px monospace; opacity: 0.85; margin: 8px 0 0 4px;';
            wrap.appendChild(name);
        }
        const holder = document.createElement('div');
        holder.innerHTML = rule.svg;
        const svg = holder.firstElementChild as SVGElement | null;
        if (svg) {
            (svg as any).style.display = 'block';
            (svg as any).style.maxWidth = '100%';
            (svg as any).style.height = 'auto';
            wrap.appendChild(svg);
        }
    }
    container.appendChild(wrap);
}

export const railroadPlugin: D3RenderPlugin = {
    name: 'railroad-renderer',
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
    canHandle: (spec: any): boolean => spec?.type === 'railroad',
    // Retained for callers that DO consult it (the headless harness, and
    // D3Renderer's string-spec path).  render() applies the same predicate
    // itself, because D3Renderer never reaches this hook for the object
    // envelope a markdown fence produces.
    isDefinitionComplete: railroadDefinitionComplete,
    render,
};
