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
    
    return sanitized;
};

export const HTMLMockupRenderer: React.FC<HTMLMockupRendererProps> = ({ html, isStreaming = false }) => {
    const { isDarkMode } = useTheme();
    const [showSource, setShowSource] = useState(false);
    const [isFullscreen, setIsFullscreen] = useState(false);
    const iframeRef = useRef<HTMLIFrameElement>(null);
    const [iframeHeight, setIframeHeight] = useState(100); // Start very small
    
    // Generate unique ID for this mockup instance
    const mockupId = useId();
    
    // Sanitize the HTML
    const sanitizedHTML = sanitizeHTML(html);
    
    // Create a complete HTML document for the iframe
    const iframeContent = `
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
            background-color: ${isDarkMode ? '#1f1f1f' : '#ffffff'};
            color: ${isDarkMode ? '#e6e6e6' : '#000000'};
        }
        * {
            box-sizing: border-box;
        }
        html, body {
            height: auto !important;
            min-height: 0 !important;
            overflow: visible !important;
        }
    </style>
</head>
<body>
    ${sanitizedHTML}
    <script>
        let heightSent = false;
        
        function updateHeight() {
            // Only send height once to prevent feedback loops
            if (heightSent) return;
            
            const firstChild = document.body.firstElementChild;
            if (firstChild) {
                // Measure the actual element directly - no cloning needed
                // Force a reflow to ensure layout is complete
                void firstChild.offsetHeight;
                
                // Use scrollHeight which includes all content and padding
                const height = firstChild.scrollHeight;
                
                const finalHeight = height + 32;
                
                heightSent = true;
                window.parent.postMessage({ type: 'resize', height: finalHeight, mockupId: '${mockupId}' }, '*');
                
                console.log('📐 Mockup height measured:', height, 'final:', finalHeight);
            } else {
                heightSent = true;
                window.parent.postMessage({ type: 'resize', height: 100 }, '*');
            }
        }
        
        // Measure after a short delay to ensure content is fully rendered
        setTimeout(updateHeight, 100);
    </script>
</body>
</html>
    `;
    
    // Listen for height updates from iframe
    useEffect(() => {
        const handleMessage = (event: MessageEvent) => {
            // Only handle messages from OUR iframe
            if (event.data.type === 'resize' && event.data.height && event.data.mockupId === mockupId) {
                setIframeHeight(event.data.height);
            }
        };
        
        window.addEventListener('message', handleMessage);
        return () => window.removeEventListener('message', handleMessage);
    }, [mockupId]);
    
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
                        <Tooltip title="Fullscreen">
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
                        ref={iframeRef}
                        srcDoc={iframeContent}
                        style={{
                            width: '100%',
                            height: `${iframeHeight}px`,
                            border: 'none',
                            borderRadius: '4px',
                            transition: 'height 0.3s ease'
                        }}
                        sandbox="allow-same-origin allow-scripts"
                        title="HTML Mockup Preview"
                    />
                </div>
            </div>
            
            {/* Fullscreen modal */}
            <Modal
                title="UI Mockup - Fullscreen"
                open={isFullscreen}
                onCancel={() => setIsFullscreen(false)}
                footer={null}
                width="90vw"
                style={{ top: 20 }}
            >
                <iframe
                    srcDoc={iframeContent}
                    style={{
                        width: '100%',
                        height: '80vh',
                        border: 'none',
                        borderRadius: '4px'
                    }}
                    sandbox="allow-same-origin allow-scripts"
                    title="HTML Mockup Fullscreen"
                />
            </Modal>
        </>
    );
};
