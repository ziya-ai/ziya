/**
 * @jest-environment jsdom
 *
 * Regression tests for the streaming code-block DOM churn that drove browser
 * renderers to a 0.4 → 2 GB RSS sawtooth per streaming tab.
 *
 * Mechanism (measured with scripts/mem_probe/probe.mjs): every 150 ms flush
 * of a streaming message re-rendered the still-open code block through
 * Prism + dangerouslySetInnerHTML, and then AGAIN through an effect that
 * wiped and rebuilt innerHTML.  Each flush destroyed and recreated every
 * highlighted <span>; the detached nodes piled up in Blink's heap (invisible
 * to V8 profilers) until Oilpan collected them.
 *
 * Fix: while a block is still arriving it renders as a single text node (no
 * Prism, no span subtree), closed blocks memoize their highlight on the text,
 * and renderTokens marks only the message's trailing block as arriving.
 *
 * Each "does not happen" assertion is paired with a positive control so a
 * silently-broken render path cannot pass.
 */

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
jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));
jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ isDarkMode: false }),
}));
// Real Prism core (ships javascript), loaded synchronously so the component's
// async language gate resolves on the next tick.
jest.mock('../../utils/prismLoader', () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const Prism = require('prismjs');
    return {
        // `globalThis` is the jsdom window here; `window` itself is not an
        // identifier jest permits inside a hoisted mock factory.
        loadPrismLanguage: async () => { (globalThis as any).Prism = Prism; },
        isLanguageLoaded: () => true,
    };
});

import React from 'react';
import { render, waitFor } from '@testing-library/react';
import { CodeBlock, renderTokens } from '../MarkdownRenderer';

const JS = 'const a = 1;\nconst b = 2;';
const token = (text: string, lang = 'javascript') =>
    ({ type: 'code', lang, text, raw: '```' + lang + '\n' + text + '\n```' } as any);

const codeEl = (container: HTMLElement) => container.querySelector('pre > code') as HTMLElement;
const spanCount = (container: HTMLElement) => container.querySelectorAll('pre > code span').length;

describe('CodeBlock while its text is still arriving', () => {
    it('positive control: a settled block IS Prism-highlighted into spans', async () => {
        const { container } = render(<CodeBlock token={token(JS)} index={0} />);
        await waitFor(() => expect(spanCount(container)).toBeGreaterThan(0));
        expect(codeEl(container).getAttribute('data-streaming')).toBeNull();
    });

    it('renders a single text node with no highlight spans', async () => {
        const { container } = render(<CodeBlock token={token(JS)} index={0} isStreaming />);
        await waitFor(() => expect(codeEl(container)?.getAttribute('data-streaming')).toBe('true'));
        const code = codeEl(container);
        expect(code.textContent).toBe(JS);
        expect(code.childNodes.length).toBe(1);
        expect(code.childNodes[0].nodeType).toBe(Node.TEXT_NODE);
        expect(spanCount(container)).toBe(0);
    });

    it('keeps the SAME <code> element and text node as the text grows (no teardown per flush)', async () => {
        const { container, rerender } = render(<CodeBlock token={token(JS)} index={0} isStreaming />);
        await waitFor(() => expect(codeEl(container)?.getAttribute('data-streaming')).toBe('true'));
        const before = codeEl(container);
        const textNodeBefore = before.childNodes[0];

        const grown = JS + '\nconst c = 3;';
        rerender(<CodeBlock token={token(grown)} index={0} isStreaming />);
        rerender(<CodeBlock token={token(grown + '\nconst d = 4;')} index={0} isStreaming />);

        const after = codeEl(container);
        expect(after).toBe(before);                       // element not replaced
        expect(after.childNodes[0]).toBe(textNodeBefore); // text node updated in place
        expect(after.childNodes.length).toBe(1);
        expect(after.textContent).toBe(grown + '\nconst d = 4;');
        expect(spanCount(container)).toBe(0);
    });

    it('does not call Prism.highlight at all while arriving, and highlights once it settles', async () => {
        const { container, rerender } = render(<CodeBlock token={token(JS)} index={0} isStreaming />);
        await waitFor(() => expect(codeEl(container)?.getAttribute('data-streaming')).toBe('true'));
        const Prism = (window as any).Prism;
        const spy = jest.spyOn(Prism, 'highlight');
        rerender(<CodeBlock token={token(JS + '\nx()')} index={0} isStreaming />);
        rerender(<CodeBlock token={token(JS + '\nx()\ny()')} index={0} isStreaming />);
        expect(spy).not.toHaveBeenCalled();

        // Stream ends: the block settles and is highlighted.
        rerender(<CodeBlock token={token(JS + '\nx()\ny()')} index={0} isStreaming={false} />);
        await waitFor(() => expect(spanCount(container)).toBeGreaterThan(0));
        expect(spy).toHaveBeenCalled();
        spy.mockRestore();
    });
});

