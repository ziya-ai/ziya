import React, { useState, useEffect, memo, useMemo, Suspense, useCallback } from 'react';
import { parseDiff, Diff, Hunk, tokenize, RenderToken, HunkProps } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import { DiffLine } from './DiffLine';
import { marked, Tokens } from 'marked';
import { Button, message, Radio, Space, Spin, RadioChangeEvent } from 'antd';
import 'prismjs/themes/prism-tomorrow.css';  // Add dark theme support
import * as Viz from '@viz-js/viz';
import { D3Renderer } from './D3Renderer';
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

type DiffChange = {
    type: 'delete' | 'insert' | 'context';
    content: string;
    isDelete?: boolean;
    isInsert?: boolean;
    isNormal?: boolean;
    lineNumber?: number | null;
    oldLineNumber?: number | null;
    newLineNumber?: number | null;
};
 
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

const DiffView: React.FC<DiffViewProps> = ({ diff, viewType, initialDisplayMode, showLineNumbers }) => {
    const [isLoading, setIsLoading] = useState(true);
    const [tokenizedHunks, setTokenizedHunks] = useState<any>(null);
    const { isDarkMode } = useTheme();
    const [parsedFiles, setParsedFiles] = useState<any[]>([]);
    const [parseError, setParseError] = useState<boolean>(false);
    const [displayMode, setDisplayMode] = useState<DisplayMode>(initialDisplayMode as DisplayMode);

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
                        const previousHunk = index > 0 ? hunks[index - 1] : null;
                        const showEllipsis = displayMode === 'pretty' && previousHunk &&
                            (hunk.oldStart - (previousHunk.oldStart + previousHunk.oldLines) > 1);
                        return (
                            <React.Fragment key={hunk.content}>
                                {showEllipsis && <tr><td colSpan={viewType === 'split' ? 4 : 3} className="diff-ellipsis">...</td></tr>}
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
	    <div className="diff-view">
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
    const [displayMode, setDisplayMode] = useState<DisplayMode>('pretty');

    // Ensure window settings are synced with initial state
    useEffect(() => {
        if (window.diffViewType !== viewType) {
            window.diffViewType = viewType;
        }
    }, [token]);

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

    const getHighlightedCode = (content: string) => {
        if (!prismInstance || token.lang === undefined) {
            return content;
        }
        try {
            const grammar = window.Prism.languages[token.lang as string] || window.Prism.languages.plaintext;
            return window.Prism.highlight(content, grammar, token.lang);
        } catch (error) {
            console.warn(`Failed to highlight code for language ${token.lang}:`, error);
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

    const codeText = token.text;
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
		         (() => {
                            // If content already contains Prism tokens, return it directly
                            if (codeText.includes('<span class="token')) {
                                return codeText;
                            }
                            // Otherwise, highlight it if we can
                            if (prismInstance && token.lang) {
                                return prismInstance.highlight(
                                    codeText,
                                    prismInstance.languages[token.lang as keyof typeof prismInstance.languages] ||
                                    prismInstance.languages.plaintext,
                                    token.lang as string
                                );
                            }
                            return codeText;
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
                console.debug(`Processing diff token:`, { type: token.type, lang: token.lang, text: token.text.substring(0, 100) });
                // Only attempt to parse as diff if it starts with 'diff --git'
                if (token.text.trim().startsWith('diff --git')) {
		    console.debug('Found diff --git marker, attempting to parse');
                    const files = parseDiff(token.text);
		    console.debug('Parsed files:', files);
                    if (files && files.length > 0) {
			console.debug('Successfully parsed diff files:', files.length);
                        return (
                            <DiffViewWrapper
                                key={index}
                                token={token}
                                index={index}
                                enableCodeApply={enableCodeApply}
                            />
                        );
                    }
		    console.debug('No files parsed from diff');
                } else {
                    console.debug('Diff token does not start with "diff --git"');
                }
                // If not a valid diff or doesn't start with diff marker, render as regular code
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
                // Parse the D3 specification
                const spec = JSON.parse(token.text);
                return (
                    <div key={index} className="d3-visualization-container" style={{
                        margin: '1em 0',
                        padding: '1em',
                        backgroundColor: isDarkMode ? '#1f1f1f' : '#f8f9fa',
                        borderRadius: '6px',
                        overflow: 'auto'
                    }}>
                        <D3Renderer spec={token.text} />
                    </div>
                );
            } catch (error) {
                return <pre key={index}><code>Error parsing D3 specification: {error instanceof Error ? error.message : String(error)}</code></pre>;
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
            // Always escape HTML entities, but preserve existing escaped entities
	    const escapedText = text
                .replace(/&/g, '&amp;')  // Must be first to not double-escape other entities
                .replace(/&amp;(?:amp|lt|gt|quot|apos);/g, '&$1;')  // Fix double-escaped entities
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
