/**
 * @jest-environment jsdom
 *
 * Regression tests for the inline `music:` codespan renderer.
 *
 * The bug these pin is a lifecycle ordering fault, not a notation fault: the
 * component returned a fallback <span> while loading and the ref'd container
 * only afterwards, so the container did not exist when the effect ran.
 * containerRef.current was null, the draw was skipped, and the fallback was
 * then replaced by an empty container -- producing neither a staff nor the
 * source text.  Nothing about this is visible to the type checker, and the
 * musicPlugin unit tests cannot see it because they call the render core
 * directly with a container they own.
 *
 * jsdom provides no 2D canvas context, so VexFlow's text measurement degrades
 * to empty metrics; drawing still completes, which is what is under test here.
 */

// ``marked`` is ESM-only (`"type": "module"`, no CJS build) and the CRA jest
// transform does not process node_modules, so importing MarkdownRenderer fails
// at its top-level ``marked`` import with "Unexpected token 'export'".  Stub it
// at module scope, matching applyButtonStreamingGate.test.ts and the other
// suites that import from this module.
jest.mock('marked', () => {
    const marked = (s: string) => s;
    Object.assign(marked, {
        parse: (s: string) => s,
        setOptions: () => {},
        use: () => {},
        walkTokens: () => {},
        parseInline: (s: string) => s,
    });
    return { marked, Tokens: {} };
});
// ``uuid`` is ESM-only too, reached transitively through the
// FolderContext -> ProjectContext -> db.ts chain MarkdownRenderer imports.
jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { MusicInlineRenderer } from '../MarkdownRenderer';

// The component is driven directly rather than through a markdown string:
// ``marked`` is stubbed above (it is ESM-only), so the tokenizer that would
// turn a `music: ...` codespan into this component is not available here.
// What is under test is the component's own mount/draw lifecycle, which is
// where the bug lived.
const renderInline = (dsl: string) =>
    render(<MusicInlineRenderer dsl={dsl} isDarkMode={false} />);

describe('inline music: codespan', () => {
    it('mounts the notation container so the draw has somewhere to go', async () => {
        // The precise failure: the container was absent during the effect.
        const { container } = renderInline('C4/q, D4/q');
        await waitFor(() => {
            expect(container.querySelector('.music-inline')).not.toBeNull();
        });
    });

    it('renders an actual staff rather than an empty span', async () => {
        const { container } = renderInline('E5/q, E5/q, F5/q, G5/q');
        await waitFor(() => {
            const svg = container.querySelector('.music-inline svg');
            expect(svg).not.toBeNull();
            // An empty container is the observed symptom; require real ink.
            expect(svg!.querySelectorAll('path').length).toBeGreaterThan(0);
        });
    });

    it('hides the fallback text once the staff has drawn', async () => {
        const { container } = renderInline('C4/q, D4/q');
        await waitFor(() => {
            expect(container.querySelector('.music-inline svg')).not.toBeNull();
        });
        expect(container.querySelector('.music-inline-fallback')).toBeNull();
    });

    it('keeps the container visible after a successful draw', async () => {
        const { container } = renderInline('C4/q, D4/q');
        await waitFor(() => {
            const el = container.querySelector('.music-inline') as HTMLElement | null;
            expect(el).not.toBeNull();
            expect(el!.style.display).toBe('inline-block');
        });
    });

    it('falls back to the source text when the DSL cannot be parsed', async () => {
        // Never silently blank: an unparseable phrase must still be readable.
        renderInline('not-a-pitch');
        await waitFor(() => {
            expect(screen.getByText(/not-a-pitch/)).toBeInTheDocument();
        });
    });

    it('hides the empty container when falling back', async () => {
        const { container } = renderInline('not-a-pitch');
        await waitFor(() => {
            expect(container.querySelector('.music-inline-fallback')).not.toBeNull();
        });
        const el = container.querySelector('.music-inline') as HTMLElement | null;
        expect(el!.style.display).toBe('none');
    });

    it('renders a fragment that does not fill a 4/4 bar', async () => {
        // Inline snippets are fragments by nature; STRICT mode rejected these.
        const { container } = renderInline('C4/q, D4/q, E4/q');
        await waitFor(() => {
            expect(container.querySelector('.music-inline svg')).not.toBeNull();
        });
    });

    it('exposes the source text for accessibility and copying', async () => {
        const { container } = renderInline('C4/q, D4/q');
        await waitFor(() => {
            expect(container.querySelector('.music-inline')).not.toBeNull();
        });
        const el = container.querySelector('.music-inline')!;
        expect(el.getAttribute('aria-label')).toContain('C4/q, D4/q');
        expect(el.getAttribute('title')).toBe('C4/q, D4/q');
    });
});
