import React, { useState, useEffect, memo, useMemo, Suspense, useCallback } from 'react';
import 'prismjs/themes/prism.css';
import { Button, message, Radio, Space, Spin, RadioChangeEvent } from 'antd';
import * as d3 from 'd3';
import { marked, Tokens, Marked } from 'marked';
import type { Diff } from 'react-diff-view';
import { parseDiff, tokenize, RenderToken, HunkProps } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import { DiffLine } from './DiffLine';
import 'prismjs/themes/prism-tomorrow.css';  // Add dark theme support
import { D3Renderer } from './D3Renderer';
import { CodeOutlined, ToolOutlined, ArrowUpOutlined, ArrowDownOutlined,
         CheckCircleOutlined, CloseCircleOutlined, CheckOutlined } from '@ant-design/icons';
import 'prismjs/themes/prism.css';
import { loadPrismLanguage, isLanguageLoaded } from '../utils/prismLoader';
import * as Viz from '@viz-js/viz';
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

    console.log('=== renderFileHeader Debug ===');
    console.log('Input file object:', {
        type: file.type,
        oldPath: file.oldPath,
        newPath: file.newPath,
        hunks: file.hunks?.length
    });

    // If we have paths in the file object, use them directly
    if (file.oldPath || file.newPath) {
        const path = file.newPath || file.oldPath;
        console.log('Using path from file object:', path);
        return `File: ${path}`;
    }

    // Helper to extract paths from unified diff header
    const extractPathFromUnifiedHeader = (line: string): string | null => {
        // Handle unified diff format (--- a/path or +++ b/path)
        const match = line.match(/^(?:---|\+\+\+)\s+(?:[ab]\/)?(.*?)(?:\s+|$)/);
        return match ? match[1] : null;
    };

    // Helper to extract paths from git diff header
    const extractPathsFromHeader = (diffHeader: string): [string | null, string | null] => {
        // First try git diff format
        const gitMatch = diffHeader.match(/^diff --git a\/(.*?) b\/(.*?)$/);
        if (gitMatch) {
            // Check if this is a new file
            if (gitMatch[1].includes('/dev/null')) {
                return [null, gitMatch[2]];
            }
            // Check if this is a deletion
            if (gitMatch[2].includes('/dev/null')) {
                return [gitMatch[1], null];
            }
            return [gitMatch[1], gitMatch[2]];
        }

        // If no git diff header, try to extract from unified diff format
        const lines = diffHeader.split('\n');
        let oldPath: string | null = null;
        let newPath: string | null = null;

        for (const line of lines) {
            console.log('Examining line for path:', line);
            if (line.startsWith('--- ')) {
                oldPath = extractPathFromUnifiedHeader(line);
            } else if (line.startsWith('+++ ')) {
                newPath = extractPathFromUnifiedHeader(line);
            }
            // Stop looking after we find both paths or hit a hunk header
            if ((oldPath && newPath) || line.startsWith('@@ ')) break;
        }

        console.log('Extracted paths from unified format:', { oldPath, newPath });
        return [oldPath, newPath];
    };

    // Try to extract from content if no file header or git header
    if (file.hunks?.[0]?.content) {
        // Get all content from all hunks
        const fullContent = file.hunks.map(h => h.content).join('\n');
        console.log('Full content:', fullContent)

        const [oldPath, newPath] = extractPathsFromHeader(fullContent);
        console.log('Extracted paths from content:', { oldPath, newPath });

        if (oldPath || newPath) {
            const path = newPath || oldPath;
            console.log('Using path:', path);
            return path ? `File: ${path}` : 'Unknown file operation';
        }

        // Fallback for any other cases
        console.log('No file path found in any format');
        return 'Unknown file operation';
    };

    // Detect rename by comparing paths in diff header
    const isRename = (oldPath: string | null, newPath: string | null): boolean => {
        if (!oldPath || !newPath || oldPath === newPath) {
            return false;
        }
        // Exclude /dev/null paths which indicate add/delete operations
        return !oldPath.includes('/dev/null') && !newPath.includes('/dev/null');
    };

    // First try to use the paths from the file object
    if (file.oldPath || file.newPath) {
        if (file.type === 'rename') {
            const similarityIndex = file.similarity || 100;
            return `Rename${similarityIndex < 100 ? ' with changes' : ''}: ${file.oldPath} → ${file.newPath} (${similarityIndex}% similar)`;
        } else if (file.type === 'delete') {
            return `Delete: ${file.oldPath}`;
        } else if (file.type === 'add') {
            return `Create: ${file.newPath}`;
        } else if (file.oldPath !== file.newPath) {
            return `Rename: ${file.oldPath} → ${file.newPath}`;
        } else {
            return `File: ${file.oldPath || file.newPath}`;
        }
    }

    // If no paths in file object, try to extract from content
    if (file.hunks?.[0]?.content) {
        const [oldPath, newPath] = extractPathsFromHeader(file.hunks[0].content);

        if (oldPath && newPath && isRename(oldPath, newPath)) {
            return `Rename: ${oldPath} → ${newPath}`;
        } else if (oldPath && !newPath) {
            return `Delete: ${oldPath}`;
        } else if (!oldPath && newPath) {
            return `Create: ${newPath}`;
        } else if (oldPath || newPath) {
            return `File: ${newPath || oldPath}`;
        }
    }
    
    // Fallback for any other cases
    return 'Unknown file operation';
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
    if (diff.startsWith('diff --git') || diff.match(/^---\s+\S+/m)) {
        const lines: string[] = diff.split('\n');
        const normalizedLines: string[] = [];

        console.log('=== normalizeGitDiff Debug ===');
        console.log('Input diff preview:', diff.split('\n').slice(0, 5));

        // Check if this is a properly formatted diff
        const hasDiffHeaders = lines.some(line =>
            (line.startsWith('---') || line.startsWith('+++'))
            ) && lines.some(line => line.startsWith('--- a/') || line.startsWith('+++ b/'));
        console.log('Has diff headers:', hasDiffHeaders);

        const hasHunkHeader = lines.some(line => 
            /^@@\s+-\d+,?\d*\s+\+\d+,?\d*\s+@@/.test(line)
        );
        console.log('Has hunk header:', hasHunkHeader);

        if (hasDiffHeaders && hasHunkHeader) {
            console.log('Diff is already properly formatted, returning original');
            return diff;  // Return original diff if it's properly formatted
        }

        // Extract file path from unified diff headers if present
        let filePath: string | null = null;
        for (const line of lines) {
            const unifiedMatch = line.match(/^(?:---|\+\+\+)\s+(?:[ab]\/)?(.+)$/);
            console.log('Checking line for unified header:', { line, match: unifiedMatch });
            if (unifiedMatch) {
                filePath = unifiedMatch[1];
                break;
            }
        }

        // If no path found from unified headers, try git diff header
        if (!filePath) {
            console.log('No path found in unified headers, trying git diff header');
            const gitMatch = lines[0].match(/diff --git a\/(.*?) b\/(.*?)$/);
            if (gitMatch) {
                filePath = gitMatch[1];
            }
        }

        console.log('Final extracted filePath:', filePath);
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

                console.log('parseDiff result:', {
                    fileCount: parsedFiles.length,
                    firstFile: parsedFiles[0] ? {
                        type: parsedFiles[0].type,
                        oldPath: parsedFiles[0].oldPath,
                        newPath: parsedFiles[0].newPath,
                        hunks: parsedFiles[0].hunks?.map(h => ({
                            content: h.content.split('\n').slice(0, 3)
                        }))
                    } : null
                });

                // If we have a unified diff without git headers, try to extract the file path
                if (parsedFiles.length > 0 && !parsedFiles[0].oldPath && !parsedFiles[0].newPath) {
                    const lines = diff.split('\n');
                    for (const line of lines) {
                        if (line.startsWith('--- a/')) {
                            parsedFiles[0].oldPath = line.substring(6);
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
                setParsedFiles(parsedFiles);
                setParseError(false);
            } catch (error) {
                console.error('Error parsing diff:', error);
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
        const status = hunkStatuses.get(hunk);
        const isApplied = status?.applied;
        const statusReason = status?.reason || '';
        
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
                    <><CheckCircleOutlined /> Applied</> :
                    <><CloseCircleOutlined /> Failed: {statusReason}</>
                }
            </span>
        );

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
                        {hunkStatusIndicator}
					</td>
                </tr>
            )}
            {renderContent(hunk, filePath, status)}
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


    const renderContent = (hunk: any, filePath: string, status?: any) => {
        // Add a status row at the top of the hunk if status is available
        const changes = [...(hunk.changes || [])];
        
        // Add visual styling based on hunk status
        const rowStyle = status ? {
            backgroundColor: status.applied ? 'rgba(82, 196, 26, 0.05)' : 'rgba(255, 77, 79, 0.05)'
        } : {};
        return hunk.changes && hunk.changes.map((change: any, i: number) => {
            // Apply the status-based styling to each row
            const style = {...rowStyle};
            
            // Add additional styling for specific change types
            if (change.type === 'insert') {
                style.backgroundColor = status?.applied ? 'rgba(82, 196, 26, 0.1)' : style.backgroundColor;
            } else if (change.type === 'delete') {
                style.backgroundColor = status?.applied ? 'rgba(255, 77, 79, 0.1)' : style.backgroundColor;
            }

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
                    style={style}
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
            data-diff-type={file.type}
            className="diff-view smaller-diff-view"
            style={{
                backgroundColor: currentTheme.content.background,
                color: currentTheme.content.color
            }}
        >
	    <div className="diff-header">
            <div className="diff-header-content">
                <b>{renderFileHeader(file)}</b>
                {!['delete'].includes(file.type) &&
                    <ApplyChangesButton
                        diff={diff}
                        filePath={file.newPath || file.oldPath}
                        enabled={window.enableCodeApply === 'true'}
                    />
                }</div>
            </div>
            <style>{`
                .diff-header {
                    background-color: ${isDarkMode ? '#1f1f1f' : '#f6f8fa'};
                    border-bottom: 1px solid ${isDarkMode ? '#303030' : '#e1e4e8'};
                    padding: 8px 16px;
                }

                .diff-header-content {
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
            `}</style>
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
        // Extract the actual diff content
        const cleanDiff = (() => {
            console.log('Pre-fetch diff content:', diff);
            // Log the incoming diff content
            console.log('Raw diff content:', {
                length: diff.length,
                firstLine: diff.split('\n')[0],
                totalLines: diff.split('\n').length,
                fullContent: diff
            });
            // If it's already a raw diff, use it directly
            if (diff.startsWith('diff --git')) {
                return diff.trim();
            }

            // Otherwise extract diff from markdown code block
            const diffMatch = diff.match(/```diff\n([\s\S]*?)```(?:\s|$)/);
            console.log('Diff match result:', {
                found: !!diffMatch,
                groups: diffMatch ? diffMatch.length : 0,
                matchContent: diffMatch ? {
                    fullMatch: diffMatch[0],
                    diffContent: diffMatch[1],
                } : null
            });
            if (diffMatch) {
                return diffMatch[1].trim();
            }

            // Fallback to original cleaning method
            return diff.trim();
        })();

        // Log the processed diff content
        console.log('Processed diff content:', {
            length: cleanDiff.length,
            lines: cleanDiff.split('\n').length,
            firstLine: cleanDiff.split('\n')[0],
            lastLine: cleanDiff.split('\n').slice(-1)[0],
            fullContent: cleanDiff,
            truncated: cleanDiff.length < diff.length
        });

        // Log the actual request body
        const requestBody = JSON.stringify({ diff: cleanDiff, filePath: filePath.trim() });
        console.log('Request body:', requestBody);

        const requestBodyParsed = JSON.parse(requestBody);
        console.log('Parsed request body diff length:', requestBodyParsed.diff.split('\n').length);

        try {
            console.log('About to send fetch request with body length:', cleanDiff.length);
            console.log('Request body:', {
                diff: cleanDiff.substring(0, 100) + '...',
                filePath: filePath.trim()
            });
            
            const response = await fetch('/api/apply-changes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    diff: cleanDiff,
                    filePath: filePath.trim(),
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
                    status: data.status,
                    message: data.message,
                    hasDetails: !!data.details,
                    detailsKeys: data.details ? Object.keys(data.details) : [],
                    succeeded: data.details?.succeeded,
                    failed: data.details?.failed,
                    hunkStatuses: data.details?.hunk_statuses
                });
                
                // Check if ANY hunks succeeded before marking as applied
                const hasSuccessfulHunks = data.details?.succeeded?.length > 0;
                console.log('Has successful hunks:', hasSuccessfulHunks);
                console.log('Succeeded hunks:', data.details?.succeeded);
                
                if (data.status === 'success') {
                    console.log('Processing success status');
                    setIsApplied(true);  // Complete success
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
                    console.log('Processing partial status');
                    // Only mark as applied if at least one hunk succeeded
                    setIsApplied(hasSuccessfulHunks);
                    console.log('Setting isApplied to:', hasSuccessfulHunks);
                    
                    // Handle the new format with hunk_statuses
                    parseDiff(cleanDiff).forEach((file, fileIndex) => {
                        file.hunks.forEach((hunk, hunkIndex) => {
                            // Get the hunk status from the response
                            // The hunk IDs in the response are 1-based, but our hunkIndex is 0-based
                            const hunkId = hunkIndex + 1;
                            const hunkStatus = data.details?.hunk_statuses?.[hunkId];
                            
                            if (hunkStatus) {
                                hunkStatuses.set(hunk, {
                                    applied: hunkStatus.status === 'succeeded',
                                    reason: hunkStatus.status === 'failed' 
                                        ? `Failed in ${hunkStatus.stage} stage` 
                                        : 'Successfully applied'
                                });
                            } else {
                                // Fallback if we can't find the specific hunk status
                                const isInFailedList = data.details?.failed?.includes(hunkId);
                                hunkStatuses.set(hunk, {
                                    applied: !isInFailedList,
                                    reason: isInFailedList ? 'Failed to apply' : 'Successfully applied'
                                });
                            }
                        });
                    });
                    triggerDiffUpdate();
 
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
                            const hunkId = hunkIndex + 1;
                            const hunkStatus = data.details?.hunk_statuses?.[hunkId];
                            
                            hunkStatuses.set(hunk, {
                                applied: false,
                                reason: hunkStatus?.stage 
                                    ? `Failed in ${hunkStatus.stage} stage` 
                                    : 'Failed to apply'
                            });
                        });
                    });
                    triggerDiffUpdate();
                    
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
                                                        {hunkStatus?.error_details ? `: ${JSON.stringify(hunkStatus.error_details)}` : ''}
                                                    </li>
                                                );
                                            })}
                                        </ul>
                                    </div>
                                )}
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
                    const errorData = await response.json();
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
                                hunkStatuses.set(hunk, {
                                    applied: false,
                                    reason: 'Failed to apply'
                                });
                            });
                        });
                        triggerDiffUpdate();
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

