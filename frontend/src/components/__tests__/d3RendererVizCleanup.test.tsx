/**
 * @jest-environment jsdom
 *
 * Seam test: plotlyPlugin exposes `__vizCleanup` on its container and
 * D3Renderer must actually RUN it on unmount. Either half alone is
 * worthless — the hook was defined but never called is exactly the class of
 * defect this guards (render() returns void; D3Renderer's plugin path used to
 * register no cleanup, so Plotly.purge never ran and WebGL contexts leaked
 * until GC, feeding Chromium's context ceiling).
 *
 * Against unpatched D3Renderer this fails: purge is never called on unmount.
 */

jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

// `d3` resolves via the jest moduleNameMapper in craco.config.js (UMD bundle).

jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ isDarkMode: false, toggleTheme: () => {}, setTheme: () => {} }),
}));

import React from 'react';
import { render, waitFor } from '@testing-library/react';
import { D3Renderer } from '../D3Renderer';

const plotlyMock: any = {
    newPlot: jest.fn().mockImplementation(async (div: HTMLElement) => {
        div.appendChild(document.createElement('canvas'));
    }),
    purge: jest.fn(),
    relayout: jest.fn().mockResolvedValue(undefined),
    Plots: { resize: jest.fn().mockResolvedValue(undefined) },
    toImage: jest.fn().mockResolvedValue('data:image/png;base64,'),
};

beforeAll(() => {
    (global as any).ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
    };
    (window as any).Plotly = plotlyMock;
    (window as any).__plotlyLoaded = true;
    (window as any).__plotlyLoading = undefined;
});

const spec = {
    type: 'plotly',
    definition: JSON.stringify({
        data: [{ type: 'scattergl', x: [1, 2], y: [3, 4] }],
        layout: { title: 'leak' },
    }),
};

describe('D3Renderer runs the plugin teardown hook', () => {
    it('purges the Plotly plot on unmount', async () => {
        const { unmount } = render(
            <D3Renderer spec={spec} type="d3" isStreaming={false} isMarkdownBlockClosed={true} />,
        );

        // Positive: the plot actually rendered through the plugin path, so
        // the hook had a chance to be installed.
        await waitFor(() => expect(plotlyMock.newPlot).toHaveBeenCalledTimes(1));
        const plotDiv = plotlyMock.newPlot.mock.calls[0][0];
        expect(plotlyMock.purge).not.toHaveBeenCalled();

        unmount();

        // The seam: D3Renderer's unmount cleanup called the plugin's hook.
        expect(plotlyMock.purge).toHaveBeenCalledTimes(1);
        expect(plotlyMock.purge).toHaveBeenCalledWith(plotDiv);
    });
});
