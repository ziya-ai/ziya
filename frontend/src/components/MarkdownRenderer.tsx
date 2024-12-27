import React, { useState, useEffect, memo, Suspense } from 'react';
import { parseDiff, Diff, Hunk, tokenize, RenderToken } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import { marked, Tokens } from 'marked';
import { Button, message, Radio, Space, Spin } from 'antd';
import 'prismjs/themes/prism-tomorrow.css';  // Add dark theme support
import * as Viz from '@viz-js/viz';
import { CheckOutlined, CodeOutlined } from '@ant-design/icons';
import 'prismjs/themes/prism.css';
import { loadPrismLanguage, isLanguageLoaded } from '../utils/prismLoader';


import { useTheme } from '../context/ThemeContext';

import type * as PrismType from 'prismjs';


declare global {
    interface Window {
        Prism: typeof PrismType;
    }
}

// Define table-specific interfaces
interface TableToken extends BaseToken {
    type: 'table';
    header: TokenWithText[];
    align: Array<'left' | 'right' | 'center' | null>;
    rows: TokenWithText[][];
}

// Define list-specific interface
interface ListToken extends BaseToken {
    type: 'list';
    items: TokenWithText[];
    ordered?: boolean;
    start?: number;
    loose: boolean;
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
}

interface ErrorBoundaryProps {
    children: React.ReactNode;
    fallback?: React.ReactNode;
    type?: 'graphviz' | 'code';
}
 
interface ErrorBoundaryState {
    hasError: boolean;
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

