import React, { useState, useEffect, memo, useMemo, useCallback, useRef, useId, useLayoutEffect } from 'react';
import { marked, Tokens } from 'marked';
import { Alert, Button, message, Tooltip, Collapse } from 'antd';
import { parseDiff } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import { DiffLine } from './DiffLine';
import { D3Renderer } from './D3Renderer';
import { useChatContext } from '../context/ChatContext';
import { parseToolCall, formatToolCallForDisplay } from '../utils/toolCallParser';
import { parseThinkingContent, removeThinkingTags } from '../utils/thinkingParser';
import { useFolderContext } from '../context/FolderContext';
import {
    SplitCellsOutlined, NumberOutlined, EyeOutlined, FileTextOutlined,
    CheckCircleOutlined, CloseCircleOutlined, CheckOutlined
} from '@ant-design/icons';
import { loadPrismLanguage, type PrismStatic } from '../utils/prismLoader';
import { useTheme } from '../context/ThemeContext';
import { detectFileOperationSyntax, renderFileOperationSafely } from '../utils/fileOperationParser';
import { FileOperationRenderer } from './FileOperationRenderer';
import { isDebugLoggingEnabled, debugLog } from '../utils/logUtils';
import 'katex/dist/katex.min.css';
import { restartStreamWithEnhancedContext } from '../apis/chatApi';
import { sendPayload } from '../apis/chatApi';
import { formatMCPOutput } from '../utils/mcpFormatter';
import { HTMLMockupRenderer } from './HTMLMockupRenderer';

const { Panel } = Collapse;

// Helper function to make "Shell Configuration settings" clickable in security error messages
const makeShellConfigLinkClickable = (message: string | React.ReactNode, onOpenShellConfig?: () => void): React.ReactNode => {
    // If message is already a React node, return it as is
    if (typeof message !== 'string') {
        return message;
    }

    if (!message.includes('Shell Configuration settings')) {
        return message;
    }

    const parts = message.split('Shell Configuration settings');

    return (
        <>
            {parts[0]}
            <a
                onClick={(e) => {
                    e.preventDefault();
                    onOpenShellConfig?.();
                }}
                style={{ cursor: 'pointer', textDecoration: 'underline', color: '#1890ff', fontWeight: 500 }}
            >
                Shell Configuration settings
            </a>
            {parts[1]}
        </>
    );
};

// Thinking component for DeepSeek reasoning content
const ThinkingBlock: React.FC<{ children: React.ReactNode; isDarkMode: boolean; isStreaming?: boolean }> = ({ children, isDarkMode, isStreaming = false }) => {
    // Start expanded during streaming, collapsed when done
    const [isExpanded, setIsExpanded] = useState(isStreaming);
    const [htmlContent, setHtmlContent] = useState('');

    // Parse markdown in children if it's a string
    const isString = typeof children === 'string';

    useEffect(() => {
        if (isString) {
            // Unescape any escaped backticks before parsing
            const unescapedContent = (children as string).replace(/\\`\\`\\`/g, '```');
            const result = marked.parse(unescapedContent, { breaks: true, gfm: true });
            if (typeof result === 'string') {
                setHtmlContent(result);
            } else {
                result.then(setHtmlContent);
            }
        }
    }, [children, isString]);

    return (
        <div className={`thinking-block ${isDarkMode ? 'dark' : 'light'}`} style={{
            border: `1px solid ${isDarkMode ? '#444' : '#ddd'}`,
            borderRadius: '8px',
            margin: '12px 0',
            backgroundColor: isDarkMode ? '#1a1a1a' : '#f8f9fa'
        }}>
            <div
                onClick={() => setIsExpanded(!isExpanded)}
                style={{
                    padding: '8px 12px',
                    cursor: 'pointer',
                    borderBottom: isExpanded ? `1px solid ${isDarkMode ? '#444' : '#ddd'}` : 'none',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    fontSize: '14px',
                    color: isDarkMode ? '#888' : '#666'
                }}
            >
                <span>{isExpanded ? '▼' : '▶'}</span>
                <span>🤔 Thinking...</span>
            </div>
            {isExpanded && (
                <div
                    style={{
                        padding: '12px',
                        fontSize: '13px',
                        color: isDarkMode ? '#ccc' : '#555'
                    }}
                    {...(isString ? { dangerouslySetInnerHTML: { __html: htmlContent } } : { children })}
                />
            )}
        </div>
    );
};

// Define the status interface
interface HunkStatus {
    applied: boolean;
    alreadyApplied?: boolean;
    reason: string;
}

// Define the status type returned from the API
interface ApiHunkStatus {
    status: 'succeeded' | 'failed' | 'already_applied';
    stage?: string;
    error_details?: any;
}

// Create a global event bus for hunk status updates
const hunkStatusEventBus = new EventTarget();
const HUNK_STATUS_EVENT = 'hunkStatusUpdate';
// Add a global set to track processed window events
const processedWindowEvents = new Set<string>();

// Add a map to track which request ID corresponds to which diff element
const diffRequestMap = new Map<string, string>();

interface ApplyChangesButtonProps {
    diff: string;
    filePath: string;
    fileIndex: number;
    diffElementId: string;
    enabled: boolean;
    isStreaming?: boolean;
    setHunkStatuses?: (updater: (prev: Map<string, HunkStatus>) => Map<string, HunkStatus>) => void;
}

interface ToolBlockProps {
    toolName: string;
    content: string;
    isDarkMode: boolean;
    toolInput?: any;
    onOpenShellConfig?: () => void;
}

const ToolBlock: React.FC<ToolBlockProps> = ({ toolName, content, isDarkMode, onOpenShellConfig }) => {
    const [isExpanded, setIsExpanded] = useState(false);
    const [renderedHtml, setRenderedHtml] = useState('');

    // Extract command/query from toolName if it contains encoded information
    const [actualToolName, encodedCommand] = toolName.includes('|')
        ? toolName.split('|', 2)
        : [toolName, ''];

    // Define cleanToolName early for use in header
    const cleanToolName = actualToolName.replace('mcp_', '').replace(/_/g, ' ');

    // Extract query from content if this is internalsearch
    const isInternalSearch = actualToolName === 'mcp_InternalSearch';
    const queryMatch = isInternalSearch && content.match(/Query:\s*"([^"]+)"/);
    const searchQuery = queryMatch ? queryMatch[1] : '';

    // Try to format the content intelligently
    const formattedOutput = useMemo(() => {
        try {
            // Parse if it looks like JSON
            if (content.trim().startsWith('{') || content.trim().startsWith('[')) {
                const parsed = JSON.parse(content);
                return formatMCPOutput(toolName, parsed, null, {
                    defaultCollapsed: true,
                    maxLength: 10000
                });
            }
        } catch (e) {
            // Not JSON, continue with regular processing
        }

        // For non-JSON content, create a simple formatted output
        const shouldCollapse = content.length > 500 || content.split('\n').length > 15;
        return {
            content,
            type: 'text' as const,
            collapsed: shouldCollapse,
            summary: shouldCollapse ? `Output (${content.length} chars, ${content.split('\n').length} lines)` : undefined
        };
    }, [toolName, content]);

    // Check if content should be rendered as markdown (contains markdown formatting)
    const shouldRenderAsMarkdown = useMemo(() => {
        return content.includes('**') || content.includes('[') || content.includes('###') || content.includes('<a href');
    }, [content]);

    // Render markdown content to HTML
    useEffect(() => {
        if (shouldRenderAsMarkdown) {
            const result = marked.parse(content, { breaks: true, gfm: true });
            if (typeof result === 'string') {
                // Post-process HTML to add link styling for dark mode
                let styledHtml = result;
                if (isDarkMode) {
                    // Add inline styles to links for dark mode readability
                    styledHtml = styledHtml.replace(
                        /<a href=/g,
                        '<a style="color: #58a6ff; text-decoration: none;" onmouseover="this.style.color=\'#79c0ff\'; this.style.textDecoration=\'underline\';" onmouseout="this.style.color=\'#58a6ff\'; this.style.textDecoration=\'none\';" href='
                    );
                }
                // Reduce excessive spacing from multiple consecutive line breaks
                styledHtml = styledHtml
                    .replace(/<p><\/p>/g, '') // Remove empty paragraphs
                    .replace(/(<\/p>)\s*<p>/g, '$1<p>') // Tighten spacing between paragraphs
                    .replace(/(<h3[^>]*>.*?<\/h3>)\s+/g, '$1\n') // Reduce space after headers
                    .replace(/<br>\s*<br>/g, '<br>'); // Remove double line breaks

                setRenderedHtml(result);
                setRenderedHtml(styledHtml);
            } else {
                result.then(setRenderedHtml);
                result.then(html => {
                    let styledHtml = html;
                    if (isDarkMode) {
                        styledHtml = styledHtml.replace(/<a href=/g, '<a style="color: #58a6ff; text-decoration: none;" onmouseover="this.style.color=\'#79c0ff\'; this.style.textDecoration=\'underline\';" onmouseout="this.style.color=\'#58a6ff\'; this.style.textDecoration=\'none\';" href=');
                    }
                    styledHtml = styledHtml
                        .replace(/<p><\/p>/g, '')
                        .replace(/(<\/p>)\s*<p>/g, '$1<p>')
                        .replace(/(<h3[^>]*>.*?<\/h3>)\s+/g, '$1\n')
                        .replace(/<br>\s*<br>/g, '<br>');
                    setRenderedHtml(styledHtml);
                });
            }
        }
    }, [content, shouldRenderAsMarkdown]);

    // Extract command/query information for display in header
    const getToolSummary = () => {
        // If we have encoded command from the lang attribute, use it
        if (encodedCommand) {
            // Check if it's a shell command
            if (encodedCommand.includes(': $ ')) {
                return `🔧 ${encodedCommand}`;
            }
            // Check if it's a search query
            if (searchQuery) {
                return `🔍 ${cleanToolName}: "${searchQuery}"`;
            }
            // Check if it's a search query from encoded command
            if (encodedCommand.includes(': "')) {
                return `🔍 ${encodedCommand}`;
            }
            // Check if it's multiple parameters
            if (encodedCommand.endsWith(': multiple')) {
                return `🛠️ ${encodedCommand}`;
            }
            // Generic display
            return `🛠️ ${encodedCommand}`;
        }

        // Fallback: extract from content or show generic tool name
        return `🛠️ ${cleanToolName}`;
    };

    // Check if this is a security error from shell command blocking
    let isSecurityError = content.includes('🚫 SECURITY BLOCK') ||
        content.includes('Command not allowed') ||
        content.includes('COMMAND BLOCKED');

    let securityErrorMessage = content;

    // Check if content is a JSON error object from MCP server
    if (content.includes("'error': True") && content.includes('SECURITY BLOCK')) {
        try {
            // Extract the message from the JSON-like string
            const messageMatch = content.match(/'message': "([^"]+)"/);
            if (messageMatch) {
                let message = messageMatch[1].replace(/\\n/g, '\n');
                // Remove the redundant "🚫 SECURITY BLOCK:" prefix since we show it in the title
                message = message.replace(/^🚫 SECURITY BLOCK:\s*/, '');
                securityErrorMessage = message;
                isSecurityError = true;
            }
        } catch (e) {
            // If parsing fails, use original content
        }
    }

    // Check if this is an MCP tool error
    const isMCPError = content.includes('MCP Tool Error') ||
        content.includes('MCP Resource Error');

    // If this is a security error, render it with special styling
    if (isSecurityError) {
        return (
            <Alert
                message="🚫 Command Blocked"
                description={makeShellConfigLinkClickable(securityErrorMessage, onOpenShellConfig)}
                type="warning"
                showIcon
                style={{ margin: '16px 0', border: '2px solid #faad14', whiteSpace: 'pre-line' }}
            />
        );
    }

    if (isMCPError) {
        return (
            <Alert
                message="MCP External Error"
                description={content}
            />
        );
    }

    const isShellCommand = actualToolName === 'mcp_run_shell_command';

    const { content: formattedContent, collapsed, summary } = formattedOutput;
    const hierarchicalResults = formattedOutput.hierarchicalResults;
    const shouldShowCollapsed = collapsed !== false && (summary || formattedContent.length > 500);

    // Don't clean tool markers - they're already properly formatted by the backend
    // Aggressive cleaning corrupts diffs, code blocks, and template literals
    const cleanContent = formattedContent.trim();

    // Color scheme based on tool type
    const getToolColors = () => {
        if (isShellCommand) {
            return {
                bg: isDarkMode ? '#0f1419' : '#f8f9fa',
                border: isDarkMode ? '#1e2328' : '#e9ecef',
                headerBg: isDarkMode ? '#1e2328' : '#e9ecef',
                headerText: isDarkMode ? '#7dd3fc' : '#0369a1',
                contentText: isDarkMode ? '#e2e8f0' : '#1e293b'
            };
        } else {
            return {
                bg: isDarkMode ? '#1a1a2e' : '#f0f4f8',
                border: isDarkMode ? '#2d2d44' : '#cbd5e0',
                headerBg: isDarkMode ? '#2d2d44' : '#cbd5e0',
                headerText: isDarkMode ? '#a78bfa' : '#6b46c1',
                contentText: isDarkMode ? '#e2e8f0' : '#1e293b'
            };
        }
    };

    const colors = getToolColors();

    // Render hierarchical results if available (e.g., workspace search)
    if (hierarchicalResults && hierarchicalResults.length > 0) {
        const isCodeContent = hierarchicalResults[0].language && hierarchicalResults[0].language !== 'text' && hierarchicalResults[0].language !== 'markdown';
        return (
            <div style={{
                backgroundColor: colors.bg,
                border: `2px solid ${colors.border}`,
                borderRadius: '12px',
                margin: '16px 0',
                overflow: 'hidden',
                fontFamily: 'Monaco, Menlo, "Ubuntu Mono", monospace',
                fontSize: '14px',
                boxShadow: isDarkMode
                    ? '0 4px 6px -1px rgba(0, 0, 0, 0.3), 0 2px 4px -1px rgba(0, 0, 0, 0.2)'
                    : '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)'
            }}>
                <div style={{
                    backgroundColor: colors.headerBg,
                    padding: '8px 16px',
                    borderBottom: `1px solid ${colors.border}`,
                    color: colors.headerText,
                    fontWeight: 'bold',
                    fontSize: '12px',
                    letterSpacing: '0.5px'
                }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <span>{getToolSummary()}</span>
                        <span style={{ fontSize: '11px', opacity: 0.7, fontWeight: 'normal' }}>
                            {formattedContent || summary}
                        </span>
                    </div>
                </div>

                <div style={{ padding: '8px' }}>
                    <Collapse
                        ghost
                        bordered={false}
                        style={{
                            backgroundColor: 'transparent',
                            color: colors.contentText
                        }}
                    >
                        {hierarchicalResults.map((result, index) => (
                            <Panel
                                header={
                                    <span style={{
                                        color: colors.contentText,
                                        fontSize: '13px',
                                        fontWeight: '500'
                                    }}>
                                        {result.title}
                                    </span>
                                }
                                key={index}
                                style={{
                                    borderBottom: index < hierarchicalResults.length - 1 ? `1px solid ${colors.border}` : 'none',
                                    marginBottom: '4px'
                                }}
                            >
                                {isCodeContent ? (
                                    <pre style={{
                                        margin: 0,
                                        padding: '12px',
                                        backgroundColor: isDarkMode ? '#0d1117' : '#f6f8fa',
                                        borderRadius: '4px',
                                        overflow: 'auto',
                                        maxHeight: '400px',
                                        fontSize: '12px',
                                        lineHeight: '1.5'
                                    }}>
                                        <code className={`language-${result.language}`}>{result.content}</code>
                                    </pre>
                                ) : (
                                    <div
                                        style={{
                                            margin: 0,
                                            padding: '12px',
                                            fontSize: '13px',
                                            lineHeight: '1.4'
                                        }}
                                        dangerouslySetInnerHTML={{ __html: marked.parse(result.content, { breaks: true, gfm: true }) as string }}
                                    />
                                )}
                            </Panel>
                        ))}
                    </Collapse>
                </div>
            </div>
        );
    }

    return (
        <div style={{
            backgroundColor: colors.bg,
            border: `2px solid ${colors.border}`,
            borderRadius: '12px',
            margin: '16px 0',
            overflow: 'hidden',
            fontFamily: 'Monaco, Menlo, "Ubuntu Mono", monospace',
            fontSize: '14px',
            boxShadow: isDarkMode
                ? '0 4px 6px -1px rgba(0, 0, 0, 0.3), 0 2px 4px -1px rgba(0, 0, 0, 0.2)'
                : '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)'
        }}>
            <div style={{
                backgroundColor: colors.headerBg,
                padding: '8px 16px',
                borderBottom: `1px solid ${colors.border}`,
                color: colors.headerText,
                fontWeight: 'bold',
                fontSize: '12px',
                letterSpacing: '0.5px'
            }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span>{getToolSummary()}</span>
                    {shouldShowCollapsed && (
                        <div style={{
                            marginLeft: 'auto',
                            fontSize: '11px',
                            opacity: 0.7,
                            cursor: 'pointer',
                            fontWeight: 'normal'
                        }} onClick={(e) => {
                            e.stopPropagation();
                            setIsExpanded(!isExpanded);
                        }}>
                            {isExpanded ? '▼ Collapse' : '▶ Expand'} {summary && `(${summary})`}
                        </div>
                    )}
                </div>
            </div>

            {shouldShowCollapsed && !isExpanded ? (
                (() => {
                    const firstLine = cleanContent.split('\n')[0];
                    const truncatedFirstLine = firstLine.length > 200 ? firstLine.substring(0, 200) + '...' : firstLine;
                    return (
                        <div
                            style={{
                                padding: '16px',
                                color: colors.contentText,
                                fontSize: '14px',
                                cursor: 'pointer'
                            }}
                            onClick={() => setIsExpanded(true)}
                        >
                            {cleanContent ? (
                                <div>
                                    <pre style={{
                                        margin: 0,
                                        padding: 0,
                                        whiteSpace: 'pre-wrap',
                                        wordBreak: 'break-word',
                                        fontFamily: 'monospace'
                                    }}>
                                        {truncatedFirstLine}
                                    </pre>
                                    <div style={{
                                        marginTop: '8px',
                                        fontStyle: 'italic',
                                        textAlign: 'center',
                                        opacity: 0.7
                                    }}>
                                        {summary}{firstLine.length > 200 ? ' (preview truncated)' : ''} - Click to expand
                                    </div>
                                </div>
                            ) : (
                                <div style={{ fontStyle: 'italic', textAlign: 'center' }}>
                                    {summary} - Click to expand
                                </div>
                            )}
                        </div>
                    );
                })()
            ) : (
                shouldRenderAsMarkdown ? (
                    <div style={{
                        padding: '16px',
                        color: colors.contentText,
                        maxHeight: isExpanded ? 'none' : '400px',
                        overflow: isExpanded ? 'visible' : 'auto',
                        fontSize: '14px',
                        lineHeight: '1.3'
                    }}
                        dangerouslySetInnerHTML={{ __html: renderedHtml }}
                    />
                ) : (
                    <pre style={{
                        margin: 0,
                        padding: '16px',
                        color: colors.contentText,
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        maxHeight: isExpanded ? 'none' : '400px',
                        overflow: isExpanded ? 'visible' : 'auto'
                    }}>
                        {cleanContent}
                    </pre>
                )
            )}
        </div>
    );
};


export type RenderPath = 'full' | 'prismOnly' | 'diffOnly' | 'raw';

// Define the Change types to match react-diff-view's internal types
type ChangeType = 'normal' | 'insert' | 'delete';

interface BaseChange {
    content: string;
    type: ChangeType;
    isInsert?: boolean;
    isDelete?: boolean;
    isNormal?: boolean;
    lineNumber?: number;
    oldLineNumber?: number;
    newLineNumber?: number;
}

// Define our own Hunk interface to match react-diff-view's structure
interface BaseHunk {
    content: string;
    oldStart: number;
    oldLines: number;
    newStart: number;
    newLines: number;
    changes: BaseChange[];
    isPlain?: boolean;
    oldLineNumber?: number;
    newLineNumber?: number;
}

// Define our extended hunk type that includes status
interface ExtendedHunk extends BaseHunk {
    status?: HunkStatus;
}

interface BaseToken {
    type: string;
    raw?: string;
}

interface TokenWithText extends BaseToken {
    text: string;
    lang?: string;
    tokens?: TokenWithText[];
    task?: boolean;
    checked?: boolean;
    toolName?: string;
}

interface ErrorBoundaryProps {
    children: React.ReactNode;
    fallback?: React.ReactNode;
    type?: 'graphviz' | 'code';
}

interface ErrorBoundaryState {
    hasError: boolean;
}

declare global {
    interface Window {
        Prism: PrismStatic;
        diffElementPaths?: Map<string, string>;
        diffViewType?: 'unified' | 'split';
        diffShowLineNumbers?: boolean;
        diffDisplayMode?: 'raw' | 'pretty';
        hunkStatusRegistry: Map<string, Map<string, HunkStatus>>;
    }
}

type ErrorType = 'graphviz' | 'code' | 'unknown';

class ErrorBoundary extends React.Component<
    ErrorBoundaryProps,
    ErrorBoundaryState> {
    constructor(props) {
        super(props);
        this.state = { hasError: false };
    }


    static getDerivedStateFromError(error) {
        return { hasError: true };
    }

    componentDidCatch(_error, errorInfo) {
        const errorType: ErrorType = this.props.type || 'unknown';
        console.error(`${errorType} rendering error:`, error, errorInfo);
    }

    render() {
        if (this.state.hasError) {
            if (this.props.type === 'graphviz') {
                return this.props.fallback || (
                    <div>Something went wrong rendering the diagram.</div>
                );
            }
            if (this.props.type === 'code') {
                return this.props.fallback || (
                    <pre><code>Error rendering code block</code></pre>
                );
            }
            return (
                <div>Something went wrong rendering the diagram.</div>
            );
        }

        return this.props.children;
    }
}

export const DisplayModes = ['raw', 'pretty'] as const;
export type DisplayMode = typeof DisplayModes[number];
export interface DiffViewProps {
    diff: string;
    viewType: 'split' | 'unified';
    initialDisplayMode: DisplayMode;
    showLineNumbers: boolean;
    fileIndex?: number;
    elementId: string;
    forceRender?: boolean;
}

interface DiffControlsProps {
    displayMode: 'raw' | 'pretty';
    viewType: 'split' | 'unified';
    showLineNumbers: boolean;
    onDisplayModeChange: (mode: 'raw' | 'pretty') => void;
    onViewTypeChange: (type: 'split' | 'unified') => void;
    onLineNumbersChange: (show: boolean) => void;
    fileTitle?: string;
}

const DiffControls = memo(({
    displayMode,
    viewType,
    showLineNumbers,
    fileTitle,
    onDisplayModeChange,
    onViewTypeChange,
    onLineNumbersChange
}: DiffControlsProps) => {
    const { isDarkMode } = useTheme();
    const [isHovered, setIsHovered] = useState(false);

    const handleDisplayModeChange = (value: any) => {
        const newMode = value as DisplayMode;
        onDisplayModeChange(newMode);
    };

    return (
        <div
            className="diff-view-controls"
            onMouseEnter={() => setIsHovered(true)}
            onMouseLeave={() => setIsHovered(false)}
            style={{
                backgroundColor: isDarkMode ? '#1f1f1f' : '#fafafa',
                display: 'flex',
                justifyContent: 'flex-end',
                alignItems: 'center',
                width: '100%',
                padding: '4px 8px',
                gap: '8px',
                borderBottom: `1px solid ${isDarkMode ? '#303030' : '#e8e8e8'}`,
                opacity: isHovered ? 1 : 0.7,
                transition: 'opacity 0.3s ease',
                zIndex: 10
            }}>
            <div style={{
                flex: 1,
                textAlign: 'left',
                fontWeight: 'bold',
                fontSize: '14px',
                overflow: 'hidden',
                textOverflow: 'ellipsis'
            }}>{fileTitle}</div>
            {displayMode === 'pretty' && (
                <>
                    <Tooltip title={showLineNumbers ? "Hide Line Numbers" : "Show Line Numbers"}>
                        <Button
                            type={showLineNumbers ? "primary" : "default"}
                            size="small"
                            icon={<NumberOutlined />}
                            onClick={() => onLineNumbersChange(!showLineNumbers)}
                            style={{
                                padding: '0 8px',
                                minWidth: '32px',
                                height: '24px'
                            }}
                        />
                    </Tooltip>
                </>
            )}
            {/* Unified/Split view toggle button - only show in pretty mode */}
            {displayMode === 'pretty' && (
                <Tooltip title={viewType === 'unified' ? "Split View" : "Unified View"}>
                    <Button
                        type={viewType === 'split' ? "primary" : "default"}
                        size="small"
                        icon={<SplitCellsOutlined />}
                        onClick={() => {
                            const newViewType = viewType === 'unified' ? 'split' : 'unified';
                            window.diffViewType = newViewType;
                            onViewTypeChange(newViewType);
                        }}
                        style={{
                            padding: '0 8px',
                            minWidth: '32px',
                            height: '24px',
                            marginRight: '8px'
                        }}
                    />
                </Tooltip>
            )}
            <Tooltip title={displayMode === 'raw' ? "Switch to Pretty View" : "Switch to Raw View"}>
                <Button
                    type="default"
                    size="small"
                    icon={displayMode === 'raw' ? <EyeOutlined /> : <FileTextOutlined />}
                    onClick={() => {
                        const newMode = displayMode === 'pretty' ? 'raw' : 'pretty';
                        window.diffDisplayMode = newMode;
                        handleDisplayModeChange(newMode);
                    }}
                    style={{
                        padding: '0 8px',
                        minWidth: '32px',
                        height: '24px'
                    }}
                />
            </Tooltip>
        </div>
    );
});

// Helper function to extract all file paths from a diff
const extractAllFilesFromDiff = (diffContent: string): string[] => {
    const files: string[] = [];
    const newFiles = new Set<string>(); // Track new file creations
    const lines = diffContent.split('\n');

    // First pass: identify new file creations
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];

        // Check for new file mode marker
        if (line.includes('new file mode')) {
            // Look backwards and forwards for the file path
            for (let j = Math.max(0, i - 5); j < Math.min(lines.length, i + 5); j++) {
                const checkLine = lines[j];
                const plusMatch = checkLine.match(/^\+\+\+ b\/(.+)$/);
                if (plusMatch && plusMatch[1] !== '/dev/null') {
                    newFiles.add(plusMatch[1]);
                }
            }
        }
    }

    for (const line of lines) {
        // Extract from git diff headers
        // Handle both standard format and malformed Gemini format
        const gitMatch = line.match(/^diff --git (?:a\/)?([^\s]+) (?:b\/)?([^\s]+)$/);
        if (gitMatch) {
            const oldPath = gitMatch[1];
            const newPath = gitMatch[2];
            if (newPath !== '/dev/null') files.push(newPath);
            if (oldPath !== '/dev/null' && oldPath !== newPath) files.push(oldPath);
        }

        // Extract from unified diff headers as backup
        const minusMatch = line.match(/^--- a\/(.+)$/);
        if (minusMatch && !minusMatch[1].includes('/dev/null')) {
            files.push(minusMatch[1]);
        }

        const plusMatch = line.match(/^\+\+\+ b\/(.+)$/);
        if (plusMatch && !plusMatch[1].includes('/dev/null')) {
            files.push(plusMatch[1]);
        }
    }

    // Remove duplicates and filter out new file creations
    const uniqueFiles = [...new Set(files)];
    const existingFiles = uniqueFiles.filter(file =>
        !newFiles.has(file) &&
        // Filter out regex patterns and invalid filenames
        !file.includes('(?:') &&
        !file.includes('$/)') &&
        !file.includes('[^') &&
        !file.endsWith(');') &&
        !file.includes('\\')
    );

    return existingFiles;
};

