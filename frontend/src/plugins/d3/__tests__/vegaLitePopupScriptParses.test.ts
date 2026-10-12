/**
 * The Vega-Lite "Open" popup's inline <script> must parse, and the window
 * must show a chart even when vega-embed cannot be loaded.
 *
 * Reported: clicking Open on any Vega-Lite chart showed the toolbar over an
 * empty pane. The popup script declared \`let currentScale = 1;\` and then
 * interpolated getZoomScript(), which declares it again. A duplicate \`let\`
 * is a parse-time SyntaxError, so none of the script ran.
 *
 * Second cause of the same symptom: the popup re-renders the chart with
 * vega-embed imported from cdn.jsdelivr.net, and cleared the pane before the
 * import. With the CDN blocked or offline the import rejected, nothing caught
 * it, and the pane stayed empty although the popup had been handed the inline
 * chart's SVG. The popup now shows that SVG at once and keeps it unless a live
 * render succeeds.
 *
 * The tests pull the script body out of the source and evaluate the outer TS
 * template-literal layer with literal stand-ins for its interpolations. The
 * behaviour tests then run it in jsdom with the CDN import replaced by a stub
 * loader. Against the unpatched plugin the parse tests throw "Identifier
 * 'currentScale' has already been declared" and the fallback tests find an
 * empty pane.
 */
import * as fs from 'fs';
import * as path from 'path';
import { getZoomScript } from '../../../utils/popupScriptUtils';

const src = fs.readFileSync(path.join(__dirname, '../vegaLitePlugin.ts'), 'utf8');
const CDN_IMPORT = "import('https://cdn.jsdelivr.net/npm/vega-embed@6')";
const SNAPSHOT = '<svg id="snapshot"><text>inline </text></svg>';

function handlerSource(): string {
    const handler = src.indexOf("openButton.className = 'diagram-action-button vega-lite-open-button'");
    expect(handler).toBeGreaterThan(-1);
    const end = src.indexOf('actionsContainer.appendChild(openButton)', handler);
    expect(end).toBeGreaterThan(handler);
    return src.slice(handler, end);
}

function popupScript(): string {
    const h = handlerSource();
    const open = h.indexOf('<script>');
    const close = h.indexOf('</script>', open);
    expect(open).toBeGreaterThan(-1);
    expect(close).toBeGreaterThan(open);
    return h.slice(open + '<script>'.length, close);
}

/** Evaluate the outer TS template-literal layer: \` and \${ unescape;
 *  ${getZoomScript()} becomes its real text; other ${...} become literals.
 *  The svgData interpolation becomes a JS string literal of SNAPSHOT. */
function materialize(body: string): string {
    const ZOOM = '\u0000ZOOM\u0000';
    let out = body.replace(/\$\{getZoomScript\(\)\}/g, ZOOM);
    out = out.replace(/\$\{JSON\.stringify\(svgData\)[^}]*\}/g, JSON.stringify(SNAPSHOT));
    out = out.replace(/\$\{JSON\.stringify\(vegaSpec[^}]*\}/g, '{}');
    out = out.replace(/\$\{isDarkMode\}/g, 'true');
    out = out.replace(/\$\{Date\.now\(\)\}/g, '0');
    out = out.replace(/\\`/g, '`').replace(/\\\$\{/g, '${');
    return out.split(ZOOM).join(getZoomScript());
}

const flush = async () => { for (let i = 0; i < 5; i++) await new Promise(r => setTimeout(r, 0)); };

function runPopup(loader: () => Promise<any>): { pane: HTMLElement; toggleTheme: () => void } {
    document.body.innerHTML =
        '<button class="theme-toggle"></button><div class="container" id="svg-container">\n</div>';
    const script = materialize(popupScript());
    expect(script).toContain(CDN_IMPORT);
    const runnable = script.split(CDN_IMPORT).join('__loadVegaEmbed()');
    // eslint-disable-next-line no-new-func
    const api = new Function('__loadVegaEmbed', runnable + '\n;return { toggleTheme };')(loader);
    return { pane: document.getElementById('svg-container')!, toggleTheme: api.toggleTheme };
}

const fakeEmbed = (el: HTMLElement) => {
    el.innerHTML = '<svg id="live"></svg>';
    return Promise.resolve({ view: {} });
};

describe('Vega-Lite popup script', () => {
    it('parses as JavaScript', () => {
        const script = materialize(popupScript());
        // Positive check: the extracted block is the popup script that starts
        // the render and has the zoom handlers.
        expect(script).toContain('reRenderVegaVisualization()');
        expect(script).toContain('function zoomIn()');
        expect(() => new Function(script)).not.toThrow();
    });

    it('declares currentScale exactly once', () => {
        const script = materialize(popupScript());
        const decls = script.match(/\b(?:let|const|var)\s+currentScale\b/g) || [];
        expect(decls).toHaveLength(1);
    });

    it('escapes "<" in the embedded snapshot so chart text cannot close the script', () => {
        expect(handlerSource()).toMatch(/JSON\.stringify\(svgData\)\.replace\(\/<\/g, '\\\\u003c'\)/);
    });
});

describe('Vega-Lite popup fallback', () => {
    let errSpy: jest.SpyInstance;
    beforeEach(() => { errSpy = jest.spyOn(console, 'error').mockImplementation(() => {}); });
    afterEach(() => errSpy.mockRestore());

    it('shows the inline snapshot while vega-embed is loading', () => {
        const { pane } = runPopup(() => new Promise(() => {}));
        expect(pane.querySelector('#snapshot')).not.toBeNull();
    });

    it('keeps the snapshot when the CDN import fails', async () => {
        const { pane } = runPopup(() => Promise.reject(new Error('blocked')));
        await flush();
        expect(pane.querySelector('#snapshot')).not.toBeNull();
        expect(pane.querySelector('#live')).toBeNull();
        expect(errSpy).toHaveBeenCalled();
    });

    it('keeps the snapshot when vega-embed itself rejects', async () => {
        const { pane } = runPopup(() => Promise.resolve({ default: () => Promise.reject(new Error('bad spec')) }));
        await flush();
        expect(pane.querySelector('#snapshot')).not.toBeNull();
    });

    it('replaces the snapshot with the live render when vega-embed succeeds', async () => {
        const { pane } = runPopup(() => Promise.resolve({ default: fakeEmbed }));
        await flush();
        expect(pane.querySelector('#live')).not.toBeNull();
        expect(pane.querySelector('#snapshot')).toBeNull();
    });

    it('keeps the current chart when a theme re-render fails', async () => {
        let mode: 'ok' | 'fail' = 'ok';
        const { pane, toggleTheme } = runPopup(() => mode === 'ok'
            ? Promise.resolve({ default: fakeEmbed })
            : Promise.reject(new Error('offline')));
        await flush();
        expect(pane.querySelector('#live')).not.toBeNull();
        mode = 'fail';
        toggleTheme();
        await flush();
        expect(pane.querySelector('#live')).not.toBeNull();
        expect(pane.querySelectorAll('svg')).toHaveLength(1);
    });
});
