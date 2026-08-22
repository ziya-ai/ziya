/**
 * @jest-environment jsdom
 *
 * ============================================================================
 * FREEZE BISECT HARNESS (diagnostic, not a permanent assertion)
 * ============================================================================
 * Mounts the REAL MarkdownRenderer against ONE message of the conversation
 * that hard-freezes the browser at 100% CPU, so we can localize the runaway
 * synchronous loop without needing a debugger attached to a wedged renderer.
 *
 * Context/stub strategy is lifted verbatim from
 * markdownRendererStubMount.spike.test.tsx, which already proved the real
 * 7155-line component can mount outside the app.
 *
 * WHY A SUBPROCESS PER MESSAGE:
 *   A synchronous infinite loop cannot be interrupted by an in-process
 *   watchdog -- jest's own timeout never fires because the event loop never
 *   turns. So the runner drives this file once per message index via
 *   FREEZE_MSG and enforces the deadline with `timeout(1)` from outside.
 *   A killed subprocess IS the positive result.
 *
 * Driven by scripts/freeze-bisect.sh.
 */

jest.mock('uuid', () => ({ v4: () => 'freeze-uuid' }));

jest.mock('prismjs/themes/prism.css', () => ({}), { virtual: true });
jest.mock('prismjs/themes/prism-tomorrow.css', () => ({}), { virtual: true });
jest.mock('react-diff-view/style/index.css', () => ({}), { virtual: true });
jest.mock('katex/dist/katex.min.css', () => ({}), { virtual: true });

jest.mock('../../context/ActiveChatContext', () => ({
    useActiveChat: () => ({
        reasoningContentMap: new Map(),
        currentConversationId: 'freeze-convo',
        currentMessages: [],
        throttlingRecoveryData: new Map(),
        addStreamingConversation: () => {},
        setThrottlingRecoveryData: () => {},
    }),
}));

jest.mock('../../context/StreamingContext', () => ({
    useStreamingContext: () => ({
        isStreaming: false,
        isStreamingAny: false,
        currentConversationId: 'freeze-convo',
        streamingConversations: new Set<string>(),
    }),
}));

jest.mock('../../context/ThemeContext', () => ({
    useTheme: () => ({
        isDarkMode: false,
        toggleTheme: () => {},
        setTheme: () => {},
        themeAlgorithm: undefined,
    }),
}));

jest.mock('../../context/ProjectContext', () => ({
    useProject: () => ({
        currentProject: { id: 'freeze', name: 'freeze', path: '/freeze' },
    }),
}));

jest.mock('../../context/FolderContext', () => ({
    useFolderContext: () => ({
        checkedKeys: [] as React.Key[],
        addFilesToContext: async () => [],
    }),
}));

jest.mock('../../hooks/useSendPayload', () => ({
    useSendPayload: () => ({ send: async () => '' }),
}));

// Diagram renderers are stubbed ONLY when FREEZE_STUB_D3=1. Pass 1 stubs them
// (so a hang implicates the markdown/diff/mockup path); pass 2 unstubs them
// (so a hang implicates a diagram plugin, e.g. vegaPlugin's `while (changed)`).
if (process.env.FREEZE_STUB_D3 === '1') {
    jest.mock('../D3Renderer', () => ({
        D3Renderer: (props: any) => {
            const React = require('react');
            return React.createElement(
                'div',
                { 'data-testid': 'd3-diagram-stub' },
                'diagram:' + (props?.spec?.type || 'unknown'),
            );
        },
    }));
}

import React from 'react';
import * as fs from 'fs';
import { render } from '@testing-library/react';

(global as any).IntersectionObserver = class {
    observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
};
(global as any).ResizeObserver = class {
    observe() {} unobserve() {} disconnect() {}
};
if (!window.matchMedia) {
    (window as any).matchMedia = (q: string) => ({
        matches: false, media: q, onchange: null,
        addEventListener() {}, removeEventListener() {},
        addListener() {}, removeListener() {}, dispatchEvent() { return false; },
    });
}

let MarkdownRenderer: any;
let ESM_TRANSFORM_ACTIVE = false;
try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires, global-require
    MarkdownRenderer = require('../MarkdownRenderer').MarkdownRenderer;
    ESM_TRANSFORM_ACTIVE = typeof MarkdownRenderer === 'function' || typeof MarkdownRenderer === 'object';
} catch (e) {
    ESM_TRANSFORM_ACTIVE = false;
}

const DUMP = process.env.FREEZE_DUMP || '/tmp/freeze-chat.json';
const IDX = parseInt(process.env.FREEZE_MSG || '0', 10);

function textOf(msg: any): string {
    const ct = msg?.content;
    if (typeof ct === 'string') return ct;
    if (Array.isArray(ct)) {
        return ct
            .filter((p: any) => p && typeof p.text === 'string')
            .map((p: any) => p.text)
            .join('\n');
    }
    return '';
}

const describeMaybe = ESM_TRANSFORM_ACTIVE ? describe : describe.skip;

describeMaybe(`FREEZE BISECT msg[${IDX}]`, () => {
    it('mounts without a runaway synchronous loop', () => {
        // FREEZE_FILE renders an arbitrary markdown file instead of a message
        // from the dump, so minimal synthetic repros can be driven directly.
        let md: string;
        if (process.env.FREEZE_FILE) {
            md = fs.readFileSync(process.env.FREEZE_FILE, 'utf8');
        } else {
            const chat = JSON.parse(fs.readFileSync(DUMP, 'utf8'));
            md = textOf(chat.messages[IDX]);
        }

        // FREEZE_LINES=start:end narrows to a line range (0-based, end
        // exclusive) so we can localize WITHIN a hanging message.
        if (process.env.FREEZE_LINES) {
            const [a, b] = process.env.FREEZE_LINES.split(':').map(Number);
            const all = md.split('\n');
            md = all.slice(a, isNaN(b) ? all.length : b).join('\n');
            // eslint-disable-next-line no-console
            console.log(`[bisect] slice lines ${a}:${b} of ${all.length}`);
        }
        // eslint-disable-next-line no-console
        console.log(`[bisect] msg ${IDX} bytes=${md.length}`);

        const t0 = Date.now();
        render(
            <MarkdownRenderer
                markdown={md}
                enableCodeApply={true}
                isStreaming={false}
                forceRender={true}
            />
        );
        // eslint-disable-next-line no-console
        console.log(`[bisect] msg ${IDX} MOUNT OK in ${Date.now() - t0}ms`);
        expect(true).toBe(true);
    });
});