// Function to check if files are in current context - do this locally!
const checkFilesInContext = (filePaths: string[], currentFiles: string[] = []): { missingFiles: string[], availableFiles: string[] } => {
    const missingFiles: string[] = [];
    const availableFiles: string[] = [];

    for (const filePath of filePaths) {
        // Clean up the file path (remove a/ or b/ prefixes from git diffs)
        let cleanPath = filePath.trim();
        if (cleanPath.startsWith('a/') || cleanPath.startsWith('b/')) {
            cleanPath = cleanPath.substring(2);
        }

        // Check if the file is in the current selected context
        const isInContext = currentFiles.some(currentFile =>
            currentFile === cleanPath ||
            cleanPath.startsWith(currentFile + '/') ||
            (currentFile.endsWith('/') && cleanPath.startsWith(currentFile))
        );

        if (isInContext) {
            availableFiles.push(cleanPath);
        } else {
            missingFiles.push(cleanPath);
        }
    }

    return { missingFiles, availableFiles };
};

DiffControls.displayName = 'DiffControls';

const extractSingleFileDiff = (fullDiff: string, filePath: string): string => {
    // If the diff doesn't contain multiple files, return it as is
    if (!fullDiff.includes("diff --git") || fullDiff.indexOf("diff --git") === fullDiff.lastIndexOf("diff --git")) {
        return fullDiff;
    }

    try {
        // Split the diff into sections by diff --git headers
        const lines: string[] = fullDiff.split('\n');
        const result: string[] = [];

        // Clean up file path for matching
        const cleanFilePath = filePath.replace(/^[ab]\//, '');

        let inTargetFile = false;
        let collectingHunk = false;
        let currentHunkHeader: string | null = null;
        let currentHunkContent: string[] = [];

        // Process each line
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            const nextLine = i < lines.length - 1 ? lines[i + 1] : '';

            // Check for file header
            if (line.startsWith('diff --git')) {
                // If we were collecting a hunk, add it to the result
                if (collectingHunk && inTargetFile && currentHunkHeader !== null) {
                    result.push(currentHunkHeader);
                    result.push(...currentHunkContent);
                }

                // Reset state for new file
                collectingHunk = false;
                currentHunkHeader = null;
                currentHunkContent = [];
                inTargetFile = false;

                // Check if this is our target file
                // Handle both standard format and malformed Gemini format
                const fileMatch = line.match(/diff --git (?:a\/)?([^\/]*(?:\/[^\/]*)*) (?:b\/)?(.*)$/);
                if (fileMatch) {
                    const oldPath = fileMatch[1];
                    const newPath = fileMatch[2];

                    // Check if this file matches our target by exact path
                    if (oldPath === cleanFilePath || newPath === cleanFilePath ||
                        oldPath.endsWith(`/${cleanFilePath}`) || newPath.endsWith(`/${cleanFilePath}`)) {
                        inTargetFile = true;
                        result.push(line);

                        // Also check the next line for index info
                        if (nextLine.startsWith('index ')) {
                            result.push(nextLine);
                            i++; // Skip this line in the next iteration
                        }
                    } else {
                        inTargetFile = false;

                        // Log for debugging
                        console.debug(`Skipping file: old=${oldPath}, new=${newPath}, target=${cleanFilePath}`);
                    }
                }
            }
            // If we're in the target file, collect all headers and content
            else if (inTargetFile) {
                // File headers (index, ---, +++)
                if (line.startsWith('index ') || line.startsWith('--- ') || line.startsWith('+++ ')) {
                    result.push(line);
                }
                // Hunk header
                else if (line.startsWith('@@ ')) {
                    // If we were collecting a previous hunk, add it to the result
                    if (collectingHunk && currentHunkHeader !== null) {
                        result.push(currentHunkHeader);
                        result.push(...currentHunkContent);
                    }

                    // Start collecting a new hunk
                    collectingHunk = true;
                    currentHunkHeader = line;
                    currentHunkContent = [];
                }
                // Hunk content (context, additions, deletions)
                else if (collectingHunk && (line.startsWith(' ') || line.startsWith('+') || line.startsWith('-') || line.startsWith('\\'))) {
                    currentHunkContent.push(line);
                }
                // Empty lines within a hunk
                else if (collectingHunk && line.trim() === '') {
                    currentHunkContent.push(line);
                }
            }
        }

        // Log the extraction results
        console.debug(`Extracted diff for ${filePath}:`, {
            targetFileFound: inTargetFile || result.length > 0,
            extractedLines: result.length
        });
        // Add the last hunk if we were collecting one
        if (collectingHunk && inTargetFile && currentHunkHeader !== null) {
            result.push(currentHunkHeader!);
            result.push(...currentHunkContent);
        }

        // If we found our target file, return the extracted diff
        if (result.length > 0) {
            return result.join('\n').trim();
        }

        // If we didn't find the target file, return the original diff
        console.warn(`Could not find file ${cleanFilePath} in the diff`);
        return fullDiff;

    } catch (error) {
        console.error("Error extracting single file diff:", error);
        return fullDiff.trim(); // Return the full diff as a fallback
    }
};

// Helper function to fix Haiku-style diffs that are missing unified diff headers
const fixHaikuStyleDiff = (diff: string): string => {
    const lines = diff.split('\n');
    const result: string[] = [];

    // Extract file path from git header
    // Handle both standard format and malformed Gemini format  
    const gitHeaderMatch = lines[0].match(/diff --git (?:a\/)?([^\/]*(?:\/[^\/]*)*) (?:b\/)?(.*)$/);
    if (!gitHeaderMatch) {
        return diff; // Can't fix without git header
    }

    const filePath = gitHeaderMatch[2] || gitHeaderMatch[1];

    // Add git header
    result.push(lines[0]);

    // Add missing unified diff headers
    result.push(`--- a/${filePath}`);
    result.push(`+++ b/${filePath}`);

    // Process the rest of the lines
    let i = 1;
    while (i < lines.length) {
        const line = lines[i];

        // Skip any existing headers that might be malformed
        if (line.startsWith('---') || line.startsWith('+++') || line.startsWith('index ')) {
            i++;
            continue;
        }

        // Handle hunk headers - fix incomplete ones
        if (line.startsWith('@@')) {
            // Check if this is a Haiku-style incomplete hunk header
            const hunkMatch = line.match(/^@@\s+-(\d+),(\d+)\s+\+(\d+),(\d+)\s+@@(.*)$/);
            if (hunkMatch) {
                result.push(line);
            } else {
                // Try to fix malformed hunk headers
                const partialMatch = line.match(/^@@\s+-(\d+),?\s*(\d*)\s+\+?(\d+),?\s*(\d*)\s*@@?(.*)$/);
                if (partialMatch) {
                    const [, oldStart, oldCount, newStart, newCount, context] = partialMatch;
                    const fixedLine = `@@ -${oldStart},${oldCount || '1'} +${newStart},${newCount || '1'} @@${context || ''}`;
                    result.push(fixedLine);
                } else {
                    result.push(line);
                }
            }
        } else {
            // Regular content line - preserve as is
            result.push(line);
        }

        i++;
    }

    const fixedDiff = result.join('\n');
    console.log('🔧 Fixed Haiku-style diff:', fixedDiff.substring(0, 200) + '...');
    return fixedDiff;
};

// Helper function to check if this is a deletion diff
const isDeletionDiff = (content: string) => {
    return content.includes('diff --git') &&
        content.includes('/dev/null') &&
        content.includes('deleted file mode') &&
        content.includes('--- a/') &&
        content.includes('+++ /dev/null');
};

const normalizeGitDiff = (diff: string): string => {
    // because LLMs tend to ignore instructions and get lazy
    if (diff.startsWith('diff --git') || diff.match(/^---\s+\S+/m) || diff.includes('/dev/null') ||
        diff.match(/^@@\s+-\d+/m)) {
        const lines: string[] = diff.split('\n');
        const normalizedLines: string[] = [];

        // Check if this is a properly formatted diff
        const hasDiffHeaders = lines.some(line =>
            (line.startsWith('---') || line.startsWith('+++'))
        ) && (
                lines.some(line => line.startsWith('--- a/') || line.startsWith('+++ b/')) ||
                lines.some(line => line.startsWith('--- /dev/null')) // Support new file diffs
            );
        const hasHunkHeader = lines.some(line =>
            /^@@\s+-\d+,?\d*\s+\+\d+,?\d*\s+@@/.test(line) ||
            /^@@\s+-\d+,\d+\s+\+\d+,\d+\s+@@/.test(line) ||
            /^@@\s+-\d+,\d+\s+@@/.test(line)
        );

        // Check for Haiku-style diffs that have git headers but missing unified diff headers
        const hasGitHeader = lines.some(line => line.startsWith('diff --git'));
        const hasHunkHeaders = lines.some(line => line.startsWith('@@'));

        if ((hasDiffHeaders && hasHunkHeader) || (hasGitHeader && hasHunkHeaders && !hasDiffHeaders)) {
            // Handle Haiku-style diffs that are missing unified diff headers
            if (hasGitHeader && hasHunkHeaders && !hasDiffHeaders) {
                return fixHaikuStyleDiff(diff);
            }
            return diff;  // Return original diff if it's properly formatted
        }

        // Extract file path from unified diff headers if present
        let filePath = '';
        for (const line of lines) {
            const unifiedMatch = line.match(/^(?:---|\+\+\+)\s+(?:[ab]\/)?(.+)$/);
            if (unifiedMatch) {
                filePath = unifiedMatch[1];
                break;
            }
        }

        // If no path found from unified headers, try git diff header
        if (!filePath) {
            // Handle both standard format and malformed Gemini format
            const gitMatch = lines[0].match(/diff --git (?:a\/)?([^\/]*(?:\/[^\/]*)*) (?:b\/)?(.*)$/);
            if (gitMatch && gitMatch[1]) {
                filePath = gitMatch[1];
            }
        }

        if (!filePath) {
            return diff;  // Return original if we can't parse the git diff line
        }

        // If we have a diff --git line but missing proper headers, add them
        if (!hasDiffHeaders) {
            // Insert headers after the diff --git line
            lines.splice(1, 0,
                `--- a/${filePath}`,
                `+++ b/${filePath}`
            );
        }

        let addCount = 0;
        let removeCount = 0;
        let contextCount = 0;

        // Always keep the diff --git line
        normalizedLines.push(lines[0]);

        // Count lines and collect content
        const contentLines = lines.slice(1).filter(line => {
            if (line.startsWith('diff --git') || line.startsWith('index ')) {
                return false;
            }
            if (line.startsWith('---') || line.startsWith('+++')) {
                return false;
            }
            if (line.startsWith('@@')) {
                // Normalize hunk headers with leading zeros or incomplete format
                const normalizedHunk = line.replace(/^@@\s+-0*(\d+),?(\d*)\s+\+?0*(\d+),?(\d*)\s*@@?.*$/,
                    (match, oldStart, oldCount, newStart, newCount) => {
                        const oldLines = oldCount || '1';
                        const newLines = newCount || '1';
                        return `@@ -${oldStart},${oldLines} +${newStart},${newLines} @@`;
                    });
                return normalizedHunk !== line; // Only include if we normalized it
            }
            if (line.startsWith('---') || line.startsWith('+++')) {
                return false;
            }
            if (line.startsWith('@@')) {
                // Keep hunk headers
                return true;
            }
            // Check for +/- anywhere in the leading whitespace
            const trimmed = line.trimStart();
            if (trimmed.startsWith('+')) {
                addCount++;
                return true;
            }
            if (trimmed.startsWith('-')) {
                removeCount++;
                return true;
            }
            // Handle case where +/- might be preceded by spaces
            const indentMatch = line.match(/^[\s]*([-+])/);
            if (indentMatch) {
                if (indentMatch[1] === '+') addCount++;
                if (indentMatch[1] === '-') removeCount++;
                return true;
            }
            if (line.trim().length > 0) {
                contextCount++;
                return true;
            }
            if (line.trim().length === 0) {
                contextCount++;
                return true;
            }
            return false;
        });

        // Find and normalize any hunk headers in the content
        const normalizedContentLines = contentLines.map(line => {
            if (line.startsWith('@@')) {
                return line.replace(/^@@\s+-0*(\d+),?(\d*)\s+\+?0*(\d+),?(\d*)\s*@@?.*$/,
                    (match, oldStart, oldCount, newStart, newCount) => {
                        const oldLines = oldCount || '1';
                        const newLines = newCount || '1';
                        return `@@ -${oldStart},${oldLines} +${newStart},${newLines} @@`;
                    });
            }
            return line;
        });

        // Add hunk header
        const existingHunkHeaders = normalizedContentLines.filter(line => line.startsWith('@@'));
        if (existingHunkHeaders.length > 0) {
            normalizedLines.push(...existingHunkHeaders);
        } else {
            normalizedLines.push(`@@ -1,${removeCount + contextCount} +1,${addCount + contextCount} @@`);
        }

        // Add content lines, preserving +/- and adding spaces for context
        normalizedContentLines.filter(line => !line.startsWith('@@')).forEach(line => {
            const trimmed = line.trimStart();
            if (trimmed.startsWith('+') || trimmed.startsWith('-')) {
                // For indented +/- lines, preserve only the content indentation
                const marker = trimmed[0];
                const content = trimmed.slice(1);
                normalizedLines.push(`${marker}${content}`);
            } else {
                // Handle case where +/- might be preceded by spaces
                const indentMatch = line.match(/^[\s]*([-+])(.*)/);
                if (indentMatch) {
                    normalizedLines.push(`${indentMatch[1]}${indentMatch[2]}`);
                } else {
                    normalizedLines.push(` ${line.trim()}`);
                }
            }
        });

        return normalizedLines.join('\n');
    }
    return diff;
};

// Shared language detection function - moved here so it can be used in both diff rendering and code block fixing
const detectLanguage = (filePath: string): string => {
    if (!filePath || filePath === '/dev/null') return 'plaintext';

    // Handle paths that might have git prefixes
    let cleanPath = filePath;
    if (cleanPath.startsWith('a/') || cleanPath.startsWith('b/')) {
        cleanPath = cleanPath.substring(2);
    }

    if (!cleanPath) return 'plaintext';

    const extension = cleanPath.split('.').pop()?.toLowerCase();

    const languageMap: { [key: string]: string } = {
        'js': 'javascript',
        'jsx': 'javascript',
        'ts': 'typescript',
        'tsx': 'typescript',
        'swift': 'swift',
        'objectivec': 'objectivec',
        'objc': 'objectivec',
        'metal': 'c',
        'py': 'python',
        'rb': 'ruby',
        'php': 'php',
        'java': 'java',
        'go': 'go',
        'rs': 'rust',
        'cpp': 'cpp',
        'c': 'clike',
        'cs': 'csharp',
        'css': 'css',
        'html': 'markup',
        'xml': 'markup',
        'md': 'markdown',
        'sh': 'bash',
        'bash': 'bash'
    };
    return languageMap[extension || ''] || 'plaintext';
};