    componentDidCatch(error, errorInfo) {
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

const GraphvizRenderer: React.FC<{ dot: string }> = ({ dot }) => {
    const { isDarkMode } = useTheme();
    const [svg, setSvg] = useState<string>('');
    const [error, setError] = useState<string | null>(null);
    const [isValidDot, setIsValidDot] = useState<boolean>(false);

    useEffect(() => {
        const renderGraph = async () => {
            try {
		// First validate DOT syntax
                if (!dot.trim().startsWith('digraph') && !dot.trim().startsWith('graph')) {
                    setError('Invalid DOT syntax');
                    setIsValidDot(false);
                    return;
                }

                // Check for incomplete DOT syntax (missing closing brace)
                if (!dot.includes('}')) {
                    setIsValidDot(false);
                    setSvg('');
                    return;
                }

                const dotSource = dot.trim();
                const themedDot = isDarkMode ?
                    dotSource.replace(/^(digraph|graph)\s+(.+?)\s*{/,
                        '$1 $2 {\n' +
                        '  bgcolor="transparent";\n' +
                        '  node [style="filled", fillcolor="#1f1f1f", color="#e6e6e6", fontcolor="#e6e6e6"];\n' +
                        '  edge [color="#e6e6e6", fontcolor="#e6e6e6"];\n' +
                        '  graph [bgcolor="transparent", color="#e6e6e6", fontcolor="#e6e6e6"];\n'
                    )
                    : dotSource;

		const instance = await Viz.instance();
                const result = await instance.renderString(dot, {
                    engine: 'dot',
                    format: 'svg'
                });
                setSvg(result);
		setIsValidDot(true);
                setError(null);
            } catch (err) {
		const errorMessage = err instanceof Error
                    ? err.message
                    : 'Failed to render graph';

                console.error('Graphviz rendering error:', errorMessage);
		setIsValidDot(false);
                setError(errorMessage);
            }
        };

        renderGraph();
    }, [dot]);

    if (!isValidDot) {
        return (
	    <ErrorBoundary type="graphviz">
            <div className="graphviz-container" style={{
                display: 'flex',
                justifyContent: 'center',
                alignItems: 'center',
                minHeight: '100px'
            }}>
                <Spin tip="Rendering graph..." size="large">
                    <div className="content" />
                </Spin>
            </div>
	    </ErrorBoundary>
        );
    }


    if (error) {
        return (
            <div className="graphviz-error">
                <p>Error rendering graph: {error}</p>
                <pre><code>{dot}</code></pre>
            </div>
        );
    }

    return (
        <div
            className="graphviz-container borderless"
            dangerouslySetInnerHTML={{ __html: svg }}
            style={{
                maxWidth: '100%',
                overflow: 'auto',
		padding: '1em 0'
            }}
        />
    );
};

interface ApplyChangesButtonProps {
    diff: string;
    filePath: string;
    enabled: boolean;
}

export interface DiffViewProps {
    diff: string;
    viewType: 'split' | 'unified';
    displayMode: 'raw' | 'pretty';
    showLineNumbers: boolean;
}

interface DiffControlsProps {
    displayMode: 'raw' | 'pretty';
    viewType: 'split' | 'unified';
    showLineNumbers: boolean;
    onDisplayModeChange: (mode: 'raw' | 'pretty') => void;
    onViewTypeChange: (type: 'split' | 'unified') => void;
    onLineNumbersChange: (show: boolean) => void;
}

const DiffControls = memo(({
    displayMode,
    viewType,
    showLineNumbers,
    onDisplayModeChange,
    onViewTypeChange,
    onLineNumbersChange
}: DiffControlsProps) => {
    return (
        <div className="diff-view-controls" style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center'
        }}>
            <div>
                {displayMode === 'pretty' && (
                    <Space>
                        <Radio.Group
                            value={viewType}
                            buttonStyle="solid"
                            onChange={(e) => onViewTypeChange(e.target.value)}
                        >
                            <Radio.Button value="unified">Unified View</Radio.Button>
                            <Radio.Button value="split">Split View</Radio.Button>
                        </Radio.Group>

                        <Radio.Group
                            value={showLineNumbers}
                            buttonStyle="solid"
                            onChange={(e) => onLineNumbersChange(e.target.value)}
                        >
                            <Radio.Button value={true}>Show Line Numbers</Radio.Button>
                            <Radio.Button value={false}>Hide Line Numbers</Radio.Button>
                        </Radio.Group>
                    </Space>
                )}
            </div>
            <div>
                <Radio.Group
                    value={displayMode}
                    buttonStyle="solid"
                    onChange={(e) => onDisplayModeChange(e.target.value)}
                >
                    <Radio.Button value="pretty">Pretty</Radio.Button>
                    <Radio.Button value="raw">Raw</Radio.Button>
                </Radio.Group>
            </div>
        </div>
    );
});

const renderFileHeader = (file: ReturnType<typeof parseDiff>[number]): string => {
    if (file.type === 'rename' && file.oldPath && file.newPath) {
        return `Rename: ${file.oldPath} → ${file.newPath}`;
    } else if (file.type === 'delete') {
        return `Delete: ${file.oldPath}`;
    } else if (file.type === 'add') {
        return `Create: ${file.newPath}`;
    } else {
        return `File: ${file.oldPath || file.newPath}`;
    }
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
    if (diff.startsWith('diff --git')) {
        const lines: string[] = diff.split('\n');
        const normalizedLines: string[] = [];

        // Check if this is a properly formatted diff
        const hasDiffHeaders = lines.some(line =>
            (line.startsWith('---') || line.startsWith('+++'))
        );
        const hasHunkHeader = lines.some(line => 
            /^@@\s+-\d+,?\d*\s+\+\d+,?\d*\s+@@/.test(line)
        );

        if (hasDiffHeaders && hasHunkHeader) {
            return diff;  // Return original diff if it's properly formatted
        }
        
        let addCount = 0;
        let removeCount = 0;
        let contextCount = 0;
        
        // Always keep the diff --git line
        normalizedLines.push(lines[0]);

        // Extract file path from diff --git line
        const filePathMatch = lines[0].match(/diff --git a\/(.*?) b\//);
        const filePath = filePathMatch ? filePathMatch[1] : 'unknown';

        // Add headers if missing
        normalizedLines.push(`--- a/${filePath}`);
        normalizedLines.push(`+++ b/${filePath}`);

        // Count lines and collect content
        const contentLines = lines.slice(1).filter(line => {
            if (line.startsWith('diff --git') || line.startsWith('index ')) {
                return false;
            }
            if (line.startsWith('---') || line.startsWith('+++')) {
                return false;
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

        // Add hunk header
        normalizedLines.push(`@@ -1,${removeCount + contextCount} +1,${addCount + contextCount} @@`);

        // Add content lines, preserving +/- and adding spaces for context
        contentLines.forEach(line => {
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

const DiffView: React.FC<DiffViewProps> = ({ diff, viewType, displayMode, showLineNumbers }) => {
    const { isDarkMode } = useTheme();
    let files;
    try {
        files = parseDiff(normalizeGitDiff(diff));
	// Special handling for deletion diffs that might not be parsed correctly
        if (files.length === 0 && isDeletionDiff(diff)) {
            // Force parse as a deletion
            const match = diff.match(/--- a\/(.*)\n/);
            if (match) {
                const filePath = match[1];
                files = [{
                    type: 'delete',
                    oldPath: filePath,
                    hunks: [{
                        content: diff,
                        oldStart: 1,
                        oldLines: diff.split('\n').length - 1,
                        newStart: 0,
                        newLines: 0
                    }]
                }];
            }
        }
    } catch (error) {
        return <pre><code>{diff}</code></pre>;
    } 

    const renderHunks = (hunks) => {
        return hunks.map((hunk, index) => {
            const previousHunk = index > 0 ? hunks[index - 1] : null;
            const showEllipsis = displayMode === 'pretty' && previousHunk &&
                (hunk.oldStart - (previousHunk.oldStart + previousHunk.oldLines) > 1);
            return (
                <React.Fragment key={hunk.content}>
                    {showEllipsis && displayMode === 'pretty' && <div className="diff-ellipsis">...</div>}
                    <Hunk hunk={hunk} />
                </React.Fragment>
            );
        });
    };

    // If raw mode is selected, return the raw diff
    if (displayMode === 'raw') {
        return (
            <pre style={{
                backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                color: isDarkMode ? '#e6e6e6' : 'inherit',
                padding: '10px',
                borderRadius: '4px'
            }}><code>{diff}</code></pre>
        );
    }

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

    const currentTheme = isDarkMode ? darkModeStyles : lightModeStyles;


    return files.map((file, fileIndex) => {  
      return (  
        <div
            key={fileIndex}
            className="diff-view smaller-diff-view"
            style={{
		width: 'auto',
                backgroundColor: currentTheme.content.background,
                color: currentTheme.content.color
            }}
        >
            <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '4px',
                padding: '4px 8px'
            }}>
                <b>{
                    file.type === 'delete'
                        ? `Delete: ${file.oldPath}`
                        : file.type === 'add'
                        ? `Create: ${file.newPath}`
                        : `File: ${file.oldPath || file.newPath}`
                }</b>
                {!['delete', 'rename'].includes(file.type) &&
                    <ApplyChangesButton
                        diff={diff}
                        filePath={file.newPath || file.oldPath}
                        enabled={window.enableCodeApply === 'true'}
                    />
                }
            </div>
	        {file.type === 'delete' ? (
                <Diff
		    viewType={file.type === 'delete' ? 'unified' : viewType}
                    diffType={file.type === 'delete' ? 'modify' : file.type}
                    hunks={file.hunks}
                    gutterType={showLineNumbers ? 'default' : 'none'}
                    className="diff-view"
                >
                    {hunks => renderHunks(hunks)}
                </Diff>
            ) : (
                <Diff
                    viewType={viewType}
                    diffType={file.type}
                    hunks={file.hunks}
                    gutterType={showLineNumbers ? 'default' : 'none'}
                    className="diff-view"
                >
                    {hunks => renderHunks(hunks)}
                </Diff>
            )}
        </div>
        );
    });
};

const ApplyChangesButton: React.FC<ApplyChangesButtonProps> = ({ diff, filePath, enabled }) => {
    const [isApplied, setIsApplied] = useState(false);

    const handleApplyChanges = async () => {
        try {
            const response = await fetch('/api/apply-changes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ diff, filePath }),
            });
            if (response.ok) {
                setIsApplied(true);
                message.success(`Changes applied to ${filePath}`);
            } else {
                message.error('Failed to apply changes');
            }
        } catch (error) {
            console.error('Error applying changes:', error);
            message.error('Error applying changes');
        }
    };
    return enabled ? <Button onClick={handleApplyChanges} disabled={isApplied} icon={<CheckOutlined />}>Apply Changes (beta)</Button> : null;
};

const hasText = (token: any): token is TokenWithText => {
    return 'text' in token;
};

const isCodeToken = (token: TokenWithText): token is TokenWithText & { lang?: string } => {
    return token.type === 'code' && 'text' in token;
};

interface DiffViewWrapperProps {
    token: TokenWithText;
    enableCodeApply: boolean;
    index?: number;
}

const DiffViewWrapper: React.FC<DiffViewWrapperProps> = ({ token, enableCodeApply, index }) => {
    const [viewType, setViewType] = useState<'unified' | 'split'>(window.diffViewType || 'unified');
    const [showLineNumbers, setShowLineNumbers] = useState<boolean>(false);
    const [displayMode, setDisplayMode] = useState<'raw' | 'pretty'>(window.diffDisplayMode || 'pretty');

    if (!hasText(token)) {
        return null;
    }

    if (!isCodeToken(token)) {
        return null;
    }

    return (
	<div className="diff-container">
            <DiffControls
                displayMode={displayMode}
                viewType={viewType}
                showLineNumbers={showLineNumbers}
                onDisplayModeChange={setDisplayMode}
                onViewTypeChange={setViewType}
                onLineNumbersChange={setShowLineNumbers}
            />
            <div id={`diff-view-${index || 0}`}>
                <DiffView
                    diff={token.text}
                    viewType={viewType}
                    displayMode={displayMode}
                    showLineNumbers={showLineNumbers}
            />
            </div>
        </div>
    );
};

// Cache for tracking which languages we've attempted to load
const attemptedLanguages = new Set<string>();

interface CodeBlockProps {
    token: TokenWithText;
    index: number;
}

const CodeBlock: React.FC<CodeBlockProps> = ({ token, index }) => {
    const [isLanguageLoaded, setIsLanguageLoaded] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);
    const { isDarkMode } = useTheme();
    const [prismInstance, setPrismInstance] = useState<typeof PrismType | null>(null);

    useEffect(() => {
        if (token.lang !== undefined && !prismInstance) {
	    const loadLanguage = async () => {
                setIsLanguageLoaded(false);
                try {
                    // Load language and get Prism instance
                    await loadPrismLanguage(token.lang || 'plaintext');
                    setPrismInstance(window.Prism);
                } catch (error: unknown) {
                    const errorMessage = error instanceof Error ? error.message : 'Unknown error';
	            setLoadError(`Error loading language ${token.lang}: ${errorMessage}`);
                    console.error(`Error loading language ${token.lang}:`, error);
		} finally {
                    setIsLanguageLoaded(true);
                }
            };
            loadLanguage();
        } else {
            setIsLanguageLoaded(true);
        }
    }, [token.lang]);

    if (!isLanguageLoaded) {
        return (
            <div style={{ padding: '16px', backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa' }}>
                <Spin size="small" /> Loading syntax highlighting...
            </div>
        );
    }

    const getHighlightedCode = () => {
        if (!prismInstance || token.lang === undefined) {
            return token.text;
        }
        try {
            const grammar = window.Prism.languages[token.lang as string] || window.Prism.languages.plaintext;
            return window.Prism.highlight(token.text, grammar, token.lang);
        } catch (error) {
            console.warn(`Failed to highlight code for language ${token.lang}:`, error);
            return token.text;
        }
    };

    if (!isLanguageLoaded) {
        return (
            <pre style={{
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

    const escapedText = token.text.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return (
        <ErrorBoundary type="code">
            <pre style={{
                padding: '16px',
                borderRadius: '6px',
                overflow: 'auto',
                backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                border: `1px solid ${isDarkMode ? '#303030' : '#e1e4e8'}`
            }}
            className={`language-${token.lang || 'plaintext'}`}
            >
                <code
                        style={{
                            textShadow: 'none',
                            color: isDarkMode ? '#e6e6e6' : '#24292e'
                         }} 
			                     dangerouslySetInnerHTML={{ __html:
                        (prismInstance && typeof token.lang === 'string')
                            ? prismInstance.highlight(
                                escapedText,
                                prismInstance.languages[token.lang as keyof typeof prismInstance.languages] ||
                                prismInstance.languages.plaintext,
                                token.lang as string
                            )
                            : escapedText
                    }}
                />
            </pre>
        </ErrorBoundary>
    );
};


const renderTokens = (tokens: TokenWithText[], enableCodeApply: boolean, isDarkMode: boolean): React.ReactNode[] => {
    return tokens.map((token, index) => {
        if (token.type === 'code' && isCodeToken(token) && token.lang === 'diff') {
            try {
                // Only attempt to parse as diff if it starts with 'diff --git'
                if (token.text.trim().startsWith('diff --git')) {
                    const files = parseDiff(token.text);
                    if (files && files.length > 0) {
                        return (
                            <DiffViewWrapper
                                key={index}
                                token={token}
                                index={index}
                                enableCodeApply={enableCodeApply}
                            />
                        );
                    }
                }
                // If not a valid diff or doesn't start with diff marker, render as regular code
                return (
                    <pre key={index} style={{
                        backgroundColor: '#f6f8fa',
                        padding: '16px',
                        borderRadius: '6px',
                        overflow: 'auto'
                    }}>
                        <code>{token.text}</code>
                    </pre>
                );
            } catch (error) {
                // If parsing fails, render as regular code
                return (
                    <pre key={index} style={{
                        backgroundColor: '#f6f8fa',
                        padding: '16px',
                        borderRadius: '6px',
                        overflow: 'auto'
                    }}>
                        <code>{token.text}</code>
                    </pre>
                );
            }
        }

	// Handle Graphviz diagrams
        if (token.type === 'code' && isCodeToken(token) && token.lang === 'graphviz') {
            try {
		    // First validate the DOT syntax
                if (!token.text.trim().startsWith('digraph') &&
                    !token.text.trim().startsWith('graph')) {
                    return (
                        <div key={index} className="graphviz-error">
                            <p>Invalid Graphviz syntax. Must start with 'digraph' or 'graph'.</p>
                            <pre><code>{token.text}</code></pre>
                        </div>
                    );
                }

                // Add theme-aware styling to the DOT source
                const dotSource = token.text.trim();
                const themedDot = isDarkMode
                    ? dotSource.replace(/^(digraph|graph)\s+(.+?)\s*{/,
                        '$1 $2 {\n' +
                        '  bgcolor="transparent";\n' +
                        '  node [style="filled", fillcolor="#1f1f1f", color="#e6e6e6", fontcolor="#e6e6e6"];\n' +
                        '  edge [color="#e6e6e6", fontcolor="#e6e6e6"];\n' +
                        '  graph [bgcolor="transparent", color="#e6e6e6", fontcolor="#e6e6e6"];\n'
                      )
                    : dotSource;

                // Wrap Graphviz in error boundary
                return (
		    <div key={index} className="graphviz-container borderless" style={{ padding: '1em 0' }}>
                        <GraphvizRenderer dot={themedDot} />
                    </div>
                );
            } catch (error) {
                console.error('Error in Graphviz rendering:', error);
                return (
                    <div key={index} className="graphviz-error">
                        <p>Error rendering diagram</p>
                        <pre><code>{token.text}</code></pre>
                    </div>
                );
            }
        }

        if (token.type === 'code' && isCodeToken(token)) {
            // Regular code blocks (non-diff)
	    return <CodeBlock key={index} token={token as TokenWithText} index={index} />;
        }

        // Handle tables specially
        if (token.type === 'table' && 'header' in token && 'rows' in token) {
            const tableToken = token as unknown as TableToken;
            return (
                <table key={index} style={{
                    borderCollapse: 'collapse',
                    width: '100%',
                    marginBottom: '1em'
                }}>
                    <thead>
                        <tr>
                            {tableToken.header.map((cell, cellIndex) => (
                                <th key={cellIndex} style={{
                                    borderBottom: '2px solid #ddd',
                                    padding: '8px',
                                    textAlign: 'left'
                                }}>
                                    {typeof cell === 'string' ? cell : cell.text}
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {tableToken.rows.map((row, rowIndex) => (
                            <tr key={rowIndex}>
                                {row.map((cell, cellIndex) => (
                                    <td key={cellIndex} style={{
                                        border: '1px solid #ddd',
                                        padding: '8px'
                                    }}>
                                        {typeof cell === 'string' ? cell : cell.text}
                                    </td>
                                ))}
                            </tr>
                        ))}
                    </tbody>
                </table>
            );
        }

        // Handle regular HTML content
        if (token.type === 'html' && 'text' in token) {
            return <div key={index} dangerouslySetInnerHTML={{ __html: token.text }} />;
        }

        // Handle ordered and unordered lists
        if (token.type === 'list' && 'items' in token) {
            const listToken = token as unknown as ListToken;
            const ListTag = listToken.ordered ? 'ol' : 'ul';
            return (
                <ListTag key={index} 
                    start={listToken.ordered ? (listToken.start || 1) : undefined}>
                    {listToken.items.map((item, itemIndex) => {
                        if ('tokens' in item && item.tokens) {
                            // Handle nested content in list items
                            return (
                                <li key={itemIndex}>
                                    {renderTokens(item.tokens, enableCodeApply, isDarkMode)}
                                </li>
                            );
                        }
                        // Handle simple text list items
                        return <li key={itemIndex}>{item.text}</li>;
                    })}
                </ListTag>
            );
        }

        // Handle regular text, only if it has content - wrap with pre tags for safety
        if ('text' in token) {
            const text = token.text || '';
            // Only escape HTML in regular text, not in code blocks
            const escapedText = token.type === 'code'
                ? text  // Leave code blocks exactly as they are
                : text.replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;');

            return text.trim() ?
                (
                <div
                    key={index}
                    style={{ marginBottom: '6px' }}
                    dangerouslySetInnerHTML={{ __html: escapedText }}
                />
            ) : null;
        }

        return null;
    });
};

interface MarkdownRendererProps {
    markdown: string;
    enableCodeApply: boolean;
}

// Configure marked options
marked.setOptions({
    renderer: new marked.Renderer(),
    gfm: true,
    breaks: true,
    pedantic: false
}) as any;

const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({ markdown, enableCodeApply }) => {

    const { isDarkMode } = useTheme();
    const tokens = marked.lexer(markdown) as TokenWithText[];;
    return <div>{renderTokens(tokens, enableCodeApply, isDarkMode)}</div>;
};

export default MarkdownRenderer;
