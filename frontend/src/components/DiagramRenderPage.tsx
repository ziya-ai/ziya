/**
 * DiagramRenderPage — Standalone page for headless diagram rendering.
 *
 * Mounted at `/render` in the React router.  Accepts a diagram spec via:
 *   1. URL hash fragment (base64-encoded JSON spec)
 *   2. `window.postMessage({ type: 'render-diagram', spec, theme })`
 *   3. `window.__renderDiagram(jsonString)` (used by Playwright page.evaluate)
 *
 * Renders the diagram using the full D3Renderer pipeline (same plugins,
 * enhancers, and post-render fixups as the chat UI) and signals completion
 * by setting `data-render-status="complete"` on the root element.
 *
 * Used by:
 *   - `app/services/diagram_renderer.py` (Playwright headless capture)
 *   - Frontend integration tests
 *   - Any automation that needs post-rendered diagram images
 */
import React, { useEffect, useRef, useState, useCallback } from 'react';
import { useTheme } from '../context/ThemeContext';

const D3Renderer = React.lazy(
    () => import('./D3Renderer').then(m => ({ default: m.D3Renderer }))
);

interface DiagramSpec {
    type: string;          // 'mermaid' | 'graphviz' | 'vega-lite' | 'drawio' | 'packet' | ...
    definition: string;    // diagram source text or JSON
    theme?: 'dark' | 'light';
    width?: number;
    height?: number;
    title?: string;
    renderTimeoutMs?: number;  // in-page safety timeout; defaults to 30s
}

type RenderStatus = 'idle' | 'loading' | 'rendering' | 'complete' | 'error';

function parseSpecFromHash(): DiagramSpec | null {
    const hash = window.location.hash.slice(1);
    if (!hash) return null;
    try {
        return JSON.parse(atob(hash));
    } catch (e) {
        console.error('DiagramRenderPage: Failed to parse spec from hash:', e);
        return null;
    }
}