const DiffView: React.FC<DiffViewProps> = ({ diff, viewType, initialDisplayMode, showLineNumbers, elementId, fileIndex }) => {
    const [isLoading, setIsLoading] = useState(true);
    const { isDarkMode } = useTheme();
    const parsedFilesRef = useRef<any[]>([]);
    const [parseError, setParseError] = useState<boolean>(false);
    const lastValidDiffRef = useRef<string | null>(null);
    const { isStreaming: isGlobalStreaming } = useChatContext();
    const [instanceHunkStatusMap, setInstanceHunkStatusMap] = useState<Map<string, HunkStatus>>(new Map());
    const [statusUpdateCounter, setStatusUpdateCounter] = useState<number>(0);
    const [errorMessage, setErrorMessage] = useState<string | null>(null);
    const [displayMode, setDisplayMode] = useState<DisplayMode>(window.diffDisplayMode || 'pretty'); // Use window setting
    const diffRef = useRef<string>(diff);
    const forceRenderRef = useRef<boolean>(false);

    // Use a stable ID that doesn't change on re-renders
    const diffId = useRef<string>(elementId || `diff-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`).current;

    // Flag to prevent rendering during streaming
    const isStreamingRef = useRef<boolean>(false);
    // Store the diff in a ref to avoid unnecessary re-renders
    useEffect(() => {
        diffRef.current = diff;
    }, [diff]);

    // Force render during streaming
    useEffect(() => {
        forceRenderRef.current = isGlobalStreaming;
        return () => { forceRenderRef.current = false; };
    }, [isGlobalStreaming]);

    // Initialize global registry if needed
    useEffect(() => {
        // Initialize the global registry if it doesn't exist
        window.diffElementPaths = window.diffElementPaths || new Map();
        window.hunkStatusRegistry = window.hunkStatusRegistry || new Map();

    }, []);

    // Listen for hunk status updates
    useEffect(() => {
        const handleStatusUpdate = (event: Event) => {
            if (!isStreamingRef.current) {
                console.log("DiffView received hunk status update event");
                setStatusUpdateCounter(prev => prev + 1);
            }
        };
        hunkStatusEventBus.addEventListener(HUNK_STATUS_EVENT, handleStatusUpdate);

        return () => {
            hunkStatusEventBus.removeEventListener(HUNK_STATUS_EVENT, handleStatusUpdate);
        };
    }, []);

    // Function to update hunk statuses from API response
    const updateHunkStatuses = useCallback((hunkStatuses: Record<string, any>, targetDiffId: string = diffId, force: boolean = false) => {
        if (!hunkStatuses) return;
        if (!diff) return;
        console.log(`Updating hunk statuses for ${targetDiffId} (we are ${diffId})`, hunkStatuses);

        try {
            const files = parseDiff(diff);
            files.forEach((file, fileIndex) => {
                file.hunks.forEach((hunk, hunkIndex) => {
                    // Create a key for this hunk
                    const hunkKey = `0-${hunkIndex}`;

                    // Get the hunk ID (1-based)
                    const hunkId = hunkIndex + 1;

                    // Check if we have status for this hunk
                    if (hunkStatuses[hunkId]) {
                        const status = hunkStatuses[hunkId];
                        console.log(`Updating status for hunk #${hunkId}:`, status);
                        // Update the status in our local map
                        setInstanceHunkStatusMap(prev => {
                            const newMap = new Map(prev);

                            // Also update the global registry
                            if (!window.hunkStatusRegistry.has(diffId)) {
                                window.hunkStatusRegistry.set(diffId, new Map());
                            }
                            const registryMap = window.hunkStatusRegistry.get(diffId);
                            if (registryMap) {
                                registryMap.set(hunkKey, newMap.get(hunkKey) as HunkStatus);

                                // Only trigger re-render if not streaming or if forced
                                if (!isStreamingRef.current || force) setStatusUpdateCounter(prev => prev + 1);
                            }

                            newMap.set(hunkKey, {
                                applied: status.status === 'succeeded' || status.status === 'already_applied',
                                alreadyApplied: status.status === 'already_applied',
                                reason: status.status === 'failed'
                                    ? 'Failed in ' + status.stage + ' stage'
                                    : status.status === 'already_applied'
                                        ? 'Already applied'
                                        : 'Successfully applied'
                            });
                            return newMap;
                        });
                        // Update will happen in the renderHunks function
                    };
                });
            });
        } catch (error) {
            const errorMsg = error instanceof Error ? error.message : String(error);
            console.error("Error updating hunk statuses:", errorMsg);
        }
    }, [diff, diffId]);

    // Listen for window-level hunk status updates with data, but don't update during streaming
    useEffect(() => {
        const handleWindowStatusUpdate = (event: CustomEvent) => {
            if (!event.detail) return;
            console.log("DiffView received window hunk status update with data:", event.detail);

            // Check if this update is for our diff element
            let isForThisDiff = false;

            // Also check if the request ID maps to our diff ID
            if (event.detail.requestId && diffRequestMap.get(event.detail.requestId) === diffId || event.detail.targetDiffElementId === diffId) {
                isForThisDiff = true;
                console.log(`direct match for diffId ${diffId}`);

                // Apply the hunk statuses directly to our component state
                if (event.detail.hunkStatuses) {
                    Object.entries(event.detail.hunkStatuses).forEach(([hunkId, status]) => {
                        updateHunkStatuses({ [hunkId]: status }, diffId);
                    });
                }

                // If not a match, skip processing
                if (!isForThisDiff) {
                    console.log(`Ignoring event for diff ${event.detail.targetDiffElementId || event.detail.requestId || 'unknown'}, we are ${diffId}`);
                    return;
                }

                // Call updateHunkStatuses with the provided data
                // Only update if not streaming or if this is a completion event
                const isCompletionEvent = event.detail.isCompletionEvent === true;
                updateHunkStatuses(event.detail.hunkStatuses || {}, diffId, isCompletionEvent);

                // Force re-render only if not streaming or if this is a completion event
                if (!isStreamingRef.current || isCompletionEvent) {
                    setStatusUpdateCounter(prev => prev + 1);
                }
            };

            window.addEventListener('hunkStatusUpdate', handleWindowStatusUpdate as EventListener);

            return () => {
                window.removeEventListener('hunkStatusUpdate', handleWindowStatusUpdate as EventListener);
            };
        }
    }, [updateHunkStatuses, diffId]);


    useEffect(() => {
        const parseAndSetFiles = () => {
            try {
                const normalizedDiff = normalizeGitDiff(diff);
                let parsedFiles = parseDiff(normalizedDiff);

                // After all parsing attempts, check if we have valid, renderable files/hunks
                if (!parsedFiles || parsedFiles.length === 0 ||
                    !parsedFiles[0].hunks || parsedFiles[0].hunks.length === 0) {
                    // If not, it's effectively a parse error for rich rendering purposes
                    if (process.env.NODE_ENV === 'development') {
                        console.warn('DiffView - Parse failed: No valid files/hunks found');
                    }
                    setParseError(true);
                    parsedFilesRef.current = []; // Ensure ref is also empty
                } else {
                    parsedFilesRef.current = parsedFiles;
                    setParseError(false);
                }

                // If we have a unified diff without git headers, try to extract the file path
                if (parsedFiles.length > 0 && !parsedFiles[0].oldPath && !parsedFiles[0].newPath) {
                    const lines = diff.split('\n');
                    for (const line of lines) {
                        if (line.startsWith('--- a/')) {
                            parsedFiles[0].oldPath = line.substring(6);
                        } else if (line.startsWith('--- /dev/null')) {
                            // This is a new file diff - set type but leave oldPath undefined
                            // so the "Create:" label will be shown
                            parsedFiles[0].type = 'add';
                        } else if (line.startsWith('+++ b/')) {
                            parsedFiles[0].newPath = line.substring(6);
                            break;
                        }
                    }
                }

                // Special handling for deletion diffs
                if (parsedFiles.length === 0 && isDeletionDiff(diff)) {
                    const match = diff.match(/--- a\/(.*)\n/);
                    if (match) {
                        const filePath = match[1];
                        parsedFiles = [{
                            type: 'delete',
                            oldPath: filePath,
                            newPath: '/dev/null',
                            oldRevision: 'HEAD',
                            newRevision: '0000000',
                            oldEndingNewLine: true,
                            newEndingNewLine: true,
                            oldMode: '100644',
                            newMode: '000000',
                            similarity: 0,
                            hunks: [{
                                content: diff,
                                oldStart: 1,
                                oldLines: diff.split('\n').filter(line => line.startsWith('-')).length,
                                newStart: 0,
                                newLines: 0,
                                changes: diff.split('\n')
                                    .filter(line => !line.match(/^(diff --git|index|---|^\+\+\+)/))
                                    .map((line, index: number) => ({
                                        type: 'delete' as const,
                                        content: line.slice(1),
                                        isDelete: true,
                                        isInsert: false,
                                        isNormal: false,
                                        lineNumber: index + 1,
                                        oldLineNumber: index + 1,
                                        newLineNumber: undefined
                                    }))
                            }]
                        }];
                    }
                }
                // Check if we have valid, renderable hunks
                if (parsedFiles && parsedFiles.length > 0 && parsedFiles[0].hunks && parsedFiles[0].hunks.length > 0) {
                    // We have a valid diff structure - update the reference and clear parse error
                    parsedFilesRef.current = parsedFiles;
                    lastValidDiffRef.current = diff; // Store this as our last valid diff
                    setParseError(false);
                } else if (lastValidDiffRef.current && isGlobalStreaming) {
                    // During streaming, if parsing fails but we have a previous valid state, keep using it
                    setParseError(false); // Don't set parse error - we'll use the last valid state
                } else {
                    // No valid previous state and current parse failed - set parse error
                    setParseError(true);
                    parsedFilesRef.current = [];
                }
            } catch (error) {
                if (process.env.NODE_ENV === 'development') {
                    console.error('DiffView - ParseDiff error:', error);
                    console.error('DiffView - Failed diff content:', diff.substring(0, 200) + '...');
                }
                setErrorMessage(error instanceof Error ? error.message : String(error));
                console.error('Error parsing diff:', error);

                // If we're streaming and have a previous valid state, keep using it
                if (lastValidDiffRef.current && isGlobalStreaming) {
                    setParseError(false);
                } else {
                    // Otherwise set parse error
                    setParseError(true);
                    parsedFilesRef.current = [];
                }
            }
        };
        parseAndSetFiles();
    }, [diff, isGlobalStreaming]);

    // Set loading to false since we're not doing any async tokenization
    useEffect(() => {
        // Skip tokenization entirely since it's unused and problematic
        setIsLoading(false);
    }, [diff, parseError]); // Re-tokenize if diff changes or parseError state changes

    const renderHunks = (hunks: any[], filePath: string, fileIndex: number) => {

        const tableClassName = `diff-table ${viewType === 'split' ? 'diff-table-split' : 'diff-table-unified'}`;

        if (!hunks || hunks.length === 0) {
            return <div className="diff-empty-hunks">No changes found in this diff.</div>;
        }

        return (
            <table className={tableClassName}>
                <colgroup>
                    {viewType === 'split' ? (
                        <>
                            {showLineNumbers && <col
                                className="diff-gutter-col"
                                style={{ width: '50px', minWidth: '50px' }}
                            />}
                            <col
                                className="diff-code-col"
                                style={{ width: 'calc(50% - 50px)' }}
                            />
                            {showLineNumbers && <col
                                className="diff-gutter-col"
                                style={{ width: '50px', minWidth: '50px' }}
                            />}
                            <col
                                className="diff-code-col"
                                style={{ width: 'calc(50% - 50px)' }}
                            />
                        </>
                    ) : (
                        <React.Fragment>
                            {showLineNumbers && <col className="diff-gutter-col" style={{ width: '50px', minWidth: '50px' }} />}
                            <col style={{ width: 'auto' }} />
                        </React.Fragment>
                    )}
                </colgroup>
                <tbody>
                    {hunks.map((hunk, hunkIndex) => {
                        const previousHunk = hunkIndex > 0 ? (hunks[hunkIndex - 1] as ExtendedHunk) : null;
                        const linesBetween = previousHunk ?
                            hunk.oldStart - (previousHunk.oldStart + previousHunk.oldLines) : 0;
                        const showEllipsis = displayMode === 'pretty' &&
                            previousHunk;
                        const ellipsisText = linesBetween <= 0 ? '...' :
                            linesBetween === 1 ?
                                '... (1 line)' :
                                `... (${linesBetween} lines)`;

                        // Get hunk status if available
                        // Create a stable key for this hunk
                        const hunkKey = `${fileIndex}-${hunkIndex}`;
                        const status = instanceHunkStatusMap.get(hunkKey);
                        const isApplied = status?.applied;
                        const statusReason = status?.reason || '';
                        const isAlreadyApplied = status?.alreadyApplied;

                        // Add visual indicator for hunk status
                        const hunkStatusIndicator = status && (
                            <span style={{
                                color: isApplied ? '#52c41a' : '#ff4d4f',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '4px',
                                marginLeft: '8px'
                            }}>
                                {isApplied ?
                                    isAlreadyApplied ?
                                        <span><CheckCircleOutlined style={{ color: '#faad14' }} /> Already Applied</span> :
                                        <span><CheckCircleOutlined style={{ color: '#52c41a' }} /> Applied</span> :
                                    <span><CloseCircleOutlined /> Failed: {statusReason}</span>
                                }
                            </span>
                        );

                        return (
                            <React.Fragment key={`${fileIndex}-${hunkIndex}`}>
                                {/* Add a hunk header with status-based styling */}
                                {status && (
                                    <tr className="hunk-status-header">
                                        <td colSpan={viewType === 'split' ? 4 : 3} style={{
                                            padding: 0,
                                            borderLeft: `3px solid ${isApplied ?
                                                (isAlreadyApplied ? '#faad14' : '#52c41a') :
                                                '#ff4d4f'}`,
                                            backgroundColor: isApplied ?
                                                (isAlreadyApplied ? 'rgba(250, 173, 20, 0.05)' : 'rgba(82, 196, 26, 0.05)') :
                                                'rgba(255, 77, 79, 0.05)'
                                        }}></td>
                                    </tr>)}
                                {showEllipsis && (
                                    <tr id={`hunk-${fileIndex}-${hunkIndex}`} data-diff-id={elementId}>
                                        <td
                                            colSpan={viewType === 'split' ? 4 : 3}
                                            className="diff-ellipsis"
                                            style={{
                                                display: 'flex',
                                                justifyContent: 'space-between',
                                                alignItems: 'center',
                                                padding: '4px 8px'
                                            }}
                                        >
                                            <span>{ellipsisText}</span>
                                            {hunkStatusIndicator}
                                        </td>
                                    </tr>
                                )}
                                <tr className="hunk-content-wrapper">
                                    <td colSpan={viewType === 'split' ? 4 : 3} style={{
                                        padding: 0,
                                        border: status ? `1px solid ${isApplied ?
                                            (isAlreadyApplied ? '#faad14' : '#52c41a') :
                                            '#ff4d4f'}` : 'none',
                                        borderLeft: status ? `3px solid ${isApplied ?
                                            (isAlreadyApplied ? '#faad14' : '#52c41a') :
                                            '#ff4d4f'}` : 'none',
                                        borderRadius: '3px',
                                        overflow: 'visible'
                                    }}>
                                        <table className="diff" style={{ width: '100%', borderCollapse: 'collapse' }}><tbody>
                                            {renderContent(hunk, filePath, status, fileIndex, hunkIndex)}
                                        </tbody></table>
                                    </td>
                                </tr>
                            </React.Fragment>
                        );
                    })}
                </tbody>
            </table >
        );
    };

    // Handle parse error case
    if (parseError) {
        if (process.env.NODE_ENV === 'development') {
            console.warn('DiffView - Rendering fallback due to parse error');
        }
        return (
            <div>
                <div style={{
                    backgroundColor: isDarkMode ? '#2d2d2d' : '#f8f8f8',
                    padding: '8px 12px',
                    borderRadius: '4px 4px 0 0',
                    fontSize: '12px',
                    color: isDarkMode ? '#888' : '#666',
                    borderBottom: '1px solid ' + (isDarkMode ? '#404040' : '#e1e4e8')
                }}>
                    📄 Diff (fallback rendering - parsing failed)
                </div>
                <pre data-testid="diff-parse-error" style={{
                    backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                    color: isDarkMode ? '#e6e6e6' : 'inherit',
                    padding: '16px',
                    borderRadius: '0 0 4px 4px',
                    margin: 0,
                    overflow: 'auto',
                    fontFamily: 'Monaco, Menlo, "Ubuntu Mono", monospace',
                    fontSize: '13px',
                    lineHeight: '1.45'
                }}>
                    <code>{diff}</code>
                </pre>
            </div>
        );
    }


    const renderContent = (hunk: any, filePath: string, status?: any, fileIndex?: number, hunkIndex?: number): JSX.Element[] => {

        // Define base style for rows
        const rowStyle: React.CSSProperties = {};

        return hunk.changes && hunk.changes.map((change: any, i: number) => {
            // Apply the status-based styling to each row
            const style = { ...rowStyle };

            // Ensure change has proper line numbers
            if (!change.oldLineNumber && !change.newLineNumber) {
                // Calculate line numbers based on hunk start and position in changes array
                const normalChangesBeforeThis = hunk.changes
                    .slice(0, i)
                    .filter(c => c.type === 'normal' || c.type === change.type).length;

                if (change.type === 'normal' || change.type === 'delete') {
                    change.oldLineNumber = hunk.oldStart + normalChangesBeforeThis;
                }
                if (change.type === 'normal' || change.type === 'insert') {
                    change.newLineNumber = hunk.newStart + normalChangesBeforeThis;
                }
            }

            // Add additional styling for specific change types and ensure line numbers are set
            if (change.type === 'insert') {
                style.backgroundColor = status?.applied ? (status?.alreadyApplied ? 'rgba(250, 173, 20, 0.1)' : 'rgba(82, 196, 26, 0.1)') : style.backgroundColor;
            } else if (change.type === 'delete') {
                style.backgroundColor = status?.applied ? (status?.alreadyApplied ? 'rgba(250, 173, 20, 0.1)' : 'rgba(82, 196, 26, 0.1)') : style.backgroundColor;
            }

            let oldLine = undefined;
            let newLine = undefined;

            if (showLineNumbers) {
                oldLine = (change.type === 'normal' || change.type === 'delete') ? change.oldLineNumber || change.lineNumber : undefined;
                if (change.type === 'delete' && !oldLine) {
                    // Ensure delete lines always have an old line number
                    oldLine = change.lineNumber;
                }

                if (change.type === 'insert' && !newLine) {
                    // Ensure insert lines always have a new line number
                    newLine = change.lineNumber;
                }
                newLine = (change.type === 'normal' || change.type === 'insert') ? change.newLineNumber || change.lineNumber : undefined;
            }

            // Add an ID to the first row of each hunk for scrolling
            const rowProps: any = {};
            if (i === 0 && fileIndex !== undefined && hunkIndex !== undefined) {
                rowProps.id = `hunk-${fileIndex}-${hunkIndex}`;
            }

            return (
                <DiffLine
                    key={i}
                    content={change.content}
                    language={detectLanguage(filePath)}
                    viewType={viewType}
                    type={change.type}
                    oldLineNumber={oldLine}
                    newLineNumber={newLine}
                    showLineNumbers={showLineNumbers}
                    similarity={change.similarity}
                    style={style}
                    {...rowProps}
                />
            );
        });
    };

    // Define theme-specific styles
    const darkModeStyles = {
        addition: {
            background: '#1a4d1a',
            color: '#c6e6c6'
        },
        deletion: {
            background: '#4d1a1a',
            color: '#e6c6c6'
        },
        gutter: {
            background: '#161b22',
            color: '#8b949e'
        },
        content: {
            background: '#1f1f1f',
            color: '#e6e6e6'
        }
    };

    const lightModeStyles = {
        addition: {
            background: '#e6ffec',
            color: '#24292e'
        },
        deletion: {
            background: '#ffebe9',
            color: '#24292e'
        },
        gutter: {
            background: '#f6f8fa',
            color: '#57606a'
        },
        content: {
            background: '#ffffff',
            color: '#24292e'
        }
    };

    const styles = `
        .hunk-status-bar {
            display: flex;
            align-items: flex-start;
            flex-wrap: wrap; 
            margin-top: 4px;
            margin: 0 auto 0 0;
            padding: 0 8px;
        }
        
        .hunk-status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
        }
        
        .hunk-status-dot.pending {
            background-color: #8c8c8c;
        }
 
        .diff-header {
            background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
            padding: 0px 16px 12px;
        }
 
        .diff-header-content {
            margin-top: 4px;
            position: sticky;
            left: 0;
            right: 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
            height: 32px;
            box-sizing: border-box;
        }
 
        .diff-header-content b {
            color: ${isDarkMode ? '#e6e6e6' : '#24292e'};
            font-size: 14px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            margin-right: 16px;
        }
        
        .hunk-status-header {
            height: 0;
            overflow: hidden;
        }
     
        .hunk-status-bar {
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            margin: 0 auto 0 0;
            padding: 0 8px;
        }
        
        .hunk-content-wrapper {
            margin-bottom: 12px;
            margin-top: 4px;
        }
        
        .hunk-status-indicator {
            display: inline-flex !important;
            align-items: center;
            justify-content: center;
            width: 24px;
            height: 28px !important;
            line-height: 28px !important;
            vertical-align: middle;
            margin: 0 2px;
            border-radius: 4px;
            cursor: pointer;
        }
        
        .hunk-status-indicator:hover {
            background-color: ${isDarkMode ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.05)'};
            overflow: hidden;
            text-overflow: ellipsis;
            margin-right: 16px;
        }
    `;

    const renderFile = (file: any, fileIndex: number) => {

        const tableClassName = `diff-table ${viewType === 'split' ? 'diff-table-split' : 'diff-table-unified'}`;

        return (
            <div
                key={`diff-file-${fileIndex}-${elementId}`}
                className={`diff-view smaller-diff-view ${viewType === 'split' ? 'diff-view-split' : 'diff-view-unified'}`}
                style={{
                    backgroundColor: currentTheme.content.background,
                    color: currentTheme.content.color
                }}
            >
                <div className="diff-header" style={{ padding: '8px 16px 12px' }}>
                    <div className="diff-header-content" style={{ display: 'flex', justifyContent: 'flex-end' }}>
                        <span className="hunk-status-bar">
                            {file.hunks.map((hunk, hunkIndex) => {
                                // Get the hunk status if available
                                // Create a stable key for this hunk
                                const hunkKey = `${fileIndex}-${hunkIndex}`;
                                const status = instanceHunkStatusMap.get(hunkKey);
                                const hunkId = hunkIndex + 1;
                                // Use the elementId to make the hunk reference unique across multiple diffs
                                const hunkRef = `hunk-${fileIndex}-${hunkIndex}`;

                                let statusIcon;
                                let statusColor = '#8c8c8c';
                                let statusTooltip;

                                if (!status) {
                                    // Pending status 
                                    statusIcon = <div className="hunk-status-dot pending" />;
                                    statusTooltip = `Hunk #${hunkId}: Pending`;
                                } else if (status.applied) {
                                    if (status.alreadyApplied) {
                                        // Already applied status
                                        statusIcon = <CheckCircleOutlined style={{ color: '#faad14' }} />;
                                        statusColor = '#faad14'; // Orange for already applied
                                        statusTooltip = `Hunk #${hunkId}: Already applied`;
                                    } else {
                                        // Successfully applied status
                                        statusIcon = <CheckCircleOutlined style={{ color: '#52c41a' }} />;
                                        statusColor = '#52c41a'; // Green
                                        statusTooltip = `Hunk #${hunkId}: Successfully applied`;
                                    }
                                } else {
                                    // Failed status
                                    statusIcon = <CloseCircleOutlined style={{ color: '#ff4d4f' }} />;
                                    statusColor = '#ff4d4f'; // Red
                                    statusTooltip = `Hunk #${hunkId}: Failed - ${status.reason}`;
                                }

                                return (
                                    <Tooltip key={hunkId} title={statusTooltip}>
                                        <div
                                            className="hunk-status-indicator"
                                            style={{
                                                display: 'inline-flex',
                                                height: '28px !important',
                                                lineHeight: '28px !important',
                                                verticalAlign: 'middle',
                                                marginTop: '4px',
                                                backgroundColor: isDarkMode ? 'rgba(0,0,0,0.2)' : 'rgba(0,0,0,0.05)',
                                                color: statusColor,
                                                border: `1px solid ${statusColor}`,
                                                // Add a subtle border to match the hunk styling
                                                boxShadow: status ? `0 0 0 1px ${statusColor}` : 'none'
                                            }}
                                            onClick={() => {
                                                // Create a more specific selector that includes the diff element ID
                                                // to ensure we're targeting the correct hunk in the correct diff
                                                const diffContainer = document.getElementById(`diff-view-wrapper-${elementId}`);
                                                const hunkElement = diffContainer ?
                                                    diffContainer.querySelector(`#${hunkRef}`) :
                                                    document.getElementById(hunkRef);
                                                if (hunkElement) {
                                                    hunkElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                                }
                                            }}
                                        >
                                            {status ? statusIcon : hunkId}
                                        </div>
                                    </Tooltip>
                                );
                            })}
                        </span>

                        <div className="header-right">
                            {!['delete'].includes(file.type) &&
                                <ApplyChangesButton
                                    diff={diff}
                                    fileIndex={fileIndex}
                                    diffElementId={elementId}
                                    filePath={file.newPath || file.oldPath}
                                    isStreaming={isGlobalStreaming}
                                    setHunkStatuses={setInstanceHunkStatusMap}
                                    enabled={window.enableCodeApply === 'true'}
                                />
                            }
                        </div>
                    </div>
                </div>
                <div className="diff-content-wrapper" style={{
                    position: 'relative',
                    overflowY: 'hidden'
                }} id={`diff-content-${elementId}`}>
                    <div className="diff-content">
                        {viewType === 'unified' && file.hunks.map((hunk: ExtendedHunk, hunkIndex: number) => {
                            const hunkKey = `${fileIndex}-${hunkIndex}`;
                            const status = instanceHunkStatusMap.get(hunkKey);
                            const isApplied = status?.applied;
                            const isAlreadyApplied = status?.alreadyApplied;
                            const statusReason = status?.reason || '';

                            // Create the status indicator component
                            const hunkStatusIndicator = status && (
                                <span style={{
                                    color: isApplied ? (isAlreadyApplied ? '#faad14' : '#52c41a') : '#ff4d4f',
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: '4px',
                                    marginLeft: '8px'
                                }}>
                                    {isApplied ?
                                        isAlreadyApplied ?
                                            <span><CheckCircleOutlined style={{ color: '#faad14' }} /> Already Applied</span> :
                                            <span><CheckCircleOutlined style={{ color: '#52c41a' }} /> Applied</span> :
                                        <span><CloseCircleOutlined /> Failed: {statusReason}</span>
                                    }
                                </span>
                            );


                            // Calculate lines between hunks for ellipsis display
                            const previousHunk = hunkIndex > 0 ? (file.hunks[hunkIndex - 1] as ExtendedHunk) : null;
                            const linesBetween = previousHunk ?
                                hunk.oldStart - (previousHunk.oldStart + previousHunk.oldLines) : 0;
                            const showEllipsis = displayMode === 'pretty' && previousHunk;
                            const ellipsisText = linesBetween <= 0 ? '...' :
                                linesBetween === 1 ? '... (1 line)' : `... (${linesBetween} lines)`;

                            return (
                                <div
                                    key={`hunk-wrapper-${fileIndex}-${hunkIndex}-${elementId}`}
                                    className="hunk-scroll-container"
                                    style={{
                                        overflowX: 'auto',
                                        marginBottom: '1em',
                                        border: status ?
                                            `1px solid ${isApplied ? (isAlreadyApplied ? '#faad14' : '#52c41a') : '#ff4d4f'}` :
                                            '1px dashed rgba(128,128,128,0.3)'
                                    }}
                                >
                                    {/* Add a hidden ellipsis for the first hunk to ensure status has a place to go */}
                                    {hunkIndex === 0 && (
                                        <div id={`hunk-${fileIndex}-${hunkIndex}`} data-diff-id={elementId} className="diff-ellipsis" style={{ display: 'none' }}></div>
                                    )}

                                    {/* Add a status-based styling row if status is available */}
                                    {status && (
                                        <div className="hunk-status-header" style={{
                                            padding: 0,
                                            borderLeft: `3px solid ${isApplied ?
                                                (isAlreadyApplied ? '#faad14' : '#52c41a') :
                                                '#ff4d4f'}`,
                                            backgroundColor: isApplied ?
                                                (isAlreadyApplied ? 'rgba(250, 173, 20, 0.05)' : 'rgba(82, 196, 26, 0.05)') :
                                                'rgba(255, 77, 79, 0.05)'
                                        }}></div>
                                    )}

                                    {/* Show ellipsis between hunks with proper jump anchor */}
                                    {showEllipsis && (
                                        <div
                                            id={`hunk-${fileIndex}-${hunkIndex}`}
                                            data-diff-id={elementId}
                                            className="diff-ellipsis"
                                            style={{
                                                padding: '4px 8px',
                                                color: isDarkMode ? '#8b949e' : '#57606a',
                                                backgroundColor: isDarkMode ? '#161b22' : '#f6f8fa',
                                                borderBottom: '1px solid ' + (isDarkMode ? '#30363d' : '#d8dee4'),
                                                fontSize: '12px',
                                                display: 'flex',
                                                justifyContent: 'space-between',
                                                alignItems: 'center'
                                            }}
                                        >
                                            <span>{ellipsisText}</span>
                                            {hunkStatusIndicator}
                                        </div>
                                    )}

                                    {/* Add jump anchor for first hunk or when no ellipsis */}
                                    {!showEllipsis && (
                                        <div
                                            id={`hunk-${fileIndex}-${hunkIndex}`}
                                            data-diff-id={elementId}
                                            style={{ display: 'none' }}
                                        ></div>
                                    )}

                                    <table className="diff-table diff-table-hunk diff-table-unified-hunk">
                                        <colgroup>
                                            {showLineNumbers && <col className="diff-gutter-col" style={{ width: '50px', minWidth: '50px' }} />}
                                            <col style={{ width: 'auto' }} />
                                        </colgroup>
                                        <tbody>
                                            {renderContent(hunk, file.newPath || file.oldPath, status, fileIndex, hunkIndex)}
                                        </tbody>
                                    </table>
                                </div>
                            );
                        })}

                        {viewType === 'split' && (
                            // Split view still uses the original renderHunks which renders a single table for the file
                            // as its TDs handle their own scrolling.
                            <table className={tableClassName}>
                                {/* ... colgroup for split view ... */}
                                <tbody>
                                    {renderHunks( // renderHunks for split view will iterate through hunks and create TRs
                                        file.hunks,
                                        file.newPath || file.oldPath,
                                        fileIndex
                                    )}
                                </tbody>
                            </table>
                        )}
                    </div>
                </div>
            </div>
        );
    }

    const currentTheme = isDarkMode ? darkModeStyles : lightModeStyles;
    return (
        <div className="diff-files-container">
            <style key={`diff-styles-${diffId}`}>{styles}</style>
            {parsedFilesRef.current.map((file, fileIndex) =>
                renderFile(file, fileIndex)
            )}
        </div>
    );
};

/**
 * Check if a diff is complete and ready for application
 */
const isDiffComplete = (diffContent: string, isStreaming: boolean): boolean => {
    if (!diffContent || !diffContent.trim()) return false;

    // If not streaming, assume diff is complete
    if (!isStreaming) return true;

    // For streaming diffs, check if they have the essential structure
    const lines = diffContent.split('\n');
    const hasGitHeader = lines.some(line => line.startsWith('diff --git'));
    const hasFileHeaders = lines.some(line => line.startsWith('---')) &&
        lines.some(line => line.startsWith('+++'));
    const hasHunkHeader = lines.some(line => line.match(/^@@\s+-\d+/));
    const hasContent = lines.some(line => line.match(/^[+-\s]/));

    // Check if the diff ends properly (not cut off mid-hunk)
    const lastNonEmptyLine = lines.filter(line => line.trim()).pop() || '';
    const endsAbruptly = lastNonEmptyLine.startsWith('@@') ||
        lastNonEmptyLine.match(/^[+-]/) &&
        !lines.slice(-3).some(line => line.trim() === '');

    // A complete diff should have header structure and not end abruptly
    const hasMinimalStructure = hasGitHeader && hasFileHeaders && hasHunkHeader && hasContent;
    const isStructurallyComplete = hasMinimalStructure && !endsAbruptly;

    return isStructurallyComplete;
};

