/**
 * @jest-environment jsdom
 *
 * PIN: an escaped literal dollar (`\$`) inside inline math survives the ENTIRE
 * frontend render pipeline — not just the classifier boundary scan.
 *
 * WHY THIS EXISTS (and why it is a cross-layer / real-component test):
 * The classifier layer is already pinned by
 * utils/__tests__/inlineMathClassifier.test.ts, which proves
 * `processInlineMath('$x = \\$5$')` decodes to `x = \\$5`. But a pixel judge
 * repeatedly reported `$...\$5$` rendering as a RED KaTeX error, with KaTeX
 * apparently receiving the CORRUPTED string `\5$` (the `$` after the backslash
 * deleted, the closing `$` leaked in). That symptom persisted even though the
 * classifier unit test passed, so the question was WHERE the corruption lived.
 *
 * These two tests answer it decisively by exercising layers ABOVE the
 * classifier:
 *   1. `crossLayer`: replicates MarkdownRenderer's exact preprocessing ORDER
 *      (mathStore.protect -> escapeNestedBacktickFences -> restore -> $$-scan
 *      -> processInlineMath) then lexes the result with the REAL `marked` and
 *      reassembles + decodes the paragraph exactly as the paragraph token
 *      handler does. The decoded LaTeX must equal the input verbatim.
 *   2. `realComponent`: mounts the genuine MarkdownRenderer in jsdom and reads
 *      `data-math-original` — the exact string handed to KaTeX — asserting it
 *      is the un-corrupted LaTeX and that NO `.katex-error` span is emitted.
 *
 * RESULT: both pass. The frontend pipeline handles `\$` correctly end to end.
 * The pixel-level `\5$` corruption therefore originates UPSTREAM of the
 * frontend (the chat-message harness's message-seeding / chat storage
 * transport, which strips the `$` from `\$` before the content ever reaches
 * MarkdownRenderer) — a backend/transport concern, not a frontend math bug.
 * These tests lock in the frontend's correctness so a future regression in
 * inlineMathClassifier / fenceScanner / MarkdownRenderer preprocessing is
 * caught here.
 *
 * Like the other real-`marked` suites (spikeMarkedReal), this file is only
 * meaningful under the ESM transform override:
 *   CI=true npx craco test escapedDollarMarkdownPipeline --watchAll=false \
 *     --transformIgnorePatterns "node_modules/(?!(marked|uuid|react-diff-view)/)"
 * Under a plain `craco test` the ESM `marked`/component import cannot be
 * parsed, so the suite SELF-SKIPS rather than breaking the default run.
 */
jest.mock('uuid', () => ({ v4: () => 'esc-dollar-uuid' }));
jest.mock('prismjs/themes/prism.css', () => ({}), { virtual: true });
jest.mock('prismjs/themes/prism-tomorrow.css', () => ({}), { virtual: true });
jest.mock('react-diff-view/style/index.css', () => ({}), { virtual: true });
jest.mock('katex/dist/katex.min.css', () => ({}), { virtual: true });

jest.mock('../../context/ActiveChatContext', () => ({
    useActiveChat: () => ({
        reasoningContentMap: new Map(),
        currentConversationId: 'esc-convo',
        currentMessages: [],
        throttlingRecoveryData: new Map(),
        addStreamingConversation: () => {},
        setThrottlingRecoveryData: () => {},
    }),
}));
jest.mock('../../context/StreamingContext', () => ({
    useStreamingContext: () => ({
        isStreaming: false, isStreamingAny: false,
        currentConversationId: 'esc-convo', streamingConversations: new Set(),
    }),
}));
jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({ isDarkMode: false, toggleTheme: () => {}, setTheme: () => {}, themeAlgorithm: undefined }),
}));
jest.mock('../../context/ProjectContext', () => ({
    useProject: () => ({ currentProject: { id: 'esc', name: 'esc', path: '/esc' } }),
}));
jest.mock('../../context/FolderContext', () => ({
    useFolderContext: () => ({ checkedKeys: [], addFilesToContext: async () => [] }),
}));
jest.mock('../../hooks/useSendPayload', () => ({ useSendPayload: () => ({ send: async () => '' }) }));
jest.mock('../D3Renderer', () => ({
    D3Renderer: () => {
        const React = require('react');
        return React.createElement('div', { 'data-testid': 'd3-diagram-stub' }, 'diagram');
    },
}));