describe('CodeBlock once settled', () => {
    it('re-renders of a parent with identical text do not re-run Prism (memoized on text)', async () => {
        const { container, rerender } = render(<CodeBlock token={token(JS)} index={0} />);
        await waitFor(() => expect(spanCount(container)).toBeGreaterThan(0));
        const Prism = (window as any).Prism;
        const spy = jest.spyOn(Prism, 'highlight');
        const html = codeEl(container).innerHTML;
        // New token OBJECT, same text — what every streaming flush hands
        // every closed block in the message.
        rerender(<CodeBlock token={token(JS)} index={0} />);
        rerender(<CodeBlock token={token(JS)} index={0} />);
        expect(spy).not.toHaveBeenCalled();
        expect(codeEl(container).innerHTML).toBe(html);
        spy.mockRestore();
    });
});

describe('renderTokens marks only the trailing block of a streaming message as arriving (the seam)', () => {
    const closed = token('closedBlock()');
    const open = token('openBlock()');
    const para = { type: 'paragraph', text: 'then some prose', tokens: [{ type: 'text', text: 'then some prose', raw: 'then some prose' }], raw: 'then some prose' } as any;
    const space = { type: 'space', raw: '\n\n' } as any;

    it('last code token (after a closed one and a paragraph) is plain text; the earlier one is highlighted', async () => {
        const { container } = render(<div>{renderTokens([closed, space, para, space, open], false, false, false, true)}</div>);
        await waitFor(() => {
            const codes = Array.from(container.querySelectorAll('pre > code')) as HTMLElement[];
            expect(codes.length).toBe(2);
            expect(codes[0].getAttribute('data-streaming')).toBeNull();
            expect(codes[0].querySelectorAll('span').length).toBeGreaterThan(0);
            expect(codes[1].getAttribute('data-streaming')).toBe('true');
            expect(codes[1].querySelectorAll('span').length).toBe(0);
        });
    });

    it('a code block followed by prose is closed, so it is highlighted even while streaming', async () => {
        const { container } = render(<div>{renderTokens([closed, space, para], false, false, false, true)}</div>);
        await waitFor(() => {
            const code = codeEl(container);
            expect(code.getAttribute('data-streaming')).toBeNull();
            expect(code.querySelectorAll('span').length).toBeGreaterThan(0);
        });
    });

    it('when not streaming, the trailing block is highlighted (positive control for the gate)', async () => {
        const { container } = render(<div>{renderTokens([closed, space, open], false, false, false, false)}</div>);
        await waitFor(() => {
            const codes = Array.from(container.querySelectorAll('pre > code')) as HTMLElement[];
            expect(codes.length).toBe(2);
            for (const c of codes) {
                expect(c.getAttribute('data-streaming')).toBeNull();
                expect(c.querySelectorAll('span').length).toBeGreaterThan(0);
            }
        });
    });
});