const ApplyChangesButton: React.FC<ApplyChangesButtonProps> = ({ diff, filePath, fileIndex, diffElementId, enabled, isStreaming = false, setHunkStatuses }) => {
    const [isApplied, setIsApplied] = useState(false);
    const [isProcessing, setIsProcessing] = useState(false);
    const [instanceHunkStatusMap, setInstanceHunkStatusMap] = useState<Map<string, HunkStatus>>(new Map());
    const statusUpdateCounterRef = useRef<number>(0);
    const isStreamingRef = useRef<boolean>(false);
    const appliedRef = useRef<boolean>(false);
    const buttonInstanceId = useRef(`button-${diffElementId}-${Date.now()}`).current;

    // Track processed request IDs to prevent infinite update loops
    const processedRequestIds = useRef(new Set<string>());

    // Check if the diff is complete and ready for application
    const diffComplete = useMemo(() => {
        return isDiffComplete(diff, isStreaming);
    }, [diff, isStreaming]);

    const shouldDisableButton = isApplied || isProcessing || (isStreaming && !diffComplete);
    const buttonId = useId();
    // Define a function to trigger diff updates
    const triggerDiffUpdate = (hunkStatuses: Record<string, any> | null = null, requestId: string | null = null, diffElementId: string | null = null) => {

        // Also dispatch a window event for backward compatibility
        console.log(`Triggering diff update event with statuses for request ${requestId}:`, hunkStatuses);

        // Prevent duplicate updates for the same request ID
        if (requestId && processedRequestIds.current.has(requestId)) {
            console.log(`Skipping duplicate update for already processed request: ${requestId}`);
            return;
        }
        if (requestId) processedRequestIds.current.add(requestId);

        const customEvent = new CustomEvent('hunkStatusUpdate', {
            detail: {
                requestId,
                hunkStatuses,
                filePath,
                isCompletionEvent: true, // Flag to indicate this is a completion event
                targetDiffElementId: diffElementId // Add the target diff element ID
            }
        });
        window.dispatchEvent(customEvent);
    };

    // Check if we're in a streaming state
    useEffect(() => {
        const checkStreamingState = () => {
            const streamingElements = document.querySelectorAll('.streaming-content');
            isStreamingRef.current = streamingElements.length > 0;
        };

        // Check immediately
        checkStreamingState();

        // Set up a mutation observer to detect streaming state changes
        const observer = new MutationObserver(checkStreamingState);
        observer.observe(document.body, { childList: true, subtree: true });

        return () => {
            observer.disconnect();
        };
    }, []);

    const handleApplyChanges = async () => {
        if (appliedRef.current) return;
        appliedRef.current = true;

        // Use our stable request ID for this specific diff application
        const requestId = `${Date.now()}`;

        // Extract the actual diff content
        setIsProcessing(true);
        const cleanDiff = (() => {
            console.log('Pre-fetch diff content for file:', filePath);
            // Log the incoming diff content
            console.debug('Raw diff content:', {
                processing: true,
                elementId: diffElementId,
                length: diff.length,
                firstLine: diff.split('\n')[0],
                totalLines: diff.split('\n').length,
                fullContent: diff
            });

            // Store the file path for this diff element ID for later matching
            if (diffElementId) {
                window.diffElementPaths = window.diffElementPaths || new Map();
                window.diffElementPaths.set(diffElementId, filePath);
            }

            // If it's already a raw diff, extract only the relevant file's diff if multipart
            if (diff.startsWith('diff --git')) {
                const singleFileDiff = extractSingleFileDiff(diff, filePath);
                console.debug('Extracted single file diff:', {
                    filePath,
                    diffLength: singleFileDiff.length
                });
                return singleFileDiff.trim();
            }

            // Otherwise extract diff from markdown code block
            const diffMatch = diff.match(/```diff\n([\s\S]*?)```(?:\s|$)/);
            console.log('Diff match result:', {
                found: Boolean(diffMatch),
                groups: diffMatch ? diffMatch.length : 0,

                matchContent: diffMatch ? {
                    fullMatch: diffMatch[0],
                    diffContent: diffMatch[1],
                } : null
            });
            if (diffMatch) {
                return diffMatch[1].trim();
            }

            // If we extracted a diff from markdown and it's a multi-file diff,
            // extract only the relevant file's diff
            if (diffMatch) {
                const diffContent = diffMatch[1] as string;
                if (diffContent.includes('diff --git') && diffContent.indexOf('diff --git') !== diffContent.lastIndexOf('diff --git')) {
                    const extractedDiff = diffContent.trim();
                    const singleFileDiff = extractSingleFileDiff(extractedDiff, filePath);
                    return singleFileDiff;
                }
            }

            // Fallback to original content
            return diff.trim();
        })();

        // Log the processed diff content
        console.log(`Processed diff content for ${diffElementId}:`, {
            length: cleanDiff.length,
            lines: cleanDiff.split('\n').length,
            firstLine: cleanDiff.split('\n')[0],
            lastLine: cleanDiff.split('\n').slice(-1)[0],
            fullContent: cleanDiff,
            truncated: cleanDiff.length < diff.length
        });

        // Log the actual request body
        const requestBody = JSON.stringify({
            diff: cleanDiff,
            filePath: filePath.trim(),
            requestId: requestId, elementId: diffElementId // Use the full, unique diffElementId
        });
        console.log('Request body:', requestBody);


        console.log(`Applying changes for diff ${diffElementId} with request ID ${requestId}, button instance: ${buttonInstanceId}`);
        const requestBodyParsed = JSON.parse(requestBody);
        console.log('Parsed request body diff length:', requestBodyParsed.diff.split('\n').length);

        try {
            console.log('About to send fetch request with body length:', cleanDiff.length);
            console.log('Request body:', {
                diff: cleanDiff.substring(0, 100) + '...',
                filePath: filePath.trim(),
                requestId: requestId, elementId: diffElementId, buttonInstanceId
            });

            const response = await fetch('/api/apply-changes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    diff: cleanDiff,
                    elementId: diffElementId, // Use the original element ID consistently
                    filePath: filePath.trim(),
                    buttonInstanceId, // Add the button instance ID for more precise matching
                    requestId: requestId
                }),
            });

            // Log the actual sent data
            const sentData = await response.clone().json();
            console.log('Actually sent to server:', sentData);
            console.log('Apply changes response:', {
                status: response.status,
                statusText: response.statusText,
                headers: Object.fromEntries([...response.headers.entries()]),
                ok: response.ok
            });

            if (response.ok || response.status === 207) {
                const data = await response.json();
                console.log('Apply changes response data:', data);
                console.log('Response data structure:', {
                    rawData: JSON.stringify(data),
                    status: data.status,
                    message: data.message || 'No message provided',
                    requestId: data.request_id,
                    hasRequestId: !!data.request_id,
                    diffElementId: diffElementId,
                    mappingAdded: data.request_id ? diffRequestMap.set(data.request_id, diffElementId) : false,
                    buttonInstanceId,
                    hasDetails: !!data.details || !!data.hunk_statuses,
                    detailsKeys: data.details ? Object.keys(data.details) : [],
                    succeeded: data.details?.succeeded,
                    failed: data.details?.failed,
                    hunkStatuses: data.details?.hunk_statuses
                });

                // Store the mapping between request ID and diff element ID
                if (data.request_id) {
                    diffRequestMap.set(data.request_id, diffElementId);
                    console.log(`Mapped request ${data.request_id} to diff element ${diffElementId} (button ${buttonInstanceId})`);
                }

                // Check if ANY hunks succeeded before marking as applied
                const hasSuccessfulHunks = data.details?.succeeded?.length > 0;
                console.log('Has successful hunks:', hasSuccessfulHunks);
                console.log('Succeeded hunks:', data.details?.succeeded || []);

                if (data.status === 'success') {
                    console.log('Processing success status');
                    setIsApplied(true);  // Complete success
                    // Update hunk statuses for successful application
                    // Check if we have detailed hunk statuses in the response
                    if (data.details?.hunk_statuses) {
                        const hunkStatuses = data.hunk_statuses || data.details?.hunk_statuses || {};
                        const files = parseDiff(cleanDiff);
                        files.forEach((file, fileIndex) => {
                            file.hunks.forEach((hunk, hunkIndex) => {
                                const hunkKey = `${fileIndex}-${hunkIndex}`;
                                // The hunk IDs in the response are 1-based, but our hunkIndex is 0-based
                                console.log(`Processing hunk status for request ${data.request_id}, hunk #${hunkIndex + 1}`);
                                const hunkId = hunkIndex + 1;
                                const hunkStatus = hunkStatuses[hunkId];

                                if (hunkStatus) {
                                    console.log(`Setting status for hunk #${hunkId} with key ${hunkKey}:`, hunkStatus);
                                    if (typeof setHunkStatuses === 'function') {
                                        console.log('Calling setHunkStatuses function');

                                        setHunkStatuses((prev: Map<string, HunkStatus>) => {
                                            const newMap = new Map(prev);
                                            newMap.set(hunkKey, {
                                                applied: hunkStatus.status === 'succeeded' || hunkStatus.status === 'already_applied',
                                                alreadyApplied: hunkStatus.status === 'already_applied',
                                                reason: hunkStatus.status === 'succeeded' ?
                                                    'Successfully applied' :
                                                    hunkStatus.status === 'already_applied' ? 'Already applied' : 'Failed'
                                            } as HunkStatus);

                                            // Also update the global registry
                                            if (window.hunkStatusRegistry) {
                                                if (!window.hunkStatusRegistry.has(diffElementId)) {
                                                    window.hunkStatusRegistry.set(diffElementId, new Map());
                                                }
                                                const registryMap = window.hunkStatusRegistry.get(diffElementId)!;
                                                registryMap.set(hunkKey, newMap.get(hunkKey) as HunkStatus);
                                            }
                                            return newMap;
                                        });
                                        // Default to success if not found in hunk_statuses

                                        const isAlreadyApplied = hunkStatus.status === 'already_applied';

                                        if (setHunkStatuses) {
                                            setHunkStatuses((prev: Map<string, HunkStatus>) => {
                                                const newMap = new Map(prev);
                                                newMap.set(hunkKey, {
                                                    applied: true,
                                                    reason: isAlreadyApplied ? 'Already applied' : 'Successfully applied',
                                                    alreadyApplied: isAlreadyApplied
                                                } as HunkStatus);

                                                // Also update the global registry
                                                if (window.hunkStatusRegistry) {
                                                    if (!window.hunkStatusRegistry.has(diffElementId)) {
                                                        window.hunkStatusRegistry.set(diffElementId, new Map());
                                                    }
                                                    const registryMap = window.hunkStatusRegistry.get(diffElementId)!;
                                                    registryMap.set(hunkKey, { applied: true, reason: isAlreadyApplied ? 'Already applied' : 'Successfully applied', alreadyApplied: isAlreadyApplied });
                                                }
                                                return newMap;
                                            });
                                            // Force a re-render by updating the status update counter
                                            statusUpdateCounterRef.current += 1;
                                        }
                                    }
                                }
                            });
                        });
                    } else {
                        console.log('Setting success status for all hunks (no detailed statuses)');
                        const files = parseDiff(cleanDiff);
                        files.forEach((file, fileIndex) => {
                            console.log(`Setting status for file hunks (${diffElementId}):`, file.hunks.length);
                            file.hunks.forEach((hunk, hunkIndex) => {
                                const hunkKey = `${fileIndex}-${hunkIndex}`;
                                setInstanceHunkStatusMap(prev => {
                                    const newMap = new Map(prev);
                                    newMap.set(hunkKey, {
                                        applied: true,
                                        reason: 'Successfully applied',
                                        alreadyApplied: false
                                    });
                                    return newMap;
                                });
                            });
                        });
                    }
                    triggerDiffUpdate(data.details?.hunk_statuses || {}, data.request_id, diffElementId);

                    message.success(`Changes applied successfully to ${filePath}`);
                } else if (data.status === 'partial') {
                    console.log('Processing partial status');
                    // Only mark as applied if at least one hunk succeeded
                    setIsApplied(hasSuccessfulHunks);
                    console.log('Setting isApplied to:', hasSuccessfulHunks);

                    // Handle the new format with hunk_statuses
                    parseDiff(cleanDiff).forEach((file, fileIndex) => {
                        file.hunks.forEach((hunk, hunkIndex) => {
                            console.log(`Processing hunk #${hunkIndex + 1} status`);
                            // Create a stable key for this hunk
                            const hunkKey = `${fileIndex}-${hunkIndex}`;
                            // Get the hunk status from the response
                            // The hunk IDs in the response are 1-based, but our hunkIndex is 0-based
                            const hunkId = hunkIndex + 1;
                            const hunkStatus = data.details?.hunk_statuses?.[hunkId];

                            if (hunkStatus) {
                                console.log(`Setting status for hunk #${hunkId} with key ${hunkKey}:`, hunkStatus);
                                instanceHunkStatusMap.set(hunkKey, {
                                    applied: hunkStatus.status === 'succeeded' || hunkStatus.status === 'already_applied',
                                    alreadyApplied: hunkStatus.status === 'already_applied',
                                    reason: hunkStatus.status === 'failed'
                                        ? 'Failed in ' + hunkStatus.stage + ' stage'
                                        : 'Successfully applied'
                                });
                            } else {
                                // Fallback if we can't find the specific hunk status
                                // Check if this hunk ID is in the failed list from the API response
                                const isInSucceededList = data.details?.succeeded?.includes(hunkId);
                                const isAlreadyAppliedList = data.details?.already_applied?.includes(hunkId);
                                const isInFailedList = data.details?.failed?.includes(hunkId);

                                // Log this for debugging
                                console.log(`Fallback status for hunk #${hunkId}: success=${isInSucceededList}, already=${isAlreadyAppliedList}, failed=${isInFailedList}`);

                                instanceHunkStatusMap.set(hunkKey, {
                                    applied: isInSucceededList || isAlreadyAppliedList,
                                    alreadyApplied: isAlreadyAppliedList,
                                    reason: isInFailedList ? 'Failed to apply' : 'Successfully applied'
                                });
                            }
                            // Force a re-render to update the UI
                            statusUpdateCounterRef.current++;
                            return;
                        });
                    });
                    console.log('Hunk statuses updated, triggering update');
                    triggerDiffUpdate(data.details?.hunk_statuses || {}, data.request_id, diffElementId);

                    // Show partial success message with failed hunks
                    message.warning({
                        content: (
                            <div>
                                <p>{data.message}</p>
                                {data.details?.failed && data.details.failed.length > 0 && (
                                    <div>
                                        <p>Failed hunks:</p>
                                        <ul style={{ marginTop: '8px', paddingLeft: '20px', listStyle: 'none' }}>
                                            {data.details.failed.map((hunkId, index) => {
                                                const hunkStatus = data.details?.hunk_statuses?.[hunkId];
                                                return (
                                                    <li key={index}>
                                                        <CloseCircleOutlined style={{ color: '#ff4d4f', marginRight: '8px' }} />
                                                        {`Hunk #${hunkId} failed`}
                                                        {hunkStatus ? ` in ${hunkStatus.stage || 'unknown'} stage` : ''}
                                                        {hunkStatus?.error_details ? `: ${JSON.stringify(hunkStatus.error_details)}` : ''}
                                                    </li>
                                                );
                                            })}
                                        </ul>
                                    </div>
                                )}
                            </div>
                        ),
                        duration: 10  // Show for 10 seconds since there's more to read
                    });
                } else if (data.status === 'error') {
                    console.log('Processing error status');
                    // Handle error status (all hunks failed)
                    setIsApplied(false);
                    console.log('Setting isApplied to false due to error status');

                    // Mark all hunks as failed
                    parseDiff(cleanDiff).forEach((file, fileIndex) => {
                        file.hunks.forEach((hunk, hunkIndex) => {
                            console.log(`Setting failed status for hunk #${hunkIndex + 1}`);
                            const hunkKey = `${fileIndex}-${hunkIndex}`;
                            const hunkId = hunkIndex + 1;
                            const hunkStatus = data.details?.hunk_statuses?.[hunkId];

                            instanceHunkStatusMap.set(hunkKey, {
                                applied: false,
                                reason: hunkStatus?.stage
                                    ? 'Failed in ' + (hunkStatus.stage || 'unknown') + ' stage'
                                    : 'Failed to apply'
                            });
                        });
                    });
                    console.log('Failed statuses set, triggering update for error');
                    triggerDiffUpdate(data.details?.hunk_statuses || null, data.request_id, diffElementId);

                    // Show error message
                    message.error({
                        content: (
                            <div>
                                <p>
                                    <CloseCircleOutlined style={{ color: '#ff4d4f', marginRight: '8px' }} />
                                    {data.message || 'All hunks failed to apply'}
                                </p>
                                {data.details?.failed && data.details.failed.length > 0 && (
                                    <div>
                                        <p>Failed hunks:</p>
                                        <ul style={{ marginTop: '8px', paddingLeft: '20px', listStyle: 'none' }}>
                                            {data.details.failed.map((hunkId, index) => {
                                                const hunkStatus = data.details?.hunk_statuses?.[hunkId];
                                                return (
                                                    <li key={index}>
                                                        <CloseCircleOutlined style={{ color: '#ff4d4f', marginRight: '8px' }} />
                                                        {`Hunk #${hunkId} failed`}
                                                        {hunkStatus ? ` in ${hunkStatus.stage || 'unknown'} stage` : ''}

                                                        {/* Update the hunk status in our map to ensure UI is consistent with message */}
                                                        {(() => { instanceHunkStatusMap.set(`0-${hunkId - 1}`, { applied: false, reason: hunkStatus?.error_details?.error || 'Failed to apply' }); return null; })()}

                                                        {hunkStatus?.error_details ? `: ${JSON.stringify(hunkStatus.error_details)}` : ''}
                                                    </li>
                                                );
                                            })}
                                        </ul>
                                    </div>
                                )}
                                triggerDiffUpdate({}, data.request_id);
                            </div>
                        ),
                        duration: 10
                    });
                } else {
                    console.log('Unknown status:', data.status);
                    message.warning(`Unknown status: ${data.status}`);
                }
            } else {
                try {
                    // Parse the error response
                    const errorData = await response.json().catch(() => ({}));

                    // Mark all hunks as failed when we get a global error
                    if (response.status === 422 || errorData.status === 'error') {
                        // Parse the diff to get the number of hunks
                        try {
                            const files = parseDiff(cleanDiff);
                            files.forEach((file, fileIndex) => {
                                file.hunks.forEach((hunk, hunkIndex) => {
                                    const hunkKey = `${fileIndex}-${hunkIndex}`;
                                    const errorMessage = errorData.detail?.message || errorData.message || errorData.detail || 'Failed to apply changes';

                                    // Update the hunk status to failed
                                    if (typeof setHunkStatuses === 'function') {
                                        setHunkStatuses((prev: Map<string, HunkStatus>) => {
                                            const newMap = new Map(prev);
                                            newMap.set(hunkKey, {
                                                applied: false,
                                                reason: `Error: ${errorMessage}`
                                            });
                                            return newMap;
                                        });
                                    }
                                });
                            });
                        } catch (parseError) {
                            console.error('Error parsing diff for error propagation:', parseError);
                        }
                    }

                    console.log('Apply changes error response:', {
                        status: response.status,
                        errorData,
                        errorDataKeys: Object.keys(errorData),
                        detail: errorData.detail,
                        detailType: typeof errorData.detail,
                        message: errorData.message || errorData.detail?.message,
                        hasDetails: !!errorData.details,
                        detailsKeys: errorData.details ? Object.keys(errorData.details) : [],
                    });

                    // Check if the error response contains a status field
                    if (errorData.detail && errorData.detail.status === 'error') {
                        console.log('Processing error status from error response');
                        setIsApplied(false);

                        // Mark all hunks as failed
                        parseDiff(cleanDiff).forEach((file, fileIndex) => {
                            file.hunks.forEach((hunk, hunkIndex) => {
                                const hunkKey = `${fileIndex}-${hunkIndex}`;
                                console.log(`Setting error status for hunk #${hunkIndex + 1}`);
                                instanceHunkStatusMap.set(hunkKey, {
                                    applied: false,
                                    reason: 'Failed to apply'
                                });
                            });
                        });
                        triggerDiffUpdate(null, errorData.request_id, diffElementId);
                        console.log('Error statuses set, triggering update');
                    }

                    message.error({
                        content: (
                            <div>
                                <p>
                                    <CloseCircleOutlined style={{ color: '#ff4d4f', marginRight: '8px' }} />
                                    {errorData.detail?.message || errorData.message || errorData.detail || 'Failed to apply changes'}
                                </p>
                                {errorData.detail?.summary && <p>{errorData.detail.summary}</p>}
                                {errorData.details?.failed && errorData.details.failed.length > 0 && (
                                    <div>
                                        <p>Failed hunks:</p>
                                        <ul style={{ marginTop: '8px', paddingLeft: '20px', listStyle: 'none' }}>
                                            {errorData.details.failed.map((hunkId, index) => {
                                                const hunkStatus = errorData.details?.hunk_statuses?.[hunkId];
                                                return (
                                                    <li key={index}>
                                                        <CloseCircleOutlined style={{ color: '#ff4d4f', marginRight: '8px' }} />
                                                        {`Hunk #${hunkId} failed`}
                                                        {hunkStatus ? ` in ${hunkStatus.stage || 'unknown'} stage` : ''}
                                                        {hunkStatus?.error_details ? `: ${JSON.stringify(hunkStatus.error_details)}` : ''}
                                                    </li>
                                                );
                                            })}
                                        </ul>
                                    </div>
                                )}
                            </div>
                        ),
                        duration: 5
                    });
                } catch (parseError) {
                    console.error('Error parsing error response:', parseError);
                    message.error('Failed to apply changes');
                }
            }
        } catch (error: unknown) {
            console.error('Error applying changes:', error);
            console.error('Error type:', typeof error);
            console.error('Error properties:', Object.keys(error as object));
            message.error({
                content: 'Error applying changes: ' + (error instanceof Error ? error.message : String(error)),
                key: 'apply-changes-error',
                duration: 5
            });
        } finally {
            setIsProcessing(false);
        }
    };

    // Listen for hunk status updates from the server
    useEffect(() => {
        const handleHunkStatusUpdate = (e: CustomEvent) => {
            if (!e.detail) return;

            const eventButtonInstanceId = e.detail.buttonInstanceId;


            // Only process events targeted at this specific diff element
            if (e.detail.targetDiffElementId && e.detail.targetDiffElementId !== diffElementId) {
                return; // Skip events not meant for this instance
            }
            if (!e.detail) return;

            // Set a timeout to remove this event from the processed set after a short delay
            const eventKey = `${e.detail.requestId || ''}-${e.detail.targetDiffElementId || ''}`;
            setTimeout(() => processedWindowEvents.delete(eventKey), 500);

            const targetDiffElementIdFromMap = diffRequestMap.get(e.detail.requestId)?.replace(/^diff-/, 'diff-view-');
            let isRelevantUpdate = false;

            // Create a unique key for this event to prevent duplicate processing
            // Skip if we've already processed this exact event
            if (processedWindowEvents.has(eventKey)) {
                console.debug(`Skipping already processed window event: ${eventKey}`);
                return;
            }

            // Log the event and our identifiers for debugging
            console.log(`Checking if update matches our element: target=${e.detail.targetDiffElementId}, ours=${diffElementId}, buttonId=${buttonInstanceId}`);

            // Only accept exact matches for our element ID
            if ((e.detail.targetDiffElementId === diffElementId) &&
                (!e.detail.filePath || e.detail.filePath === filePath)) {
                isRelevantUpdate = true;
            }

            // Otherwise check if the request ID maps to our element ID via the map
            else if (targetDiffElementIdFromMap === diffElementId && (!e.detail.filePath || e.detail.filePath === filePath)) {
                isRelevantUpdate = true;
            }

            // Check if the button instance ID matches
            if (eventButtonInstanceId === buttonInstanceId) {
                isRelevantUpdate = true;
            }

            // Log the matching attempt - this helps us debug
            console.log(`ApplyChangesButton ${diffElementId}: Matching update. Event target: ${e.detail.targetDiffElementId}, Mapped target: ${targetDiffElementIdFromMap}, Button: ${eventButtonInstanceId}/${buttonInstanceId}, Match: ${isRelevantUpdate}`);


            if (!isRelevantUpdate) {
                // This update is for a different diff element or file, ignore it
                console.log(`Ignoring update for ${e.detail.targetDiffElementId || 'unknown'} (we are ${diffElementId})`);
                return; // Exit early if not relevant
            }

            // Mark this event as processed
            isRelevantUpdate = true;

            console.log(`Received hunk status update for diff ${diffElementId} (request ${e.detail.requestId}, button ${eventButtonInstanceId || 'unknown'}, isRelevant=${isRelevantUpdate}):`, e.detail.hunkStatuses);

            // Process and update the status for each hunk
            if (e.detail.hunkStatuses && isRelevantUpdate) {
                Object.entries(e.detail.hunkStatuses).forEach(([hunkId, status]) => {
                    const hunkIndex = parseInt(hunkId, 10) - 1; // Convert 1-based to 0-based 
                    const hunkKey = `${fileIndex}-${hunkIndex}`;
                    if (typeof setHunkStatuses === 'function') {
                        setHunkStatuses((prev: Map<string, HunkStatus>) => {
                            const newMap = new Map(prev);
                            newMap.set(hunkKey, {
                                applied: (status as ApiHunkStatus).status === 'succeeded' || (status as ApiHunkStatus).status === 'already_applied',
                                alreadyApplied: (status as ApiHunkStatus).status === 'already_applied',
                                reason: (status as ApiHunkStatus).status === 'failed' ? 'Failed in ' + ((status as ApiHunkStatus).stage || 'unknown') + ' stage' : 'Successfully applied'
                            } as HunkStatus);
                            return newMap;
                        });
                    }
                    // Force a re-render to update the UI
                    statusUpdateCounterRef.current += 1;
                });
            }
        }
        window.addEventListener('hunkStatusUpdate', handleHunkStatusUpdate as EventListener);
        return () => window.removeEventListener('hunkStatusUpdate', handleHunkStatusUpdate as EventListener);
    }, [diffElementId, filePath, setHunkStatuses, fileIndex]);

    // Clear processed request IDs when component unmounts
    useEffect(() => {
        return () => processedRequestIds.current.clear();
    }, []);

    return enabled ? (
        <Button
            onClick={handleApplyChanges}
            disabled={shouldDisableButton}
            loading={isProcessing}
            type={isApplied ? "default" : "primary"}
            style={{ marginLeft: '8px' }} id={`apply-changes-${buttonId}`}
            icon={<CheckOutlined />}
        >
            Apply Changes
        </Button>
    ) : null;
};

