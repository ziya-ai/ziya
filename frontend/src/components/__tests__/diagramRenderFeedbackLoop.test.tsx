/**
 * @jest-environment jsdom
 *
 * The headless-render feedback loop that produced "Diagram render timed out
 * after 35000ms (type=vega-lite) ... last_event='timeout-no-output'" with a
 * console tail showing render #232,449 in a 30s window.
 *
 * Two independent defects in DiagramRenderPage compose into an unbounded
 * re-render storm:
 *
 *   1. `d3Spec` was rebuilt as a fresh object literal on every render, with
 *      no useMemo.  It is passed straight to D3Renderer as `spec`, and
 *      D3Renderer's main render effect lists `spec` in its dependency array.
 *      So EVERY re-render of the page handed the renderer a new prop
 *      identity and re-triggered a render attempt.
 *
 *   2. The MutationObserver's no-output branch called
 *      `setDiag({ elapsedMs: Date.now() - startedAt, ... })`.  `elapsedMs`
 *      differs on every invocation, so the state object was always new and
 *      React could never bail out — every observed mutation forced a
 *      re-render.
 *
 * Together: renderer writes to the DOM -> observer fires -> setDiag -> page
 * re-renders -> new d3Spec identity -> renderer re-triggers -> writes to the
 * DOM -> ... The loop starves the task queue, which is why the in-flight
 * dynamic import of the plugin chunk never settled and `hasPlugin` stayed
 * false for a quarter of a million render attempts.
 *
 * This is a behavioural test, not a source-contract guard: it mounts the real
 * page, drives the real MutationObserver with real DOM mutations, and counts
 * the prop identities the renderer actually receives.  Either fix alone
 * breaks the loop, so the assertions target the observable outcome (bounded
 * render attempts) rather than either mechanism.
 */

import React from 'react';
import { render, act } from '@testing-library/react';
import '@testing-library/jest-dom';

jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ isDarkMode: false, setTheme: jest.fn() }),
}));

/**
 * Every distinct `spec` prop identity the renderer is handed.  A new entry
 * means the page re-rendered AND rebuilt the object, which is exactly what
 * re-triggers D3Renderer's effect in production.
 */
const specIdentities: unknown[] = [];

jest.mock('../D3Renderer', () => ({
    D3Renderer: (props: { spec: unknown }) => {
        specIdentities.push(props.spec);
        return <div data-testid="d3-renderer-mounted" />;
    },
}));

import { DiagramRenderPage } from '../DiagramRenderPage';

const VEGA_LITE_SPEC = {
    type: 'vega-lite',
    definition: JSON.stringify({
        $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
        data: { values: [{ d: 'a', s: 3 }, { d: 'b', s: 4 }] },
        mark: 'bar',
        encoding: { x: { field: 'd', type: 'nominal' }, y: { field: 's', type: 'quantitative' } },
    }),
};

/** Count of distinct object identities (not deep-equal values). */
function distinctIdentities(items: unknown[]): number {
    return new Set(items).size;
}

function container(root: HTMLElement): HTMLElement {
    const node = root.querySelector('#diagram-render-container');
    if (!node) throw new Error('render container not found - the page did not mount it');
    return node as HTMLElement;
}

/**
 * Drive the real MutationObserver the way a plugin does: append nodes into
 * the render container.  jsdom delivers observer callbacks on a microtask,
 * so each batch is flushed before returning.
 */
async function mutate(node: HTMLElement, times: number): Promise<void> {
    for (let i = 0; i < times; i++) {
        await act(async () => {
            node.appendChild(document.createElement('div'));
            // Let the observer callback (microtask) and any resulting state
            // update flush before the next mutation.
            await Promise.resolve();
        });
    }
}

describe('DiagramRenderPage render feedback loop', () => {
    beforeEach(() => {
        specIdentities.length = 0;
    });

    it('mounts the renderer and the container once a spec is injected', async () => {
        // Positive control. Without this, every bound assertion below could
        // pass simply because nothing ever rendered.
        const { container: root } = render(<DiagramRenderPage />);
        await act(async () => {
            (window as any).__renderDiagram(JSON.stringify(VEGA_LITE_SPEC));
        });
        expect(specIdentities.length).toBeGreaterThan(0);
        expect(container(root)).toBeInTheDocument();
    });

    it('does not hand the renderer a new spec identity per DOM mutation', async () => {
        const { container: root } = render(<DiagramRenderPage />);
        await act(async () => {
            (window as any).__renderDiagram(JSON.stringify(VEGA_LITE_SPEC));
        });

        const node = container(root);
        const before = distinctIdentities(specIdentities);

        const MUTATIONS = 30;
        await mutate(node, MUTATIONS);

        const added = distinctIdentities(specIdentities) - before;
        // Pre-fix this grows ~1:1 with mutations (each observer callback
        // re-renders and rebuilds d3Spec), which in production compounded
        // into 232k render attempts. A stable prop yields 0.
        expect(added).toBeLessThan(MUTATIONS / 2);
    });

    it('keeps total render attempts bounded under sustained mutation', async () => {
        const { container: root } = render(<DiagramRenderPage />);
        await act(async () => {
            (window as any).__renderDiagram(JSON.stringify(VEGA_LITE_SPEC));
        });

        const node = container(root);
        await mutate(node, 60);

        // The real failure was unbounded growth. A generous ceiling still
        // separates "bounded" from "one render per mutation".
        expect(specIdentities.length).toBeLessThan(20);
    });

    it('reaches a fixed point: further mutations change nothing', async () => {
        // The sharpest statement of the defect. Once the spec is settled,
        // DOM churn must be inert - a renderer writing into its own
        // container cannot be allowed to provoke its own re-render.
        const { container: root } = render(<DiagramRenderPage />);
        await act(async () => {
            (window as any).__renderDiagram(JSON.stringify(VEGA_LITE_SPEC));
        });

        const node = container(root);
        await mutate(node, 5);          // settle
        const settled = specIdentities.length;

        await mutate(node, 25);         // must be inert
        expect(specIdentities.length).toBe(settled);
    });

    it('still re-renders when the spec genuinely changes', async () => {
        // Guards against "fixing" the loop by freezing the prop forever,
        // which would leave the page unable to render a second diagram.
        const { container: root } = render(<DiagramRenderPage />);
        await act(async () => {
            (window as any).__renderDiagram(JSON.stringify(VEGA_LITE_SPEC));
        });
        const first = specIdentities.length;
        expect(first).toBeGreaterThan(0);

        await act(async () => {
            (window as any).__renderDiagram(JSON.stringify({
                type: 'mermaid', definition: 'flowchart LR\n A-->B',
            }));
        });

        expect(specIdentities.length).toBeGreaterThan(first);
        const latest = specIdentities[specIdentities.length - 1] as any;
        expect(latest.type).toBe('mermaid');
    });
});
