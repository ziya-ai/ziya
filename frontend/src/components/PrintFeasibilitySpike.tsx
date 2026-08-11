/**
 * PrintFeasibilitySpike — Stage-1 feasibility spike (Card I).
 *
 * PURPOSE: prove BY EXPERIMENT that the real 7114-line MarkdownRenderer can
 * mount outside the running chat UI and render faithfully, driven headlessly.
 * This is NOT the production pipeline — it renders a HARDCODED conversation
 * (a syntax-highlighted code block, a diff, and a mermaid diagram) so a
 * Playwright harness can assert on the presence of genuinely-rendered
 * structures (Prism token spans, per-line diff add/remove rows, a completed
 * diagram) rather than "it didn't crash".
 *
 * KEY ARCHITECTURAL FINDING it validates: the six contexts MarkdownRenderer
 * consumes (useActiveChat, useFolderContext, useProject, useSendPayload,
 * useStreamingContext, useTheme) do NOT need bespoke stubs. index.tsx already
 * wraps EVERY route — including this one — in the full real provider stack
 * (ConfigProvider → ThemeProvider → ProjectProvider → ChatProvider →
 * FolderProvider → QuestionProvider). A component mounted at /print-spike
 * therefore inherits all six from the real providers. The only extra seams
 * needed are (a) an ActiveChatProvider/StreamingProvider pair supplying the
 * read-only, non-streaming values MarkdownRenderer reads, and (b) forcing the
 * light theme + a readiness gate. Card II reuses exactly this shape.
 *
 * Completion is signalled by data-render-status="complete" on the root, the
 * same contract as DiagramRenderPage, so the Playwright driver
 * (app/services/diagram_renderer.py style) can wait deterministically.
 */
import React, { useEffect, useRef, useState, useCallback } from 'react';
import { useTheme } from '../context/ThemeContext';
import { MarkdownRenderer } from './MarkdownRenderer';
// The read-only ActiveChat + Streaming providers. These are the real,
// prop-driven providers (the underlying context objects are module-private
// and cannot be provided directly), so values flow through the real contexts.
import { ActiveChatProvider } from '../context/ActiveChatContext';
import { StreamingProvider } from '../context/StreamingContext';

// Hardcoded conversation exercising the three fidelity-critical constructs
// the PDF defects are about: (1) syntax-highlighted code -> Prism spans,
// (2) a unified diff -> react-diff-view per-line insert/delete rows,
// (3) a mermaid diagram -> D3Renderer completion.
const SPIKE_CODE_BLOCK = [
    '```javascript',
    'function greet_MRK_fn_5c(name) {',
    "  const msg = `hello ${name}`;   // string interpolation",
    '  return msg.length > 0 ? msg : null;',
    '}',
    '```',
].join('\n');

const SPIKE_DIFF_BLOCK = [
    '```diff',
    '--- a/file.py',
    '+++ b/file.py',
    '@@ -1,3 +1,3 @@',
    ' unchanged_MRK_ctx',
    '-removed_MRK_del_line',
    '+added_MRK_add_line',
    ' trailing_MRK_ctx',
    '```',
].join('\n');

const SPIKE_DIAGRAM_BLOCK = [
    '```mermaid',
    'graph LR',
    '  NodeAlphaMRK --> NodeBetaMRK',
    '  NodeBetaMRK --> NodeGammaMRK',
    '```',
].join('\n');

const SPIKE_MESSAGES: Array<{ role: 'human' | 'assistant'; content: string }> = [
    { role: 'human', content: 'MRK_HUMAN_PROMPT_7q3: show me code, a diff, and a diagram.' },
    {
        role: 'assistant',
        content:
            'MRK_INTRO_PROSE_3k8 with **bold**, *italic*, `inline code` and a [link](https://example.com).\n\n' +
            SPIKE_CODE_BLOCK + '\n\n' +
            SPIKE_DIFF_BLOCK + '\n\n' +
            SPIKE_DIAGRAM_BLOCK + '\n\n' +
            'MRK_CLOSING_2f7 done.',
    },
];

type RenderStatus = 'mounting' | 'rendering' | 'complete' | 'error';

/**
 * Minimal read-only ActiveChat + Streaming providers.
 *
 * These are imported lazily and only if available; the whole point of the
 * spike is that they supply the narrow non-streaming values MarkdownRenderer
 * reads. We import the real providers so the values flow through the real
 * context objects (which are module-private and cannot be provided directly).
 */
const noop = () => {};
const asyncNoop = async () => undefined as any;