const hasText = (token: any): token is TokenWithText => {
    return 'text' in token;
};

const isCodeToken = (token: TokenWithText): token is TokenWithText & { lang?: string } => {
    return token.type === 'code' && 'text' in token;
};

interface DiffTokenProps {
    token: TokenWithText;
    index: number;
    enableCodeApply: boolean;
    isDarkMode: boolean;
    isStreaming?: boolean;
}

const DiffToken = memo(({ token, index, enableCodeApply, isDarkMode }: DiffTokenProps): JSX.Element => {
    const { isStreaming, streamingConversations, currentConversationId,
        currentMessages, addMessageToConversation, setStreamedContentMap,
        removeStreamingConversation, setIsStreaming } = useChatContext();
    const { checkedKeys, addFilesToContext } = useFolderContext();
    // Generate a unique ID once when the component mounts
    const [diffId] = useState(() =>
        `diff-${Math.random().toString(36).substring(2, 9)}-${Date.now()}`);
    const contentRef = useRef<string | null>(null);
    const [isCheckingFiles, setIsCheckingFiles] = useState(false);
    const hasCheckedFilesRef = useRef(false);
    const checkTimeoutRef = useRef<NodeJS.Timeout>();
    const [needsContextEnhancement, setNeedsContextEnhancement] = useState(false);
    const [missingFilesList, setMissingFilesList] = useState<string[]>([]);
    const lastTokenLengthRef = useRef(0);
    const hasCheckedAfterStreamingRef = useRef(false);

    // Check for missing files after streaming completes if we haven't checked yet
    useEffect(() => {
        if (!streamingConversations.has(currentConversationId) &&
            !hasCheckedAfterStreamingRef.current &&
            !hasCheckedFilesRef.current &&
            token.text.includes('diff --git')) {

            hasCheckedAfterStreamingRef.current = true;

            const checkAfterStreaming = async () => {
                const referencedFiles = extractAllFilesFromDiff(token.text);
                if (referencedFiles.length > 0) {
                    const currentFiles = Array.from(checkedKeys).map(String);
                    const response = checkFilesInContext(referencedFiles, currentFiles);
                    if (response.missingFiles.length > 0) {
                        await addFilesToContext(response.missingFiles);
                        setMissingFilesList(response.missingFiles);
                        setNeedsContextEnhancement(true);
                    }
                }
            };

            checkAfterStreaming();
        }
    }, [streamingConversations, currentConversationId, token.text, addFilesToContext]);

    // Debounced check function
    const debouncedCheck = useCallback((checkFn: () => Promise<void>) => {
        if (checkTimeoutRef.current) clearTimeout(checkTimeoutRef.current);
        checkTimeoutRef.current = setTimeout(checkFn, 500); // Wait 500ms for diff to stabilize
    }, []);

    // Check if referenced files are in context when diff is rendered during streaming
    useEffect(() => {
        const checkMissingFiles = async () => {
            if (!token.text || hasCheckedFilesRef.current || isCheckingFiles) return;

            // Check streaming state more comprehensively
            const isCurrentlyStreaming = streamingConversations.has(currentConversationId);

            // Only log when we actually find something interesting
            if (!isCurrentlyStreaming) return;

            // Only check during active streaming to avoid interrupting completed responses
            if (!isCurrentlyStreaming) {
                return;
            }

            const referencedFiles = extractAllFilesFromDiff(token.text);
            if (referencedFiles.length === 0) return;

            // Mark as checked BEFORE doing the work to prevent race conditions
            hasCheckedFilesRef.current = true;
            setIsCheckingFiles(true);

            try {
                const currentFiles = Array.from(checkedKeys).map(String);
                const response = checkFilesInContext(referencedFiles, currentFiles);
                const { missingFiles } = response;

                if (missingFiles.length > 0) {

                    // Add files to context using the proper context method
                    await addFilesToContext(missingFiles);

                    // Instead of interrupting, show enhancement overlay
                    setMissingFilesList(missingFiles);
                    setNeedsContextEnhancement(true);
                } else {
                    console.log('🔄 CONTEXT_ENHANCEMENT: No missing files found, all referenced files are already in context');
                }
            } catch (error) {
                console.error('Error checking missing files:', error);
            } finally {
                setIsCheckingFiles(false);
            }
        };

        // Only trigger if token content has significantly grown (to avoid spam)
        const currentLength = token.text.length;
        const lengthGrowth = currentLength - lastTokenLengthRef.current;
        lastTokenLengthRef.current = currentLength;

        // Only check if content has grown significantly or if streaming completed
        if (lengthGrowth > 200 || !streamingConversations.has(currentConversationId)) {
            if (streamingConversations.has(currentConversationId)) {
                debouncedCheck(checkMissingFiles);
            } else if (!hasCheckedFilesRef.current) {
                checkMissingFiles();
            }
        }
    }, [token.text.length, currentConversationId, streamingConversations, isCheckingFiles]);

    // Restart stream with enhanced context
    const restartStreamWithFiles = async (addedFiles: string[]) => {
        try {
            // First, explicitly abort the current stream
            document.dispatchEvent(new CustomEvent('abortStream', {
                detail: { conversationId: currentConversationId }
            }));

            // Wait a moment for the stream to be aborted
            await new Promise(resolve => setTimeout(resolve, 200));

            // Use the imported function
            const allCurrentFiles = Array.from(checkedKeys).map(String);
            await restartStreamWithEnhancedContext(currentConversationId, addedFiles, allCurrentFiles);

            // Show subtle notification
            message.info({
                content: `Adding missing files to context: ${addedFiles.join(', ')}...`,
                duration: 3,
                key: `context-enhanced-${currentConversationId}`
            });
        } catch (error) {
            console.error('🔄 CONTEXT_ENHANCEMENT: Failed to restart stream:', error);

            // Show fallback message
            message.warning({
                content: `Added files to context: ${addedFiles.join(', ')}. Please retry your request to use the enhanced context.`,
                duration: 8,
                key: `context-enhanced-fallback-${currentConversationId}`
            });

            // Reset the checking state so the diff can render normally
            setIsCheckingFiles(false);
        }
    };

    // Reset check flag when conversation changes
    useEffect(() => {
        hasCheckedFilesRef.current = false;
        hasCheckedAfterStreamingRef.current = false;
        setNeedsContextEnhancement(false);
        setMissingFilesList([]);
        // Clear any pending timeouts
        if (checkTimeoutRef.current) {
            clearTimeout(checkTimeoutRef.current);
        }
        return () => {
            if (checkTimeoutRef.current) clearTimeout(checkTimeoutRef.current);
        };
    }, [currentConversationId]);

    // Clean up any MATH_INLINE expansions that might have slipped through
    const cleanedText = useMemo(() => {
        if (!token.text) return '';
        // Replace any MATH_INLINE expansions with their original form
        return token.text.replace(/⟨MATH_INLINE:(\d+)⟩/g, '$$1');
    }, [token.text]);

    // Store the content in a ref to avoid re-renders
    useEffect(() => {
        if (!contentRef.current || contentRef.current !== cleanedText) {
            contentRef.current = cleanedText;
        }
    }, [currentConversationId]);

    // Function to add files to context
    const addMissingFilesToContext = async () => {
        setNeedsContextEnhancement(false);
        try {
            const allCurrentFiles = Array.from(checkedKeys).map(String);
            await restartStreamWithEnhancedContext(currentConversationId, missingFilesList, allCurrentFiles);

            message.success({
                content: `Added missing files to context: ${missingFilesList.join(', ')}`,
                duration: 3,
                key: `context-enhanced-${currentConversationId}`
            });
        } catch (error) {
            console.error('Error adding files to context:', error);
            message.error('Failed to add files to context. Please try again.');
        }
    };

    // Show context enhancement overlay when files are missing
    const contextEnhancementOverlay = (isCheckingFiles || needsContextEnhancement) ? (
        <div style={{
            position: 'relative', width: '100%',
            backgroundColor: needsContextEnhancement ? 'rgba(255,193,7,0.9)' : 'rgba(0,0,0,0.7)',
            color: needsContextEnhancement ? '#000' : 'white',
            padding: '12px', textAlign: 'center',
            borderRadius: '4px'
        }}>
            {isCheckingFiles ? (
                '🔄 Checking context...'
            ) : needsContextEnhancement ? (
                <div>
                    <div style={{ marginBottom: '8px' }}>
                        ⚠️ This diff references files not in context: <strong>{missingFilesList.join(', ')}</strong>
                    </div>
                    <Button
                        type="primary"
                        size="small"
                        onClick={addMissingFilesToContext}
                        style={{ backgroundColor: '#52c41a', borderColor: '#52c41a' }}
                    >
                        Add Files to Context
                    </Button>
                </div>
            ) : null}
        </div>
    ) : null;


    return (
        <div>
            {contextEnhancementOverlay}
            <DiffViewWrapper
                token={{ ...token, text: cleanedText }}
                index={index}
                elementId={diffId}
                enableCodeApply={enableCodeApply}
                isStreaming={isStreaming}
            />
        </div>
    );
});

interface DiffViewWrapperProps {
    token: TokenWithText;
    enableCodeApply: boolean;
    isStreaming?: boolean;
    forceRender?: boolean;
    index?: number;
    elementId?: string;
}

const DiffViewWrapper = memo(({ token, enableCodeApply, index, elementId }: DiffViewWrapperProps) => {
    const [viewType, setViewType] = useState<'unified' | 'split'>(window.diffViewType || 'unified');
    const [showLineNumbers, setShowLineNumbers] = useState<boolean>(window.diffShowLineNumbers || false);
    const { currentConversationId } = useChatContext(); // Add access to currentConversationId
    const [displayMode, setDisplayMode] = useState<DisplayMode>(window.diffDisplayMode || 'pretty');
    const [isVisible, setIsVisible] = useState<boolean>(true);
    const [currentContent, setCurrentContent] = useState<string>(token.text || '');
    const lastValidDiffRef = useRef<string | null>(null);
    const { isStreaming: isGlobalStreaming } = useChatContext();
    const { isDarkMode } = useTheme();
    const initialFileTitleRef = useRef<string | null>(null);
    const stableElementIdRef = useRef(elementId);
    const isStreamingRef = useRef<boolean>(false);
    const streamingContentRef = useRef<string>(token.text || '');
    const parseTimeoutRef = useRef<number | null>(null);
    // Track component visibility
    const containerRef = useRef<HTMLDivElement>(null);

    // Extract file title early from the diff content, even during streaming
    const extractFileTitle = useCallback((diffContent: string): string => {
        if (!diffContent) return '';
        const lines = diffContent.split('\n');

        // Check for new file creation first - prioritize 'new file mode' over git header paths
        const isNewFile = lines.some(line => line.includes('new file mode')) ||
            lines.some(line => line.startsWith('--- /dev/null'));

        // Check for file deletion
        const isDeletedFile = lines.some(line => line.includes('deleted file mode')) ||
            lines.some(line => line.startsWith('+++ /dev/null'));

        // Look for git diff header  
        for (const line of lines) {
            if (line.startsWith('diff --git')) {
                // Handle both standard format and malformed Gemini format
                const match = line.match(/diff --git (?:a\/)?([^\/]*(?:\/[^\/]*)*) (?:b\/)?(.*)$/);
                if (match) {
                    const oldPath = match[1];
                    const newPath = match[2];

                    // Handle new file creation - check for isNewFile flag first
                    if (isNewFile) {
                        return `Create New File: ${newPath !== '/dev/null' ? newPath : oldPath}`;
                    }
                    // Only use git header /dev/null detection if we don't have 'new file mode' marker
                    if (oldPath === '/dev/null' && !isNewFile) {
                        return `Create New File: ${newPath !== '/dev/null' ? newPath : 'Unknown'}`;
                    }
                    // Handle file deletion (new path is /dev/null)  
                    if (newPath === '/dev/null') {
                        return `Delete File: ${oldPath}`;
                    }
                    // Handle file rename (different paths)
                    if (oldPath !== newPath) {
                        return `Rename: ${oldPath} → ${newPath}`;
                    }
                    // Regular file modification
                    return `Modify: ${newPath || oldPath}`;
                }
            }

            // Also check unified diff headers for new/deleted files
            // Look for unified diff headers
            if (line.startsWith('+++ b/')) {
                const filePath = line.substring(6);
                if (isNewFile && filePath !== '/dev/null') {
                    return `Create New File: ${filePath}`;
                }
                return filePath !== '/dev/null' ? filePath : 'Unknown file';
            }
            // Handle new file creation from unified diff headers
            if (line.startsWith('--- a/') || line.startsWith('--- /dev/null')) {
                const filePath = line.substring(6);

                // Skip /dev/null paths for new files - look for the +++ line instead
                if (filePath === '/dev/null' && isNewFile) {
                    continue; // Keep looking for the actual file path
                }

                // Check if this is a deletion diff
                if (isDeletedFile) {
                    return `Delete File: ${filePath}`;
                }
                return filePath;
            }
        }

        return 'Unknown file';
    }, []);

    useEffect(() => {
        const observer = new IntersectionObserver(([entry]) => {
            setIsVisible(entry.isIntersecting);
        }, { threshold: 0.01, rootMargin: '200px 0px' });

        if (containerRef.current) {
            observer.observe(containerRef.current);
        }
        return () => {
            observer.disconnect();
        };
    }, []);

    // Clear cached file title when conversation changes to prevent sticky /dev/null labels
    useEffect(() => {
        initialFileTitleRef.current = null;
        parsedFilesRef.current = []; // Also clear parsed files cache
    }, [currentConversationId]);

    // Cleanup async operations
    useEffect(() => {
        return () => {
            if (parseTimeoutRef.current) {
                clearTimeout(parseTimeoutRef.current);
            }
        };
    }, []);

    // Ensure window settings are synced with initial state
    useEffect(() => {
        // Sync window settings with component state
        if (window.diffViewType && window.diffViewType !== viewType) {
            setViewType(window.diffViewType);
        }
        if (window.diffShowLineNumbers !== undefined && window.diffShowLineNumbers !== showLineNumbers) {
            window.diffViewType = viewType;
        }
    }, [token, viewType]);

    // Update content when token text changes (for streaming)
    useEffect(() => {
        if (isGlobalStreaming) {
            streamingContentRef.current = token.text || '';
            // Queue the update to allow multiple chunks to arrive
            if (parseTimeoutRef.current) {
                window.clearTimeout(parseTimeoutRef.current);
            }
            parseTimeoutRef.current = null;

            parseTimeoutRef.current = window.setTimeout(() => {
                setCurrentContent(token.text || '');
                parseTimeoutRef.current = null;
            }, 10); // Adjust debounce time as needed
        } else {
            setCurrentContent(token.text || '');
            streamingContentRef.current = token.text || '';
        }
    }, [token.text, isGlobalStreaming]);

    // Maintain last valid parsed diff
    const parsedFilesRef = useRef<any[]>([]);

    useEffect(() => {
        try {
            const parsed = parseDiff(normalizeGitDiff(currentContent));
            if (parsed.length > 0) {
                parsedFilesRef.current = parsed;
                lastValidDiffRef.current = currentContent;
            }
        } catch (error) {
            // Use last valid parse if available
            if (lastValidDiffRef.current) {
                try {
                    parsedFilesRef.current = parseDiff(normalizeGitDiff(lastValidDiffRef.current));
                } catch (e) { }
            }
        }
    }, [currentContent]);

    // Get file title immediately, even during streaming
    const fileTitle = useMemo(() => {
        const extractedTitle = extractFileTitle(currentContent);

        // Store the first non-empty title we extract
        if (extractedTitle && extractedTitle !== 'Unknown file' && !initialFileTitleRef.current) {
            initialFileTitleRef.current = extractedTitle;
        } else if (!isGlobalStreaming) {
            // When not streaming, always use the fresh title to prevent stale cache
            return extractedTitle;
        }

        // During streaming, use the initial title if current extraction fails
        return (isGlobalStreaming && initialFileTitleRef.current && extractedTitle === 'Unknown file') ? initialFileTitleRef.current : extractedTitle;
    }, [currentContent, extractFileTitle, isGlobalStreaming]);

    // Track streaming state in a ref to avoid re-renders
    useEffect(() => {
        isStreamingRef.current = isGlobalStreaming;
        return () => { isStreamingRef.current = false; };
    }, [isGlobalStreaming]);

    if (!hasText(token)) {
        return <div>Loading content...</div>;
    }

    if (!isCodeToken(token)) {
        return null;
    }

    // If we're streaming and have any parsed files, always show them
    if ((isGlobalStreaming) && parsedFilesRef.current.length > 0) {
        // Keep rendering even when not visible to maintain state
        if (!isVisible && !isGlobalStreaming) return null; // Always render during streaming

        return (
            <div>
                <DiffControls
                    fileTitle={parsedFilesRef.current?.[0] ? parsedFilesRef.current[0].oldPath || parsedFilesRef.current[0].newPath || '' : ''}
                    displayMode={displayMode}
                    viewType={viewType}
                    showLineNumbers={showLineNumbers}
                    onDisplayModeChange={setDisplayMode}
                    onViewTypeChange={setViewType}
                    onLineNumbersChange={setShowLineNumbers}
                />
                <div id={`diff-view-${index || 0}`}>
                    {parsedFilesRef.current.map((file, fileIndex) => (
                        <DiffView
                            key={`file-${fileIndex}`}
                            diff={streamingContentRef.current || lastValidDiffRef.current || ''}
                            viewType={viewType}
                            initialDisplayMode={displayMode}
                            showLineNumbers={showLineNumbers}
                            fileIndex={fileIndex}
                            elementId={`${stableElementIdRef.current}-file-${fileIndex}`}
                        />
                    ))}
                </div>
            </div>
        );
    }

    const diffText = currentContent; // Use the state variable for streaming support

    return (
        <div id={`diff-view-wrapper-${stableElementIdRef.current}`}>
            <DiffControls
                displayMode={displayMode}
                viewType={viewType}
                showLineNumbers={showLineNumbers && !isStreamingRef.current}
                onDisplayModeChange={setDisplayMode}
                onViewTypeChange={setViewType}
                onLineNumbersChange={setShowLineNumbers}
                fileTitle={fileTitle}
            />
            <div
                ref={containerRef}
                className="diff-container"
                id={`diff-view-wrapper-${stableElementIdRef.current}`}
                style={{
                    // overflowX: viewType === 'split' ? 'auto' : 'hidden',
                    /*                    maxWidth: '100%'     */
                }}>
                {(displayMode as DisplayMode) === 'raw' ? (
                    <pre className="diff-raw-block" style={{
                        padding: '16px',
                        backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                        color: isDarkMode ? '#e6e6e6' : 'inherit' // Add theme text color
                    }}>
                        <code>{diffText}</code>
                    </pre>
                ) : (
                    <DiffView
                        diff={diffText}
                        viewType={viewType}
                        initialDisplayMode={displayMode}
                        key={stableElementIdRef.current}
                        forceRender={isGlobalStreaming} // Force render during streaming
                        elementId={stableElementIdRef.current!}
                        showLineNumbers={showLineNumbers}
                    />
                )}
            </div>
        </div>
    );
}, (prev, next) => !next.forceRender && prev.token.text === next.token.text && prev.enableCodeApply === next.enableCodeApply);

interface CodeBlockProps {
    token: TokenWithText;
    index: number;
}

