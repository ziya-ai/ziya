/**
 * @jest-environment jsdom
 *
 * Seam test: D3Renderer must actually wire attachDiagramDrag onto the
 * plugin container for a static diagram, and must NOT for a plugin that
 * owns pointer gestures (pointerInteractive). The utility alone being
 * correct proves nothing if the renderer never mounts it.
 *
 * Against unpatched D3Renderer both halves fail: the packet container is
 * never draggable, and the first assertion rejects.
 */

jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ isDarkMode: false, toggleTheme: () => {}, setTheme: () => {} }),
}));

import React from 'react';
import { render, waitFor } from '@testing-library/react';
import { D3Renderer } from '../D3Renderer';

const plotlyMock: any = {
    newPlot: jest.fn(),
    purge: jest.fn(),
    relayout: jest.fn(),
    Plots: { resize: jest.fn() },
    toImage: jest.fn(),
};

// CRA's jest config has resetMocks: true, which strips implementations from
// module-level jest.fn()s before every test, so they are (re)installed here.
beforeEach(() => {
    plotlyMock.newPlot.mockImplementation(async (div: HTMLElement) => {
        div.appendChild(document.createElement('canvas'));
    });
    plotlyMock.relayout.mockResolvedValue(undefined);
    plotlyMock.Plots.resize.mockResolvedValue(undefined);
    plotlyMock.toImage.mockResolvedValue('data:image/png;base64,QUJD');
});

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

const packetSpec = {
    type: 'packet',
    definition: JSON.stringify({
        type: 'packet',
        title: 'Header',
        sections: [{ name: 'Version', bits: 4 }, { name: 'IHL', bits: 4 }],
    }),
};

const plotlySpec = {
    type: 'plotly',
    definition: JSON.stringify({ data: [{ type: 'scatter', x: [1, 2], y: [3, 4] }], layout: {} }),
};

describe('D3Renderer drag-out wiring', () => {
    it('makes a static (packet) diagram container a native drag source', async () => {
        const { container } = render(
            <D3Renderer spec={packetSpec} type="d3" isStreaming={false} isMarkdownBlockClosed={true} />,
        );
        // Positive: the plugin rendered an SVG, so there is something to drag.
        await waitFor(() => expect(container.querySelector('svg')).not.toBeNull());
        await waitFor(() => {
            const dragSource = container.querySelector('[data-diagram-drag="true"]') as HTMLElement | null;
            expect(dragSource).not.toBeNull();
            // The drag source is the element holding the rendered SVG.
            expect(dragSource!.querySelector('svg')).not.toBeNull();
        });
        // Armed per press, not at rest (a draggable ancestor would block
        // selection and scrolling in a plugin's error panel).
        const dragSource = container.querySelector('[data-diagram-drag="true"]') as HTMLElement;
        expect(dragSource.draggable).toBe(false);
        dragSource.dispatchEvent(Object.assign(new Event('pointerdown', { bubbles: true }), { button: 0 }));
        expect(dragSource.draggable).toBe(true);
        dragSource.dispatchEvent(new Event('pointerup', { bubbles: true }));
        expect(dragSource.draggable).toBe(false);
        // A static diagram gets NO handle: the whole container is the drag
        // source, and the handle only exists where a plugin owns the pointer.
        expect(container.querySelector('[data-diagram-drag-handle="true"]')).toBeNull();
    });

    it('gives an interactive (plotly) plugin a drag handle instead of a draggable container', async () => {
        const { container } = render(
            <D3Renderer spec={plotlySpec} type="d3" isStreaming={false} isMarkdownBlockClosed={true} />,
        );
        await waitFor(() => expect(plotlyMock.newPlot).toHaveBeenCalled());

        // The handle is mounted AND wired: attachDiagramDrag stamps the drag
        // source it was given.
        let handle: HTMLElement | null = null;
        await waitFor(() => {
            handle = container.querySelector('[data-diagram-drag-handle="true"]');
            expect(handle).not.toBeNull();
            expect(handle!.getAttribute('data-diagram-drag')).toBe('true');
            expect(handle!.draggable).toBe(true);
        });
        // The plugin container itself keeps its pointer gestures.
        const pluginContainer = container.querySelector('.plotly-renderer-container') as HTMLElement;
        expect(pluginContainer).not.toBeNull();
        expect(pluginContainer.draggable).toBe(false);
        expect(pluginContainer.hasAttribute('data-diagram-drag')).toBe(false);

        // Hovering the diagram prepares the image through the plugin's own
        // exporter (the host's SVG rasterizer cannot see WebGL traces).
        expect(plotlyMock.toImage).not.toHaveBeenCalled();
        pluginContainer.dispatchEvent(new Event('pointerenter'));
        await waitFor(() => expect(plotlyMock.toImage).toHaveBeenCalledTimes(1));
        expect(plotlyMock.toImage.mock.calls[0][1]).toMatchObject({ format: 'png' });
        // Let the exporter's resolved PNG land in the prepared entry.
        await new Promise((r) => setTimeout(r, 0));

        // Dragging the handle publishes that PNG.
        const data: Record<string, string> = {};
        const ev = new Event('dragstart', { bubbles: true, cancelable: true }) as any;
        ev.dataTransfer = { effectAllowed: 'uninitialized', setData: (k: string, v: string) => { data[k] = v; } };
        handle!.dispatchEvent(ev);
        expect(data['DownloadURL']).toMatch(/^image\/png:plotly-[a-z0-9]+\.png:data:image\/png;base64,QUJD$/);
    });
});
