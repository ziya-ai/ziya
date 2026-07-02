/**
 * @jest-environment jsdom
 *
 * Regression test for PenPal #153 (CWE-345, HIGH): the postMessage
 * listener in DiagramRenderPage.tsx accepted a diagram spec from ANY
 * window.postMessage sender, with no event.origin check. A malicious
 * page that opened /render via window.open() could inject arbitrary
 * HTML/JS through a crafted Mermaid/Graphviz definition and have it
 * render (and, pre-#160, execute unescaped) with no user interaction.
 *
 * The fix adds a single guard: only messages whose event.origin matches
 * window.location.origin are processed. This test proves the guard is
 * actually enforced end-to-end via the real onmessage listener, not
 * just present as dead code — a cross-origin message must never reach
 * applySpec (observable via data-render-status staying 'idle').
 */

import React from 'react';
import { render, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ isDarkMode: false, setTheme: jest.fn() }),
}));

// D3Renderer is heavy (mermaid/graphviz/etc. async imports); it's never
// expected to mount in these tests since a rejected/absent spec means
// d3Spec stays falsy, but stub it out defensively so a future change
// that DOES let a spec through fails loudly on a real assertion rather
// than an unrelated import error.
jest.mock('../D3Renderer', () => ({
    D3Renderer: () => <div data-testid="d3-renderer-mounted" />,
}));

import { DiagramRenderPage } from '../DiagramRenderPage';

function dispatchDiagramMessage(origin: string, spec: Record<string, unknown>) {
    const event = new MessageEvent('message', {
        data: { type: 'render-diagram', spec },
        origin,
    });
    window.dispatchEvent(event);
}

describe('DiagramRenderPage postMessage origin guard (PenPal #153)', () => {
    it('ignores a render-diagram message from a foreign origin', async () => {
        const { container } = render(<DiagramRenderPage />);
        const root = () => container.querySelector('#diagram-render-root');

        expect(root()).toHaveAttribute('data-render-status', 'idle');

        dispatchDiagramMessage('https://attacker.example', {
            type: 'mermaid',
            definition: 'flowchart LR\nA["<img src=x onerror=alert(1)>"]',
        });

        // Give any (incorrect) async state update a chance to land, then
        // assert the status never left 'idle' and D3Renderer never mounted.
        await new Promise((r) => setTimeout(r, 20));
        expect(root()).toHaveAttribute('data-render-status', 'idle');
        expect(container.querySelector('[data-testid="d3-renderer-mounted"]')).toBeNull();
    });

    it('accepts a render-diagram message from window.location.origin', async () => {
        const { container } = render(<DiagramRenderPage />);
        const root = () => container.querySelector('#diagram-render-root');

        dispatchDiagramMessage(window.location.origin, {
            type: 'mermaid',
            definition: 'flowchart LR\nA --> B',
        });

        await waitFor(() => {
            expect(root()).not.toHaveAttribute('data-render-status', 'idle');
        });
    });
});
