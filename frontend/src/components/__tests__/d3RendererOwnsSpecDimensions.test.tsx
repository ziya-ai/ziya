/**
 * @jest-environment jsdom
 *
 * Seam test: renderer-derived width/height must not be written onto a spec
 * that IS the rendered document.
 *
 * D3Renderer built the plugin argument as
 *   { ...spec, width: pluginDims.width, height: pluginDims.height, ... }
 * where pluginDims fell back to the component props (600x400) whenever the
 * spec's width/height were not positive numbers. For an inline Vega-Lite
 * spec — which has no `definition` wrapper, so the spec object is the
 * document — this meant:
 *   - `width: 'container'` (a string) was rewritten to 600;
 *   - an omitted `height` had 400 injected, which vegaLitePlugin then read
 *     as an AUTHORED height (`_heightWasAuthored`) and skipped its height
 *     derivation for.
 *
 * Fix: a plugin declares `ownsSpecDimensions`, and D3Renderer then spreads
 * no width/height at all. Both halves are asserted here — the real
 * vegaLitePlugin sets the flag, and D3Renderer honours it — plus a control
 * plugin without the flag to prove the legacy injection is unchanged.
 *
 * Against unpatched code: `pluginDimensionProps` does not exist (compile
 * failure), and behaviourally the spec arrives with width 600 / height 400.
 */

jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ isDarkMode: false, toggleTheme: () => {}, setTheme: () => {} }),
}));

const renderSpy = jest.fn();
let activePlugin: any;

jest.mock('../../plugins/d3/registry', () => ({
    findPluginForSpec: jest.fn(async () => activePlugin),
    loadPlugin: jest.fn(async () => activePlugin),
    getAvailablePlugins: jest.fn(() => [{ name: 'stub', priority: 1 }]),
}));

import React from 'react';
import { render, waitFor } from '@testing-library/react';
import { D3Renderer } from '../D3Renderer';
import { vegaLitePlugin } from '../../plugins/d3/vegaLitePlugin';
import { pluginDimensionProps, RENDERER_ENVELOPE_KEYS } from '../../utils/pluginDimensions';

const makePlugin = (ownsSpecDimensions: boolean) => ({
    name: ownsSpecDimensions ? 'document-plugin' : 'envelope-plugin',
    priority: 1,
    ownsSpecDimensions,
    canHandle: () => true,
    render: renderSpy.mockImplementation(async () => {}),
});

// Inline Vega-Lite document: no `definition` wrapper, container width, no height.
const inlineVegaSpec = {
    $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
    width: 'container',
    data: { values: [{ a: 'x', b: 1 }] },
    mark: 'bar',
    encoding: {
        x: { field: 'a', type: 'nominal' },
        y: { field: 'b', type: 'quantitative' },
    },
};

beforeAll(() => {
    (global as any).ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
    };
});

beforeEach(() => {
    renderSpy.mockClear();
});

describe('vegaLitePlugin declares that its spec is the document', () => {
    it('sets ownsSpecDimensions (the half D3Renderer keys on)', () => {
        expect(vegaLitePlugin.ownsSpecDimensions).toBe(true);
    });

    it('pluginDimensionProps yields no keys for such a plugin, legacy dims otherwise', () => {
        expect(pluginDimensionProps(vegaLitePlugin, inlineVegaSpec, 600, 400)).toEqual({});
        expect(pluginDimensionProps({ ownsSpecDimensions: false }, {}, 600, 400))
            .toEqual({ width: 600, height: 400 });
        expect(pluginDimensionProps(undefined, { width: 110, height: 80 }, 600, 400))
            .toEqual({ width: 110, height: 80 });
    });

    it('containerWidth is an envelope key the plugin strips before compiling', () => {
        expect(RENDERER_ENVELOPE_KEYS).toContain('containerWidth');
    });
});

describe('D3Renderer honours ownsSpecDimensions at the render call', () => {
    it('leaves width:"container" and an absent height untouched for a document plugin', async () => {
        activePlugin = makePlugin(true);
        render(
            <D3Renderer spec={inlineVegaSpec} type="d3" isStreaming={false} isMarkdownBlockClosed={true} />,
        );
        await waitFor(() => expect(renderSpy).toHaveBeenCalledTimes(1));

        const passed = renderSpy.mock.calls[0][2];
        expect(passed.width).toBe('container');
        expect('height' in passed).toBe(false);
        // Renderer geometry still arrives, under its own key, not the document's.
        expect(passed).toHaveProperty('containerWidth');
        // Positive: it is the real document that was passed, not a stand-in.
        expect(passed.mark).toBe('bar');
    });

    it('still injects the 600x400 fallback for an envelope plugin (legacy path unchanged)', async () => {
        activePlugin = makePlugin(false);
        render(
            <D3Renderer spec={inlineVegaSpec} type="d3" isStreaming={false} isMarkdownBlockClosed={true} />,
        );
        await waitFor(() => expect(renderSpy).toHaveBeenCalledTimes(1));

        const passed = renderSpy.mock.calls[0][2];
        expect(passed.width).toBe(600);
        expect(passed.height).toBe(400);
    });
});
