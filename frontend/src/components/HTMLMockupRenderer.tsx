import React, { useState, useEffect, useRef, useId } from 'react';
import { Button, Tooltip, Modal, message } from 'antd';
import { EyeOutlined, CodeOutlined, CopyOutlined, ExpandOutlined } from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';

interface HTMLMockupRendererProps {
    html: string;
    isStreaming?: boolean;
}

// Simple HTML sanitization - removes script tags and dangerous event handlers
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

    return sanitized;
};

export const HTMLMockupRenderer: React.FC<HTMLMockupRendererProps> = ({ html, isStreaming = false }) => {
    const { isDarkMode } = useTheme();
    const [showSource, setShowSource] = useState(false);
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
            /* Don't set background or color on body - let mockups define their own */
            /* Mockups are self-contained designs that should look the same in any theme */
            background-color: transparent;
            /* Remove color property entirely so it doesn't inherit to mockup content */
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
    
    const inlineIframeContent = createIframeContent(inlineMockupId);
    const fullscreenIframeContent = createIframeContent(`${mockupId}-fullscreen`);
    
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
                                icon={<CodeOutlined />}
                                onClick={() => setShowSource(!showSource)}
                            />
                        </Tooltip>
                        <Tooltip title="Copy HTML">
                            <Button
                                size="small"
                                icon={<CopyOutlined />}
                                onClick={copyHTML}
                            />
                        </Tooltip>
                        <Tooltip title="Pop-out">
                            <Button
                                size="small"
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
                    backgroundColor: isDarkMode ? '#1f1f1f' : '#ffffff',
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
                title="UI Mockup"
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
                        borderRadius: '4px'
                    }}
                    sandbox="allow-scripts"
                    title="HTML Mockup Fullscreen"
                />
            </Modal>
        </>
    );
};