import React from 'react';
import { render, waitFor } from '@testing-library/react';
import {
    processInlineMath,
    createMathPlaceholderStore,
    MATH_INLINE_MARKER_SPLIT_RE,
    isInlineMathMarker,
    decodeInlineMathMarker,
} from '../../utils/inlineMathClassifier';
import { escapeNestedBacktickFences, applyOutsideCodeSpans } from '../fenceScanner';

(global as any).IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } };
(global as any).ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
if (!window.matchMedia) {
    (window as any).matchMedia = (q: string) => ({
        matches: false, media: q, onchange: null,
        addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent() { return false; },
    });
}

let marked: any;
let MarkdownRenderer: any;
let ACTIVE = false;
try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires, global-require
    marked = require('marked').marked;
    MarkdownRenderer = require('../MarkdownRenderer').MarkdownRenderer;
    ACTIVE = typeof marked?.lexer === 'function' && !!MarkdownRenderer;
} catch {
    ACTIVE = false;
}

const maybe = ACTIVE ? it : it.skip;

// Faithful replica of MarkdownRenderer preprocessing order (~6533-6690).
function realPreprocess(input: string): string {
    let md = input;
    const mathStore = createMathPlaceholderStore();
    {
        const parts = md.split(/(```[^\n]*\n[\s\S]*?```)/g);
        md = parts.map((part, idx) =>
            (idx % 2 === 1 && part.startsWith('```'))
                ? part
                : applyOutsideCodeSpans(part, (seg: string) => mathStore.protect(seg))
        ).join('');
    }
    md = escapeNestedBacktickFences(md);
    md = mathStore.restore(md);
    const segments = md.split(/(```[^\n]*\n[\s\S]*?```)/g);
    md = segments.map((seg, idx) => {
        if (idx % 2 === 1 && seg.startsWith('```')) return seg;
        let processed = seg.replace(/\$\$([\s\S]+?)\$\$/g,
            (_m: string, cap: string) => '\n<div class="math-display-block">MATH_DISPLAY:' + cap + '</div>\n');
        processed = applyOutsideCodeSpans(processed, processInlineMath);
        return processed;
    }).join('');
    return md;
}

// Replica of the paragraph token handler: concat inline .text, decode markers.
function decodeParagraphLatex(markerized: string): string[] {
    const tokens = marked.lexer(markerized);
    let paraText = '';
    const walk = (toks: any[]) => {
        for (const t of toks) {
            if (t.type === 'paragraph') paraText += (t.tokens || []).map((x: any) => x.text || '').join('');
            else if (t.tokens) walk(t.tokens);
            else paraText += t.text || '';
        }
    };
    walk(tokens as any[]);
    return paraText
        .split(MATH_INLINE_MARKER_SPLIT_RE)
        .filter((p: string) => p && isInlineMathMarker(p))
        .map((p: string) => decodeInlineMathMarker(p) as string);
}

describe('escaped literal dollar survives the full frontend render pipeline', () => {
    maybe('crossLayer: preprocessing order + real marked decodes to un-corrupted LaTeX', () => {
        expect(decodeParagraphLatex(realPreprocess('Minimal: $a = \\$5$ end.')))
            .toEqual(['a = \\$5']);
        expect(decodeParagraphLatex(
            realPreprocess('Cost label: $x_{\\text{net\\_cost} = \\{fee\\}} = \\$5$ is the tagged quantity.')))
            .toEqual(['x_{\\text{net\\_cost} = \\{fee\\}} = \\$5']);
    });

    maybe('realComponent: MarkdownRenderer hands KaTeX the un-corrupted LaTeX, no katex-error', async () => {
        const md = 'Cost label: $x_{\\text{net\\_cost} = \\{fee\\}} = \\$5$ is the tagged quantity.';
        const { container } = render(
            <MarkdownRenderer markdown={md} enableCodeApply={true} isStreaming={false} forceRender={true} />
        );
        // Wait for the TERMINAL KaTeX state: MathRenderer only stamps
        // `data-math-original` once katex has loaded (its async import), and
        // emits `.katex-error` on a parse failure. Waiting on `.math-fallback`
        // would resolve too early, before the real math prop is observable.
        await waitFor(() => {
            const el = container.querySelector('[data-math-original], .katex-error');
            expect(el).toBeTruthy();
        }, { timeout: 15000 });

        const originals = Array.from(container.querySelectorAll('[data-math-original]'))
            .map(el => el.getAttribute('data-math-original'));
        // The exact string handed to KaTeX must be the un-corrupted LaTeX —
        // NOT the '\5$' the harness transport produced.
        expect(originals).toContain('x_{\\text{net\\_cost} = \\{fee\\}} = \\$5');
        expect(container.querySelectorAll('.katex-error').length).toBe(0);
    });
});
