/**
 * @jest-environment jsdom
 *
 * Regression test: a diagram whose body is still arriving when D3Renderer
 * first mounts rendered only AFTER the whole message stream ended, not when
 * its own fenced block closed.  Music showed it 100% of the time (music JSON
 * is verbose, so the fence never arrives whole in one throttled display
 * update); smaller diagrams that happened to arrive complete-on-first-mount
 * escaped it, which is why "about half" of railroad/wavedrom rendered at block
 * close and the rest waited for end of stream.
 *
 * ROOT CAUSE
 *
 * D3Renderer.initializeVisualization is a useCallback with EMPTY deps, so the
 * `spec` (and isStreaming / isMarkdownBlockClosed / derived cacheKey) it closes
 * over are FROZEN at first mount.  The main render effect correctly re-fires
 * when the `spec` prop changes and even recomputes a fresh spec hash, but it
 * then calls the frozen callback, which re-evaluates the STALE first-mount
 * (partial) spec every time.  For a content-gated plugin like music, canHandle
 * keeps rejecting the truncated body, so no plugin ever loads and the raw
 * "Specification" pane stays up until the parent remounts the component at end
 * of stream.
 *
 * FIX: the callback reads the live spec/flags through refs the component keeps
 * in sync, shadowing the stale closure bindings.
 *
 * WHAT THIS PINS (the seam, not a half): the SAME D3Renderer instance is handed
 * an incomplete music spec and then the completed one via rerender (no key, so
 * React preserves the instance and its frozen closure).  The completed diagram
 * must appear without a remount.  Against unpatched source the frozen closure
 * keeps evaluating the incomplete body, no <svg> is ever produced, and the
 * final waitFor times out.
 *
 * NON-VACUITY: the incomplete-body assertion pins that a partial spec really
 * does NOT render (so the test is exercising the gate, not a plugin that draws
 * anything regardless), and the completed-body assertion is what fails without
 * the fix.
 *
 * jsdom has no 2D canvas context, so VexFlow text metrics degrade to empty, but
 * drawing still emits an <svg> — the same property MusicInlineRenderer.test.tsx
 * relies on.
 */

// D3Renderer does not import marked, but it transitively reaches uuid through
// the plugin registry's context chain; uuid is ESM-only and the CRA jest
// transform skips node_modules, so stub it at module scope.
jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ isDarkMode: false, toggleTheme: () => {}, setTheme: () => {} }),
}));

import React from 'react';
import { render, waitFor } from '@testing-library/react';
import { D3Renderer } from '../D3Renderer';

// Truncated mid-token — exactly what a chunk holds before the fence closes.
const INCOMPLETE = '{"notes":[{"keys":["c/4';
// Minimal complete, renderable music body (no top-level `type`, matching the
// {type,definition} envelope MarkdownRenderer sends).
const COMPLETE = '{"notes":[{"keys":["c/4"],"duration":"q"}]}';

const musicSpec = (definition: string) => ({ type: 'music', definition });

describe('D3Renderer — live spec across a streaming prop update', () => {
    it('renders the completed body on the SAME instance, mid-stream', async () => {
        const { container, rerender } = render(
            <D3Renderer spec={musicSpec(INCOMPLETE)} type="d3" isStreaming={true} />,
        );

        // A partial body must not draw: canHandle rejects it, so no plugin
        // loads and no <svg> appears.  This also guards non-vacuity — the test
        // is watching a gate, not a plugin that draws unconditionally.
        await new Promise((r) => setTimeout(r, 60));
        expect(container.querySelector('svg')).toBeNull();

        // The fence closes: the body is now complete, the message is STILL
        // streaming, and the component is NOT remounted (same element type, no
        // key).  The completed diagram must appear anyway.
        rerender(
            <D3Renderer spec={musicSpec(COMPLETE)} type="d3" isStreaming={true} />,
        );

        await waitFor(
            () => expect(container.querySelector('svg')).not.toBeNull(),
            { timeout: 5000 },
        );
    });
});
