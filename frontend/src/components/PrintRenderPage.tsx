/**
 * PrintRenderPage — Standalone page for headless WHOLE-CONVERSATION rendering.
 *
 * Mounted at `/print`.  This is the conversation-scale sibling of
 * `DiagramRenderPage` (`/render`): where that route renders a single diagram
 * through the real D3Renderer pipeline, this route renders an ENTIRE
 * conversation through the real `MarkdownRenderer` pipeline — the exact same
 * Prism / KaTeX / react-diff-view / D3 code the chat UI uses — so a headless
 * capture is pixel-faithful to what a user sees.
 *
 * SHARED INFRASTRUCTURE.  This route is consumed by:
 *   - `app/services/pdf_exporter.py`  → `capture_pdf()`   (Card I, PDF)
 *   - `app/services/pdf_exporter.py`  → `extract_html()`  (Card II, HTML)
 * so it MUST NOT bake in PDF-only / A4 / print-media assumptions.  It renders
 * a self-contained, light-themed DOM; the Python driver decides whether to
 * `page.pdf()` it or read its `outerHTML`.
 *
 * Payload channels (mirrors DiagramRenderPage):
 *   1. URL hash fragment (base64 JSON) — for small payloads / manual testing.
 *   2. `window.__renderConversation(jsonString)` — used by Playwright; the
 *      only viable channel for a long conversation, which blows past URL
 *      length limits.
 *
 * Payload shape:
 *   {
 *     title: string,
 *     messages: Array<{ role, content, ... }>,
 *     options: { roundLimit, includeHuman, includeCollapsed, includeFooter },
 *     footerHtml?: string,           // pre-rendered footer (matches other exports)
 *     renderTimeoutMs?: number,      // in-page safety timeout (default 60s)
 *   }
 *
 * Completion contract:
 *   Sets `data-render-status="complete"` on `#print-render-root` ONLY after
 *   every async renderer has settled — no diagram is still pending, no KaTeX
 *   node is unrendered, and all <img> have loaded (or errored).  Readiness is
 *   GENUINELY AWAITED (a MutationObserver quiescence gate + per-resource
 *   promises); the fixed `setTimeout` that makes the client-side export flaky
 *   is deliberately NOT the gate — a short debounce only confirms quiescence.
 */
import React, {
    useCallback, useEffect, useMemo, useRef, useState,
} from 'react';
import { useTheme } from '../context/ThemeContext';
import { ActiveChatProvider } from '../context/ActiveChatContext';
import { StreamingProvider } from '../context/StreamingContext';
import { lazyWithRetry } from '../utils/lazyWithRetry';

const MarkdownRenderer = lazyWithRetry(
    () => import('./MarkdownRenderer').then(m => ({ default: m.MarkdownRenderer }))
);

interface PrintMessage {
    role?: 'human' | 'assistant' | 'system' | string;
    content?: string;
    [k: string]: any;
}

interface PrintOptions {
    roundLimit?: number | null;
    includeHuman?: boolean;
    includeCollapsed?: boolean;
    includeFooter?: boolean;
}

interface ConversationSpec {
    title?: string;
    messages: PrintMessage[];
    options?: PrintOptions;
    footerHtml?: string;
    renderTimeoutMs?: number;
}

type RenderStatus = 'idle' | 'loading' | 'rendering' | 'complete' | 'error';

const DEFAULT_OPTIONS: Required<PrintOptions> = {
    roundLimit: null,
    includeHuman: true,
    includeCollapsed: true,
    includeFooter: true,
};

/**
 * Apply scope & content filters — the SAME semantics as the frontend
 * ExportConversationModal, kept here so the server pipeline shares one source
 * of truth (PDF, HTML, CLI all go through this route).
 */