const CodeBlock: React.FC<CodeBlockProps> = ({ token, index }) => {
    const tokenRef = useRef<TokenWithText>(token);
    const contentRef = useRef<HTMLDivElement>(null);

    // Stable reference to prevent unnecessary re-renders
    const stableToken = useMemo(() => token, [token.text, token.lang, token.type]);

    const [isLanguageLoaded, setIsLanguageLoaded] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);
    const { isDarkMode } = useTheme();
    const [prismInstance, setPrismInstance] = useState<PrismStatic | null>(null);

    const { isStreaming: isGlobalStreaming } = useChatContext();

    // Normalize the language identifier
    const normalizedLang = useMemo(() => {
        if (!token.lang) return 'plaintext';
        // Map 'typescript jsx' to 'tsx' since we know tsx highlighting works
        if (token.lang === 'typescript jsx') {
            return 'tsx';
        }
        return token.lang;
    }, [token.lang]);

    // Get the highlighted code callback  
    const getHighlightedCode = useCallback((content: string): string => {
        // Ensure content is a string
        const codeToHighlight = typeof content === 'string' ? content : '';

        // If not loaded yet, or no content, return escaped text
        if (!isLanguageLoaded || !prismInstance || !codeToHighlight) {
            return codeToHighlight.replace(/</g, '<').replace(/>/g, '>');
        }

        // Get the grammar, fallback to plaintext if specific language not found
        const grammar = prismInstance.languages[normalizedLang] || prismInstance.languages.plaintext;

        if (!grammar) {
            console.warn(`Grammar not found for ${normalizedLang}, rendering plain text.`);
            // Fallback to basic escaping if even plaintext grammar is missing (shouldn't happen)
            return codeToHighlight.replace(/</g, '<').replace(/>/g, '>');
        }

        try {
            // Perform the highlighting
            return prismInstance.highlight(codeToHighlight, grammar, normalizedLang);
        } catch (error) {
            console.warn(`Highlighting failed for language ${normalizedLang}:`, error);
            // Fallback to basic escaping on highlighting error
            return codeToHighlight.replace(/</g, '<').replace(/>/g, '>');
        }
    }, [normalizedLang, isLanguageLoaded, prismInstance]);

    // Function to highlight code and update DOM directly
    const highlightCodeIfNeeded = useCallback(() => {
        if (!contentRef.current || !isLanguageLoaded || !prismInstance) return;

        const content = tokenRef.current.text || '';
        const highlighted = getHighlightedCode(content);

        // Only update DOM if content has changed
        if (contentRef.current.innerHTML !== highlighted) {
            // Safely set innerHTML while preventing script execution
            if (contentRef.current) {
                // Create a document fragment to safely parse the HTML
                const template = document.createElement('template');
                template.innerHTML = highlighted;

                // Remove any potentially dangerous elements
                template.content.querySelectorAll('script, object, embed, iframe').forEach(el => el.remove());

                // Clear and append the safe content
                contentRef.current.innerHTML = '';
                contentRef.current.appendChild(template.content.cloneNode(true));
            }
            contentRef.current.style.visibility = 'visible';
            // Debug log for streaming updates
            if (content.endsWith('\n') || content.includes('```')) {
                console.debug('Streaming code update:', {
                    language: normalizedLang,
                    contentLength: content.length,
                    isPartial: content.endsWith('\n')
                });
            }
        }
    }, [getHighlightedCode, isLanguageLoaded, normalizedLang, prismInstance]);

    // Store token in ref to avoid unnecessary re-renders
    useEffect(() => {
        // Only update if content actually changed
        if (tokenRef.current.text !== token.text || tokenRef.current.lang !== token.lang) {
            tokenRef.current = token;
            if (contentRef.current) highlightCodeIfNeeded();
        }
    }, [token.text, token.lang, highlightCodeIfNeeded]);

    // Remove the effect that was causing continuous re-renders
    /*
    useEffect(() => {
        tokenRef.current = token;
        if (contentRef.current) highlightCodeIfNeeded();
    }, [token, highlightCodeIfNeeded]);
    */

    useEffect(() => {
        if (token.lang !== undefined) {
            const loadLanguage = async () => {
                setIsLanguageLoaded(false);
                try {
                    if (isDebugLoggingEnabled()) {
                        debugLog('CodeBlock language info:', {
                            originalLang: token.lang,
                            effectiveLang: getEffectiveLang(token.lang),
                            tokenType: token.type,
                            prismLoaded: Boolean(window.Prism),
                            availableLanguages: window.Prism ? Object.keys(window.Prism.languages) : [],
                            tokenContent: token.text.substring(0, 100) + '...'
                        });
                    }
                    // Load language and get Prism instance
                    await loadPrismLanguage(normalizedLang);
                    setPrismInstance(window.Prism);
                    const effectiveLang = getEffectiveLang(token.lang);
                } catch (error: unknown) {
                    const errorMessage = error instanceof Error ? error.message : 'Unknown error';
                    setLoadError(`Error loading language ${normalizedLang}: ${errorMessage}`);
                    console.error(`Error loading language ${normalizedLang}:`, error);
                } finally {
                    setIsLanguageLoaded(true);
                }
            };
            loadLanguage();
        } else {
            setIsLanguageLoaded(true);
        }
    }, [normalizedLang]);

    //  Check if this should be a tool block instead
    if (token.lang?.startsWith('tool:')) {
        const toolName = token.lang.substring(5);

        // Special handling for thinking blocks
        if (toolName === 'mcp_sequentialthinking' || toolName === 'sequentialthinking' || token.lang?.startsWith('thinking:')) {
            return <ThinkingBlock isDarkMode={isDarkMode}>{token.text || ''}</ThinkingBlock>;
        }

        console.log('🔧 CodeBlock redirecting to ToolBlock:', toolName);
        return <ToolBlock toolName={toolName} content={token.text || ''} isDarkMode={isDarkMode} />;
    }

    // Remove debug logging that was causing performance overhead

    // Get the effective language for highlighting
    const getEffectiveLang = (rawLang: string | undefined): string => {
        if (!rawLang) return 'plaintext';
        if (rawLang === 'typescript jsx') return 'tsx';
        return rawLang;
    };

    const highlightedHtml = getHighlightedCode(tokenRef.current.text || '');

    if (!isLanguageLoaded) {
        return (
            <pre style={{
                visibility: isGlobalStreaming ? 'visible' : 'hidden',
                padding: '16px',
                borderRadius: '6px',
                overflow: 'auto',
                backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                border: `1px solid ${isDarkMode ? '#303030' : '#e1e4e8'}`
            }}>
                <code>{token.text}</code>
            </pre>
        );
    }

    // Only escape if the content isn't already escaped
    return (
        <ErrorBoundary type="code">
            <pre
                style={{
                    padding: '16px',
                    borderRadius: '6px',
                    overflow: 'auto',
                    visibility: 'visible',
                    backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                    border: `1px solid ${isDarkMode ? '#303030' : '#e1e4e8'}`
                }}
                className={`language-${normalizedLang}`}
            >
                <code
                    style={{
                        textShadow: 'none',
                        visibility: 'visible',
                        color: isDarkMode ? '#e6e6e6' : '#24292e'
                    }}
                    ref={contentRef}
                    dangerouslySetInnerHTML={{ __html: highlightedHtml }}
                />
            </pre>
        </ErrorBoundary>
    );
};

// Define the possible determined types
type DeterminedTokenType = 'diff' | 'graphviz' | 'vega-lite' |
    'd3' | 'mermaid' | 'file-operation' | 'tool' |
    'joint' | 'jointjs' | 'code' | 'html' | 'text' | 'list' | 'table' | 'escape' | 'math' |
    'paragraph' | 'heading' | 'hr' | 'blockquote' | 'space' |
    'circuitikz' | 'html-mockup' |
    'codespan' | 'strong' | 'em' | 'del' | 'link' | 'image' |
    'br' | 'list_item' | 'circuitikz' | 'latex' |
    'unknown';

// Track last log timestamp to prevent excessive logging
let lastLogTimestamp = 0;