const extractFilePathFromDiff = (content: string): string | null => {
    // Try to find the target file path from diff headers
    const lines = content.split('\n');
    for (const line of lines) {
        // Check for +++ line first as it's the target file
        if (line.startsWith('+++ b/')) {
            return line.substring(6);
        }
        // Fallback to --- line if +++ isn't found yet
        if (line.startsWith('--- a/')) {
            return line.substring(6);
        }
    }
    return null;
};

const DiffToken = memo(({ token, index, enableCodeApply, isDarkMode }: DiffTokenProps): JSX.Element => {
    console.log(">>> STEP 2: DiffToken received token", { index, lang: token.lang, textPreview: token.text?.substring(0, 100) });
    const isDiffValid = useMemo(() => {
        const trimmedText = token.text?.trim();
        // Allow diffs starting with 'diff --git' OR '--- a/'
        if (!trimmedText || (!trimmedText.startsWith('diff --git') && !trimmedText.startsWith('--- a/'))) {
            console.log(">>> STEP 3: Diff text doesn't start with 'diff --git' or '--- a/', skipping parseDiff.");
            return false;
        }
        try {
            console.log(">>> STEP 3: Calling parseDiff with text preview:", token.text?.substring(0, 100));
            // Ensure token.text is a string before passing
            const diffInput = typeof token.text === 'string' ? token.text : '';
            const files = parseDiff(diffInput); // From react-diff-view
            console.log(">>> STEP 3: parseDiff raw result:", files); // Log the raw result
            console.log(">>> STEP 3: parseDiff structured result:", { fileCount: files?.length, firstFileHunks: files?.[0]?.hunks?.length }); // Log structured result
            return files && files.length > 0 && files[0].hunks && files[0].hunks.length > 0;
        } catch (e) {
            console.error('Error parsing diff:', e);
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
        // Fallback: Render as a plain code block if parseDiff failed or returned no files/hunks
        console.warn("DiffToken: Rendering as plain code block because isDiffValid is false.", { textPreview: token.text?.substring(0,100) });
        return <CodeBlock key={index} token={{...token, lang: 'plaintext'}} index={index} />;
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
    console.log('CodeBlock constructor called');
    console.debug('CodeBlock mounting:', {
        tokenType: token.type,
        language: token.lang,
        contentLength: token.text?.length,
        prismLoaded: Boolean(window.Prism),
        availableLanguages: window.Prism ? Object.keys(window.Prism.languages) : []
    });

    const [isLanguageLoaded, setIsLanguageLoaded] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);
    const { isDarkMode } = useTheme();
    const [prismInstance, setPrismInstance] = useState<typeof PrismType | null>(null);
    const [debugInfo, setDebugInfo] = useState<any>({});

    console.debug('CodeBlock rendering:', {
        tokenType: token.type,
        language: token.lang,
        contentLength: token.text?.length,
        contentPreview: token.text?.substring(0, 50)
    });

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

// Define the possible determined types
type DeterminedTokenType = 'diff' | 'graphviz' | 'vega-lite' | 'd3' | 'mermaid' | 'code' | 'html' | 'text' | 'list' | 'table' | 'paragraph' | 'heading' | 'hr' | 'blockquote' | 'space' | 'codespan' | 'strong' | 'em' | 'del' | 'link' | 'image' | 'br' | 'list_item' | 'unknown';

// Helper function to determine the definitive type of a token
function determineTokenType(token: Tokens.Generic | TokenWithText): DeterminedTokenType {
    const tokenType = token.type as string;

    // 1. Handle Code Blocks with explicit lang tags first
    if (tokenType === 'code' && 'lang' in token && typeof token.lang === 'string' && token.lang) {

        const lang = token.lang.toLowerCase().trim();
        if (lang === 'diff') return 'diff';
        if (lang === 'graphviz' || lang === 'dot') return 'graphviz';
        if (lang === 'vega-lite') return 'vega-lite';
        if (lang === 'mermaid') return 'mermaid';
        if (lang === 'd3') return 'd3';
        // If it has a specific lang tag but isn't special, it's 'code'
        return 'code';
    }

    // 2. Content-based detection for code blocks *without* specific lang tags
    if (tokenType === 'code' && 'text' in token && typeof token.text === 'string') { 
        const text = token.text;
        const trimmedText = text.trim();
        // Strict Graphviz check
        // Look for 'digraph' or 'graph' followed by an identifier and '{'
        // Allows for optional whitespace and comments before the opening brace
        const graphvizRegex = /^\s*(?:strict\s+)?(digraph|graph)\s+\w*\s*\{/i;
        if (graphvizRegex.test(trimmedText)) {
             console.log(">>> Content matched as Graphviz (strict regex)");
             return 'graphviz';
        }
        // Fallback for simpler cases (might be less reliable)
        if (trimmedText.startsWith('digraph') || trimmedText.startsWith('graph')) {
            console.log(">>> Content matched as Graphviz (simple prefix)");
            return 'graphviz';
        }
        const linesToCheck = text.split('\n').slice(0, 10); // Check first 10 lines
        const hasGitHeader = linesToCheck.some(line => /^diff --git /m.test(line));
        const hasMinusHeader = linesToCheck.some(line => /^--- a\//m.test(line));
        const hasPlusHeader = linesToCheck.some(line => /^\+\+\+ b\//m.test(line));
        const hasHunkHeader = linesToCheck.some(line => /^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@/m.test(line));

        // Check for common valid diff starting patterns
        if (hasGitHeader || (hasMinusHeader && hasPlusHeader) || hasHunkHeader) {
            // Log which condition matched for debugging
            const matchReason = hasGitHeader ? "git header" : (hasMinusHeader && hasPlusHeader) ? "---/+++ headers" : "hunk header";
            console.log(`>>> Content matched as Diff (reason: ${matchReason})`);
            return 'diff';
        }
        // If no special content detected, treat as generic code
        return 'code';
    }

    // 3. Map other standard marked token types directly
    // Add more types from marked.Tokens here as needed
    const knownTypes: DeterminedTokenType[] = [
        'paragraph', 'heading', 'hr', 'blockquote', 'list', 'list_item', 'table',
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
    const textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    return textarea.value;
};

const renderTokens = (tokens: (Tokens.Generic | TokenWithText)[], enableCodeApply: boolean, isDarkMode: boolean): React.ReactNode => {
    console.debug('renderTokens called with:', {
        tokenCount: tokens.length,
        tokenTypes: tokens.map(t => t.type)
    });

    return tokens.map((token, index) => {
        // Determine the definitive type for rendering
        const determinedType = determineTokenType(token);
        const tokenWithText = token as TokenWithText; // Helper cast

        console.log(`>>> Processing token index ${index}: Type=${token.type}, Determined=${determinedType}, Lang=${(token as any).lang}`);

        try {
            switch (determinedType) {
                case 'diff':
                    console.log(`>>> Rendering as Diff (lang: ${(token as any).lang})`, { tokenTextPreview: tokenWithText.text?.substring(0, 100) });
                    // Ensure lang is set to 'diff' for the component
                    const diffToken = { ...tokenWithText, lang: 'diff' };
                    return <DiffToken key={index} token={diffToken} index={index} enableCodeApply={enableCodeApply} isDarkMode={isDarkMode} />;

                case 'graphviz':
                    console.log(`>>> Rendering as Graphviz (lang: ${(token as any).lang})`);
                    if (!hasText(tokenWithText) || !tokenWithText.text?.trim()) return null;
                    return (
                        <D3Renderer
                            spec={{
                                type: 'graphviz',
                                definition: token.text
                            }}
                            type="d3"
                        />
                    );                
                case 'mermaid':
                    console.log(`>>> Rendering as Mermaid (lang: ${(token as any).lang})`);
                    if (!hasText(tokenWithText) || !tokenWithText.text?.trim()) return null;
                    // Pass the definition directly to D3Renderer, which will use the mermaidPlugin
                    // We need a spec object that the mermaidPlugin can handle
                    const mermaidSpec = { type: 'mermaid', definition: tokenWithText.text };
                    return <D3Renderer key={index} spec={mermaidSpec} type="d3" />;
                case 'vega-lite':
                    console.log(`>>> Rendering as VegaLite (lang: ${(token as any).lang})`);
                     if (!hasText(tokenWithText)) return null;
                    return (
                        <div key={index} className="vega-lite-container">
                            <D3Renderer spec={tokenWithText.text} type="vega-lite" />
                        </div>
                    );

                case 'd3':
                    console.log(`>>> Rendering as D3 (lang: ${(token as any).lang})`);
                    if (!hasText(tokenWithText)) return null;
                    return (
                         <D3Renderer key={index} spec={tokenWithText.text} type="d3" />
                    );

                case 'code':
                    console.log(`>>> Rendering as CodeBlock (lang: ${(token as any).lang})`);
                    if (!isCodeToken(tokenWithText)) return null; // Type guard
                    // Pass the original lang tag (or plaintext) for highlighting
                    const codeToken = { ...tokenWithText, lang: tokenWithText.lang || 'plaintext' };
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
                    // Filter out empty text tokens that might remain after processing
                    const filteredPTokens = pTokens.filter(t => t.type !== 'text' || (t as Tokens.Text).text.trim() !== '');
                    if (filteredPTokens.length === 0) return null; // Don't render empty paragraphs
                    return <p key={index}>{renderTokens(filteredPTokens, enableCodeApply, isDarkMode)}</p>;

                case 'list':
                    // Render list, processing items recursively
                    const listToken = token as Tokens.List;
                    const ListTag = listToken.ordered ? 'ol' : 'ul';
                    return (
                        <ListTag key={index} start={listToken.ordered ? (listToken.start || 1) : undefined}>
                            {listToken.items.map((item, itemIndex) => (
                                // Render list items using the 'list_item' case below
                                <React.Fragment key={itemIndex}>
                                    {renderTokens([item], enableCodeApply, isDarkMode)}
                                </React.Fragment>
                            ))}
                        </ListTag>
                    );

                case 'list_item':
                    const listItemToken = token as Tokens.ListItem;
                    // --- DEBUG LOG for list item tokens ---
                    console.log(`>>> List Item Index ${index} Tokens:`, listItemToken.tokens);
                    // --- END DEBUG LOG ---
                    
                    // Handle task list items
                    if (listItemToken.task) {
                        return (
                            <li key={index} style={{ listStyle: 'none' }}>
                                <input
                                    type="checkbox"
                                    checked={listItemToken.checked}
                                    readOnly
                                    style={{ marginRight: '0.5em', verticalAlign: 'middle' }}
                                />
                                {renderTokens(listItemToken.tokens || [], enableCodeApply, isDarkMode)}
                            </li>
                        );
                    }
                    // Regular list item
                    return <li key={index}>{renderTokens(listItemToken.tokens || [], enableCodeApply, isDarkMode)}</li>;

                case 'table':
                    const tableToken = token as Tokens.Table;
                    return (
                        <table key={index} style={{ borderCollapse: 'collapse', width: '100%', marginBottom: '1em' }}>
                            <thead>
                                <tr>
                                    {tableToken.header.map((cell, cellIndex) => (
                                        <th key={cellIndex} style={{ borderBottom: '2px solid #ddd', padding: '8px', textAlign: tableToken.align[cellIndex] || 'left' }}>
                                            {renderTokens(cell.tokens || [], enableCodeApply, isDarkMode)}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {tableToken.rows.map((row, rowIndex) => (
                                    <tr key={rowIndex}>
                                        {row.map((cell, cellIndex) => (
                                            <td key={cellIndex} style={{ border: '1px solid #ddd', padding: '8px', textAlign: tableToken.align[cellIndex] || 'left' }}>
                                                {renderTokens(cell.tokens || [], enableCodeApply, isDarkMode)}
                                            </td>
                                        ))}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    );

                case 'html':
                    if (!hasText(tokenWithText)) return null;                    // Be cautious with dangerouslySetInnerHTML
                    return <div key={index} dangerouslySetInnerHTML={{ __html: tokenWithText.text }} />;

                case 'text':
                    if (!hasText(tokenWithText)) return null;
                    const textContent = tokenWithText.text;
                    // Check if this 'text' token has nested inline tokens (like strong, em, etc.)
                    if (tokenWithText.tokens && tokenWithText.tokens.length > 0) {
                        // If it has nested tokens, render them recursively
                        return <>{renderTokens(tokenWithText.tokens, enableCodeApply, isDarkMode)}</>;
                    } else {
                        // Otherwise, just render the decoded text content
                        return decodeHtmlEntities(tokenWithText.text);
                    }

                // --- Handle Inline Markdown Elements (Recursively) ---
                case 'strong':
                    return <strong key={index}>{renderTokens((token as Tokens.Strong).tokens || [], enableCodeApply, isDarkMode)}</strong>;
                case 'em':
                    return <em key={index}>{renderTokens((token as Tokens.Em).tokens || [], enableCodeApply, isDarkMode)}</em>;
                case 'codespan':
                    if (!hasText(tokenWithText)) return null;
                    // Basic escaping for codespan content
                    const escapedCode = tokenWithText.text.replace(/</g, '&lt;').replace(/>/g, '&gt;');
                    return <code key={index} dangerouslySetInnerHTML={{ __html: escapedCode }} />;
                case 'br':
                    return <br key={index} />;
                case 'del':
                    return <del key={index}>{renderTokens((token as Tokens.Del).tokens || [], enableCodeApply, isDarkMode)}</del>;
                    
                case 'link':
                    const linkToken = token as Tokens.Link;
                    return <a key={index} href={linkToken.href} title={linkToken.title ?? undefined}>{renderTokens(linkToken.tokens || [], enableCodeApply, isDarkMode)}</a>;
                case 'image':
                    const imageToken = token as Tokens.Image;
                    return <img key={index} src={imageToken.href} alt={imageToken.text} title={imageToken.title ?? undefined} />;

                // --- Other Block Types ---
                case 'heading':
                    const headingToken = token as Tokens.Heading;
                    const Tag = `h${headingToken.depth}` as keyof JSX.IntrinsicElements;
                    return <Tag key={index}>{renderTokens(headingToken.tokens || [], enableCodeApply, isDarkMode)}</Tag>;
                case 'hr':
                    return <hr key={index} />;
                case 'blockquote':
                    return <blockquote key={index}>{renderTokens((token as Tokens.Blockquote).tokens || [], enableCodeApply, isDarkMode)}</blockquote>;
                case 'space': // Usually ignored
                    return null;

                // --- Fallback ---
                case 'unknown':
                default:
                    console.warn("Unhandled token type in renderTokens switch:", token.type, token);
                    // Attempt to render raw text if available
                    return hasText(tokenWithText) ? <span key={index}>{tokenWithText.text}</span> : null;
            }
        } catch (error) {
            console.error(`Error rendering token index ${index} (type: ${token.type}):`, error);
            // Fallback for errors during rendering a specific token
            return <div key={index} style={{ color: 'red' }}>[Error rendering content]</div>;
        }
    });
};

interface MarkdownRendererProps {
    markdown: string;
    enableCodeApply: boolean;
    renderPath?: RenderPath;
}

// Configure marked options
marked.setOptions({
    gfm: true,
    breaks: false,
    pedantic: false
});

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = memo(({ markdown, enableCodeApply }) => {
    console.debug('MarkdownRenderer received:', {
        markdownLength: markdown?.length || 0,
        hasCodeBlock: markdown?.includes('```'),
        firstLine: markdown?.split('\n')[0],
        enableCodeApply
    });
    
    const { isDarkMode } = useTheme();

    interface CodeToken {
        type: 'code';
        lang?: string;
        text: string;
    }

    const isCodeToken = (token: Tokens.Generic): token is Tokens.Code => {
        return token.type === 'code';
    };

    const tokens = useMemo(() => {
        if (!markdown?.trim()) {
            return [];
        }

        const lexedTokens = marked.lexer(markdown);
        console.debug('Lexer output:', {
            tokenCount: lexedTokens.length,
            tokens: lexedTokens.map(t => ({
                type: t.type,
                lang: t.type === 'code' ? (t as any).lang : undefined,
                preview: t.type === 'code' ?
                    (t as any).text?.substring(0, 50) : undefined
            }))
        });
        const codeBlocks = new Map<string, {lang?: string}>();

        // Build a map of code block content to their detected languages
        lexedTokens.filter(isCodeToken).forEach(token => {
            codeBlocks.set(token.text, {lang: token.lang});
        });

        console.debug('Marked lexer found code blocks:', {
            count: codeBlocks.size,
            languages: Array.from(codeBlocks.values()).map(b => b.lang || 'text')
        });

        const processedTokens: TokenWithText[] = [];

        const isDiffContent = (text: string): boolean => {
            // Must have either git diff header or unified diff header
            const hasGitHeader = text.trim().startsWith('diff --git');
            const hasUnifiedHeader = text.match(/^---\s+a\/.*\n\+\+\+\s+b\/.*$/m) !== null;
            const hasHunkHeader = text.match(/^@@\s+-\d+,?\d*\s+\+\d+,?\d*\s+@@/m) !== null;

            return (hasGitHeader || hasUnifiedHeader) && hasHunkHeader;
        };

        // Split content by code blocks first
        const parts = markdown.split(/(```diff[\s\S]*?```|```[\s\S]*?```)/g);
        
        // Helper to clean line numbers from diff content (gemini is insisting on doing this despite instructions)
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
                // Handle content lines with various line number formats
                // Matches: [NNN ], [NNN+], [NNN,+], [NNN*]
                const match = line.match(/^(\s*)([+-]+\s*)?\[(\d+)(?:[+*]|\s*,\s*[+-\s])?\s*\](\s*)(.*?)$/);
                if (match) {
                    const [_, leadingSpace, marker, _num, postSpace, content] = match;
                    // Preserve exact whitespace and handle markers
                    if (marker && marker.trim()) {
                        // For add/remove lines, keep original marker and all whitespace
                        return `${marker.trim()}${postSpace.substring(1)}${content}`;
                    } else {
                        // For context lines, ensure we have a space marker
                        return `${postSpace}${content}`;
                    }
                }
                return line;
            });
            return cleanedLines.join('\n');
        };

        parts.forEach((part, index) => {
            if (!part.trim()) return; // Skip empty parts
            // Check if this part is a diff code block
            const isDiffBlock = part.startsWith('```diff') || (part.startsWith('```') && isDiffContent(part));
            const isCodeBlock = part.startsWith('```');

            if (isDiffBlock) {
                // Extract diff content from between the backticks
                const diffContent = part.replace(/^```diff\n/, '').replace(/```$/, '');
                const cleanedDiff = cleanDiffContent(diffContent);
                processedTokens.push({
                    type: 'code',
                    text: cleanedDiff,
                    lang: 'diff'
                });
            } else if (isCodeBlock) {
                // Handle other code blocks
                const match = part.match(/^```(\w+)?\n([\s\S]*?)```$/);
                if (match) {
                    const content = match[2];
                    // use lexer-detected language if available, otherwise use the one from markdown
                    const detectedBlock = codeBlocks.get(content);
                    processedTokens.push({
                        type: 'code',
                        text: content,
                        lang: detectedBlock?.lang || match[1] || 'plaintext'
                    });
                }
            } else {
                // Check if this part contains a raw diff
                if (part.trim().startsWith('diff --git') ||
                    part.match(/^---\s+a\/.*\n\+\+\+\s+b\/.*$/m)) {
                    const cleanedDiff = cleanDiffContent(part);
                    processedTokens.push({
                        type: 'code',
                        text: cleanedDiff,
                        lang: 'diff'
                    });
                } else {
                    // Process regular text through marked
                    const regularTokens = marked.lexer(part) as TokenWithText[];
                    processedTokens.push(...regularTokens);
                }
            }
        });

        return processedTokens;
    }, [markdown]);

    const renderedContent = useMemo(() => {
	return renderTokens(tokens, enableCodeApply, isDarkMode);
    }, [tokens, enableCodeApply, isDarkMode]);

    return <div>{renderedContent}</div>;
});

export default MarkdownRenderer;