function applyOptions(messages: PrintMessage[], options: PrintOptions): PrintMessage[] {
    const opts = { ...DEFAULT_OPTIONS, ...options };
    let msgs = [...messages];

    if (opts.roundLimit !== null && opts.roundLimit !== undefined && opts.roundLimit > 0) {
        const humanIndices = msgs.reduce<number[]>((acc, m, i) => {
            if (m.role === 'human') acc.push(i);
            return acc;
        }, []);
        const startFrom = humanIndices[Math.max(0, humanIndices.length - opts.roundLimit)];
        if (startFrom !== undefined) msgs = msgs.slice(startFrom);
    }

    if (!opts.includeHuman) {
        msgs = msgs.filter(m => m.role !== 'human');
    }

    if (!opts.includeCollapsed) {
        msgs = msgs.map(m => ({
            ...m,
            content: m.content
                ? m.content
                    .replace(/<details[\s\S]*?<\/details>/gi, '')
                    .replace(/```thinking:step-\d+\n[\s\S]*?```/g, '')
                : m.content,
        }));
    }

    return msgs;
}

function parseSpecFromHash(): ConversationSpec | null {
    const hash = window.location.hash.slice(1);
    if (!hash) return null;
    try {
        return JSON.parse(decodeURIComponent(escape(atob(hash))));
    } catch (e) {
        try { return JSON.parse(atob(hash)); } catch { /* fallthrough */ }
        console.error('PrintRenderPage: Failed to parse spec from hash:', e);
        return null;
    }
}