// The read-only ActiveChat value. Only reasoningContentMap,
// currentConversationId, currentMessages, throttlingRecoveryData are read on
// a non-streaming render; the rest are interactive mutations stubbed no-op.
const READONLY_ACTIVE_CHAT: any = {
    currentConversationId: 'print-spike',
    currentMessages: [],
    setCurrentConversationId: noop,
    addMessageToConversation: noop,
    loadConversation: noop,
    loadConversationAndScrollToMessage: asyncNoop,
    startNewChat: asyncNoop,
    startNewEphemeralChat: asyncNoop,
    promoteEphemeralToRetained: asyncNoop,
    editingMessageIndex: null,
    setEditingMessageIndex: noop,
    isStreaming: false,
    setIsStreaming: noop,
    streamingConversations: new Set<string>(),
    runningTaskConversations: new Set<string>(),
    addStreamingConversation: noop,
    removeStreamingConversation: noop,
    streamedContentMap: new Map<string, string>(),
    setStreamedContentMap: noop,
    reasoningContentMap: new Map(),
    setReasoningContentMap: noop,
    getProcessingState: () => ({} as any),
    updateProcessingState: noop,
    dynamicTitleLength: 0,
    setDynamicTitleLength: noop,
    lastResponseIncomplete: false,
    setDisplayMode: noop,
    toggleMessageMute: noop,
    setChatContexts: asyncNoop,
    currentDisplayMode: 'pretty',
    throttlingRecoveryData: new Map(),
    setThrottlingRecoveryData: noop,
};

export const PrintFeasibilitySpike: React.FC = () => {
    const rootRef = useRef<HTMLDivElement | null>(null);
    const [status, setStatus] = useState<RenderStatus>('mounting');
    const [errorMessage, setErrorMessage] = useState('');
    const { setTheme } = useTheme();

    // Force light theme deterministically: setTheme + strip .dark + white bg.
    useEffect(() => {
        try {
            setTheme('light');
            document.body.classList.remove('dark');
            document.documentElement.classList.remove('dark');
            document.body.style.backgroundColor = '#ffffff';
            document.documentElement.setAttribute('data-ziya-print-theme', 'light');
        } catch (e) {
            // non-fatal
        }
    }, [setTheme]);

    // Readiness gate: wait for DOM quiescence (no mutations for a debounce
    // window) AND all <img> settled, then mark complete. A safety timeout
    // bounds hangs. Same completion contract as DiagramRenderPage.
    const onRootReady = useCallback((node: HTMLDivElement | null) => {
        rootRef.current = node;
        if (!node || status !== 'mounting') return;
        setStatus('rendering');

        let settleTimer: ReturnType<typeof setTimeout> | undefined;
        let done = false;

        const finalize = () => {
            if (done) return;
            done = true;
            observer.disconnect();
            if (settleTimer) clearTimeout(settleTimer);
            if (safety) clearTimeout(safety);
            setStatus('complete');
        };

        const scheduleSettle = () => {
            if (settleTimer) clearTimeout(settleTimer);
            settleTimer = setTimeout(() => {
                // All images settled?
                const imgs = Array.from(node.querySelectorAll('img'));
                const pending = imgs.filter(i => !i.complete);
                if (pending.length === 0) finalize();
                else Promise.all(pending.map(i => new Promise<void>(res => {
                    i.addEventListener('load', () => res(), { once: true });
                    i.addEventListener('error', () => res(), { once: true });
                }))).then(finalize);
            }, 800);
        };

        const observer = new MutationObserver(scheduleSettle);
        observer.observe(node, { childList: true, subtree: true, attributes: true });
        scheduleSettle();

        const safety = setTimeout(() => {
            // Safety: if nothing rendered at all, that's an error; otherwise
            // accept whatever is present.
            const hasContent = node.querySelector('.markdown-content, pre, code, svg, .diff');
            if (hasContent) finalize();
            else { setErrorMessage('Safety timeout: no rendered content'); setStatus('error'); observer.disconnect(); }
        }, 25000);
    }, [status]);

    return (
        <div
            id="print-spike-root"
            data-render-status={status}
            data-error={errorMessage || undefined}
            style={{ background: '#ffffff', color: '#1a1a1a', padding: 24, minHeight: '100vh' }}
        >
            <ActiveChatProvider {...READONLY_ACTIVE_CHAT}>
                <StreamingProvider
                    isStreaming={false}
                    isStreamingAny={false}
                    currentConversationId="print-spike"
                    streamingConversations={new Set()}
                >
                    <div id="print-spike-content" ref={onRootReady} className="conversation-messages-container">
                        {SPIKE_MESSAGES.map((m, i) => (
                            <div
                                key={i}
                                className={`print-spike-message role-${m.role}`}
                                data-role={m.role}
                                style={{ marginBottom: 20, paddingBottom: 12, borderBottom: '1px solid #eee' }}
                            >
                                <MarkdownRenderer
                                    markdown={m.content}
                                    enableCodeApply={false}
                                    forceRender={true}
                                    isStreaming={false}
                                    role={m.role}
                                />
                            </div>
                        ))}
                    </div>
                </StreamingProvider>
            </ActiveChatProvider>
        </div>
    );
};

export default PrintFeasibilitySpike;
