import React, { useState, useEffect, memo, useMemo, Suspense, useCallback } from 'react';
import 'prismjs/themes/prism.css';
import { Button, message, Radio, Space, Spin, RadioChangeEvent } from 'antd';
import * as d3 from 'd3';
import { marked } from 'marked';
import type { Diff } from 'react-diff-view';
import { parseDiff, tokenize, RenderToken, HunkProps } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import { DiffLine } from './DiffLine';
import 'prismjs/themes/prism-tomorrow.css';  // Add dark theme support
import * as Viz from '@viz-js/viz';
import { D3Renderer } from './D3Renderer';
import { CodeOutlined, ToolOutlined, ArrowUpOutlined, ArrowDownOutlined,
         CheckCircleOutlined, CloseCircleOutlined, CheckOutlined } from '@ant-design/icons';
import 'prismjs/themes/prism.css';
import { loadPrismLanguage, isLanguageLoaded } from '../utils/prismLoader';
import { useTheme } from '../context/ThemeContext';
import type * as PrismType from 'prismjs';

// Define the status interface
interface HunkStatus {
    applied: boolean;
    reason: string;
}

const hunkStatuses = new WeakMap<object, HunkStatus>();

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

// Define the status interface
interface HunkStatus {
    applied: boolean;
    reason: string;
}

// Define our extended hunk type that includes status
interface ExtendedHunk extends BaseHunk {
    status?: HunkStatus;
}

// Type guard to check if a hunk is extended
const isExtendedHunk = (hunk: BaseHunk): hunk is ExtendedHunk =>
    'status' in hunk;

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

export const DisplayModes = ['raw', 'pretty'] as const;
export type DisplayMode = typeof DisplayModes[number];
export interface DiffViewProps {
    diff: string;
    viewType: 'split' | 'unified';
    initialDisplayMode: DisplayMode;
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
    const { isDarkMode } = useTheme();
    const handleDisplayModeChange = (e: RadioChangeEvent) => {
	const newMode = e.target.value as DisplayMode;
        onDisplayModeChange(newMode);
    };

    return (
	<div className="diff-view-controls" style={{
	    backgroundColor: isDarkMode ? '#1f1f1f' : '#fafafa',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            width: '100%'
        }}>
            <div>
                {displayMode === 'pretty' && (
                    <Space>
                        <Radio.Group
                            value={viewType}
                            buttonStyle="solid"
			    style={{
                                    backgroundColor: isDarkMode ? '#141414' : '#ffffff',
                                    color: isDarkMode ? '#ffffff' : '#000000'
                            }}
                            onChange={e => {
                                window.diffViewType = e.target.value;
                                onViewTypeChange(e.target.value);
                            }}
                        >
                            <Radio.Button value="unified">Unified View</Radio.Button>
                            <Radio.Button value="split">Split View</Radio.Button>
                        </Radio.Group>
                        <Radio.Group
                            value={showLineNumbers}
                            buttonStyle="solid"
			    style={{
                                    backgroundColor: isDarkMode ? '#141414' : '#ffffff',
                                    color: isDarkMode ? '#ffffff' : '#000000'
                            }}
                            onChange={(e) => onLineNumbersChange(e.target.value)}
                        >
                            <Radio.Button value={true}>Show Line Numbers</Radio.Button>
                            <Radio.Button value={false}>Hide Line Numbers</Radio.Button>
                        </Radio.Group>
                    </Space>
                )}
            </div>
            <Radio.Group
                value={displayMode}
                buttonStyle="solid"
		style={{
                    backgroundColor: isDarkMode ? '#141414' : '#ffffff',
                    color: isDarkMode ? '#ffffff' : '#000000'
                }}
                onChange={handleDisplayModeChange}
            >
                <Radio.Button value="pretty">Pretty</Radio.Button>
                <Radio.Button value="raw">Raw</Radio.Button>
            </Radio.Group>
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
            ) && lines.some(line => line.startsWith('--- a/') || line.startsWith('+++ b/'));
        const hasHunkHeader = lines.some(line => 
            /^@@\s+-\d+,?\d*\s+\+\d+,?\d*\s+@@/.test(line)
        );

        if (hasDiffHeaders && hasHunkHeader) {
            return diff;  // Return original diff if it's properly formatted
        }