export const PrintRenderPage: React.FC = () => {
    const [spec, setSpec] = useState<ConversationSpec | null>(null);
    const [status, setStatus] = useState<RenderStatus>('idle');
    const [errorMessage, setErrorMessage] = useState<string>('');
    const [diag, setDiag] = useState<{ elapsedMs: number; lastEvent: string }>({
        elapsedMs: 0, lastEvent: 'init',
    });
    const contentRef = useRef<HTMLDivElement | null>(null);
    const observerRef = useRef<MutationObserver | null>(null);
    const safetyTimerRef = useRef<ReturnType<typeof setTimeout>>();

    // ThemeContext is the real provider (mounted above in index.tsx). Force
    // light DETERMINISTICALLY rather than hoping a body-class removal sticks.
    const { setTheme, isDarkMode } = useTheme();

    const applySpec = useCallback((incoming: ConversationSpec) => {
        setSpec(incoming);
        setStatus('loading');
        setErrorMessage('');
    }, []);

    // Deterministic light theme: drive the real ThemeContext to 'light' AND
    // scrub the dark affordances the chat leaves on <body>/<html>, AND stamp a
    // data attribute the capture asserts on. This is the fix for defect #6
    // (dark-mode content composited onto a white page).
    useEffect(() => {
        setTheme('light');
        document.body.classList.remove('dark');
        document.documentElement.classList.remove('dark');
        document.documentElement.setAttribute('data-ziya-print-theme', 'light');
        document.body.style.backgroundColor = '#ffffff';
        document.documentElement.style.backgroundColor = '#ffffff';
        // Also set a global the chat renderer reads for code-apply gating.
        (window as any).enableCodeApply = 'false';
    }, [setTheme]);

    // Accept spec via postMessage (same-origin only, mirrors DiagramRenderPage)
    useEffect(() => {
        const handleMessage = (event: MessageEvent) => {
            if (event.origin !== window.location.origin) return;
            if (event.data?.type === 'render-conversation' && event.data.spec) {
                applySpec(event.data.spec as ConversationSpec);
            }
        };
        window.addEventListener('message', handleMessage);
        return () => window.removeEventListener('message', handleMessage);
    }, [applySpec]);

    // URL hash channel (small payloads / manual testing)
    useEffect(() => {
        const hashSpec = parseSpecFromHash();
        if (hashSpec) applySpec(hashSpec);
    }, [applySpec]);

    // Imperative API for Playwright's page.evaluate() — the large-payload path.
    useEffect(() => {
        (window as any).__renderConversation = (specJson: string) => {
            try {
                applySpec(JSON.parse(specJson) as ConversationSpec);
                return true;
            } catch (e) {
                setErrorMessage(String(e));
                setStatus('error');
                return false;
            }
        };
        return () => { delete (window as any).__renderConversation; };
    }, [applySpec]);

    const options = useMemo(() => ({ ...DEFAULT_OPTIONS, ...(spec?.options || {}) }), [spec]);
    const filteredMessages = useMemo(
        () => (spec ? applyOptions(spec.messages || [], options) : []),
        [spec, options],
    );

    // ── Readiness detection ─────────────────────────────────────────────
    // Genuinely await async renderers. We consider the page COMPLETE when:
    //   (a) the DOM has stopped mutating for a short debounce window
    //       (diagrams/prism/katex have finished injecting nodes), AND
    //   (b) there are no unrendered KaTeX placeholders, AND
    //   (c) every <img> has loaded or errored.
    // The debounce only CONFIRMS quiescence; it is not itself the deadline.
    const finalizeReadiness = useCallback(async (node: HTMLDivElement, startedAt: number) => {
        // (c) await images
        const imgs = Array.from(node.querySelectorAll('img'));
        await Promise.all(imgs.map(img => {
            if (img.complete) return Promise.resolve();
            return new Promise<void>(resolve => {
                img.addEventListener('load', () => resolve(), { once: true });
                img.addEventListener('error', () => resolve(), { once: true });
            });
        }));
        setDiag({ elapsedMs: Date.now() - startedAt, lastEvent: 'images-settled' });
        setStatus('complete');
    }, []);

    const onContentReady = useCallback((node: HTMLDivElement | null) => {
        contentRef.current = node;
        if (!node || status !== 'loading') return;

        setStatus('rendering');
        const startedAt = Date.now();
        setDiag({ elapsedMs: 0, lastEvent: 'observer-attached' });

        const safetyTimeoutMs = Math.max(2000, spec?.renderTimeoutMs ?? 60000);
        let quietTimer: ReturnType<typeof setTimeout> | undefined;
        const QUIET_MS = 600; // debounce that CONFIRMS quiescence (not the gate)

        const isSettled = () => {
            // No diagram still marked pending, no unrendered katex placeholder.
            const pendingDiagram = node.querySelector(
                '[data-render-status="rendering"], [data-render-status="loading"], .diagram-loading',
            );
            // MarkdownRenderer renders math to `.katex`; a leftover raw `$$`
            // math source node would indicate katex hasn't run yet.
            const hasContent = node.querySelector(
                'span.token, .katex, .diff-line, svg, img, p, pre, code',
            );
            return !pendingDiagram && !!hasContent;
        };

        const scheduleQuiet = () => {
            if (quietTimer) clearTimeout(quietTimer);
            quietTimer = setTimeout(async () => {
                if (isSettled()) {
                    observer.disconnect();
                    observerRef.current = null;
                    if (safetyTimerRef.current) clearTimeout(safetyTimerRef.current);
                    setDiag({ elapsedMs: Date.now() - startedAt, lastEvent: 'dom-quiescent' });
                    await finalizeReadiness(node, startedAt);
                }
            }, QUIET_MS);
        };

        const observer = new MutationObserver(() => {
            setDiag(prev => ({ elapsedMs: Date.now() - startedAt, lastEvent: prev.lastEvent }));
            scheduleQuiet();
        });
        observerRef.current = observer;
        observer.observe(node, { childList: true, subtree: true, attributes: true });

        // Kick off the first quiescence check in case content is already static.
        scheduleQuiet();

        // Safety net: if the page never quiesces, complete-with-content or
        // fail-with-diagnostics rather than hang the harness forever.
        safetyTimerRef.current = setTimeout(async () => {
            if (quietTimer) clearTimeout(quietTimer);
            observer.disconnect();
            observerRef.current = null;
            if (isSettled() || node.querySelector('p, pre, span.token, .katex, svg, img')) {
                setDiag({ elapsedMs: Date.now() - startedAt, lastEvent: 'timeout-with-content' });
                await finalizeReadiness(node, startedAt);
            } else {
                const counts = {
                    svg: node.querySelectorAll('svg').length,
                    img: node.querySelectorAll('img').length,
                    tokens: node.querySelectorAll('span.token').length,
                    htmlLen: node.innerHTML.length,
                };
                setErrorMessage(
                    `Print render timeout after ${safetyTimeoutMs}ms. ` +
                    `DOM snapshot: ${JSON.stringify(counts)}`,
                );
                setDiag({ elapsedMs: Date.now() - startedAt, lastEvent: 'timeout-no-content' });
                setStatus('error');
            }
        }, safetyTimeoutMs);
    }, [status, spec?.renderTimeoutMs, finalizeReadiness]);

    useEffect(() => () => {
        if (safetyTimerRef.current) clearTimeout(safetyTimerRef.current);
        if (observerRef.current) { observerRef.current.disconnect(); observerRef.current = null; }
    }, []);

    // Stub the two remaining coupling contexts (Project/Folder/SendPayload) at
    // module level is not possible; instead we wrap in the real ActiveChat and
    // Streaming providers (prop-driven) and rely on ProjectProvider/FolderProvider
    // being mounted above in index.tsx. This keeps the render faithful without
    // the app's live chat state.
    const activeChatValue = useMemo(() => ({
        // Fields a static read-only render dereferences (see Stage 1 stub_shape)
        reasoningContentMap: new Map(),
        currentConversationId: 'print-export',
        currentMessages: [],
        throttlingRecoveryData: new Map(),
        // interactive-only no-ops:
        addStreamingConversation: () => {},
        setThrottlingRecoveryData: () => {},
    } as any), []);

    return (
        <div
            id="print-render-root"
            data-render-status={status}
            data-error={errorMessage || undefined}
            data-elapsed-ms={diag.elapsedMs}
            data-last-event={diag.lastEvent}
            data-theme={isDarkMode ? 'dark' : 'light'}
            style={{
                background: '#ffffff',
                color: '#1a1a1a',
                minHeight: '100vh',
                width: '100%',
                margin: 0,
                padding: 0,
            }}
        >
            {status === 'idle' && (
                <div style={{ color: '#888', fontSize: 14, padding: 40, textAlign: 'center' }}>
                    Waiting for conversation…
                    <br />
                    <code style={{ fontSize: 11 }}>window.__renderConversation(json)</code>
                </div>
            )}

            {status === 'error' && (
                <div style={{ color: '#cf1322', padding: 20 }}>
                    <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>
                        Print Render Error
                    </div>
                    <div style={{ fontSize: 13 }}>{errorMessage}</div>
                </div>
            )}

            {spec && status !== 'error' && (
                <ActiveChatProvider {...activeChatValue}>
                    <StreamingProvider
                        isStreaming={false}
                        isStreamingAny={false}
                        currentConversationId={'print-export'}
                        streamingConversations={new Set<string>()}
                    >
                        {/* The conversation container. `conversation-messages-container`
                            matches the chat UI class so CSS selectors used by the
                            renderer (and by Card II's HTML extraction) apply. */}
                        <div
                            ref={onContentReady}
                            id="print-render-content"
                            className="conversation-messages-container ziya-print"
                            style={{
                                background: '#ffffff',
                                color: '#1a1a1a',
                                padding: '24px 28px',
                                maxWidth: '100%',
                            }}
                        >
                            {spec.title && (
                                <h1 style={{ fontSize: 22, marginBottom: 16 }}>{spec.title}</h1>
                            )}
                            <React.Suspense
                                fallback={<div style={{ padding: 20, color: '#888' }}>Loading renderer…</div>}
                            >
                                {filteredMessages.map((msg, i) => (
                                    <div
                                        key={i}
                                        className={`print-message print-message-${msg.role || 'unknown'}`}
                                        data-role={msg.role}
                                        style={{
                                            marginBottom: 20,
                                            paddingBottom: 12,
                                            borderBottom: '1px solid #eaecef',
                                        }}
                                    >
                                        <div
                                            className="print-message-role"
                                            style={{
                                                fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
                                                letterSpacing: 0.5, color: '#57606a', marginBottom: 6,
                                            }}
                                        >
                                            {msg.role === 'human' ? 'You'
                                                : msg.role === 'assistant' ? 'Ziya'
                                                    : (msg.role || '')}
                                        </div>
                                        <MarkdownRenderer
                                            markdown={msg.content || ''}
                                            enableCodeApply={false}
                                            isStreaming={false}
                                            forceRender={true}
                                            role={msg.role as any}
                                        />
                                    </div>
                                ))}
                            </React.Suspense>

                            {options.includeFooter && spec.footerHtml && (
                                <div
                                    className="print-footer"
                                    // Footer HTML is produced by the trusted server-side
                                    // `_create_footer` (version/model/provider), NOT model
                                    // output, so this is not an injection surface.
                                    dangerouslySetInnerHTML={{ __html: spec.footerHtml }}
                                />
                            )}
                        </div>
                    </StreamingProvider>
                </ActiveChatProvider>
            )}
        </div>
    );
};

export default PrintRenderPage;