// Helper function to determine the definitive type of a token
function determineTokenType(token: Tokens.Generic | TokenWithText): DeterminedTokenType {
    const tokenType = token.type as string;

    // 1. Prioritize content-based detection for diffs, regardless of lang tag
    if (tokenType === 'code' && 'text' in token && typeof token.text === 'string') {
        const text = token.text;

        // Check first few lines for diff markers
        const linesToCheck = text.split('\n').slice(0, 5);

        const hasGitHeader = linesToCheck.some(line => line.trim().startsWith('diff --git '));
        const hasMinusHeader = linesToCheck.some(line => line.trim().startsWith('--- a/'));
        const hasPlusHeader = linesToCheck.some(line => line.trim().startsWith('+++ b/'));
        const hasHunkHeader = linesToCheck.some(line => {
            const trimmed = line.trim();
            return trimmed.startsWith('@@') && trimmed.match(/^@@\s+-\d+/);
        });
        const diffMarkersFound = [hasGitHeader, hasMinusHeader, hasPlusHeader, hasHunkHeader].filter(Boolean).length;

        // More lenient check for diff --git, allowing it not to be the very first thing
        const containsDiffGit = text.includes('diff --git');

        // Only log when debug logging is enabled
        if (isDebugLoggingEnabled() && false) {
            debugLog('Diff markers analysis:', {
                hasGitHeader,
                hasMinusHeader,
                hasPlusHeader,
                hasHunkHeader,
                diffMarkersFound,
                containsDiffGit,
                shouldBeDiff: containsDiffGit || diffMarkersFound >= 2
            });
        }

        if (containsDiffGit || diffMarkersFound >= 2) {
            if (isDebugLoggingEnabled()) {
                debugLog('DETECTED AS DIFF (content-based)');
            }
            return 'diff';
        }
    }

    // 2. Handle Code Blocks with explicit lang tags
    if (tokenType === 'code' && 'lang' in token && typeof token.lang === 'string' && token.lang) {
        const lang = token.lang.toLowerCase().trim();

        // Check for DrawIO blocks
        if (lang === 'drawio' || lang === 'draw.io') {
            return 'drawio';
        }

        // Check for HTML mockup blocks
        if (lang === 'html-mockup' || lang === 'ui-mockup' || lang === 'mockup') {
            return 'html-mockup';
        }

        // Check for visualization types BEFORE tool types
        // This prevents Vega-Lite blocks from being misidentified
        if (lang === 'vega-lite' || lang === 'vegalite') {
            return 'vega-lite';
        }
        if (lang === 'mermaid') {
            return 'mermaid';
        }
        if (lang === 'graphviz' || lang === 'dot') {
            return 'graphviz';
        }
        if (lang === 'circuitikz' || lang === 'tikz' || lang === 'latex') {
            return 'circuitikz';
        }
        if (lang === 'latex-circuit') {
            return 'circuitikz';
        }
        if (lang === 'd3') return 'd3';

        // Only log when debug logging is enabled and only for debugging specific issues
        if (isDebugLoggingEnabled() && false) {
            debugLog('Processing code block with lang:', lang);
            debugLog('determineTokenType - Code block with lang:', lang, 'tokenType:', tokenType);
            debugLog('Code block detected with lang:', lang, 'content preview:', (token as TokenWithText).text?.substring(0, 50));
        }

        // Check for MCP tool blocks first
        if (lang.startsWith('tool:mcp_')) {
            const toolName = lang.substring(5); // Remove 'tool:' prefix to get 'mcp_...'
            (token as TokenWithText).toolName = toolName;
            // Only log when debug logging is enabled
            if (isDebugLoggingEnabled()) {
                debugLog('MCP tool block detected:', toolName);
            }
            return 'tool';
        }
        if (lang.startsWith('tool:')) {
            const toolName = lang.substring(5); // Remove 'tool:' prefix
            (token as TokenWithText).toolName = toolName;
            // Only log when debug logging is enabled
            if (isDebugLoggingEnabled()) {
                debugLog('Tool block detected:', toolName);
            }
            return 'tool';
        }

        // Check for thinking blocks
        if (lang.startsWith('thinking:')) {
            const stepInfo = lang.substring(9); // Remove 'thinking:' prefix
            (token as TokenWithText).toolName = `thinking_${stepInfo}`;
            if (isDebugLoggingEnabled()) {
                debugLog('Thinking block detected:', stepInfo);
            }
            (token as TokenWithText).toolName = `thinking_${stepInfo}`;
            return 'tool';
        }

        // Check for JSON code blocks that might contain Vega-Lite specs
        if (lang === 'json' && 'text' in token && typeof token.text === 'string') {
            const text = token.text.trim();
            if (text.startsWith('{') && (text.includes('"$schema"') || text.includes('"mark"'))) {
                try {
                    const parsed = JSON.parse(text);
                    if (parsed.$schema?.includes('vega-lite') ||
                        (parsed.mark && (parsed.encoding || parsed.data)) ||
                        (parsed.data && (parsed.mark || parsed.layer || parsed.concat || parsed.facet || parsed.repeat))) {
                        return 'vega-lite';
                    }
                } catch (error) {
                    // Not valid JSON, continue with other checks
                }
            }
        }

        // Check remaining diagram types
        if (lang === 'joint' || lang === 'jointjs' || lang === 'diagram') return 'joint';

        if (lang === 'diff') {
            console.log('✅ MarkdownRenderer - DETECTED AS DIFF (lang tag)');
            return 'diff';
        }

        // If it has a specific lang tag but isn't special, it's 'code'
        return 'code';
    }

    // 2. Content-based detection for code blocks *without* specific lang tags
    // This is where trimmedText is available
    if (tokenType === 'code' && 'text' in token && typeof token.text === 'string') {
        const text = token.text;
        const trimmedText = text.trim();

        // Enhanced tool block detection by content
        // Look for tool block markers that might have been missed by lang detection
        if (trimmedText.startsWith('\`\`\`tool:mcp_') ||
            trimmedText.includes('\cp_') ||
            (trimmedText.startsWith('$ ') && trimmedText.length > 10 &&
                !trimmedText.includes('\n') && // Single line commands only
                !trimmedText.includes('ERROR:') && // Not error messages
                !trimmedText.includes('WARNING:') && // Not warning messages
                !trimmedText.includes('DEBUG:')) || // Not debug messages
            trimmedText.includes('🔧') || // Tool emoji markers
            trimmedText.includes('🛠️')) {

            // Try to extract tool name from content
            const toolMatch = trimmedText.match(/```?tool:(mcp_\w+)/);
            const toolName = toolMatch ? toolMatch[1] : 'mcp_run_shell_command';
            (token as TokenWithText).toolName = toolName;
            (token as TokenWithText).lang = `tool:${toolName}`;
            return 'tool';
        }
    }

    if (tokenType === 'code' && 'text' in token && typeof token.text === 'string') {
        const text = token.text;
        const trimmedText = text.trim();

        // Check for DrawIO XML content
        if (trimmedText.includes('<mxGraphModel') ||
            trimmedText.includes('<mxfile') ||
            trimmedText.includes('<diagram')) {
            if (isDebugLoggingEnabled()) {
                debugLog('DETECTED AS DRAWIO (content-based)');
            }
            // Set the token type so it can be rendered properly
            (token as TokenWithText).lang = 'drawio';
            return 'drawio';
        }

        // Check for tool blocks by content if lang detection failed
        if (trimmedText.startsWith('$ ') || trimmedText.includes('🔧') || trimmedText.includes('🛠️')) {
            // This might be a tool result that wasn't properly tagged
            console.log('Potential tool content detected without proper lang tag:', trimmedText.substring(0, 50));
            // Try to infer tool type from content
            (token as TokenWithText).toolName = trimmedText.startsWith('$ ') ? 'mcp_run_shell_command' : 'unknown_tool';
            return 'tool';
        }

        // Check for Vega-Lite JSON specifications with better error handling
        if (trimmedText.startsWith('{')) {
            try {
                const parsed = JSON.parse(trimmedText);
                // Check for Vega-Lite schema or typical Vega-Lite structure
                if (parsed.$schema?.includes('vega-lite') ||
                    (parsed.mark && (parsed.encoding || parsed.data)) ||
                    (parsed.data && (parsed.mark || parsed.layer || parsed.concat || parsed.facet || parsed.repeat))) {
                    return 'vega-lite';
                }
            } catch (error) {
                // Not valid JSON, continue with other checks
                console.debug("JSON parse failed for potential Vega-Lite:", error);
            }
        }

        // Check if this is a diff block by looking for diff marker
        if (text.startsWith('diff') || text.includes('\ndiff')) {
            return 'diff';
        }

        // Check for file operations (apply blocks)
        if (text.includes('<apply>') && text.includes('</apply>')) {
            return 'file-operation';
        }

        // Strict Graphviz check
        // Look for 'digraph' or 'graph' followed by an identifier and '{'
        // Allows for optional whitespace and comments before the opening brace
        const graphvizRegex = /^\s*(?:strict\s+)?(digraph|graph)\s+\w*\s*\{/i;
        if (graphvizRegex.test(trimmedText)) {
            return 'graphviz';
        }
        // Fallback for simpler cases (might be less reliable)
        if (trimmedText.startsWith('digraph') ||
            (trimmedText.startsWith('graph') && trimmedText.match(/^graph\s+\w*\s*\{/))) {
            return 'graphviz';
        }


        // Check for diff content more robustly within the first few lines
        const linesToCheck = text.split('\n').slice(0, 5); // Check first 5 lines for diff markers
        const hasGitHeader = linesToCheck.some(line => line.trim().startsWith('diff --git '));
        const hasMinusHeader = linesToCheck.some(line => line.trim().startsWith('--- a/'));
        const hasPlusHeader = linesToCheck.some(line => line.trim().startsWith('+++ b/'));
        const hasHunkHeader = linesToCheck.some(line => {
            const trimmed = line.trim();
            return trimmed.startsWith('@@') && trimmed.match(/^@@\s+-\d+/);
        });

        // Check for common valid diff starting patterns
        // Require at least two characteristic lines for content-based detection
        const diffMarkersFound = [hasGitHeader, hasMinusHeader, hasPlusHeader, hasHunkHeader].filter(Boolean).length;
        if (diffMarkersFound >= 2) {
            return 'diff';
        }
        // If no special content detected, treat as generic code
        return 'code';
    }

    // 3. Map other standard marked token types directly
    // Add more types from marked.Tokens here as needed
    const knownTypes: DeterminedTokenType[] = [
        'paragraph', 'heading', 'hr', 'blockquote', 'list', 'list_item', 'table', 'escape',
        'html', 'text', 'space', 'codespan', 'strong', 'em', 'del', 'link',
        'image', 'br'
    ];
    if (knownTypes.includes(tokenType as DeterminedTokenType)) {
        return tokenType as DeterminedTokenType;
    }

    // Fallback for unknown types
    console.warn("Unknown token type encountered:", tokenType, token);
    return 'unknown';
}

// Helper function to decode HTML entities using the browser's capabilities
const decodeHtmlEntities = (text: string): string => {
    if (typeof document === 'undefined') {
        // Basic fallback for server-side or environments without DOM
        return text.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/"/g, '"').replace(/'/g, "'");
    }

    // Use a more controlled approach to avoid false entity decoding
    // Only decode known HTML entities to prevent issues like ¶m becoming ¶m
    return text
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&amp;/g, '&')
        .replace(/&quot;/g, '"')
        .replace(/&apos;/g, "'")
        .replace(/&#39;/g, "'")
        .replace(/&#x27;/g, "'")
        .replace(/&#x60;/g, '`')
        .replace(/&#x3D;/g, '=')
        .replace(/&nbsp;/g, ' ')
        .replace(/&copy;/g, '©')
        .replace(/&reg;/g, '®')
        .replace(/&trade;/g, '™');
};

const renderTokens = (tokens: (Tokens.Generic | TokenWithText)[], enableCodeApply: boolean, isDarkMode: boolean, isSubRender: boolean = false, isStreaming: boolean = false, thinkingContentRef?: React.MutableRefObject<string>, onOpenShellConfig?: () => void): React.ReactNode => {
    const shouldLog = isDebugLoggingEnabled() &&
        (Date.now() - lastLogTimestamp > 10000);

    if (shouldLog && tokens.length > 0) {
        lastLogTimestamp = Date.now();
        debugLog(`Processing ${tokens.length} tokens`);
    }

    return tokens.map((token, index) => {
        const previousToken = index > 0 ? tokens[index - 1] : null;
        // Determine the definitive type for rendering
        const determinedType = determineTokenType(token);
        const tokenWithText = token as TokenWithText; // Helper cast

        // Only log tool tokens when debug logging is enabled
        if (isDebugLoggingEnabled() &&
            index === 0 &&
            (token as any).lang?.startsWith('tool:')) {
            debugLog(`Tool token detected: ${(token as any).lang}`);
        }

        // Override code detection if this token follows a tool token
        if (determinedType === 'code' && previousToken) {
            const prevTokenWithText = previousToken as TokenWithText;
            if (prevTokenWithText.toolName || (prevTokenWithText.lang && prevTokenWithText.lang.startsWith('tool:'))) {
                // This token follows a tool - check if it should really be treated as regular text/paragraph
                if (!tokenWithText.text?.startsWith('```') && !tokenWithText.text?.startsWith('    ')) {
                    // Force this to be treated as markdown instead of code
                    return (
                        <div key={index}>
                            <MarkdownRenderer
                                markdown={tokenWithText.text || ''}
                                enableCodeApply={enableCodeApply}
                                isStreaming={isStreaming}
                            />
                        </div>
                    );
                }
            }
        }

        try {
            switch (determinedType) {
                case 'diff':
                    // Only log when debug logging is enabled and only for debugging
                    if (isDebugLoggingEnabled() && false) {
                        debugLog('Rendering DIFF token');
                    }
                    const rawDiffText = tokenWithText.text || '';
                    // Apply cleaning specific to diff content AFTER decoding
                    const cleanedDiff = cleanDiffContent(rawDiffText);
                    // Ensure lang is set to 'diff' for the component
                    const diffToken = { ...tokenWithText, text: cleanedDiff, lang: 'diff' };
                    // Only log when debug logging is enabled and only for debugging
                    if (isDebugLoggingEnabled() && false) {
                        debugLog('Created diffToken:', {
                            hasText: !!diffToken.text,
                            textLength: diffToken.text?.length,
                            lang: diffToken.lang,
                            textPreview: diffToken.text?.substring(0, 100) + '...'
                        });
                    }

                    // Check if this is a multi-file diff and not already a sub-render
                    if (!isSubRender) {
                        const fileDiffs = splitMultiFileDiffs(cleanedDiff);
                        if (fileDiffs.length > 1) {
                            console.log('🎨 MarkdownRenderer - Rendering multi-file diff');
                            return renderMultiFileDiff(diffToken, index, enableCodeApply, isDarkMode);
                        }
                    }

                    // Only log when debug logging is enabled and only for debugging
                    if (isDebugLoggingEnabled() && false) {
                        debugLog('Rendering single DiffToken component');
                    }
                    return <DiffToken key={index} token={diffToken} index={index} enableCodeApply={enableCodeApply} isDarkMode={isDarkMode} />;

                case 'html-mockup':
                    if (!hasText(tokenWithText) || !tokenWithText.text?.trim()) return null;
                    return (
                        <HTMLMockupRenderer key={index} html={tokenWithText.text} isStreaming={isStreaming} />
                    );

                case 'file-operation':
                    if (!hasText(tokenWithText) || !tokenWithText.text?.trim()) return null;

                    const safetyResult = renderFileOperationSafely(tokenWithText.text);
                    if (safetyResult.shouldRender) {
                        return (
                            <FileOperationRenderer
                                key={index}
                                content={tokenWithText.text}
                                enableApply={enableCodeApply}
                            />
                        );
                    }
                    // Fall through to code block if invalid
                    return <CodeBlock token={{ ...tokenWithText, text: safetyResult.safeContent, lang: 'xml' }} index={index} />;

                case 'graphviz':
                    if (!hasText(tokenWithText) || !tokenWithText.text?.trim()) return null;
                    return (
                        <D3Renderer
                            spec={{
                                type: 'graphviz',
                                isStreaming: isStreaming,
                                definition: token.text,
                                isMarkdownBlockClosed: true,
                                timestamp: Date.now(), // Add timestamp to force re-renders
                                forceRender: true // Force render even if incomplete
                            }}
                            type="d3" isStreaming={isStreaming}
                        />
                    );
                case 'mermaid':
                    if (!hasText(tokenWithText) || !tokenWithText.text?.trim()) return null;
                    // Only log when debug logging is enabled and only for debugging
                    if (isDebugLoggingEnabled() && false) {
                        debugLog(`CREATING MERMAID SPEC:`, { text: tokenWithText.text.substring(0, 100) });
                    }
                    // Pass the definition directly to D3Renderer, which will use the mermaidPlugin
                    // We need a spec object that the mermaidPlugin can handle with streaming flag
                    const mermaidSpec = {
                        type: 'mermaid',
                        definition: tokenWithText.text,
                        isStreaming: isStreaming,
                        isMarkdownBlockClosed: true,
                        timestamp: Date.now(), // Add timestamp to force re-renders
                        forceRender: true // Force render even if incomplete
                    };
                    console.log(`🎯 CALLING D3RENDERER WITH MERMAID SPEC:`, mermaidSpec);
                    return <D3Renderer key={index} spec={mermaidSpec} type="d3" isStreaming={isStreaming} />;

                case 'drawio':
                    if (!hasText(tokenWithText) || !tokenWithText.text?.trim()) return null;
                    return (
                        <D3Renderer
                            spec={{
                                type: 'drawio',
                                definition: tokenWithText.text,
                                isStreaming: isStreaming,
                                forceRender: true
                            }}
                            type="d3"
                            isStreaming={isStreaming}
                        />
                    );

                case 'vega-lite':
                    if (!hasText(tokenWithText)) return null;

                    let vegaLiteSpec: any;
                    try {
                        // Parse the JSON specification
                        const parsedSpec = JSON.parse(tokenWithText.text);

                        // Create the spec object for D3Renderer
                        vegaLiteSpec = {
                            type: 'vega-lite',
                            ...parsedSpec,  // Spread the parsed JSON
                            isStreaming: isStreaming,
                            forceRender: true
                        };

                    } catch (error) {
                        // If parsing fails, treat as definition string
                        vegaLiteSpec = {
                            type: 'vega-lite',
                            definition: tokenWithText.text,
                            isStreaming: isStreaming,
                            forceRender: true
                        };
                    }

                    return (
                        <D3Renderer
                            key={index}
                            spec={vegaLiteSpec}
                            type="d3"
                            isStreaming={isStreaming}
                        />
                    );

                case 'joint':
                case 'jointjs':
                    if (!hasText(tokenWithText) || !tokenWithText.text?.trim()) return null;

                    // Try to parse as JSON first, otherwise treat as definition string
                    let jointSpec;
                    try {
                        jointSpec = JSON.parse(tokenWithText.text);
                        // Ensure it has the joint type
                        if (!jointSpec.type) {
                            jointSpec.type = 'joint';
                        }
                    } catch (error) {
                        // If JSON parsing fails, treat as definition string
                        jointSpec = {
                            type: 'joint',
                            definition: tokenWithText.text,
                            isStreaming: isStreaming,
                            forceRender: true
                        };
                    }

                    return <D3Renderer key={index} spec={jointSpec} type="d3" isStreaming={isStreaming} />;

                case 'tool':
                    if (!hasText(tokenWithText) || !tokenWithText.toolName) {
                        console.warn('Tool token missing toolName or text:', { hasText: hasText(tokenWithText), toolName: tokenWithText.toolName });
                        return null;
                    }

                    // Strip tool block fence markers if present in content
                    let toolContent = tokenWithText.text || '';
                    const fenceMatch = toolContent.match(/^\n*```tool:[^\n]+\n([\s\S]*?)```\n*$/);
                    if (fenceMatch) {
                        toolContent = fenceMatch[1];
                    }

                    // Check for security errors in tool output and render them prominently
                    const isSecurityError = toolContent && (
                        toolContent.includes('🚫 SECURITY BLOCK') ||
                        toolContent.includes('Command not allowed') ||
                        toolContent.includes('COMMAND BLOCKED') ||
                        (toolContent.includes("'error': True") && toolContent.includes('SECURITY BLOCK'))
                    );

                    if (isSecurityError) {
                        // Extract the actual message from Python dict format
                        let errorMessage = toolContent;
                        const pythonDictMatch = toolContent.match(/\{'error': True, 'message': "([^"]*(?:\\.[^"]*)*)"/);
                        if (pythonDictMatch) {
                            errorMessage = pythonDictMatch[1].replace(/\\n/g, '\n').replace(/^🚫 SECURITY BLOCK:\s*/, '');
                        }
                        return (
                            <Alert key={index} message="🚫 Command Blocked" description={makeShellConfigLinkClickable(errorMessage, onOpenShellConfig)} type="warning" showIcon style={{ margin: '16px 0', border: '2px solid #faad14', whiteSpace: 'pre-line' }} />
                        );
                    }

                    // Special handling for thinking content
                    if (tokenWithText.toolName?.startsWith('thinking_')) {
                        return (
                            <ThinkingBlock key={index} isDarkMode={isDarkMode} isStreaming={isStreaming}>
                                {toolContent}
                            </ThinkingBlock>
                        );
                    }

                    // Only log successful tool rendering when debug logging is enabled
                    if (isDebugLoggingEnabled()) {
                        debugLog('Successfully rendering tool block:', { toolName: tokenWithText.toolName, contentLength: toolContent.length });
                    }
                    return (
                        <ToolBlock key={index} toolName={tokenWithText.toolName} content={toolContent} isDarkMode={isDarkMode} onOpenShellConfig={onOpenShellConfig} />
                    );

                case 'd3':
                    if (!hasText(tokenWithText)) return null;
                    return (
                        <D3Renderer key={index} spec={tokenWithText.text} type="d3" isStreaming={isStreaming} />
                    );

                case 'code':
                    if (!isCodeToken(tokenWithText)) return null; // Type guard

                    // Skip empty code blocks
                    if (!tokenWithText.text || tokenWithText.text.trim() === '') {
                        console.log('Skipping empty code block');
                        return null;
                    }

                    // CORE FIX: Check if this code block contains diff content and should use file-based language detection
                    const rawCodeText = decodeHtmlEntities(tokenWithText.text || '');

                    // Add debugging to see if this fix is being triggered
                    const isDiffContent = rawCodeText.includes('diff --git') ||
                        rawCodeText.includes('new file mode') ||
                        rawCodeText.includes('deleted file mode') ||
                        (rawCodeText.includes('+++') && rawCodeText.includes('---'));

                    if ((!tokenWithText.lang || tokenWithText.lang === 'plaintext') &&
                        isDiffContent) {

                        console.log('🔍 DIFF_FIX: Applying language fix for diff content:', {
                            hasLang: !!tokenWithText.lang,
                            currentLang: tokenWithText.lang,
                            contentPreview: rawCodeText.substring(0, 100)
                        });

                        // Extract file path from diff content
                        const lines = rawCodeText.split('\n');
                        for (const line of lines) {
                            // Handle both standard format and malformed Gemini format
                            const gitMatch = line.match(/diff --git (?:a\/)?([^\/]*(?:\/[^\/]*)*) (?:b\/)?(.*)$/);
                            if (gitMatch) {
                                // For new files, prefer target path; for deleted files, prefer source path
                                const filePath = gitMatch[1] === '/dev/null' ? gitMatch[2] :
                                    gitMatch[2] === '/dev/null' ? gitMatch[1] :
                                        (gitMatch[2] || gitMatch[1]);
                                tokenWithText.lang = detectLanguage(filePath);
                                console.log('🔍 DIFF_FIX: Language detection result:', {
                                    filePath,
                                    detectedLang: tokenWithText.lang,
                                    gitMatch1: gitMatch[1],
                                    gitMatch2: gitMatch[2]
                                });
                                break;
                            }
                        }
                    } else if (isDiffContent) {
                        console.log('🔍 DIFF_FIX: Diff content detected but not applying fix:', {
                            hasLang: !!tokenWithText.lang,
                            currentLang: tokenWithText.lang,
                            reason: tokenWithText.lang && tokenWithText.lang !== 'plaintext' ? 'already has language' : 'unknown'
                        });
                    }

                    // Add safety check for tool blocks that might have slipped through
                    if (tokenWithText.lang?.startsWith('tool:')) {
                        console.error('CRITICAL ERROR: Tool block reached code case!', { lang: tokenWithText.lang, determinedType });
                        // Force redirect to tool rendering
                        return <ToolBlock key={index} toolName={tokenWithText.lang.substring(5)} content={tokenWithText.text} isDarkMode={isDarkMode} onOpenShellConfig={onOpenShellConfig} />
                    }

                    // Check for file operations first
                    if (detectFileOperationSyntax(tokenWithText.text)) {
                        const safetyResult = renderFileOperationSafely(tokenWithText.text);

                        if (safetyResult.shouldRender) {
                            return (
                                <FileOperationRenderer
                                    key={index}
                                    content={tokenWithText.text}
                                    enableApply={enableCodeApply}
                                />
                            );
                        } else {
                            // Render as safe code block with warnings
                            return (
                                <div key={index}>
                                    {safetyResult.warnings.length > 0 && (
                                        <Alert
                                            type="warning"
                                            message="File Operation Warnings"
                                            description={safetyResult.warnings.join(', ')}
                                            style={{ marginBottom: 8 }}
                                        />
                                    )}
                                    <CodeBlock token={{ ...tokenWithText, text: safetyResult.safeContent }} index={index} />
                                </div>
                            );
                        }
                    }

                    // Pass the original lang tag (or plaintext) for highlighting
                    const codeToken = { ...tokenWithText, text: rawCodeText, lang: tokenWithText.lang || 'plaintext' };
                    // Decode HTML entities before passing to CodeBlock
                    const decodedToken = {
                        ...codeToken,
                        text: decodeHtmlEntities(codeToken.text)
                    };
                    return <CodeBlock key={index} token={decodedToken} index={index} />;

                // --- Handle Standard Markdown Elements ---
                case 'paragraph':
                    // Render paragraph, processing inline tokens recursively
                    const pTokens = (token as Tokens.Paragraph).tokens || [];

                    // Check if any paragraph content has inline math
                    const paragraphContent = pTokens.map(t => (t as TokenWithText).text || '').join('');
                    if (paragraphContent.includes('⟨MATH_INLINE:')) {
                        const parts = paragraphContent.split(/(⟨MATH_INLINE:[\s\S]*?⟩)/);
                        return <p key={index}>{parts.map((part, i) => {
                            if (part.startsWith('⟨MATH_INLINE:')) {
                                const math = part.slice('⟨MATH_INLINE:'.length, -1).trim();
                                return <MathRenderer key={i} math={math} displayMode={false} />;
                            }
                            return part || null;
                        })}</p>;
                    }

                    // Filter out empty text tokens that might remain after processing
                    const filteredPTokens = pTokens.filter(t => t.type !== 'text' || (t as TokenWithText).text.trim() !== '');
                    if (filteredPTokens.length === 0) return null; // Don't render empty paragraphs
                    return <p key={index}>{renderTokens(filteredPTokens, enableCodeApply, isDarkMode, isSubRender, isStreaming, thinkingContentRef, onOpenShellConfig)}</p>;

                case 'list':
                    // Render list, processing items recursively
                    const listToken = token as Tokens.List;
                    const ListTag = listToken.ordered ? 'ol' : 'ul';
                    return (
                        <ListTag key={index} start={listToken.ordered ? (listToken.start || 1) : undefined}>
                            {listToken.items.map((item, itemIndex) => (
                                // Render list items using the 'list_item' case below
                                <React.Fragment key={itemIndex}>
                                    {renderTokens([item], enableCodeApply, isDarkMode, isSubRender, isStreaming, thinkingContentRef, onOpenShellConfig)}
                                </React.Fragment>
                            ))}
                        </ListTag>
                    );

                case 'list_item':
                    const listItemToken = token as Tokens.ListItem;

                    const itemContent = renderTokens(listItemToken.tokens || [], enableCodeApply, isDarkMode, isSubRender, isStreaming, thinkingContentRef, onOpenShellConfig);

                    // Handle task list items
                    if (listItemToken.task) {
                        return (
                            <li key={index} style={{ listStyle: 'none' }}>
                                <input
                                    type="checkbox"
                                    checked={listItemToken.checked}
                                    id={`task-checkbox-${index}-${Math.random().toString(36).substring(2, 9)}`}
                                    name={`task-checkbox-${index}`}
                                    readOnly
                                    style={{ marginRight: '0.5em', verticalAlign: 'middle' }}
                                />
                                {itemContent}
                            </li>
                        );
                    }
                    // Regular list item
                    return <li key={index}>{itemContent}</li>;

                case 'table':
                    const tableToken = token as Tokens.Table;
                    return (
                        <table key={index} style={{ borderCollapse: 'collapse', width: '100%', marginBottom: '1em' }}>
                            <thead>
                                <tr>
                                    {tableToken.header.map((cell, cellIndex) => (
                                        <th key={cellIndex} style={{ borderBottom: '2px solid #ddd', padding: '8px', textAlign: tableToken.align[cellIndex] || 'left' }}>
                                            {renderTokens(cell.tokens || [{ type: 'text', text: cell.text }], enableCodeApply, isDarkMode, isSubRender, isStreaming, thinkingContentRef, onOpenShellConfig)}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {tableToken.rows.map((row, rowIndex) => (
                                    <tr key={rowIndex}>
                                        {row.map((cell, cellIndex) => (
                                            <td key={cellIndex} style={{ border: '1px solid #ddd', padding: '8px', textAlign: tableToken.align[cellIndex] || 'left' }}>
                                                {renderTokens(cell.tokens || [], enableCodeApply, isDarkMode, isSubRender, isStreaming, thinkingContentRef, onOpenShellConfig)}
                                            </td>
                                        ))}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    );

                case 'html':
                    if (!hasText(tokenWithText)) return null;

                    const HTMLtoolBlockMatch = tokenWithText.text.match(/<!-- TOOL_BLOCK_START:(mcp_\w+)\|(.+?) -->\s*([\s\S]*?)\s*<!-- TOOL_BLOCK_END:\1 -->/);
                    if (HTMLtoolBlockMatch) {
                        const [, toolName, displayHeader, toolContent] = HTMLtoolBlockMatch;

                        // Special handling for thinking tools
                        if (toolName === 'mcp_sequentialthinking' || toolName.includes('thinking')) {
                            return (
                                <ThinkingBlock key={index} isDarkMode={isDarkMode} isStreaming={isStreaming}>
                                    {toolContent}
                                </ThinkingBlock>
                            );
                        }

                        // Regular tool blocks
                        return (
                            <ToolBlock key={index} toolName={`${toolName}|${displayHeader}`} content={toolContent} isDarkMode={isDarkMode} onOpenShellConfig={onOpenShellConfig} />
                        );
                    }

                    // Handle thinking blocks - check for thinking-data tags
                    if (tokenWithText.text.includes('thinking-wrapper') || tokenWithText.text.match(/<thinking-data>([\s\S]*?)<\/thinking-data>/)) {
                        console.log('🤔 Detected thinking-data tag in HTML token:', tokenWithText.text.substring(0, 100));
                        const match = tokenWithText.text.match(/<thinking-data>([\s\S]*?)<\/thinking-data>/);
                        if (match) {
                            const content = match[1];
                            console.log('🤔 Extracted thinking content:', content.substring(0, 50) + '...');
                            console.log('🤔 Returning ThinkingBlock component');
                            return <ThinkingBlock key={index} isDarkMode={isDarkMode} isStreaming={isStreaming}>{content}</ThinkingBlock>;
                        }
                    }

                    // Be cautious with dangerouslySetInnerHTML

                    // List of known/safe HTML tags that we want to actually render as HTML
                    const knownHtmlTags = [
                        'div', 'span', 'p', 'br', 'hr', 'strong', 'em', 'b', 'i', 'u', 's',
                        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'button',
                        'ul', 'ol', 'li', 'dl', 'dt', 'dd',
                        'table', 'thead', 'tbody', 'tr', 'th', 'td',
                        'a', 'img', 'video', 'audio',
                        'blockquote', 'pre', 'code',
                        'details', 'summary',
                        'math', 'mi', 'mo', 'mn', 'mrow', 'mfrac', 'msup', 'msub', 'msubsup', 'msqrt', 'mroot',
                        'thinking-data'

                    ];

                    const htmlContent = tokenWithText.text;

                    // Check for angle-bracketed math markers first
                    if (htmlContent.includes('⟨MATH_INLINE:')) {
                        const parts = htmlContent.split(/(⟨MATH_INLINE:[\s\S]*?⟩)/);
                        return (
                            <React.Fragment key={index}>
                                {parts.map((part, i) => {
                                    if (part.startsWith('⟨MATH_INLINE:')) {
                                        const math = part.slice('⟨MATH_INLINE:'.length, -1).trim();
                                        return <MathRenderer key={i} math={math} displayMode={false} />;
                                    }
                                    return part || null;
                                })}
                            </React.Fragment>
                        );
                    }

                    // Check if this is a MathML element and render it inline
                    if (htmlContent.match(/^<(math|mi|mo|mn|mrow|mfrac|msup|msub|msubsup|msqrt|mroot)/)) {
                        try {
                            const mathWithNamespace = htmlContent.includes('xmlns=')
                                ? htmlContent
                                : htmlContent.replace('<math', '<math xmlns="http://www.w3.org/1998/Math/MathML"');
                            return <span key={index} dangerouslySetInnerHTML={{ __html: mathWithNamespace }} />;
                        } catch (error) {
                            console.error('MathML rendering error:', error);
                            return <span key={index}>{htmlContent}</span>;
                        }
                    }

                    // Detect throttling/rate limit messages and render them directly
                    // These contain interactive retry buttons that must not be escaped
                    const isThrottlingMessage = htmlContent.includes('throttle-retry-button') ||
                        (htmlContent.includes('Rate Limit') && htmlContent.includes('<button'));
                    if (isThrottlingMessage) {
                        return <div key={index} dangerouslySetInnerHTML={{ __html: htmlContent }} />;
                    }

                    // Check if this is a wrapped MathML block
                    if (htmlContent.includes('class="mathml-block"')) {
                        return <div key={index} dangerouslySetInnerHTML={{ __html: htmlContent }} />;
                    }

                    // Check for KATEX/LATEX math expressions
                    if (htmlContent.includes('MATH_DISPLAY:')) {
                        const mathMatch = htmlContent.match(/MATH_DISPLAY:([^<]*)/s);
                        if (mathMatch) return <MathRenderer key={index} math={mathMatch[1]} displayMode={true} />;
                    }
                    if (htmlContent.includes('MATH_INLINE:')) {
                        const mathMatch = htmlContent.match(/MATH_INLINE:([^<]*)/s);
                        if (mathMatch) return <MathRenderer key={index} math={mathMatch[1]} displayMode={false} />;
                    }

                    if (htmlContent.includes('class="math-inline-span"') && htmlContent.includes('MATH_INLINE:')) {
                        const mathMatch = htmlContent.match(/MATH_INLINE:([^<]*)/);
                        if (mathMatch) return <MathRenderer key={index} math={mathMatch[1]} displayMode={false} />;
                    }

                    const tagMatches = htmlContent.match(/<\/?([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>/g);

                    if (tagMatches) {
                        const hasUnknownTags = tagMatches.some(tag => {
                            const tagName = tag.match(/<\/?([a-zA-Z][a-zA-Z0-9]*)/)?.[1]?.toLowerCase();
                            return tagName && !knownHtmlTags.includes(tagName);
                        });

                        if (hasUnknownTags) {
                            // If there are unknown tags, render as literal text
                            return <span key={index}>{htmlContent}</span>;
                        }
                    }

                    // Render as text content to avoid HTML parsing issues with angle brackets
                    return <div key={index}>{decodeHtmlEntities(htmlContent)}</div>;

                case 'text':
                    if (!hasText(tokenWithText)) return null;
                    let decodedText = decodeHtmlEntities(tokenWithText.text);

                    // Check for encoded tool blocks
                    const toolBlockMatch = decodedText.match(/⟨TOOL:(mcp_\w+)\|([^|]+)\|([^⟩]+)⟩/);
                    if (toolBlockMatch) {
                        const [, toolName, displayHeader, encodedContent] = toolBlockMatch;

                        // Decode the content
                        let toolContent;
                        try {
                            toolContent = encodedContent === 'LOADING' ? '⏳ Running...' :
                                decodeURIComponent(escape(atob(encodedContent)));
                        } catch (e) {
                            console.error('Failed to decode tool content:', e);
                            toolContent = 'Error decoding tool content';
                        }

                        // Render as ThinkingBlock or ToolBlock
                        return toolName === 'mcp_sequentialthinking' || toolName.includes('thinking') ?
                            <ThinkingBlock key={index} isDarkMode={isDarkMode} isStreaming={isStreaming}>{toolContent}</ThinkingBlock> :
                            <ToolBlock key={index} toolName={`${toolName}|${displayHeader}`} content={toolContent} isDarkMode={isDarkMode} />;
                    }

                    // Handle thinking block tokens
                    if (decodedText.startsWith('THINKING_MARKER')) {
                        const thinkingContent = thinkingContentRef?.current || '';
                        return <ThinkingBlock key={index} isDarkMode={isDarkMode} isStreaming={isStreaming}>{thinkingContent}</ThinkingBlock>;
                    }

                    // Handle math expressions in text tokens
                    if (decodedText.includes('⟨MATH_INLINE:')) {
                        const parts = decodedText.split(/(⟨MATH_INLINE:[\s\S]*?⟩)/);
                        return (
                            <>
                                {parts.map((part, i) => {
                                    if (part.startsWith('⟨MATH_INLINE:')) {
                                        const math = part.slice('⟨MATH_INLINE:'.length, -1).trim();
                                        return <MathRenderer key={i} math={math} displayMode={false} />;
                                    }
                                    return part || null;
                                })}
                            </>
                        );
                    }

                    // Check if this 'text' token has nested inline tokens (like strong, em, etc.)
                    if (tokenWithText.tokens && tokenWithText.tokens.length > 0) {
                        // If it has nested tokens, render them recursively
                        return renderTokens(tokenWithText.tokens, enableCodeApply, isDarkMode, isSubRender, isStreaming, thinkingContentRef, onOpenShellConfig);
                    } else {
                        // Otherwise, just render the decoded text content directly
                        return decodedText; // Direct text rendering prevents JSX interpretation
                    }

                // --- Handle Inline Markdown Elements (Recursively) ---
                case 'strong':
                    return <strong key={index}>{renderTokens((token as Tokens.Strong).tokens || [], enableCodeApply, isDarkMode, isSubRender, isStreaming, thinkingContentRef, onOpenShellConfig)}</strong>;
                case 'em':
                    return <em key={index}>{renderTokens((token as Tokens.Em).tokens || [], enableCodeApply, isDarkMode, isSubRender, isStreaming, thinkingContentRef, onOpenShellConfig)}</em>;
                case 'codespan':
                    if (!hasText(tokenWithText)) return null;
                    const decodedCode = decodeHtmlEntities(tokenWithText.text);
                    // Use text content instead of dangerouslySetInnerHTML to prevent HTML parsing issues
                    return <code key={index}>{decodedCode}</code>;
                case 'br':
                    return <br key={index} />;
                case 'del':
                    return <del key={index}>{renderTokens((token as Tokens.Del).tokens || [], enableCodeApply, isDarkMode, isSubRender, isStreaming, thinkingContentRef, onOpenShellConfig)}</del>;

                case 'link':
                    const linkToken = token as Tokens.Link;
                    return <a key={index} href={linkToken.href} title={linkToken.title ?? undefined}>{renderTokens(linkToken.tokens || [], enableCodeApply, isDarkMode, isSubRender, isStreaming, thinkingContentRef, onOpenShellConfig)}</a>;

                case 'escape':
                    if (!hasText(tokenWithText)) return null;
                    return decodeHtmlEntities(tokenWithText.text || '');

                case 'image':
                    const imageToken = token as Tokens.Image;
                    // Check if href is a valid URL or path before rendering
                    const imageHref = imageToken.href || '';
                    // Skip rendering if href is empty or contains template literals
                    if (!imageHref || imageHref.includes('{') || imageHref.includes('}')) {
                        console.warn('Invalid image URL detected:', imageHref);
                        return <span key={index}>[Invalid image: {imageToken.text || 'No description'}]</span>;
                    }
                    // Only render valid image URLs
                    return <img key={index} src={imageHref} alt={imageToken.text || ''} title={imageToken.title ?? undefined} />;

                // --- Other Block Types ---
                case 'heading':
                    const headingToken = token as Tokens.Heading;
                    const Tag = `h${headingToken.depth}` as keyof JSX.IntrinsicElements;
                    return <Tag key={index}>{renderTokens(headingToken.tokens || [], enableCodeApply, isDarkMode, isSubRender, isStreaming, thinkingContentRef, onOpenShellConfig)}</Tag>;
                case 'hr':
                    return <hr key={index} />;
                case 'blockquote':
                    return <blockquote key={index}>{renderTokens((token as Tokens.Blockquote).tokens || [], enableCodeApply, isDarkMode, isSubRender, isStreaming, thinkingContentRef, onOpenShellConfig)}</blockquote>;
                case 'space': // Usually ignored
                    return null;

                // --- Fallback ---
                case 'unknown':
                default:
                    console.warn("Unhandled token type in renderTokens switch:", token.type, token);
                    // Attempt to render raw text if available
                    return hasText(tokenWithText) ? <span key={index}>{decodeHtmlEntities(tokenWithText.text || '')}</span> : null;
            }
        } catch (error) {
            console.error(`Error rendering token index ${index} (type: ${token.type}):`, error);
            // Fallback for errors during rendering a specific token
            return <div key={index} style={{ color: 'red' }}>[Error rendering content]</div>;
        }
    });
};

// Function to split multi-file diffs into separate diffs
const splitMultiFileDiffs = (diffText: string): string[] => {
    if (!diffText) return [];

    // Regular expression to match the start of a new file diff
    const fileHeaderRegex = /^diff --git .*$/m;

    // If the diff doesn't start with a proper header, return it as is
    if (!diffText.match(fileHeaderRegex)) {
        return [diffText];
    }

    const fileDiffs: string[] = [];
    let currentDiff = '';
    let lines = diffText.split('\n');

    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];

        // If we find a new file header and we already have content,
        // save the current diff and start a new one
        if (line.match(fileHeaderRegex) && currentDiff) {
            fileDiffs.push(currentDiff);
            currentDiff = line;
        } else {
            // Add the line to the current diff
            currentDiff = currentDiff ? `${currentDiff}\n${line}` : line;
        }
    }

    // Add the last diff if there's any content
    if (currentDiff) {
        fileDiffs.push(currentDiff);
    }

    return fileDiffs;
};

// Function to handle multi-file diffs with proper recursive rendering
const renderMultiFileDiff = (token: TokenWithText, index: number, enableCodeApply: boolean, isDarkMode: boolean, onOpenShellConfig?: () => void): JSX.Element => {
    // Split the diff into separate file diffs
    const fileDiffs = splitMultiFileDiffs(token.text);

    // If there's only one diff or splitting failed, render as a single diff
    if (fileDiffs.length <= 1) {
        return (
            <DiffToken
                key={index}
                token={{ ...token, text: token.text.trim() }}
                index={index}
                enableCodeApply={enableCodeApply}
                isDarkMode={isDarkMode}
            />
        );
    }

    // Render each file diff as a complete component with its own controls
    return (
        <div key={index} className="multi-file-diff">
            {fileDiffs.map((diffContent, fileIndex) => {
                // Create a stable key for each file diff
                const stableKey = `diff-${index}-file-${fileIndex}`;

                // Wrap each diff in markdown code block syntax for proper rendering
                const wrappedDiff = `\`\`\`diff\n${diffContent}\n\`\`\``;

                return (
                    <div key={stableKey} className="multi-file-diff-container" style={{ marginBottom: '20px' }}>
                        {/* Use a separate MarkdownRenderer instance for each file diff */}
                        <MarkdownRenderer
                            markdown={wrappedDiff}
                            enableCodeApply={enableCodeApply}
                            onOpenShellConfig={onOpenShellConfig}
                            forceRender={true}
                            // isMarkdownBlockClosed will be true by default for sub-renders if they get a complete diff
                            isSubRender={true}
                        />
                    </div>
                );
            })}
        </div>
    );
};

interface MarkdownRendererProps {
    markdown: string;
    isStreaming?: boolean;
    enableCodeApply: boolean;
    forceRender?: boolean;
    isSubRender?: boolean; // Add flag to prevent infinite recursion
    onOpenShellConfig?: () => void;
}

// Configure marked options
const markedOptions = {
    gfm: true,
    breaks: false,
    pedantic: false
};

// Math rendering component
const MathRenderer: React.FC<{ math: string; displayMode: boolean }> = ({ math, displayMode }) => {
    const [katex, setKatex] = useState<any>(null);
    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
        const loadKatex = async () => {
            try {
                const katexModule = await import('katex');
                setKatex(katexModule);
            } catch (error) {
                console.warn('Failed to load KaTeX:', error);
            } finally {
                setIsLoading(false);
            }
        };
        loadKatex();
    }, []);

    if (isLoading || !katex) {
        // Fallback while KaTeX is loading or if it fails
        return displayMode ?
            <div className="math-fallback" style={{ fontFamily: 'monospace', padding: '4px' }}>{math}</div> :
            <span className="math-fallback" style={{ fontFamily: 'monospace' }}>{math}</span>;
    }

    try {
        const html = katex.renderToString(math, {
            displayMode,
            throwOnError: false,
            strict: false,
            errorColor: '#cc0000',
            macros: {
                "\\f": "#1f(#2)"
            }
        });

        return displayMode ?
            <div className="math-display" dangerouslySetInnerHTML={{ __html: html }} /> :
            <span className="math-inline" dangerouslySetInnerHTML={{ __html: html }} />;
    } catch (error) {
        // Silently handle math errors and render as plain text instead of showing error
        console.debug('KaTeX rendering error (handled):', error);
        return displayMode ?
            <div className="math-fallback" style={{ fontFamily: 'monospace', padding: '4px' }}>{math}</div> :
            <span className="math-fallback" style={{ fontFamily: 'monospace' }}>{math}</span>;
    }
};

/**
 * Detects and normalizes indented diff blocks that Gemini sometimes produces
 * @param content The markdown content to process
 * @returns Normalized content with indented diffs fixed
 */
const normalizeIndentedDiffs = (content: string): string => {
    const lines = content.split('\n');
    const result: string[] = [];
    let i = 0;

    while (i < lines.length) {
        const line = lines[i];

        // Look for indented diff headers (common patterns from Gemini)
        const indentedDiffMatch = line.match(/^(\s{4,})```diff$/);
        if (indentedDiffMatch) {
            const indentLevel = indentedDiffMatch[1].length;
            console.log(`Found indented diff block with ${indentLevel} spaces of indentation`);

            // Add the diff header without indentation
            result.push('```diff');
            i++;

            // Process the diff content, removing the same amount of indentation
            while (i < lines.length) {
                const diffLine = lines[i];

                // Check for end of diff block
                if (diffLine.match(/^\s*```\s*$/)) {
                    result.push('```');
                    i++;
                    break;
                }

                // Remove the indentation from diff content lines
                if (diffLine.startsWith(' '.repeat(indentLevel))) {
                    // Remove exactly the same amount of indentation as the opening ```diff
                    const normalizedLine = diffLine.substring(indentLevel);
                    result.push(normalizedLine);
                } else if (diffLine.trim() === '') {
                    // Preserve empty lines
                    result.push('');
                } else {
                    // Line has less indentation than expected - might be end of block or malformed
                    result.push(diffLine);
                }
                i++;
            }
        } else {
            result.push(line);
            i++;
        }
    }

    return result.join('\n');
};

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = memo(({ markdown, enableCodeApply, isStreaming: externalStreaming = false, forceRender = false, isSubRender = false, onOpenShellConfig }) => {
    const { isStreaming } = useChatContext();
    const { isDarkMode } = useTheme();
    const containerRef = useRef<HTMLDivElement>(null);

    // All refs declared at the top to ensure they're in scope for useMemo
    const previousTokensRef = useRef<(Tokens.Generic | TokenWithText)[]>([]);
    const parseTimeoutRef = useRef<NodeJS.Timeout>();
    const markdownRef = useRef<string>(markdown);
    const thinkingRenderedRef = useRef(false);
    const thinkingContentRef = useRef<string>('');

    // State for the tokens that are currently displayed with stable reference
    const [displayTokens, setDisplayTokens] = useState<(Tokens.Generic | TokenWithText)[]>([]);
    const isStreamingState = isStreaming;

    // Memoize the parsing of markdown into tokens.
    // This is critical for stability - we need to ensure tokens don't change unnecessarily
    const lexedTokens = useMemo(() => {
        if (!markdown?.trim()) {
            clearTimeout(parseTimeoutRef.current);
            return previousTokensRef.current.length > 0 ? previousTokensRef.current : [];
        }

        try {
            // Reset thinking refs only when content actually shrinks (indicating a new message)
            // Don't reset when content is just growing during streaming
            if (markdownRef.current && markdown.length < markdownRef.current.length) {
                thinkingRenderedRef.current = false;
                thinkingContentRef.current = '';
            }
            markdownRef.current = markdown;
            // During streaming, if we already have a diff being rendered, keep it stable
            let processedMarkdown = markdown;
            // Pre-process indented diff blocks before any other processing
            processedMarkdown = normalizeIndentedDiffs(processedMarkdown);

            // Pre-process HTML comment tool blocks to prevent marked.js from fragmenting them
            // This handles cases where marked doesn't recognize them as 'html' tokens
            try {
                // Strip out TOOL_MARKER comments before rendering
                // These are internal anchors used by chatApi.ts for replacement logic
                // and should never be visible to users
                processedMarkdown = processedMarkdown.replace(/<!-- TOOL_MARKER:[^>]+ -->\n?/g, '');
                
                const toolBlockRegex = /<!-- TOOL_BLOCK_START:(mcp_\w+)\|(.+?) -->\s*([\s\S]*?)\s*<!-- TOOL_BLOCK_END:\1 -->/g;
                const toolBlocks: Array<{ match: string, toolName: string, displayHeader: string, content: string }> = [];

                let match;
                while ((match = toolBlockRegex.exec(processedMarkdown)) !== null) {
                    toolBlocks.push({
                        match: match[0],
                        toolName: match[1],
                        displayHeader: match[2],
                        content: match[3]
                    });
                }

                // Replace tool blocks with markdown code blocks using a special lang tag
                toolBlocks.forEach(({ match, toolName, displayHeader, content }) => {
                    processedMarkdown = processedMarkdown.replace(
                        match,
                        `\`\`\`tool:${toolName}|${displayHeader}\n${content}\n\`\`\``
                    );
                });
            } catch (toolBlockError) {
                console.debug('Tool block preprocessing error (handled):', toolBlockError);
            }

            // Ensure blank line before code fences in all problematic cases
            // Marked.js requires blank lines before code blocks for proper parsing

            // Fix 0: Code fence immediately after bold/emphasis markers (e.g., "**text**\n```language")
            processedMarkdown = processedMarkdown.replace(
                /(\*\*[^*]+\*\*|\*[^*]+\*|__[^_]+__|_[^_]+_)\n(```[a-zA-Z0-9_-]*)/gm,
                '$1\n\n$2'
            );

            // Fix 0b: Code fence after any markdown formatting without blank line
            processedMarkdown = processedMarkdown.replace(/(\*\*)\n(```)/g, '$1\n\n$2');

            // Fix 1: Code fence on same line as heading (e.g., "### Title ```language")
            processedMarkdown = processedMarkdown.replace(
                /(^#{1,6}\s+[^\n\`]+?)\s+(\`\`\`[a-zA-Z0-9_-]*)/gm,
                '$1\n\n$2'
            );

            // Fix 2: Code fence immediately after numbered list (e.g., "1. Item ```language")
            processedMarkdown = processedMarkdown.replace(
                /(\d+\.\s+[^\n\`]+?)\s+(\`\`\`[a-zA-Z0-9_-]*)/gm,
                '$1\n\n$2'
            );

            // Also fix after paragraphs or text that directly precedes code fences
            processedMarkdown = processedMarkdown.replace(/([^\n])\n(\`\`\`[a-zA-Z0-9_-]*)/g, '$1\n\n$2');

            // Don't process empty or whitespace-only markdown during streaming
            if (isStreamingState && (!processedMarkdown || processedMarkdown.trim() === '')) {
                return previousTokensRef.current.length > 0 ? previousTokensRef.current : [];
            }

            // Pre-process tool calls to handle both <n> and <name> formats
            const toolCallMatch = parseToolCall(processedMarkdown);
            if (toolCallMatch) {
                // Replace the tool call with a formatted display version
                const formattedToolCall = formatToolCallForDisplay(toolCallMatch);
                processedMarkdown = processedMarkdown.replace(
                    /<TOOL_SENTINEL>[\s\S]*?<\/TOOL_SENTINEL>/,
                    formattedToolCall
                );
            } else if (isStreamingState && processedMarkdown.includes('<TOOL_SENTINEL>')) {
                // During streaming, if we have an incomplete tool call, don't try to parse it yet
                // This prevents showing malformed content while the tool call is being streamed
                const incompleteToolMatch = processedMarkdown.match(/<TOOL_SENTINEL>[\s\S]*$/);
                if (incompleteToolMatch && !processedMarkdown.includes('</TOOL_SENTINEL>')) {
                    // Remove the incomplete tool call from display until it's complete
                    processedMarkdown = processedMarkdown.replace(/<TOOL_SENTINEL>[\s\S]*$/, '');
                    // Add a placeholder to show tool execution is starting
                    processedMarkdown += '\n\n🔧 Preparing tool execution...\n';
                }
            }

            // Pre-process thinking content to extract and handle separately (only once)
            if (!thinkingRenderedRef.current) {
                const thinkingMatch = parseThinkingContent(processedMarkdown);
                if (thinkingMatch) {
                    // Store thinking content in ref IMMEDIATELY
                    thinkingContentRef.current = thinkingMatch.content;
                    thinkingRenderedRef.current = true;
                    // Remove thinking tags from main content
                    processedMarkdown = removeThinkingTags(processedMarkdown);
                    // Add simple marker at the beginning
                    processedMarkdown = `THINKING_MARKER\n\n${processedMarkdown}`;
                }
            } else {
                // Remove thinking tags from subsequent renders
                processedMarkdown = removeThinkingTags(processedMarkdown);
            }

            // Pre-process tool blocks to clean up ONLY duplicate tool markers
            processedMarkdown = processedMarkdown.replace(/```tool:(mcp_\w+)\n```tool:\1/g, '```tool:$1');

            // CRITICAL FIX: Escape backticks inside diff code blocks before markdown parsing
            // This prevents backticks in diff content from being interpreted as fence delimiters
            processedMarkdown = processedMarkdown.replace(
                /```diff\n([\s\S]*?)```/g,
                (match, diffContent) => {
                    // Only escape fence sequences (3+ consecutive backticks) to prevent breaking out of diff block
                    // Single/double backticks are safe and should remain unescaped for proper rendering
                    const escapedContent = diffContent.replace(/```+/g, (fence) => '&#96;'.repeat(fence.length));
                    return `\`\`\`diff\n${escapedContent}\`\`\``;
                }
            );

            // Also handle multi-fence diff blocks (````)
            processedMarkdown = processedMarkdown.replace(
                /````diff\n([\s\S]*?)````/g,
                (match, diffContent) => {
                    // Only escape fence sequences (3+ consecutive backticks)
                    const escapedContent = diffContent.replace(/```+/g, (fence) => '&#96;'.repeat(fence.length));
                    return `\`\`\`\`diff\n${escapedContent}\`\`\`\``;
                }
            );

            // First check if this is a diff or code block that shouldn't have math processing
            const isDiff = processedMarkdown.includes('diff --git') ||
                (processedMarkdown.includes('```diff') && processedMarkdown.includes('+++')) ||
                (processedMarkdown.match(/^---\s+\S+/m) && processedMarkdown.match(/^\+\+\+\s+\S+/m)) ||
                // Skip processing for content containing tool sentinels or template variables
                // TODO: Get actual sentinel values from backend instead of hardcoding
                processedMarkdown.includes('<TOOL_SENTINEL>') ||
                processedMarkdown.includes('</TOOL_SENTINEL>');

            // Check for template variables separately, but exclude LaTeX commands
            const hasTemplateVars = !processedMarkdown.includes('\\') && /\{[A-Z_][A-Z_0-9]*\}/g.test(processedMarkdown);

            // Only process math expressions if this doesn't look like a diff
            if (!isDiff && !hasTemplateVars) {
                try {
                    // Split the markdown into code blocks and non-code blocks
                    const segments = processedMarkdown.split(/(```[^\n]*\n[\s\S]*?```)/g);

                    // Process each segment separately
                    processedMarkdown = segments.map((segment, index) => {
                        // Skip math processing for code blocks (odd indices in the split)
                        if (index % 2 === 1 && segment.startsWith('```')) {
                            return segment;
                        }

                        // Process math only in non-code segments
                        let processed = segment;

                        try {
                            // Handle display math $$...$$
                            processed = processed.replace(
                                /\$\$([\s\S]+?)\$\$/g,
                                '\n<div class="math-display-block">MATH_DISPLAY:$1</div>\n'
                            );

                            // Handle inline math $...$
                            processed = processed.replace(
                                /\$([^$\n]+?)\$/g,
                                (match, p1) => {
                                    // Skip processing if this looks like a regex replacement ($1, $2, etc.)
                                    if (/^\d+$/.test(p1.trim())) {
                                        return match; // Keep $1, $2, etc. as is
                                    }

                                    // Skip processing if this is inside code-like contexts
                                    const surroundingText = match.substring(0, 50) + match.substring(match.length - 50);
                                    if (surroundingText.includes('replace(') ||
                                        surroundingText.includes('processedDef') ||
                                        surroundingText.includes('regex') ||
                                        surroundingText.includes('command') ||
                                        surroundingText.includes('shell')) {
                                        return match; // Keep as is in code contexts
                                    }

                                    // Only treat as math if it contains LaTeX commands or mathematical symbols
                                    const hasLatex = /\\[a-zA-Z]+/.test(p1); // \frac, \sqrt, \alpha, etc.
                                    const hasMathSymbols = /[∫∑∏√∞≠≤≥±∓∈∉⊂⊃∪∩αβγδεζηθικλμνξοπρστυφχψω]/.test(p1);
                                    const hasComplexMath = /[{}^_]/.test(p1) && p1.length > 2; // Subscripts, superscripts, braces

                                    // Be more conservative - only process if it really looks like math
                                    return (hasLatex || hasMathSymbols || hasComplexMath) ? `⟨MATH_INLINE:${p1.trim()}⟩` : match;
                                }
                            );
                        } catch (mathError) {
                            console.debug('Math processing error (handled):', mathError);
                            // Return original segment if math processing fails
                            return segment;
                        }

                        return processed;
                    }).join('');
                } catch (mathProcessingError) {
                    console.debug('Math segment processing error (handled):', mathProcessingError);
                    // Continue without math processing if there's an error
                }
            }

            // Pre-process MathML blocks to prevent fragmentation
            const mathMLRegex = /<math[^>]*>[\s\S]*?<\/math>/gi;
            const mathMLBlocks = processedMarkdown.match(mathMLRegex);

            if (mathMLBlocks) {
                mathMLBlocks.forEach((mathBlock, _index) => {
                    // Add namespace if missing and wrap in a way that preserves it as a single token
                    const mathWithNamespace = mathBlock.includes('xmlns=')
                        ? mathBlock
                        : mathBlock.replace('<math', '<math xmlns="http://www.w3.org/1998/Math/MathML"');

                    // Replace with a placeholder that won't be fragmented
                    processedMarkdown = processedMarkdown.replace(mathBlock, `<div class="mathml-block">${mathWithNamespace}</div>`);
                });
            }

            const lexedTokens = marked.lexer(processedMarkdown, markedOptions);
            return lexedTokens as (Tokens.Generic | TokenWithText)[] || [];
        } catch (error) {
            // Don't create fallback code blocks for empty content
            if (!markdown || markdown.trim() === '') {
                return [];
            }
            console.error("Error lexing markdown:", error);
            // Fallback to rendering the raw markdown in a code block on error
            return [{ type: 'code', lang: 'text', text: markdown }] as TokenWithText[];
        }
    }, [markdown, externalStreaming, isStreamingState]);

    // Cleanup timeout on unmount
    useEffect(() => {
        return () => clearTimeout(parseTimeoutRef.current);
    }, []);

    // Update tokens state when the memoized tokens change
    useEffect(() => {
        // Always update immediately for live streaming
        if (lexedTokens.length > 0) {
            previousTokensRef.current = lexedTokens;
            setDisplayTokens(lexedTokens);
        }
    }, [lexedTokens]); // Remove streaming state dependency for immediate updates

    // Only memoize the rendered content when not streaming or when streaming completes
    const renderedContent = useMemo(() => {
        return renderTokens(displayTokens, enableCodeApply, isDarkMode, isSubRender, isStreaming, thinkingContentRef, onOpenShellConfig);
    }, [displayTokens, enableCodeApply, isDarkMode, forceRender, isSubRender]); // Use forceRender to trigger re-renders

    // Attach event listeners to throttle retry buttons after render
    const { currentConversationId, currentMessages, addMessageToConversation, streamedContentMap,
        setStreamedContentMap, setIsStreaming, removeStreamingConversation, streamingConversations,
        updateProcessingState, addStreamingConversation, throttlingRecoveryData,
        setThrottlingRecoveryData } = useChatContext();
    const { checkedKeys } = useFolderContext();

    // Track attached handlers to prevent duplicates
    const attachedHandlersRef = useRef<Set<Element>>(new Set());

    // Separate function to attach handler with proper closure
    const attachThrottleRetryHandler = useCallback((button: HTMLButtonElement) => {
        const conversationId = button.getAttribute('data-conversation-id');
        const throttleWait = button.getAttribute('data-throttle-wait');

        if (!conversationId) return;

        console.log(`✅ Attaching throttle retry handler to button for conversation: ${conversationId}`);

        const handleClick = async () => {
            console.log('🔄 RETRY: User clicked retry button after throttling');

            // Disable button and show loading state
            button.disabled = true;
            const originalText = button.textContent;
            button.textContent = '⏳ Retrying...';

            try {
                const recoveryData = throttlingRecoveryData.get(conversationId);
                const lastUserMessage = currentMessages.filter(msg => msg.role === 'human').pop();

                if (!lastUserMessage) {
                    message.error('No message to retry');
                    button.disabled = false;
                    button.textContent = originalText;
                    return;
                }

                const messagesForRetry = [...currentMessages.filter(msg => !msg.muted)];

                if (recoveryData?.toolResults && recoveryData.toolResults.length > 0) {
                    recoveryData.toolResults.forEach((toolResult, index) => {
                        messagesForRetry.push({
                            role: 'assistant',
                            content: `Tool execution result ${index + 1}:\n\`\`\`tool:result\n${toolResult}\n\`\`\``,
                            _timestamp: Date.now(),
                            _isToolResult: true
                        });
                    });
                }

                addStreamingConversation(conversationId);
                await sendPayload(
                    messagesForRetry,
                    lastUserMessage.content,
                    checkedKeys as string[],
                    conversationId,
                    streamedContentMap,
                    setStreamedContentMap,
                    setIsStreaming,
                    removeStreamingConversation,
                    addMessageToConversation,
                    streamingConversations.has(conversationId),
                    (state) => updateProcessingState(conversationId, state)
                );

                const next = new Map(throttlingRecoveryData);
                next.delete(conversationId);
                setThrottlingRecoveryData(next);
            } catch (error) {
                console.error('Retry failed:', error);
                message.error('Failed to retry request');
                button.disabled = false;
                button.textContent = originalText;
            }
        };

        button.addEventListener('click', handleClick);
    }, [currentMessages, throttlingRecoveryData, checkedKeys, streamedContentMap,
        setStreamedContentMap, setIsStreaming, removeStreamingConversation,
        addMessageToConversation, streamingConversations, updateProcessingState,
        addStreamingConversation, setThrottlingRecoveryData]);

    // Helper to attach handlers to all buttons
    const attachAllThrottleHandlers = useCallback(() => {
        const allButtons = document.querySelectorAll('.throttle-retry-button');
        console.log(`🔍 GLOBAL-ATTACH: Found ${allButtons.length} throttle buttons`);
        allButtons.forEach(button => {
            if (!attachedHandlersRef.current.has(button)) {
                attachThrottleRetryHandler(button as HTMLButtonElement);
                attachedHandlersRef.current.add(button);
            }
        });
    }, [attachThrottleRetryHandler]);

    // Setup MutationObserver to watch for dynamically added throttle buttons
    useLayoutEffect(() => {
        if (!containerRef.current) return;

        // Listen for throttling recovery data
        const handleThrottlingRecoveryData = (event: CustomEvent) => {
            const { conversationId, toolResults, partialContent } = event.detail;

            if (conversationId && toolResults) {
                console.log('📦 RECOVERY_DATA: Storing tool results for conversation:', conversationId);
                const next = new Map(throttlingRecoveryData);
                next.set(conversationId, { toolResults, partialContent });
                setThrottlingRecoveryData(next);
            }
        };

        document.addEventListener('throttlingRecoveryData', handleThrottlingRecoveryData as EventListener);

        return () => {
            document.removeEventListener('throttlingRecoveryData', handleThrottlingRecoveryData as EventListener);
        };

        const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                mutation.addedNodes.forEach((node) => {
                    if (node.nodeType === Node.ELEMENT_NODE) {
                        const element = node as Element;

                        // Check if this node or its children contain throttle buttons
                        const buttons = element.classList?.contains('throttle-retry-button')
                            ? [element]
                            : Array.from(element.querySelectorAll?.('.throttle-retry-button') || []);

                        buttons.forEach((button: Element) => {
                            if (!attachedHandlersRef.current.has(button)) {
                                attachThrottleRetryHandler(button as HTMLButtonElement);
                                attachedHandlersRef.current.add(button);
                            }
                        });
                    }
                });
            });
        });

        // Start observing
        if (containerRef.current) {
            const container = containerRef.current!;
            observer.observe(container as Node, {
                childList: true,
                subtree: true
            });

            // Also check for any existing buttons when effect runs
            const existingButtons = container.querySelectorAll('.throttle-retry-button');
            existingButtons.forEach(button => {
                if (!attachedHandlersRef.current.has(button)) {
                    attachThrottleRetryHandler(button as HTMLButtonElement);
                    console.log('✅ INITIAL-ATTACH: Handler attached to existing button');
                }
            });

            // CRITICAL FIX: Check globally for buttons that might be outside containerRef
            setTimeout(attachAllThrottleHandlers, 100);
            setTimeout(attachAllThrottleHandlers, 500);
        }

        return () => {
            observer.disconnect();
            attachedHandlersRef.current.clear();
        };
    }, [containerRef.current, currentConversationId, attachThrottleRetryHandler, attachAllThrottleHandlers]);

    const isMultiFileDiff = markdown?.includes('diff --git') && markdown.split('diff --git').length > 2;
    return isMultiFileDiff && !isSubRender && displayTokens.length === 1 && displayTokens[0].type === 'code' && (displayTokens[0] as TokenWithText).lang === 'diff' ?
        renderMultiFileDiff(displayTokens[0] as TokenWithText, 0, enableCodeApply, isDarkMode, onOpenShellConfig) :
        <div ref={containerRef}>{renderedContent}</div>;
}, (prevProps, nextProps) => prevProps.markdown === nextProps.markdown && prevProps.enableCodeApply === nextProps.enableCodeApply);
// Note: forceRender prop is intentionally not included in the memo comparison to ensure re-rendering during streaming

const cleanDiffContent = (content: string): string => {
    const lines = content.split('\n');
    const cleanedLines = lines.map(line => {
        // Preserve diff headers unchanged
        if (line.startsWith('diff --git') ||
            line.startsWith('index ') ||
            line.startsWith('--- ') ||
            line.startsWith('+++ ') ||
            line.startsWith('@@ ')) {
            return line;
        }

        // Fix any MATH_INLINE expansions that might have slipped through
        // This handles cases like $1 in regex replacements being converted to ⟨MATH_INLINE:1⟩
        if (line.includes('⟨MATH_INLINE:')) {
            // Replace ⟨MATH_INLINE:1⟩ with $1, ⟨MATH_INLINE:2⟩ with $2, etc.
            line = line.replace(/⟨MATH_INLINE:(\d+)⟩/g, '$$1');
        }

        // Handle offset diff format lines
        // Pattern: optional leading spaces + optional +/- + [number + optional modifier] + space + content
        // Examples: [001 ], [002+], [003*], [004,+], +[005 ], -[006 ]
        const offsetMatch = line.match(/^(\s*)([+-]?)?\[(\d+)([+*,\s]*)\]\s(.*)⟩/);
        if (offsetMatch) {
            const [_, leadingSpace, diffMarker, lineNum, modifier, content] = offsetMatch;

            // Determine the actual diff marker based on the modifier or explicit marker
            let actualMarker = '';
            if (diffMarker) {
                // Explicit +/- before the bracket
                actualMarker = diffMarker;
            } else if (modifier.includes('+')) {
                // [NNN+] format - addition
                actualMarker = '+';
            } else if (modifier.includes('*')) {
                // [NNN*] format - modification (treat as context)
                actualMarker = ' ';
            } else {
                // [NNN ] format - context line
                actualMarker = ' ';
            }

            return `${actualMarker}${content}`;
        }

        // Handle lines that might have been partially processed or malformed
        const simpleOffsetMatch = line.match(/^\s*\[(\d+)[+*\s]*\]\s*(.*)$/);
        if (simpleOffsetMatch) {
            const [_, lineNum, content] = simpleOffsetMatch;
            return ` ${content}`;
        }

        // Return line unchanged if no offset format detected
        return line;
    });
    return cleanedLines.join('\n');
};

export default MarkdownRenderer;