export const DiagramRenderPage: React.FC = () => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [spec, setSpec] = useState<DiagramSpec | null>(null);
    const [status, setStatus] = useState<RenderStatus>('idle');
    const [diag, setDiag] = useState<{ elapsedMs: number; lastEvent: string }>({ elapsedMs: 0, lastEvent: 'init' });
    const [errorMessage, setErrorMessage] = useState<string>('');
    const { isDarkMode, setTheme } = useTheme();
    const renderTimeoutRef = useRef<ReturnType<typeof setTimeout>>();

    const applySpec = useCallback((incoming: DiagramSpec) => {
        if (incoming.theme) {
            setTheme(incoming.theme === 'dark' ? 'dark' : 'light');
        }
        setSpec(incoming);
        setStatus('loading');
        setErrorMessage('');
    }, [setTheme]);

    // Accept specs via postMessage (used by Playwright)
    useEffect(() => {
        const handleMessage = (event: MessageEvent) => {
            if (event.data?.type === 'render-diagram' && event.data.spec) {
                applySpec(event.data.spec as DiagramSpec);
            }
        };
        window.addEventListener('message', handleMessage);
        return () => window.removeEventListener('message', handleMessage);
    }, [applySpec]);

    // Check URL hash on mount
    useEffect(() => {
        const hashSpec = parseSpecFromHash();
        if (hashSpec) applySpec(hashSpec);
    }, [applySpec]);

    // Expose imperative API for Playwright's page.evaluate()
    useEffect(() => {
        (window as any).__renderDiagram = (specJson: string) => {
            try {
                applySpec(JSON.parse(specJson) as DiagramSpec);
                return true;
            } catch (e) {
                setErrorMessage(String(e));
                setStatus('error');
                return false;
            }
        };
        return () => { delete (window as any).__renderDiagram; };
    }, [applySpec]);

    const d3Spec = spec ? {
        type: spec.type,
        definition: spec.definition,
        isStreaming: false,
        forceRender: true,
        isMarkdownBlockClosed: true,
        ...(spec.title ? { title: spec.title } : {}),
    } : null;

    // Detect render completion via MutationObserver.
    // D3 plugins render asynchronously; we watch for SVG/canvas/img
    // elements appearing inside the container as the completion signal.
    const onContainerReady = useCallback((node: HTMLDivElement | null) => {
        containerRef.current = node;
        if (!node || status !== 'loading') return;

        setStatus('rendering');
        const startedAt = Date.now();
        setDiag({ elapsedMs: 0, lastEvent: 'observer-attached' });
        if (renderTimeoutRef.current) clearTimeout(renderTimeoutRef.current);

        const safetyTimeoutMs = Math.max(1000, spec?.renderTimeoutMs ?? 30000);

        const observer = new MutationObserver(() => {
            const hasSvg = node.querySelector('svg');
            const hasCanvas = node.querySelector('canvas');
            const hasImage = node.querySelector('img');
            const hasContent = node.querySelector(
                '.vega-embed, .mermaid-output, [data-processed], .drawio-viewer'
            );

            if (hasSvg || hasCanvas || hasImage || hasContent) {
                setDiag({
                    elapsedMs: Date.now() - startedAt,
                    lastEvent: hasSvg ? 'svg-detected'
                        : hasCanvas ? 'canvas-detected'
                        : hasImage ? 'img-detected'
                        : 'content-detected',
                });
                // Allow post-render enhancers time to apply fixups
                setTimeout(() => {
                    setStatus('complete');
                    observer.disconnect();
                }, 500);
            } else {
                setDiag(prev => ({
                    elapsedMs: Date.now() - startedAt,
                    lastEvent: prev.lastEvent === 'observer-attached' ? 'mutation-no-output' : prev.lastEvent,
                }));
            }
        });

        observer.observe(node, { childList: true, subtree: true, attributes: true });

        // Safety timeout — configurable via spec.renderTimeoutMs (default 30s).
        // On timeout, capture diagnostic info so the caller can see what
        // was actually in the DOM when we gave up.
        renderTimeoutRef.current = setTimeout(() => {
            const hasSvg = node.querySelector('svg');
            if (hasSvg) {
                setStatus('complete');
                setDiag({ elapsedMs: Date.now() - startedAt, lastEvent: 'timeout-with-svg' });
            } else {
                const counts = {
                    svg: node.querySelectorAll('svg').length,
                    canvas: node.querySelectorAll('canvas').length,
                    img: node.querySelectorAll('img').length,
                    children: node.children.length,
                    htmlLen: node.innerHTML.length,
                };
                setErrorMessage(
                    `Render timeout after ${safetyTimeoutMs}ms (type=${spec?.type}). ` +
                    `DOM snapshot: ${JSON.stringify(counts)}`
                );
                setDiag({ elapsedMs: Date.now() - startedAt, lastEvent: 'timeout-no-output' });
                setStatus('error');
            }
            observer.disconnect();
        }, safetyTimeoutMs);
    }, [status, spec?.renderTimeoutMs, spec?.type]);

    // Cleanup timeout on unmount
    useEffect(() => {
        return () => {
            if (renderTimeoutRef.current) clearTimeout(renderTimeoutRef.current);
        };
    }, []);

    const containerStyle: React.CSSProperties = {
        width: spec?.width || '100%',
        height: spec?.height || 'auto',
        minHeight: 200,
        padding: 16,
        backgroundColor: isDarkMode ? '#1a1a2e' : '#ffffff',
        color: isDarkMode ? '#e0e0e0' : '#1a1a1a',
        overflow: 'hidden',
    };

    return (
        <div
            id="diagram-render-root"
            data-render-status={status}
            data-error={errorMessage || undefined}
            data-elapsed-ms={diag.elapsedMs}
            data-last-event={diag.lastEvent}
            style={{
                width: '100vw',
                height: '100vh',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                backgroundColor: isDarkMode ? '#0d0d1a' : '#f5f5f5',
                overflow: 'hidden',
                margin: 0,
                padding: 0,
            }}
        >
            {status === 'idle' && (
                <div style={{ color: '#888', fontSize: 14, textAlign: 'center' }}>
                    Waiting for diagram spec…
                    <br />
                    <code style={{ fontSize: 11 }}>
                        POST /api/render-diagram or window.__renderDiagram(json)
                    </code>
                </div>
            )}

            {status === 'error' && (
                <div style={{ color: '#ff4d4f', padding: 20, textAlign: 'center' }}>
                    <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>
                        Render Error
                    </div>
                    <div style={{ fontSize: 13 }}>{errorMessage}</div>
                </div>
            )}

            {d3Spec && status !== 'error' && (
                <div ref={onContainerReady} id="diagram-render-container" style={containerStyle}>
                    <React.Suspense
                        fallback={
                            <div style={{ textAlign: 'center', padding: 40, color: '#888' }}>
                                Loading renderer…
                            </div>
                        }
                    >
                        <D3Renderer
                            spec={d3Spec}
                            type="d3"
                            isStreaming={false}
                            forceRender={true}
                            width={spec?.width}
                            height={spec?.height}
                        />
                    </React.Suspense>
                </div>
            )}
        </div>
    );
};

export default DiagramRenderPage;
