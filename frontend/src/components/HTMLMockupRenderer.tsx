import React, { useState, useEffect, useRef, useId } from 'react';
import { Button, Tooltip, Modal, message } from 'antd';
import { EyeOutlined, CodeOutlined, CopyOutlined, ExpandOutlined, BulbOutlined } from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';
import { sanitizeMockupHtml } from '../utils/domSanitize';
import type { MockupVariant } from '../utils/mockupFence';

interface HTMLMockupRendererProps {
    html: string;
    isStreaming?: boolean;
    /**
     * 'mockup' (default) frames the block for UX review. 'figure' renders the
     * graphic bare, for a block used to inline an illustration in discussion
     * rather than to propose an interface.
     */
    variant?: MockupVariant;
}

// HTML sanitization for mockups rendered in a sandboxed (allow-scripts,
// null-origin) iframe. DOMPurify (sanitizeMockupHtml) is the authoritative
// parser-based pass; the regex below is defense-in-depth on top of the
// iframe sandbox (ASR F-026 — regex alone is bypassable, never the boundary).
const sanitizeHTML = (html: string): string => {
    // Remove script tags and their content
    let sanitized = html.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '');
    
    // Remove event handler attributes (onclick, onload, onerror, etc.)
    sanitized = sanitized.replace(/\s*on\w+\s*=\s*["'][^"']*["']/gi, '');
    sanitized = sanitized.replace(/\s*on\w+\s*=\s*[^\s>]*/gi, '');
    
    // Remove javascript: protocol from href and src
    sanitized = sanitized.replace(/href\s*=\s*["']javascript:[^"']*["']/gi, 'href="#"');
    sanitized = sanitized.replace(/src\s*=\s*["']javascript:[^"']*["']/gi, 'src=""');
    
    // Remove dialog-triggering calls from inline content
    sanitized = sanitized.replace(/\balert\s*\(/g, 'void(');
    sanitized = sanitized.replace(/\bconfirm\s*\(/g, 'void(');
    sanitized = sanitized.replace(/\bprompt\s*\(/g, 'void(');

    return sanitizeMockupHtml(sanitized);
};

export const HTMLMockupRenderer: React.FC<HTMLMockupRendererProps> = ({ html, isStreaming = false, variant = 'mockup' }) => {
    const { isDarkMode } = useTheme();
    const [showSource, setShowSource] = useState(false);
    // The preview surface follows the app theme, so a mockup destined for the
    // real UI is judged against the background it will actually sit on. The
    // override exists because a mockup has to be legible in BOTH themes and
    // the author needs to check the other one without flipping the whole app;
    // it is exposed only inside the pop-out, since checking both themes is
    // design work and the inline header is also the frame around figures that
    // are simply part of a conversation. A figure never gets the override —
    // it has one correct background, the one the message is already on.
    const [previewOverride, setPreviewOverride] = useState<null | boolean>(null);
    const previewDark = previewOverride === null ? isDarkMode : previewOverride;
    const previewBg = previewDark ? '#1f1f1f' : '#ffffff';
    const [isFullscreen, setIsFullscreen] = useState(false);
    const iframeRef = useRef<HTMLIFrameElement>(null);
    const [iframeHeight, setIframeHeight] = useState(150); // Start small, grow to fit
    
    // Generate unique ID for this mockup instance
    const mockupId = useId();
    const inlineMockupId = `${mockupId}-inline`;
    
    // Sanitize the HTML
    const sanitizedHTML = sanitizeHTML(html);
    
    // Create HTML document for iframes. Each gets a unique mockupId so
    // messages from the fullscreen modal don't stomp the inline height.
    const createIframeContent = (targetMockupId: string) => `
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            /* The preview surface behind this iframe is painted with the app
               theme, so the iframe must supply a matching foreground. Leaving
               'color' unset made it fall back to the browser default (black),
               which rendered any mockup that didn't hardcode its own color as
               black-on-#1f1f1f in dark mode — illegible, and not a faithful
               preview of how the same markup renders inside the real UI. */
            background-color: transparent;
            color: ${previewDark ? '#e6e6e6' : '#1f1f1f'};
            color-scheme: ${previewDark ? 'dark' : 'light'};
        }
        /* Chrome for a mockup that relies on borders/rules for structure. */
        body {
            --mockup-border: ${previewDark ? '#434343' : '#e8e8e8'};
            --mockup-muted: ${previewDark ? '#a0a0a0' : '#666666'};
        }
        * {
            box-sizing: border-box;
        }
        html, body {
            height: auto !important;
            min-height: 100% !important;
            overflow: visible !important;
        }
    </style>
</head>
<body>
    ${sanitizedHTML}
    <script>
        (function() {
            var mid = "${targetMockupId}";
            var lastHeight = 0;

            function measureAndSend() {
                // Force reflow
                void document.body.offsetHeight;

                var height = Math.max(
                    document.body.scrollHeight,
                    document.body.offsetHeight,
                    document.documentElement.scrollHeight,
                    document.documentElement.offsetHeight
                );

                // Only send if height actually changed (avoid feedback loops)
                if (height !== lastHeight && height > 0) {
                    lastHeight = height;
                    window.parent.postMessage({ type: 'resize', height: height, mockupId: mid }, '*');
                }
            }

            // Initial measurement after layout settles
            setTimeout(measureAndSend, 50);
            setTimeout(measureAndSend, 200);
            setTimeout(measureAndSend, 600);

            // Use ResizeObserver for continuous accurate sizing
            if (typeof ResizeObserver !== 'undefined') {
                var ro = new ResizeObserver(function() {
                    measureAndSend();
                });
                ro.observe(document.body);
                ro.observe(document.documentElement);
            }
        
            // Watch for image loads and other late content
            window.addEventListener('load', function() {
                setTimeout(measureAndSend, 50);
            });
        })();
    </script>
</body>
</html>
    `;
    
    // Recreated when previewDark flips so the toggle actually re-renders the
    // iframe document (srcDoc is the only channel into the sandbox).
    const inlineIframeContent = React.useMemo(
        () => createIframeContent(inlineMockupId), [sanitizedHTML, previewDark, inlineMockupId]);
    const fullscreenIframeContent = React.useMemo(
        () => createIframeContent(`${mockupId}-fullscreen`), [sanitizedHTML, previewDark, mockupId]);
    
    // Listen for height updates from iframe
    useEffect(() => {
        const handleMessage = (event: MessageEvent) => {
            // Only handle messages from our INLINE iframe (not the fullscreen modal)
            if (event.data.type === 'resize' && event.data.height && event.data.mockupId === inlineMockupId) {
                setIframeHeight(Math.ceil(event.data.height));
            }
        };
        
        window.addEventListener('message', handleMessage);
        return () => window.removeEventListener('message', handleMessage);
    }, [inlineMockupId]);
    
    // Copy HTML to clipboard
    const copyHTML = () => {
        navigator.clipboard.writeText(html).then(() => {
            message.success('HTML copied to clipboard');
        }).catch(() => {
            message.error('Failed to copy HTML');
        });
    };
    
    // A figure is an inline graphic, not a design artifact. It gets no frame,
    // no header and no controls — the surrounding message is its container,
    // and a transparent body lets it sit flush on the conversation surface.
    // The iframe is still the boundary: the sandbox and sanitization pass are
    // identical, only the chrome differs.
    if (variant === 'figure') {
        return (
            <div style={{ margin: '12px 0' }}>
                <iframe
                    srcDoc={inlineIframeContent}
                    ref={iframeRef}
                    sandbox="allow-scripts"
                    style={{
                        width: '100%',
                        height: `${iframeHeight}px`,
                        border: 'none',
                        background: 'transparent',
                        transition: 'height 0.3s ease'
                    }}
                    title="HTML Mockup Preview"
                />
            </div>
        );
    }

    return (
        <>
            <div style={{
                backgroundColor: isDarkMode ? '#141414' : '#f8f9fa',
                border: `2px solid ${isDarkMode ? '#303030' : '#dee2e6'}`,
                borderRadius: '8px',
                margin: '16px 0',
                overflow: 'hidden'
            }}>
                {/* Header with controls */}
                <div style={{
                    backgroundColor: isDarkMode ? '#1f1f1f' : '#e9ecef',
                    padding: '8px 16px',
                    borderBottom: `1px solid ${isDarkMode ? '#303030' : '#dee2e6'}`,
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center'
                }}>
                    <span style={{
                        fontWeight: 'bold',
                        fontSize: '13px',
                        color: isDarkMode ? '#a78bfa' : '#6b46c1'
                    }}>
                        🎨 UI Mockup {isStreaming && '(streaming...)'}
                    </span>
                    
                    <div style={{ display: 'flex', gap: '8px' }}>
                        <Tooltip title="View Source">
                            <Button
                                size="small"
                                aria-label="View source"
                                icon={<CodeOutlined />}
                                onClick={() => setShowSource(!showSource)}
                            />
                        </Tooltip>
                        <Tooltip title="Copy HTML">
                            <Button
                                size="small"
                                aria-label="Copy HTML"
                                icon={<CopyOutlined />}
                                onClick={copyHTML}
                            />
                        </Tooltip>
                        <Tooltip title="Pop-out">
                            <Button
                                size="small"
                                aria-label="Pop-out"
                                icon={<ExpandOutlined />}
                                onClick={() => setIsFullscreen(true)}
                            />
                        </Tooltip>
                    </div>
                </div>
                
                {/* Source view */}
                {showSource && (
                    <div style={{
                        backgroundColor: isDarkMode ? '#0d1117' : '#f6f8fa',
                        padding: '16px',
                        borderBottom: `1px solid ${isDarkMode ? '#303030' : '#dee2e6'}`
                    }}>
                        <pre style={{
                            margin: 0,
                            fontSize: '12px',
                            lineHeight: '1.5',
                            overflow: 'auto',
                            maxHeight: '300px',
                            color: isDarkMode ? '#e6e6e6' : '#24292e'
                        }}>
                            <code>{html}</code>
                        </pre>
                    </div>
                )}
                
                {/* Mockup preview in iframe */}
                <div style={{
                    backgroundColor: previewBg,
                    padding: '16px'
                }}>
                    <iframe
                        srcDoc={inlineIframeContent}
                        ref={iframeRef}
                        sandbox="allow-scripts"
                        style={{
                            width: '100%',
                            height: `${iframeHeight}px`,
                            border: 'none',
                            borderRadius: '4px',
                            transition: 'height 0.3s ease'
                        }}
                        title="HTML Mockup Preview"
                    />
                </div>
            </div>
            
            {/* Fullscreen modal */}
            <Modal
                title={
                    <span style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                        UI Mockup
                        {/* Both-theme legibility is a design check, so the
                            override lives here rather than on the inline
                            header, which also frames conversational figures. */}
                        <Tooltip title={`Preview on ${previewDark ? 'light' : 'dark'} background`}>
                            <Button
                                size="small"
                                aria-label="Toggle preview background"
                                icon={<BulbOutlined />}
                                type={previewOverride !== null ? 'primary' : 'default'}
                                ghost={previewOverride !== null}
                                onClick={() => setPreviewOverride(!previewDark)}
                            />
                        </Tooltip>
                    </span>
                }
                open={isFullscreen}
                onCancel={() => setIsFullscreen(false)}
                footer={null}
                width="90vw"
                style={{ top: 20 }}
            >
                <iframe
                    srcDoc={fullscreenIframeContent}
                    style={{
                        width: '100%',
                        height: '80vh',
                        border: 'none',
                        borderRadius: '4px',
                        backgroundColor: previewBg
                    }}
                    sandbox="allow-scripts"
                    title="HTML Mockup Fullscreen"
                />
            </Modal>
        </>
    );
};
