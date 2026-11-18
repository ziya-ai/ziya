/**
 * Seams of "light-mode diagrams follow the paper tone" that cannot be
 * exercised by importing a pure function: the renderers that paint their
 * surface inside render(), the re-render when the tone changes, and the two
 * capture pages that must stay white.
 *
 * Source assertions, comments stripped.  The helper itself and the pure
 * resolvers are in plugins/d3/__tests__/chartSurfacePaperTone.test.ts.
 */
import * as fs from 'fs';
import * as path from 'path';

const read = (...p: string[]) =>
    fs.readFileSync(path.resolve(__dirname, '..', '..', ...p), 'utf8')
        .split('\n').filter(l => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n');

const plugin = (name: string) => read('plugins', 'd3', name);

describe('renderers that paint a light surface use the paper colour', () => {
    it('vega-lite: the embed config sets the background in light mode only', () => {
        const src = plugin('vegaLitePlugin.ts');
        expect(src).toContain('...(isDarkMode ? {} : { background: lightChartSurface() }),');
        expect(src).toMatch(/import \{[^}]*\blightChartSurface\b[^}]*\} from '\.\/chartTheme';/);
    });

    it('joint paper', () => {
        expect(plugin('jointPlugin.ts'))
            .toContain("color: theme === 'dark' ? '#1f1f1f' : lightChartSurface()");
    });

    it('packet backdrop (also the reference for field-label contrast)', () => {
        const src = plugin('packetPlugin.ts');
        expect(src).toContain("const bgColor = isDarkMode ? '#1e1e1e' : lightChartSurface();");
        expect(src).toContain('effectiveCellBackdrop(c.bg, bgColor)');
    });

    it('orbit theme background (wrapper, globe clear colour, label halo)', () => {
        const src = plugin('orbitPlugin.ts');
        expect(src).toContain('background: lightChartSurface(), ocean:');
        expect(src).toContain('.backgroundColor(theme.background)');
        expect(src).toContain('0 0 3px ${theme.background}');
    });
});

describe('masks and contrast references no longer assume white', () => {
    it.each([
        ['drawioPlugin.ts'], ['mermaidEnhancer.ts'], ['chartTheme.ts'],
    ])('%s has no light/dark ternary on the constants', (file) => {
        expect(plugin(file)).not.toMatch(/\?\s*CHART_DARK_BG\s*:\s*CHART_LIGHT_BG/);
        expect(plugin(file)).toContain('chartSurface(isDarkMode)');
    });

    it.each([
        ['d2Plugin.ts', 'D2_DARK_BG : lightChartSurface()', 2],
        ['graphvizPlugin.ts', 'GRAPHVIZ_DARK_PANEL_BG : lightChartSurface()', 2],
        ['networkDiagram.ts', 'NETWORK_DARK_BG : lightChartSurface()', 1],
        ['forceDirectedPlugin.ts', 'FORCE_DARK_BG : lightChartSurface()', 1],
        ['mermaidPlugin.ts', "'#1f1f1f' : lightChartSurface()", 2],
    ])('%s', (file, needle, count) => {
        expect(plugin(file as string).split(needle as string).length - 1).toBe(count);
    });
});

describe('D3Renderer re-renders and re-keys on the paper tone', () => {
    const src = read('components', 'D3Renderer.tsx');

    it('a parked render is keyed on theme AND paper', () => {
        expect(src).toMatch(/function renderThemeKey\(isDarkMode: boolean\): string/);
        expect(src).toContain('getAppearance().paper');
        expect(src).toContain('parkRender(cacheKeyRef.current, parkable, cleanupFunctionsRef.current, renderThemeKey(isDarkModeRef.current))');
        expect(src).toContain('claimParkedRender(cacheKey, renderThemeKey(isDarkModeRef.current))');
        expect(src).toContain('entry.themeKey !== themeKey');
    });

    it('a tone change forces a render, in light mode only, and unsubscribes', () => {
        const at = src.indexOf('subscribeAppearance(');
        expect(at).toBeGreaterThan(-1);
        const effect = src.slice(src.lastIndexOf('useEffect(() => {', at), src.indexOf('}, []);', at));
        expect(effect).toContain('if (prefs.paper === lastPaper) return;');
        expect(effect).toContain('isDarkModeRef.current');
        expect(effect).toContain('hasSuccessfulRenderRef.current');
        expect(effect).toContain('initializeVisualization(true)');
        expect(effect).toContain('unsubscribe();');
    });
});

describe('capture pages keep a white surface', () => {
    it.each([['DiagramRenderPage.tsx'], ['PrintRenderPage.tsx']])('%s pins it', (file) => {
        const src = read('components', file);
        expect(src).toContain("pinLightChartSurface('#ffffff');");
        expect(src).toMatch(/import \{ pinLightChartSurface \} from '\.\.\/plugins\/d3\/chartTheme';/);
    });
});

describe('images that leave the page are white in light mode', () => {
    it('plotly: both image exports go through plotlyExportLayout', () => {
        const src = plugin('plotlyPlugin.ts');
        expect(src).toContain('export function plotlyExportLayout(');
        // The drag-out exporter and the Save button.
        expect(src.split('plotlyExportImage(Plotly, ').length - 1).toBe(2);
        const at = src.indexOf('function plotlyExportImage(');
        expect(at).toBeGreaterThan(-1);
        const body = src.slice(at, src.indexOf('\n}\n', at));
        expect(body).toContain('plotlyExportLayout(gd.layout, isDarkMode)');
        expect(body).toContain('Plotly.toImage({ data: gd.data, layout }, opts)');
        // Unchanged layout: the live plot is exported as before.
        expect(body).toContain('Plotly.toImage(gd, opts)');
    });

    it('diagram and table drag default to exportBackground', () => {
        const dragSrc = read('utils', 'diagramDrag.ts');
        expect(dragSrc).toContain('options.getBackground ?? (() => exportBackground(container))');
        expect(dragSrc).toContain('whitenPaperSurface(serialized.svgString, lightChartSurface())');
        const tableSrc = read('utils', 'tableDrag.ts');
        expect(tableSrc).toContain('options.getBackground ?? (() => exportBackground(container))');
        expect(tableSrc).not.toContain('resolveBackground(container)');
    });
});