        // Extract file paths from diff --git line
        const gitMatch = lines[0].match(/diff --git a\/(.*?) b\/(.*?)$/);
        if (!gitMatch) {
            return diff;  // Return original if we can't parse the git diff line
        }
        const filePath = gitMatch[1];

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

const DiffView: React.FC<DiffViewProps> = ({ diff, viewType, initialDisplayMode, showLineNumbers }) => {
    const [isLoading, setIsLoading] = useState(true);
    const [tokenizedHunks, setTokenizedHunks] = useState<any>(null);
    const { isDarkMode } = useTheme();
    const [parsedFiles, setParsedFiles] = useState<any[]>([]);
    const [parseError, setParseError] = useState<boolean>(false);
    const [displayMode, setDisplayMode] = useState<DisplayMode>(initialDisplayMode as DisplayMode);
    const [statusUpdateCounter, setStatusUpdateCounter] = useState(0);

    // detect language from file path
    const detectLanguage = (filePath: string): string => {
        if (!filePath) return 'plaintext';
        const extension = filePath.split('.').pop()?.toLowerCase();
        const languageMap: { [key: string]: string } = {
            'js': 'javascript',
            'jsx': 'javascript',
            'ts': 'typescript',
            'tsx': 'typescript',
            'py': 'python',
            'rb': 'ruby',
            'php': 'php',
            'java': 'java',
            'go': 'go',
            'rs': 'rust',
            'cpp': 'cpp',
            'c': 'c',
            'cs': 'csharp',
            'css': 'css',
            'html': 'markup',
            'xml': 'markup',
            'md': 'markdown'
        };
        return languageMap[extension || ''] || 'plaintext';
    };

    useEffect(() => {
        const parseAndSetFiles = () => {
            try {
                let parsedFiles = parseDiff(normalizeGitDiff(diff));
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
                setParsedFiles(parsedFiles);
                setParseError(false);
            } catch (error) {
                setParseError(true);
                setParsedFiles([]);
            }
        };
        parseAndSetFiles();
    }, [diff]);


    // tokenize hunks
    useEffect(() => {
        const tokenizeHunks = async (hunks: any[], filePath: string) => {
            setIsLoading(true);
            const language = detectLanguage(filePath);
            try {
                // always load basic languages first
                await Promise.all([
                    loadPrismLanguage('markup'),
                    loadPrismLanguage('clike'),
                    loadPrismLanguage(language)
                ]);

		// Verify Prism is properly initialized
                if (!window.Prism?.languages?.[language]) {
                    console.warn(`Prism language ${language} not available, falling back to plain text`);
                    // Try without syntax highlighting
                    const tokens = tokenize(hunks, {
                        highlight: true,
                        refractor: window.Prism,
                        language: 'plaintext'
                    });
                    setTokenizedHunks(tokens);
                } else {
                    // Try with the detected language
                    setTokenizedHunks(null);
                }
            } catch (error) {
                console.warn(`Error during tokenization for ${language}:`, error);
                setTokenizedHunks(null);
            } finally {
                setIsLoading(false);
            }
        };

        if (parsedFiles?.[0]) {
            const file = parsedFiles[0];
            tokenizeHunks(file.hunks, file.newPath || file.oldPath);
        }
    }, [parsedFiles]);

    const renderHunks = (hunks: any[], filePath: string) => {
        const tableClassName = `diff-table ${viewType === 'split' ? 'diff-split' : ''}`;

	if (viewType === 'split') {
            const table = document.querySelector('.diff-table.diff-split');
        }

	/*
        if (isLoading) {
            return (
                <div style={{ padding: '8px' }}>
                    <Spin size="small" />
                    <span style={{ marginLeft: '8px' }}>Loading syntax highlighting...</span>
                </div>
            );
        }
        */

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
		        <>
			    <col className="diff-gutter-col" />
			    {showLineNumbers && <col className="diff-gutter-col" />}
                            <col style={{ width: 'auto' }} />
			</>
                    )}
                </colgroup>
                <tbody>
                    {hunks.map((hunk, index) => {
                        const previousHunk = index > 0 ? (hunks[index - 1] as ExtendedHunk) : null;
                        const linesBetween = previousHunk ?
                            hunk.oldStart - (previousHunk.oldStart + previousHunk.oldLines) : 0;
                        const showEllipsis = displayMode === 'pretty' && 
                            previousHunk;
                        const ellipsisText = linesBetween <= 0 ? '...' :
                            linesBetween === 1 ? 
                                '... (1 line)' : 
                `... (${linesBetween} lines)`;

        // Get hunk status if available
        const hunkStatus = hunkStatuses.get(hunk) && {
            applied: false,
            reason: 'Not attempted'
        }

                        return (
                            <React.Fragment key={hunk.content}>
                                {/* Only show line count if there are lines between hunks */}
				{showEllipsis && (
				    <tr>
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
					    {hunkStatus && (
					        <span style={{
						    color: hunkStatus.applied ? '#52c41a' : '#ff4d4f',
						    display: 'flex',
						    alignItems: 'center',
						    gap: '4px'
					        }}>
                                            {hunkStatus.applied ?
                                                <><CheckCircleOutlined /> Applied</> :
                                                <><CloseCircleOutlined /> Failed: {hunkStatus.reason}</>
                                            }
					        </span>
					    )}
					</td>
				    </tr>
				)}
                                {renderContent(hunk, filePath)}
                            </React.Fragment>
                        );
                    })}
                </tbody>
            </table>
        );
    };

    // Handle parse error case
    if (parseError) {
        return (
            <pre style={{
                backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                color: isDarkMode ? '#e6e6e6' : 'inherit',
                padding: '10px',
                borderRadius: '4px'
            }}>
                <code>{diff}</code>
            </pre>
        );
    }


    const renderContent = (hunk: any, filePath: string) => {
        return hunk.changes && hunk.changes.map((change: any, i: number) => {

            let oldLine = undefined;
            let newLine = undefined;
            
            if (showLineNumbers) {
                oldLine = (change.type === 'normal' || change.type === 'delete') ? change.oldLineNumber || change.lineNumber : undefined;
                newLine = (change.type === 'normal' || change.type === 'insert') ? change.newLineNumber || change.lineNumber : undefined;
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

    const currentTheme = isDarkMode ? darkModeStyles : lightModeStyles;
    return <>{parsedFiles.map((file, fileIndex) => {
      return (  
        <div
	    key={`diff-${fileIndex}`}
            className="diff-view smaller-diff-view"
            style={{
                backgroundColor: currentTheme.content.background,
                color: currentTheme.content.color
            }}
        >
	    <div className="diff-header">
                <div style={{
	            position: 'sticky',
		    left: 0,
		    right: 0,
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    height: '32px',
                    boxSizing: 'border-box'
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
                }</div>
            </div>
	    <div className="diff-view" style={{
                position: 'relative',
                width: '100%',
                overflowX: 'auto',
                overflowY: 'hidden'
            }}>
	        <div className="diff-content">
                    {renderHunks(
                        file.hunks,
                        file.type === 'delete' ? file.oldPath : file.newPath || file.oldPath
                    )}
		</div>
            </div>
        </div>
        );
    })}</>;
};

const ApplyChangesButton: React.FC<ApplyChangesButtonProps> = ({ diff, filePath, enabled }) => {
    const [isApplied, setIsApplied] = useState(false);
    const forceUpdate = useCallback(() => setIsApplied(current => current), []);

    const triggerDiffUpdate = () => {
	window.dispatchEvent(new Event('hunkStatusUpdate'));
    };

    const handleApplyChanges = async () => {
        // Clean the diff content - stop at first triple backtick
        const cleanDiff = (() => {
            const endMarker = diff.indexOf('```');
            return endMarker !== -1 ? diff.slice(0, endMarker).trim() : diff.trim();
        })();

        try {
            const response = await fetch('/api/apply-changes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    diff: cleanDiff, 
                    filePath 
                }),
            });
            if (response.ok || response.status === 207) {
                setIsApplied(true);
                const data = await response.json();
                if (data.status === 'success') {
                    // Update hunk statuses for successful application
                    const files = parseDiff(cleanDiff);
                    files.forEach(file => {
                        file.hunks.forEach(hunk => {
                            hunkStatuses.set(hunk, {
                                applied: true,
                                reason: 'Successfully applied'
                            });
                        });
                    });
                    triggerDiffUpdate();

                    message.success(`Changes applied successfully to ${filePath}`);
                } else if (data.status === 'partial') {
                    parseDiff(cleanDiff).forEach(file => {
                        file.hunks.forEach((hunk, index) => {
                            const statusData = data.details.hunks[index];
                            hunkStatuses.set(hunk, {
                                applied: statusData.status === 'success',
                                reason: statusData.reason || 'Unknown error'
                            });
                        }); 
		    });
                    triggerDiffUpdate();
 
		    // Show partial success message
                    message.warning({
                        content: (
                            <div>
                                <p>{data.message}</p>
                                <p>{data.details?.summary}</p>
				{data.details?.hunks && (
                                    <div>
                                        <ul style={{ marginTop: '8px', paddingLeft: '20px', listStyle: 'none' }}>
                                            {data.details.hunks.map((hunk, i) => (
                                                <li key={i}>
						    {hunk.status === 'failed' ?
                                                        <CloseCircleOutlined style={{ color: '#ff4d4f', marginRight: '8px' }} /> :
                                                        <CheckCircleOutlined style={{ color: '#52c41a', marginRight: '8px' }} />
                                                    }
                                                    {hunk.status === 'failed' ? 
                                                        `Failed at line ${hunk.start_line}: ${hunk.reason}` :
                                                        `Successfully applied hunk at line ${hunk.start_line}`
                                                    }
                                                </li>
                                            ))}
                                        </ul>
                                </div>
                                )}
                            </div>
                        ),
                        duration: 10  // Show for 10 seconds since there's more to read
                    });
                    setIsApplied(true);  // Mark as applied for partial success
                }
            } else {
                try {
                    const errorData = await response.json();
                    message.error({
                        content: (
                            <div>
			        <p>
                                    <CloseCircleOutlined style={{ color: '#ff4d4f', marginRight: '8px' }} />
                                    {errorData.detail?.message || errorData.detail || 'Failed to apply changes'}
                                </p>
                                {errorData.detail?.summary && <p>{errorData.detail.summary}</p>}
                            </div>
                        ),
                        duration: 5
                    });
                } catch (parseError) {
                    message.error('Failed to apply changes');
                }
            }
        } catch (error: unknown) {
            console.error('Error applying changes:', error);
            message.error({
                content: 'Error applying changes: ' + (error instanceof Error ? error.message : String(error)),
                duration: 5
            });
        }
    };

    return enabled ? (
        <Button
            onClick={handleApplyChanges} 
            disabled={isApplied} 
            icon={<CheckOutlined />}
        >
            Apply Changes (beta)
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
}

const DiffToken = memo(({ token, index, enableCodeApply, isDarkMode }: DiffTokenProps): JSX.Element => {
    const isDiffValid = useMemo(() => {
        if (!token.text.trim().startsWith('diff --git')) {
            return false;
        }
        try {
            const files = parseDiff(token.text);
            return files && files.length > 0;
        } catch (e) {
            return false;
        }
    }, [token.text]);
    if (isDiffValid) {
        return (
            <DiffViewWrapper
                token={token}
                index={index}
                enableCodeApply={enableCodeApply}
            />
        );
    } else {
        // Return regular code block if not a valid diff
        return (
            <pre style={{
                padding: '16px',
                backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                color: isDarkMode ? '#e6e6e6' : '#24292e',
                borderRadius: '6px',
                overflow: 'auto'
            }}><code>{token.text}</code></pre>
        );
    }
});

interface DiffViewWrapperProps {
    token: TokenWithText;
    enableCodeApply: boolean;
    index?: number;
}

const DiffViewWrapper: React.FC<DiffViewWrapperProps> = ({ token, enableCodeApply, index }) => {
    const [viewType, setViewType] = useState<'unified' | 'split'>(window.diffViewType || 'unified');
    const [showLineNumbers, setShowLineNumbers] = useState<boolean>(false);
    const [displayMode, setDisplayMode] = useState<DisplayMode>('pretty');
    const [statusUpdateCounter, setStatusUpdateCounter] = useState(0);

    // Ensure window settings are synced with initial state
    useEffect(() => {
        if (window.diffViewType !== viewType) {
            window.diffViewType = viewType;
        }
    }, [token]);

    // Listen for status updates
    useEffect(() => {
        const handleStatusUpdate = () => setStatusUpdateCounter(c => c + 1);
        window.addEventListener('hunkStatusUpdate', handleStatusUpdate);
        return () => window.removeEventListener('hunkStatusUpdate', handleStatusUpdate);
    }, []);

    if (!hasText(token)) {
        return null;
    }

    if (!isCodeToken(token)) {
        return null;
    }

    return (
	<div> 
            <DiffControls
                displayMode={displayMode}
                viewType={viewType}
                showLineNumbers={showLineNumbers}
                onDisplayModeChange={setDisplayMode}
                onViewTypeChange={setViewType}
                onLineNumbersChange={setShowLineNumbers}
            />
	    <div className="diff-container" id={`diff-view-${index || 0}`}>
		{(displayMode as DisplayMode) === 'raw' ? (
                    <pre className="diff-raw-block" style={{
                        padding: '16px'
                    }}>
                        <code>{token.text}</code>
                    </pre>
                ) : (
                    <DiffView
                        diff={token.text}
                        viewType={viewType}
                        initialDisplayMode={displayMode}
                        key={statusUpdateCounter}  // Force re-render on status updates
                        showLineNumbers={showLineNumbers}
                    />
                )}
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
    const [debugInfo, setDebugInfo] = useState<any>({});

    // Get the effective language for highlighting
    const getEffectiveLang = (rawLang: string | undefined): string => {
        if (!rawLang) return 'plaintext';
        if (rawLang === 'typescript jsx') return 'tsx';
        return rawLang;
    };

    // Normalize the language identifier
    const normalizedLang = useMemo(() => {
        if (!token.lang) return 'plaintext';
        // Map 'typescript jsx' to 'tsx' since we know tsx highlighting works
        if (token.lang === 'typescript jsx') {
            return 'tsx';
        }
        return token.lang;
    }, [token.lang]);

    useEffect(() => {
        if (token.lang !== undefined && !prismInstance) {
	    const loadLanguage = async () => {
                setIsLanguageLoaded(false);
                try {
		    console.debug('CodeBlock language info:', {
                        originalLang: token.lang,
			effectiveLang: getEffectiveLang(token.lang),
                        tokenType: token.type,
                        prismLoaded: Boolean(window.Prism),
                        availableLanguages: window.Prism ? Object.keys(window.Prism.languages) : [],
                        tokenContent: token.text.substring(0, 100) + '...'
                    });
                    // Load language and get Prism instance
                    await loadPrismLanguage(normalizedLang);
                    setPrismInstance(window.Prism);
		    const effectiveLang = getEffectiveLang(token.lang);
		    setDebugInfo({
                        loadedLang: token.lang,
                        prismAvailable: Boolean(window.Prism),
                        languagesAfterLoad: window.Prism ? Object.keys(window.Prism.languages) : [],
			grammarAvailable: window.Prism?.languages[effectiveLang] ? true : false
                    });
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

    const getHighlightedCode = (content: string) => {
        if (!prismInstance || token.lang === undefined) {
            return content;
        }

	const effectiveLang = getEffectiveLang(token.lang);

        try {
	    console.debug('Highlighting attempt:', {
                effectiveLang,
                hasGrammar: Boolean(window.Prism.languages[effectiveLang]),
                contentPreview: content.substring(0, 50)
            });
            const grammar = window.Prism.languages[effectiveLang] || window.Prism.languages.plaintext;
            return window.Prism.highlight(content, grammar, effectiveLang);
        } catch (error) {
            console.warn(`Failed to highlight code for language ${normalizedLang}:`, error);
            return content;
        }
    };

    const processContent = (content: string) => {
        // If content is already HTML with Prism tokens, return it directly
        if (content.includes('<span class="token')) {
            return content;
        }

        // If content contains HTML but not Prism tokens, escape it first
        if (content.includes('<') || content.includes('>')) {
            content = content
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;');
        }

        return getHighlightedCode(content);
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

    // Only escape if the content isn't already escaped
    const codeText = token.text;
    return (
        <ErrorBoundary type="code">
            <pre 
	        style={{
                padding: '16px',
                borderRadius: '6px',
                overflow: 'auto',
                backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                border: `1px solid ${isDarkMode ? '#303030' : '#e1e4e8'}`
                }}
            className={`language-${normalizedLang}`}
            >
                <code
                        style={{
                            textShadow: 'none',
                            color: isDarkMode ? '#e6e6e6' : '#24292e'
                         }} 
			 dangerouslySetInnerHTML={{ __html: (() => {
                         // If already has Prism tokens, return as-is
                         if (codeText.includes('<span class="token')) {
                             return codeText;
                         }
 
			 // Decode HTML entities before highlighitng
                         const decodedText = token.lang !== 'diff' ?
		             codeText.replace(/&(amp|lt|gt|quot|apos);/g, (match, entity) =>
                                 ({ amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" })[entity]) :
                             codeText;
 
                         // If no Prism instance or no language specified, just escape HTML
                         if (!prismInstance || !token.lang) {
			     return decodedText;
                         }
			    const grammar = prismInstance.languages[token.lang] || prismInstance.languages.plaintext;
                            try {
				const codeToHighlight = decodedText;
                                return prismInstance.highlight(codeToHighlight, grammar, token.lang);
                            } catch (error) {
                                console.warn(`Highlighting failed for ${token.lang}:`, error);
				return decodedText;
                            }
                        })()
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
                return (
                    <DiffToken
                        key={index}
                        token={token}
                        index={index}
                        enableCodeApply={enableCodeApply}
                        isDarkMode={isDarkMode}
                    />
                );
            } catch (error) {
                console.error('Error parsing diff:', error, '\nDiff content:', token.text);
                // If parsing fails, render as regular code
                return (
                    <pre key={index} style={{
                        padding: '16px',
                        backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                        color: isDarkMode ? '#e6e6e6' : '#24292e',
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

	// Handle D3.js via Vega-Lite specifications
        if (token.type === 'code' && isCodeToken(token) && token.lang === 'vega-lite') {
            try {
                return (
                    <div key={index} className="vega-lite-container">
                        <D3Renderer spec={token.text} />
                    </div>
                );
            } catch (error) {
                return <pre key={index}><code>Error parsing Vega-Lite spec: {error instanceof Error ? error.message : String(error)}</code></pre>;
            }
        }


// Handle direct D3.js visualizations
if (token.type === 'code' && isCodeToken(token) && token.lang === 'd3') {
    try {
        // Return D3Renderer component directly
        const containerId = `d3-viz-${Date.now()}`;
        const SpinnerComponent = () => (
            <div style={{
                padding: '20px',
                textAlign: 'center',
                backgroundColor: isDarkMode ? '#141414' : '#f0f2f5',
                border: '1px solid ' + (isDarkMode ? '#303030' : '#d9d9d9'),
                borderRadius: '4px',
                margin: '10px 0'
            }}>
                <Spin tip="Preparing visualization..." />
            </div>
        );

        // Return the spinner immediately
        const containerElement = (
            <div key={index}>
                <div id={containerId}>
                    <SpinnerComponent />
                </div>
            </div>
        );

        return (
            <D3Renderer
                key={index}
                spec={token.text}
                width={800}
                height={400}
                type="d3"
            />
        );
        return containerElement;
    } catch (error) {
        return <pre key={index}><code>Error rendering D3 visualization: {error instanceof Error ? error.message : String(error)}</code></pre>;
    }
}

        if (token.type === 'code' && isCodeToken(token)) {
            // Regular code blocks (non-diff)
	    const decodedToken = {
                ...token,
                text: token.text.replace(/&(amp|lt|gt|quot|apos);/g, (match, entity) => ({ amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" })[entity])
            };
            return <CodeBlock key={index} token={decodedToken} index={index} />;
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
            // Always escape HTML entities, but preserve existing escaped entities
	    const escapedText = text
                .replace(/&/g, '&amp;')  // Must be first to not double-escape other entities
		.replace(/&amp;(amp|lt|gt|quot|apos);/g, '&$1;')  // Fix double-escaped entities
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&apos;');

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
    renderPath?: RenderPath;
}

// Configure marked options
marked.setOptions({
    renderer: new marked.Renderer(),
    gfm: true,
    breaks: true,
    pedantic: false
}) as any;

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = memo(({ markdown, enableCodeApply }) => {
    const { isDarkMode } = useTheme();

    const tokens = useMemo(() => {
        return (typeof markdown === 'string' ? marked.lexer(markdown) : []) as TokenWithText[];
    }, [markdown]);

    const renderedContent = useMemo(() => {
	return renderTokens(tokens, enableCodeApply, isDarkMode);
    }, [tokens, enableCodeApply, isDarkMode]);

    return <div>{renderedContent}</div>;
});

export default MarkdownRenderer;
