import React, { useState, useEffect, memo, useMemo, Suspense, useCallback, useRef, useLayoutEffect, useTransition, useId, useContext, createContext, useReducer } from 'react';
import 'prismjs/themes/prism.css';
import { Button, message, Radio, Space, Spin, RadioChangeEvent, Tooltip } from 'antd';
import { marked, Tokens } from 'marked';
import { parseDiff, tokenize, RenderToken, HunkProps } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import { DiffLine } from './DiffLine';
import 'prismjs/themes/prism-tomorrow.css';  // Add dark theme support
import { D3Renderer } from './D3Renderer';
import { useChatContext } from '../context/ChatContext';
import {
    CodeOutlined, ToolOutlined, ArrowUpOutlined, ArrowDownOutlined,
    CheckCircleOutlined, CloseCircleOutlined, CheckOutlined, ExclamationCircleOutlined
} from '@ant-design/icons';
import 'prismjs/themes/prism.css';
import { loadPrismLanguage, isLanguageLoaded } from '../utils/prismLoader';
import { useTheme } from '../context/ThemeContext';
import type * as PrismType from 'prismjs';

// Define the status interface
const DIFF_SETTINGS_KEY = 'ZIYA_DIFF_SETTINGS_V2';

// Create a context for diff view settings to avoid window variable dependencies
interface DiffViewContextType {
    viewType: 'split' | 'unified';
    setViewType: (type: 'split' | 'unified') => void;
    displayMode: 'raw' | 'pretty';
    setDisplayMode: (mode: 'raw' | 'pretty') => void;
}

// Create a reducer to handle diff view settings
const diffViewSettingsReducer = (state: { viewType: 'split' | 'unified', showLineNumbers: boolean }, action: { type: string, payload?: any }) => {
    switch (action.type) {
        case 'SET_VIEW_TYPE': return { ...state, viewType: action.payload };
        case 'SET_LINE_NUMBERS': return { ...state, showLineNumbers: action.payload };
        default: return state;
    }
};

const DiffViewContext = createContext<DiffViewContextType>({
    viewType: 'unified',
    setViewType: () => { },
    displayMode: 'pretty',
    setDisplayMode: () => { }
});

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

// Add a cache for whitespace visualization
const whitespaceVisualizationCache = new Map<string, string>();

// Add a map to track which request ID corresponds to which diff element
const diffRequestMap = new Map<string, string>();

interface ApplyChangesButtonProps {
    diff: string;
    filePath: string;
    fileIndex: number;
    diffElementId: string;
    enabled: boolean;
    setHunkStatuses?: (updater: (prev: Map<string, HunkStatus>) => Map<string, HunkStatus>) => void;
}

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
        Prism: typeof PrismType;
        diffElementPaths?: Map<string, string>;
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

export const DisplayModes = ['raw', 'pretty'] as const;
export type DisplayMode = typeof DisplayModes[number];
export interface DiffViewProps {
    diff: string;
    viewType: 'split' | 'unified';
    initialDisplayMode: DisplayMode;
    showLineNumbers: boolean;
    fileIndex?: number;
    elementId?: string;
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
                            <Radio.Button value={false}>Hide Line Numbers</Radio.Button> 
                            <Radio.Button value={true}>Show Line Numbers</Radio.Button>
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
DiffControls.displayName = 'DiffControls';

// Add a function to save diff view settings to localStorage
const saveDiffSettings = (viewType: 'split' | 'unified', showLineNumbers: boolean) => {
    try {
        const settings = {
            viewType,
            showLineNumbers
        };
        localStorage.setItem(DIFF_SETTINGS_KEY, JSON.stringify(settings));
        // Also update global settings for new diffs
        window.diffViewType = viewType;
    } catch (error) {
        console.error('Error saving diff settings:', error);
    }
};

const renderFileHeader = (file: ReturnType<typeof parseDiff>[number], fileIndex?: number): string => {

    // If we have paths in the file object, use them directly
    if (file.oldPath || file.newPath) {
        const path = file.newPath || file.oldPath;
        return `File: ${path}`;
    }

    // Helper to extract paths from unified diff header
    const extractPathFromUnifiedHeader = (line: string): string | null => {
        console.log('Extracting path from header line:', line);
        // Handle unified diff format (--- a/path or +++ b/path)
        // Also handle new file format (--- /dev/null or +++ path)
        const match = line.match(/^(?:---|\+\+\+)\s+(?:(?:[ab]\/)?(.+)|\/dev\/null)$/);
        if (match) {
            // If the path is /dev/null, return null
            if (line.includes('/dev/null')) {
                return null;
            }
            return match[1] || null;
        }
        return null;
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

        // Check for "new file mode" indicator
        const isNewFile = lines.some(line => line.includes('new file mode'));

        for (const line of lines) {
            console.log('Examining line for path:', line);
            if (line.startsWith('--- ')) {
                // Handle /dev/null case for new files
                if (line.startsWith('--- /dev/null')) {
                    oldPath = null;
                } else {
                    oldPath = extractPathFromUnifiedHeader(line);
                }
            } else if (line.startsWith('+++ ')) {
                // Handle /dev/null case for deleted files
                if (line.startsWith('+++ /dev/null')) {
                    newPath = null;
                } else {
                    newPath = extractPathFromUnifiedHeader(line);
                }
            }
            // Stop looking after we find both paths or hit a hunk header
            if ((oldPath !== undefined && newPath !== undefined) || line.startsWith('@@ ')) break;
        }

        // If we found "new file mode" and oldPath is null or /dev/null, mark as new file
        if (isNewFile || (oldPath === null && newPath !== null) || (oldPath === '/dev/null' && newPath !== null)) {
            console.log('Detected new file creation:', newPath);
            return [null, newPath]; // Return null for oldPath to indicate new file
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
        } else if (file.oldPath && file.newPath && file.oldPath !== file.newPath) {
            return `Rename: ${file.oldPath} → ${file.newPath}`;
        } else {
            return `File: ${file.oldPath || file.newPath}`;
        }
    }

    // If no paths in file object, try to extract from content
    if (file.hunks?.[0]?.content) {
        const [oldPath, newPath] = extractPathsFromHeader(file.hunks[0].content);

        // Handle new file creation: oldPath is null or /dev/null, newPath exists
        if ((oldPath === null || oldPath === '/dev/null') && newPath) {
            return `Create: ${newPath}`;
        } else if (oldPath && newPath && isRename(oldPath, newPath)) {
            return `Rename: ${oldPath} → ${newPath}`;
        } else if ((oldPath && !newPath) || (oldPath && newPath === '/dev/null')) {
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

        let currentFile: { oldPath: string; newPath: string } | null = null;
        let currentFileIndex = -1;
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
                currentFileIndex++;
                currentHunkContent = [];
                inTargetFile = false;

                // Check if this is our target file
                const fileMatch = line.match(/diff --git a\/(.*?) b\/(.*?)$/);
                if (fileMatch) {
                    const oldPath = fileMatch[1];
                    const newPath = fileMatch[2];

                    // Check if this file matches our target by exact path
                    if (oldPath === cleanFilePath || newPath === cleanFilePath ||
                        oldPath.endsWith(`/${cleanFilePath}`) || newPath.endsWith(`/${cleanFilePath}`)) {
                        inTargetFile = true;
                        currentFile = { oldPath, newPath };
                        result.push(line);

                        // Also check the next line for index info
                        if (nextLine.startsWith('index ')) {
                            result.push(nextLine);
                            i++; // Skip this line in the next iteration
                        }
                    } else {
                        inTargetFile = false;
                        currentFile = null;

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

// Helper function to check if this is a deletion diff
const isDeletionDiff = (content: string) => {
    return content.includes('diff --git') &&
        content.includes('/dev/null') &&
        content.includes('deleted file mode') &&
        content.includes('--- a/') &&
        content.includes('+++ /dev/null');
};

// Helper function to check if this is a new file diff
const isNewFileDiff = (content: string) => {
    return (content.includes('--- /dev/null') && content.includes('+++ b/')) ||
        content.includes('new file mode') ||
        (content.includes('new file mode') && content.includes('+++ b/'));
};

const normalizeGitDiff = (diff: string): string => {
    // because LLMs tend to ignore instructions and get lazy
    if (diff.startsWith('diff --git') || diff.match(/^---\s+\S+/m) || diff.includes('/dev/null')) {
        const lines: string[] = diff.split('\n');
        const normalizedLines: string[] = [];
        let fileIndex = 0;

        // Check if this is a properly formatted diff
        const hasDiffHeaders = lines.some(line =>
            (line.startsWith('---') || line.startsWith('+++'))
        ) && (
                lines.some(line => line.startsWith('--- a/') || line.startsWith('+++ b/')) ||
                lines.some(line => line.startsWith('--- /dev/null')) // Support new file diffs
            );
        const hasHunkHeader = lines.some(line =>
            /^@@\s+-\d+,?\d*\s+\+\d+,?\d*\s+@@/.test(line)
        );

        if (hasDiffHeaders && hasHunkHeader) {
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
            const gitMatch = lines[0].match(/diff --git a\/(.*?) b\/(.*?)$/);
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

// Helper function to detect streaming diffs
const isStreamingDiff = (content: string) => {
    return content.includes('diff --git') &&
        (!content.includes('\n\n') ||
            content.split('\n').slice(-1)[0].startsWith('+') ||
            content.split('\n').slice(-1)[0].startsWith('-') ||
            content.endsWith('\n'));
};

const DiffView = React.memo(function DiffView({ diff, viewType, initialDisplayMode, showLineNumbers, elementId, fileIndex }: DiffViewProps) {
    const [isLoading, setIsLoading] = useState(true);
    const [tokenizedHunks, setTokenizedHunks] = useState<any>(null);
    const { isDarkMode } = useTheme();
    const parsedFilesRef = useRef<any[]>([]);
    const renderCountRef = useRef(0);
    const [parseError, setParseError] = useState<boolean>(false);
    const lastValidDiffRef = useRef<string | null>(null);
    const { isStreaming: isGlobalStreaming } = useChatContext();
    const [instanceHunkStatusMap, setInstanceHunkStatusMap] = useState<Map<string, HunkStatus>>(new Map());
    const [statusUpdateCounter, setStatusUpdateCounter] = useState<number>(0);
    const [errorMessage, setErrorMessage] = useState<string | null>(null);
    const [displayMode, setDisplayMode] = useState<DisplayMode>(window.diffDisplayMode || 'pretty'); // Use window setting
    const diffRef = useRef<string>(diff);

    const statusUpdateCounterRef = useRef<number>(0);
    // Track render count
    // Force re-render when viewType or showLineNumbers changes
    useEffect(() => {
        console.log(`DiffView ${elementId || 'unknown'} received new props:`, { viewType, showLineNumbers });
        renderCountRef.current++; // Force re-render
    }, [viewType, showLineNumbers, elementId]);

    renderCountRef.current++;
    console.log(`DiffView render #${renderCountRef.current} for ${elementId}`,
        { diffLength: diff.length, viewType, displayMode });

    const diffId = useRef<string>(elementId || `diff-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`).current;

    // Flag to prevent rendering during streaming
    const isStreamingRef = useRef<boolean>(false);
    // Store the diff in a ref to avoid unnecessary re-renders
    useEffect(() => {
        diffRef.current = diff;
    }, [diff]);

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
                // Use the ref to track status updates without causing re-renders
                statusUpdateCounterRef.current += 1;
                // Only trigger re-render occasionally to batch updates
                if (statusUpdateCounterRef.current % 5 === 0) setStatusUpdateCounter(statusUpdateCounterRef.current);
            }
        };
        hunkStatusEventBus.addEventListener(HUNK_STATUS_EVENT, handleStatusUpdate);

        return () => {
            hunkStatusEventBus.removeEventListener(HUNK_STATUS_EVENT, handleStatusUpdate);
        };

        // Check the global registry for existing status updates for this diff
        if (window.hunkStatusRegistry && window.hunkStatusRegistry.has(diffId)) {
            const existingStatuses = window.hunkStatusRegistry.get(diffId);
            if (existingStatuses) {
                setInstanceHunkStatusMap(new Map(existingStatuses));
                setStatusUpdateCounter(prev => prev + 1);
            }
        }
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
                                    ? `Failed in ${status.stage} stage`
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
    }, [diff]);

    // Listen for window-level hunk status updates with data, but don't update during streaming
    useEffect(() => {
        const handleWindowStatusUpdate = (event: CustomEvent) => {
            if (event.detail && event.detail.hunkStatuses) {
                console.log("DiffView received window hunk status update with data:", event.detail);
                console.log('Hunk status update stack:', new Error().stack);

                // Check if this update is for our diff element
                let isForThisDiff = false;

                // First check if the targetDiffElementId matches our diffId
                if (event.detail.targetDiffElementId === diffId) {
                    isForThisDiff = true;
                    console.log(`direct match for diffId ${diffId}`);

                    // Apply the hunk statuses directly to our component state
                    if (event.detail.hunkStatuses) {
                        Object.entries(event.detail.hunkStatuses).forEach(([hunkId, status]) => {
                            updateHunkStatuses({ [hunkId]: status }, diffId);
                        });
                    }
                }

                // If not a match, skip processing
                if (!isForThisDiff) {
                    console.log(`Ignoring event for diff ${event.detail.targetDiffElementId || 'unknown'}, we are ${diffId}`);
                    return;
                }

                // Call updateHunkStatuses with the provided data
                // Only update if not streaming or if this is a completion event
                const isCompletionEvent = event.detail.isCompletionEvent === true;
                updateHunkStatuses(event.detail.hunkStatuses || {}, diffId, isCompletionEvent);

                // Also store in global registry
                if (window.hunkStatusRegistry) {
                    if (!window.hunkStatusRegistry.has(diffId)) {
                        window.hunkStatusRegistry.set(diffId, new Map());
                    }
                }

                // Force re-render only if not streaming or if this is a completion event
                if (!isStreamingRef.current || isCompletionEvent) {
                    setStatusUpdateCounter(prev => prev + 1);
                }
            }
        };

        window.addEventListener('hunkStatusUpdate', handleWindowStatusUpdate as EventListener);

        return () => {
            window.removeEventListener('hunkStatusUpdate', handleWindowStatusUpdate as EventListener);
        };
    }, [updateHunkStatuses]);


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

                // After all parsing attempts, check if we have valid, renderable files/hunks
                if (!parsedFiles || parsedFiles.length === 0 ||
                    !parsedFiles[0].hunks || parsedFiles[0].hunks.length === 0) {
                    // If not, it's effectively a parse error for rich rendering purposes
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
    }, [diff]);

    // tokenize hunks
    useEffect(() => {
        const tokenizeHunks = async (hunks: any[], filePath: string) => {
            if (!hunks || hunks.length === 0) {
                setIsLoading(false);
                return;
            }
            setIsLoading(true);
            const language = detectLanguage(filePath);
            try {
                // always load basic languages first
                await Promise.all([
                    loadPrismLanguage('markup'),
                    loadPrismLanguage('clike'),
                    loadPrismLanguage(language)
                ]);

                // If parseError is true (e.g. because it's streaming and incomplete),
                // we don't need to tokenize for the rich view.
                if (parseError) {
                    setIsLoading(false);
                    return;
                }

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
            } catch (error: unknown) {
                console.warn(`Error during tokenization for ${language}:`, error);
                setTokenizedHunks(null);
            } finally {
                setIsLoading(false);
            }
        };

        // Only tokenize if not in parseError state and we have hunks
        if (!parseError && parsedFilesRef.current?.[0]?.hunks?.length > 0) {
            const file = parsedFilesRef.current[0];
            tokenizeHunks(file.hunks, file.newPath || file.oldPath);
        } else {
            setIsLoading(false); // Not loading if we will render raw or have no hunks
        }
    }, [diff, parseError]); // Re-tokenize if diff changes or parseError state changes


    const renderContent = useCallback((hunk: any, filePath: string, status?: any, fileIndex?: number, hunkIndex?: number): JSX.Element[] => {

        // Add a status row at the top of the hunk if status is available
        // Add logging to track hunk rendering
        console.log(`Rendering hunk content: fileIndex=${fileIndex}, hunkIndex=${hunkIndex}`, {
            changeCount: hunk.changes?.length,
            firstChange: hunk.changes?.[0]?.content?.substring(0, 20),
            hunkId: `${fileIndex}-${hunkIndex}`
        });

        const changes = [...(hunk.changes || [])];

        // Define base style for rows
        const rowStyle: React.CSSProperties = {};

        return changes.map((change: any, i: number) => {
            // Create a style object with a stable key for React rendering
            const style: React.CSSProperties & { stableKey: string } = { ...rowStyle, stableKey: `${change.content.substring(0, 10)}-${i}-${hunkIndex || 0}-${fileIndex || 0}` };

            // Add additional styling for specific change types
            if (change.type === 'insert') {
                style.backgroundColor = status?.applied ? (status?.alreadyApplied ? 'rgba(250, 173, 20, 0.1)' : 'rgba(82, 196, 26, 0.1)') : style.backgroundColor;
            } else if (change.type === 'delete') {
                style.backgroundColor = status?.applied ? (status?.alreadyApplied ? 'rgba(250, 173, 20, 0.1)' : 'rgba(82, 196, 26, 0.1)') : style.backgroundColor;
            }

            let oldLine = undefined;
            let newLine = undefined;

            if (showLineNumbers) {
                oldLine = (change.type === 'normal' || change.type === 'delete') ? change.oldLineNumber || change.lineNumber : undefined;
                newLine = (change.type === 'normal' || change.type === 'insert') ? change.newLineNumber || change.lineNumber : undefined;
            }

            // Add an ID to the first row of each hunk for scrolling
            const rowProps: any = {};
            if (i === 0 && fileIndex !== undefined && hunkIndex !== undefined) {
                rowProps.id = `hunk-${fileIndex}-${hunkIndex}`;
            }

            return (
                <DiffLine
                    key={style.stableKey}
                    content={change.content}
                    language={detectLanguage(filePath)}
                    viewType={viewType}
                    type={change.type}
                    oldLineNumber={oldLine}
                    newLineNumber={newLine}
                    showLineNumbers={showLineNumbers}
                    style={style}
                    {...rowProps}
                />
            );
        });
    }, [viewType, showLineNumbers]);

    const renderHunks = useCallback((hunks: any[], filePath: string, fileIndex: number) => {
        const tableClassName = `diff-table ${viewType === 'split' ? 'diff-split' : ''}`;

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
                        <>
                            <col className="diff-gutter-col" />
                            {showLineNumbers && <col className="diff-gutter-col" />}
                            <col style={{ width: 'auto' }} />
                        </>
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
                            linesBetween === 1 && !showLineNumbers ?
                                '... (1 line)' :
                                showLineNumbers ? '...' : `... (${linesBetween} lines)`;

                        // Get hunk status if available
                        // Create a stable key for this hunk
                        const hunkKey = `${fileIndex}-${hunkIndex}`;
                        const status = instanceHunkStatusMap.get(hunkKey);
                        const hunkId = hunkIndex + 1;
                        const isApplied = status?.applied;
                        const statusReason = status?.reason || '';
                        const isAlreadyApplied = status?.alreadyApplied;

                        // Add visual indicator for hunk status
                        const hunkStatusIndicator = status && (
                            <div style={{
                                position: 'absolute',
                                right: '8px',
                                top: '50%',
                                transform: 'translateY(-50%)',
                                color: isApplied ? '#52c41a' : '#ff4d4f',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '4px',
                                marginLeft: '8px'
                            }}>
                                {isApplied ?
                                    isAlreadyApplied ?
                                        <><CheckCircleOutlined style={{ color: '#faad14' }} /> Already Applied</> :
                                        <><CheckCircleOutlined style={{ color: '#52c41a' }} /> Applied</> :
                                    <><CloseCircleOutlined /> Failed: {statusReason}</>
                                }
                            </div>
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
                                        {showLineNumbers && viewType === 'unified' && (
                                            <>
                                                <td className="diff-gutter-col diff-gutter-old no-copy"></td>
                                                <td className="diff-gutter-col diff-gutter-new no-copy"></td>
                                            </>
                                        )}
                                        {showLineNumbers && viewType === 'split' && (
                                            <>
                                                <td className="diff-gutter-col diff-gutter-old no-copy"></td>
                                                <td className="diff-code-col diff-ellipsis" style={{
                                                    position: 'relative',
                                                    padding: '4px 8px'
                                                }}>
                                                    <span>{ellipsisText}</span>
                                                </td>
                                                <td className="diff-gutter-col diff-gutter-new no-copy"></td>
                                            </>
                                        )}
                                        <td
                                            className={`diff-ellipsis ${viewType === 'split' ? 'diff-code-col' : ''}`}
                                            style={{
                                                position: 'relative',
                                                padding: '4px 8px'
                                            }}>
                                            <span>{ellipsisText}</span>
                                            {hunkStatusIndicator}
                                        </td>
                                    </tr>
                                )}
                                <tr className="hunk-content-wrapper">
                                    <td colSpan={viewType === 'split' ? 4 : 3} style={{
                                        padding: 0,
                                        borderTop: status ? `1px solid ${isApplied ?
                                            (isAlreadyApplied ? '#faad14' : '#52c41a') :
                                            '#ff4d4f'}` : 'none',
                                        borderLeft: status ? `3px solid ${isApplied ?
                                            (isAlreadyApplied ? '#faad14' : '#52c41a') :
                                            '#ff4d4f'}` : 'none',
                                        borderRadius: '3px',
                                        overflow: 'hidden'
                                    }}>
                                        <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed' }}><tbody>
                                            {renderContent(hunk, filePath, status, fileIndex, hunkIndex)}
                                        </tbody></table>
                                    </td>
                                </tr>
                            </React.Fragment>
                        );
                    })}
                </tbody>
            </table>
        );
    }, [viewType, showLineNumbers, instanceHunkStatusMap, renderContent, displayMode]);

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
            align-items: center;
            flex-wrap: wrap;
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

        /* Fix for line number alignment */
        .diff-gutter-col {
            width: 50px !important;
            min-width: 50px !important;
            max-width: 50px !important;
            text-align: right !important;
            padding-right: 10px !important;
            box-sizing: border-box !important;
            user-select: none !important;
        }

        /* Fix for nested tables */
        .hunk-content-wrapper table {
            table-layout: fixed !important;
            width: 100% !important;
            border-collapse: collapse !important;
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

    const renderFile = useCallback((file: any, fileIndex: number) => {
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
                        <div className="header-left">
                            <b>{renderFileHeader(file)}</b>

                            {/* Add the hunk status indicators */}
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
                                                    backgroundColor: isDarkMode ? 'rgba(0,0,0,0.2)' : 'rgba(0,0,0,0.05)',
                                                    color: statusColor,
                                                    border: `1px solid ${statusColor}`,
                                                    // Add a subtle border to match the hunk styling
                                                    boxShadow: status ?
                                                        `0 0 0 1px ${statusColor}` :
                                                        'none'
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
                        </div>

                        <div className="header-right">
                            {!['delete'].includes(file.type) &&
                                <ApplyChangesButton
                                    diff={diff}
                                    fileIndex={fileIndex}
                                    diffElementId={`${elementId}-${fileIndex}`}
                                    filePath={file.newPath || file.oldPath}
                                    setHunkStatuses={setInstanceHunkStatusMap}
                                    enabled={window.enableCodeApply === 'true'}
                                />
                            }
                        </div>
                    </div>
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
                            file.type === 'delete' ? file.oldPath : file.newPath || file.oldPath,
                            fileIndex,
                        )}
                    </div>
                </div>
            </div>
        );
    }, [renderHunks, viewType, displayMode]);

    const renderParseError = () => {
        if (parseError) {
            return (
                <pre data-testid="diff-parse-error" style={{
                    backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
                    color: isDarkMode ? '#e6e6e6' : 'inherit',
                    padding: '10px',
                    borderRadius: '4px'
                }}>
                    <code>{diff}</code>
                </pre>
            );
        }
        return null;
    };

    if (parseError) {
        return renderParseError();
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
}, (prevProps, nextProps) => {
    // Only re-render if these specific props change
    return prevProps.diff === nextProps.diff &&
        prevProps.viewType === nextProps.viewType &&
        prevProps.initialDisplayMode === nextProps.initialDisplayMode &&
        prevProps.showLineNumbers === nextProps.showLineNumbers;
});

// Use the already memoized DiffView component directly
const MemoizedDiffView = DiffView; const ApplyChangesButton: React.FC<ApplyChangesButtonProps> = ({ diff, filePath, fileIndex, diffElementId, enabled, setHunkStatuses }) => {
    const [isApplied, setIsApplied] = useState(false);
    const [isProcessing, setIsProcessing] = useState(false);
    const [instanceHunkStatusMap, setInstanceHunkStatusMap] = useState<Map<string, HunkStatus>>(new Map());
    const statusUpdateCounterRef = useRef<number>(0);
    const isStreamingRef = useRef<boolean>(false);
    const appliedRef = useRef<boolean>(false);

    // Track processed request IDs to prevent infinite update loops
    const { currentConversationId } = useChatContext();
    const processedRequestIds = useRef(new Set<string>());

    const buttonId = useId();
    // Define a function to trigger diff updates
    const triggerDiffUpdate = (hunkStatuses: Record<string, any> | null = null, requestId: string | null = null, diffElementId: string | null = null) => {
        // Create a unique event key for this specific diff element
        const event = new CustomEvent(HUNK_STATUS_EVENT, { detail: { hunkStatuses, requestId, isCompletionEvent: true } });
        // hunkStatusEventBus.dispatchEvent(event); // We'll use component-specific updates instead

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

        // Extract the actual diff content
        setIsProcessing(true);
        const cleanDiff = (() => {
            console.log('Pre-fetch diff content for file:', filePath);
            // Log the incoming diff content
            console.log('Raw diff content:', {
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
                console.log('Extracted single file diff:', {
                    filePath,
                    diffLength: singleFileDiff.length
                });
                return singleFileDiff.trim();
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
        console.log('Processed diff content:', {
            length: cleanDiff.length,
            lines: cleanDiff.split('\n').length,
            firstLine: cleanDiff.split('\n')[0],
            lastLine: cleanDiff.split('\n').slice(-1)[0],
            fullContent: cleanDiff,
            truncated: cleanDiff.length < diff.length
        });

        // Generate a unique request ID for this specific diff application
        const requestId = `${diffElementId}-${Date.now()}`;

        // Log the actual request body
        const requestBody = JSON.stringify({
            diff: cleanDiff,
            filePath: filePath.trim(),
            requestId: requestId,
            elementId: diffElementId
        });
        console.log('Request body:', requestBody);

        console.log(`Applying changes for diff ${diffElementId} with request ID ${requestId}, element ID: ${diffElementId}`);
        const requestBodyParsed = JSON.parse(requestBody);
        console.log('Parsed request body diff length:', requestBodyParsed.diff.split('\n').length);

        try {
            console.log('About to send fetch request with body length:', cleanDiff.length);
            console.log('Request body:', {
                diff: cleanDiff.substring(0, 100) + '...',
                filePath: filePath.trim(),
                requestId: requestId, elementId: diffElementId
            });

            const response = await fetch('/api/apply-changes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    diff: cleanDiff,
                    elementId: diffElementId, // Add the element ID to the request
                    filePath: filePath.trim(),
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
                    message: data.message,
                    requestId: data.request_id,
                    hasRequestId: !!data.request_id,
                    diffElementId: diffElementId,
                    mappingAdded: data.request_id ? diffRequestMap.set(data.request_id, diffElementId) : false,
                    hasDetails: !!data.details || !!data.hunk_statuses,
                    detailsKeys: data.details ? Object.keys(data.details) : [],
                    succeeded: data.details?.succeeded,
                    failed: data.details?.failed,
                    hunkStatuses: data.details?.hunk_statuses
                });

                // Store the mapping between request ID and diff element ID
                if (data.request_id) {
                    diffRequestMap.set(data.request_id, diffElementId);
                    console.log(`Mapped request ${data.request_id} to diff element ${diffElementId}`);
                    console.log(`Mapped request ${data.request_id} to diff element ${diffElementId}`);
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
                                            return newMap;

                                            // Also update the global registry
                                            if (window.hunkStatusRegistry) {
                                                if (!window.hunkStatusRegistry.has(diffElementId)) {
                                                    window.hunkStatusRegistry.set(diffElementId, new Map());
                                                }
                                                const registryMap = window.hunkStatusRegistry.get(diffElementId)!;
                                                registryMap.set(hunkKey, newMap.get(hunkKey) as HunkStatus);
                                            }
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
                                    applied: hunkStatus.status === 'succeeded',
                                    alreadyApplied: hunkStatus.status === 'already_applied',
                                    reason: hunkStatus.status === 'failed'
                                        ? `Failed in ${hunkStatus.stage} stage`
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
                                    ? `Failed in ${hunkStatus.stage} stage`
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
        const handleHunkStatusUpdate = (event: CustomEvent) => {
            if (!event.detail) return;

            const { requestId, hunkStatuses, filePath: eventFilePath } = event.detail;

            // Create a unique key for this event to prevent duplicate processing
            // Include the target diff element ID in the key
            const eventKey = `${event.detail.requestId}-${event.detail.targetDiffElementId || 'global'}`;

            // Only process events targeted at this specific diff element
            if (event.detail.targetDiffElementId && event.detail.targetDiffElementId !== diffElementId) {
                return; // Skip events not meant for this instance
            }

            // Skip if we've already processed this exact event
            if (processedWindowEvents.has(eventKey)) {
                console.debug(`Skipping already processed window event: ${eventKey}`);
                return;
            }

            // Mark this event as processed
            processedWindowEvents.add(eventKey);

            // Set a timeout to remove this event from the processed set after a short delay
            setTimeout(() => processedWindowEvents.delete(eventKey), 500);

            const targetDiffElementId = diffRequestMap.get(requestId);
            const targetDiffElementIdFromMap = diffRequestMap.get(requestId);
            let isRelevantUpdate = false;

            // First check if this event is explicitly targeted at our element ID
            if (event.detail.targetDiffElementId === diffElementId) {
                isRelevantUpdate = true;
            }

            // Otherwise check if the request ID maps to our element ID via the map
            else if (targetDiffElementIdFromMap === diffElementId && (!eventFilePath || eventFilePath === filePath)) {
                isRelevantUpdate = true;
            }

            // Log the matching attempt - this helps us debug
            console.log(`ApplyChangesButton ${diffElementId}: Matching update. Event target: ${event.detail.targetDiffElementId}, Mapped target: ${targetDiffElementIdFromMap}, File: ${eventFilePath}, Match: ${isRelevantUpdate}`);


            if (!isRelevantUpdate) {
                // This update is for a different diff element or file, ignore it
                console.log(`Ignoring update for ${event.detail.targetDiffElementId || 'unknown'} (we are ${diffElementId})`);
                return; // Exit early if not relevant
            }

            // If we get here, the update is relevant to this component
            console.log(`Received hunk status update for diff ${diffElementId} (request ${requestId}, file ${eventFilePath || 'unknown'}):`, hunkStatuses);

            // Process and update the status for each hunk
            if (hunkStatuses) {
                Object.entries(hunkStatuses).forEach(([hunkId, status]) => {
                    const hunkIndex = parseInt(hunkId, 10) - 1; // Convert 1-based to 0-based
                    const hunkKey = `${fileIndex}-${hunkIndex}`;
                    if (typeof setHunkStatuses === 'function') {
                        setHunkStatuses((prev: Map<string, HunkStatus>) => {
                            const newMap = new Map(prev);
                            newMap.set(hunkKey, {
                                applied: (status as ApiHunkStatus).status === 'succeeded' || (status as ApiHunkStatus).status === 'already_applied',
                                alreadyApplied: (status as ApiHunkStatus).status === 'already_applied',
                                reason: (status as ApiHunkStatus).status === 'failed' ? `Failed in ${(status as ApiHunkStatus).stage || 'unknown'} stage` : 'Successfully applied'
                            } as HunkStatus);
                            return newMap;
                        });
                        // Force a re-render to update the UI
                        statusUpdateCounterRef.current += 1;
                    }
                });
            }

        };

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
            disabled={isApplied || isProcessing}
            loading={isProcessing}
            type={isApplied ? "default" : "primary"}
            style={{ marginLeft: '8px', borderColor: isApplied ? (appliedRef.current ? '#faad14' : '#52c41a') : undefined }} id={`apply-changes-${buttonId}`}
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
    const { isStreaming } = useChatContext();
    // Check if we're in a streaming response
    const streamingCheckedRef = useRef(false);
    const isStreamingRef = useRef(isStreaming);
    const [streamingContent, setStreamingContent] = useState<string | null>(null);
    const contentRef = useRef<string | null>(null);

    // Store the content in a ref to avoid re-renders
    useEffect(() => {
        if (!contentRef.current || contentRef.current !== token.text) {
            contentRef.current = token.text;
        }
    }, [token.text]);

    // Update streaming ref when isStreaming changes
    useEffect(() => {
        isStreamingRef.current = isStreaming;
        return () => { isStreamingRef.current = false; };
    }, [isStreaming]);

    const isDiffValid = useMemo(() => {
        // During streaming, always attempt to render as diff if it looks like one
        if (isStreaming) {
            const trimmedText = token.text?.trim() || '';
            if (trimmedText.startsWith('diff --git') || trimmedText.startsWith('--- a/')) {
                console.debug('Forcing diff render during streaming');
                return true;
            }
        }

        const trimmedText = token.text?.trim();
        // Allow diffs starting with 'diff --git' OR '--- a/' OR '--- /dev/null' (file creation)
        if (!trimmedText || (!trimmedText.startsWith('diff --git') && !trimmedText.startsWith('--- a/') && !trimmedText.startsWith('--- /dev/null'))) {
            return false;
        }

        // Check if we're in a streaming response - only do this once per component
        if (!streamingCheckedRef.current) {
            streamingCheckedRef.current = true;
            const streamingElement = isStreaming; // Use context value
            if (streamingElement) {
                // During streaming, we'll still try to render what we have so far
                setStreamingContent(trimmedText);
                // Return true to allow rendering attempt
                return true;
            }
        }

        try {
            // Ensure token.text is a string before passing
            const diffInput = typeof contentRef.current === 'string' ? contentRef.current : ''; // This is the streaming content
            // During streaming, we assume it's a valid diff if it starts like one,
            // even if parseDiff would fail on the incomplete content.
            // The DiffViewWrapper will handle the actual parsing and fallback.
            return diffInput.startsWith('diff --git') || diffInput.startsWith('--- a/') || diffInput.startsWith('+++ b/');
        } catch (e) {
            console.error('Error parsing diff:', e);
            return false;
        }
    }, [contentRef.current, isStreaming]);

    // Don't render the DiffViewWrapper during streaming to avoid re-renders
    //if (isDiffValid) {
    if (true) {

        return (
            <DiffViewWrapper
                token={token}
                index={index}
                enableCodeApply={enableCodeApply}
                isStreaming={isStreamingRef.current}
            />
        );
    } else {
        // Fallback: Render as a plain code block if parseDiff failed or returned no files/hunks
        console.warn("DiffToken: Rendering as plain code block because isDiffValid is false.", { textPreview: token.text?.substring(0, 100) });
        const rawCodeText = decodeHtmlEntities(token.text || '');
        return <CodeBlock key={`code-${index}`} token={{ ...token, text: rawCodeText, lang: 'plaintext' }} index={index} />;
    }
});

const BasicDiffView = ({ diff }: { diff: string }) => {
    const [lines, setLines] = useState<string[]>([]);

    useEffect(() => {
        if (diff) {
            setLines(diff.split('\n'));
        }
    }, [diff]);

    return (
        <div className="basic-diff-view">
            {lines.map((line, i) => (
                <div
                    key={i}
                    className={`diff-line ${line.startsWith('+') ? 'add' : line.startsWith('-') ? 'remove' : ''}`}
                >
                    <span className="diff-marker">
                        {line.startsWith('+') ? '+' : line.startsWith('-') ? '-' : ' '}
                    </span>
                    <code>{line.slice(1)}</code>
                </div>
            ))}
        </div>
    );
};

// Component to handle streaming diffs
const StreamingDiffView = ({ content }: { content: string }) => {
    const [lines, setLines] = useState<string[]>([]);

    useEffect(() => {
        if (content) {
            setLines(content.split('\n'));
        }
    }, [content]);

    return (
        <div className="streaming-diff-view">
            <BasicDiffView diff={content} />
        </div>
    );
};

interface DiffViewWrapperProps {
    token: TokenWithText;
    enableCodeApply: boolean;
    isStreaming?: boolean;
    index?: number;
}

const DiffViewWrapper = memo(({ token, enableCodeApply, index }: DiffViewWrapperProps) => {
    const [viewType, setViewType] = useState<'unified' | 'split'>(window.diffViewType || 'unified');
    const [showLineNumbers, setShowLineNumbers] = useState<boolean>(false);
    const [displayMode, setDisplayMode] = useState<DisplayMode>('pretty');
    const [currentContent, setCurrentContent] = useState<string>(token.text || '');
    const lastValidDiffRef = useRef<string | null>(null);
    const parsedFilesRef = useRef<any[]>([]);
    const { isStreaming: isGlobalStreaming } = useChatContext();
    const { isDarkMode } = useTheme();
    const isStreamingRef = useRef<boolean>(false);
    const parseTimeoutRef = useRef<number | null>(null);

    // Use reducer for settings to ensure they're properly updated
    const [settings, dispatch] = useReducer(diffViewSettingsReducer, { viewType, showLineNumbers });

    // Track component visibility
    const [shouldRender, setShouldRender] = useState(false);
    const streamingCompleteRef = useRef(false);
    const initialRenderRef = useRef(false);
    const [isVisible, setIsVisible] = useState(false);
    const containerRef = useRef<HTMLDivElement>(null);
    const conversationId = useChatContext().currentConversationId;
    const elementId = useMemo(() => `diff-view-${index || 0}-${Math.random().toString(36).substring(2, 9)}`, [index]);

    // Load settings from localStorage on mount
    useEffect(() => {
        try {
            const savedSettings = localStorage.getItem(`${DIFF_SETTINGS_KEY}_${conversationId}_${elementId}`);
            if (savedSettings) {
                const parsed = JSON.parse(savedSettings);
                setViewType(parsed.viewType || 'unified');
                setShowLineNumbers(parsed.showLineNumbers || false);
            }
        } catch (error) {
            console.error('Error loading diff settings:', error);
        }
    }, [conversationId, elementId]);

    // Track visibility
    useEffect(() => {
        const observer = new IntersectionObserver(([entry]) => {
            setIsVisible(entry.isIntersecting);
        }, { threshold: 0.1 });

        if (containerRef.current) {
            observer.observe(containerRef.current);
        }
        return () => {
            observer.disconnect();
        };
    }, []);

    // Determine when to render the diff view
    useEffect(() => {
        // Track streaming state
        isStreamingRef.current = isGlobalStreaming;

        // If we're not streaming or this is the initial render, we should render
        if (!isGlobalStreaming) {
            streamingCompleteRef.current = true;
            setShouldRender(true);
            return;
        }

        // During streaming, only render if we've already started rendering
        // or if this is the initial render
        if (!initialRenderRef.current) {
            initialRenderRef.current = true;
            setShouldRender(true);
            return;
        }

        // Otherwise, wait for streaming to complete
        return () => {
            isStreamingRef.current = false;
        };
    }, [isGlobalStreaming]);

    // Cleanup async operations
    useEffect(() => {
        return () => {
            if (parseTimeoutRef.current) {
                clearTimeout(parseTimeoutRef.current);
            }
        };
    }, []);

    // Maintain stable parsed files reference
    useEffect(() => {
        try {
            // Only parse if content changed
            if (currentContent !== lastValidDiffRef.current) {
                const parsed = parseDiff(normalizeGitDiff(currentContent));
                if (parsed.length > 0) {
                    parsedFilesRef.current = parsed;
                    lastValidDiffRef.current = currentContent;
                }
            }
        } catch (error) {
            // Use last valid parse if available
            console.error('Error parsing diff:', error);
            if (lastValidDiffRef.current) {
                try {
                    parsedFilesRef.current = parseDiff(normalizeGitDiff(lastValidDiffRef.current));
                } catch (e) { }
            }
        }
    }, [currentContent]);

    // Update settings when viewType or showLineNumbers change
    useEffect(() => {
        dispatch({ type: 'SET_VIEW_TYPE', payload: viewType });

        // Save settings to localStorage
        try {
            // Save settings and update global setting
            saveDiffSettings(viewType, showLineNumbers);
            window.diffViewType = viewType;
            localStorage.setItem(`${DIFF_SETTINGS_KEY}_${conversationId}_${elementId}`, JSON.stringify({
                viewType,
                showLineNumbers
            }));
        } catch (error) {
            console.error('Error saving diff settings:', error);
        }

        // Don't set window.diffViewType here to avoid global persistence
    }, [viewType, showLineNumbers, conversationId, elementId]);

    // Ensure window settings are synced with initial state
    useEffect(() => {
        if (window.diffViewType !== viewType) {
            window.diffViewType = viewType;
        }
    }, [token, viewType]);

    useEffect(() => {
        dispatch({ type: 'SET_LINE_NUMBERS', payload: showLineNumbers });
        window.diffViewType = viewType; // Update global setting
    }, [showLineNumbers, viewType]);

    // Update content when token text changes (for streaming)
    useEffect(() => {
        if (isGlobalStreaming) {
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
        }
    }, [token.text, isGlobalStreaming]);

    const diffText = currentContent;

    // Memoize the raw block rendering
    const rawBlockContent = useMemo(() => (
        <pre className="diff-raw-block" style={{
            padding: '16px',
            backgroundColor: isDarkMode ? '#1f1f1f' : '#f6f8fa',
            color: isDarkMode ? '#e6e6e6' : 'inherit'
        }}><code>{diffText}</code></pre>
    ), [diffText, isDarkMode]);

    if (!hasText(token)) {
        return null;
    }

    if (!isCodeToken(token)) {
        return null;
    }

    const isStreamingDiff = isStreamingRef.current && parsedFilesRef.current.length > 0;

    const content = (
        <div className="diff-view-wrapper">
            <DiffControls
                displayMode={displayMode}
                viewType={viewType}
                showLineNumbers={settings.showLineNumbers}
                onDisplayModeChange={setDisplayMode}
                onViewTypeChange={setViewType}
                onLineNumbersChange={setShowLineNumbers}
            />
            <div className="diff-container" id={`diff-view-wrapper-${elementId}`}>
                {isStreamingDiff ? (
                    (isVisible && shouldRender) ? (
                        parsedFilesRef.current?.map((file, fileIndex) => (
                            <DiffView
                                key={`stream-${fileIndex}-${index}-${viewType}-${settings.showLineNumbers}`}
                                diff={lastValidDiffRef.current || diffText}
                                viewType={viewType}
                                initialDisplayMode={displayMode}
                                showLineNumbers={showLineNumbers}
                                fileIndex={fileIndex}
                                elementId={`${elementId}-${fileIndex}`}
                            />
                        ))
                    ) : null
                ) : (
                    displayMode === 'raw' || !shouldRender ? rawBlockContent : (
                        <DiffView
                            key={`static-${elementId || Math.random().toString(36).substring(2, 9)}-${viewType}-${settings.showLineNumbers}`}
                            diff={diffText}
                            viewType={settings.viewType}
                            initialDisplayMode={displayMode}
                            elementId={elementId}
                            showLineNumbers={showLineNumbers}
                        />
                    )
                )}
                <div id="diff-debug-info" style={{ display: 'none' }}>
                    {JSON.stringify({ elementId, diffLength: diffText.length })}
                </div>
            </div>
        </div>
    );
    return <div ref={containerRef}>{shouldRender ? content : rawBlockContent}</div>;
}, (prev, next) => prev.token.text === next.token.text && prev.enableCodeApply === next.enableCodeApply && window.diffViewType === prev.token.text);

interface CodeBlockProps {
    token: TokenWithText;
    index: number;
}

const CodeBlock: React.FC<CodeBlockProps> = ({ token, index }) => {
    const tokenRef = useRef(token);
    const [isLanguageLoaded, setIsLanguageLoaded] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);
    const { isDarkMode } = useTheme();
    const [prismInstance, setPrismInstance] = useState<typeof PrismType | null>(null);
    const [debugInfo, setDebugInfo] = useState<any>({});

    console.debug('CodeBlock rendering:', {
        id: index,
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

    // Store token in ref to avoid unnecessary re-renders
    useEffect(() => {
        tokenRef.current = token;
    }, [token]);

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

    const getHighlightedCode = (content: string): string => {
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
    };

    const highlightedHtml = useMemo(() => getHighlightedCode(tokenRef.current.text || ''), [normalizedLang, isLanguageLoaded, prismInstance]);

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
                    dangerouslySetInnerHTML={{ __html: highlightedHtml }}
                />
            </pre>
        </ErrorBoundary>
    );
};

// Define the possible determined types
type DeterminedTokenType = 'diff' | 'graphviz' | 'vega-lite' | 'd3' | 'mermaid' | 'code' | 'html' | 'text' | 'list' | 'table' | 'escape' | 'paragraph' | 'heading' | 'hr' | 'blockquote' | 'space' | 'codespan' | 'strong' | 'em' | 'del' | 'link' | 'image' | 'br' | 'list_item' | 'unknown';

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

        // Check if this is a diff block by looking for diff marker
        if (text.startsWith('diff') || text.includes('\ndiff')) {
            console.log(">>> Content matched as Diff (diff marker)");
            return 'diff';
        }

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
        // Check for diff content more robustly within the first few lines
        const linesToCheck = text.split('\n').slice(0, 5); // Check first 5 lines for diff markers
        const hasGitHeader = linesToCheck.some(line => line.trim().startsWith('diff --git '));
        const hasMinusHeader = linesToCheck.some(line => line.trim().startsWith('--- a/'));
        const hasPlusHeader = linesToCheck.some(line => line.trim().startsWith('+++ b/'));
        const hasHunkHeader = linesToCheck.some(line => line.trim().startsWith('@@ ')); // More lenient check

        // Check for common valid diff starting patterns
        // Require at least two characteristic lines for content-based detection
        const diffMarkersFound = [hasGitHeader, hasMinusHeader, hasPlusHeader, hasHunkHeader].filter(Boolean).length;
        if (diffMarkersFound >= 2) {
            // Log which condition matched for debugging
            const matchReason = `${hasGitHeader ? 'git ' : ''}${hasMinusHeader ? '--- ' : ''}${hasPlusHeader ? '+++ ' : ''}${hasHunkHeader ? '@@ ' : ''}`.trim();
            console.log(`>>> Content matched as Diff (reason: ${matchReason})`);
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
        return text.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&apos;/g, "'");
    }
    const textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    return textarea.value;
};

const renderTokens = (tokens: (Tokens.Generic | TokenWithText)[], enableCodeApply: boolean, isDarkMode: boolean): React.ReactNode => {

    return tokens.map((token, index) => {
        // Determine the definitive type for rendering
        const determinedType = determineTokenType(token);
        const tokenWithText = token as TokenWithText; // Helper cast

        try {
            switch (determinedType) {
                case 'diff':
                    const rawDiffText = decodeHtmlEntities(tokenWithText.text || '');
                    // Apply cleaning specific to diff content AFTER decoding
                    const cleanedDiff = cleanDiffContent(rawDiffText);
                    // Ensure lang is set to 'diff' for the component
                    const diffToken = { ...tokenWithText, text: cleanedDiff, lang: 'diff' };
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
                    const rawCodeText = decodeHtmlEntities(tokenWithText.text || '');
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
                    // Filter out empty text tokens that might remain after processing
                    const filteredPTokens = pTokens.filter(t => t.type !== 'text' || (t as TokenWithText).text.trim() !== '');
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

                    const itemContent = renderTokens(listItemToken.tokens || [], enableCodeApply, isDarkMode);

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
                                            {renderTokens(cell.tokens || [{ type: 'text', text: cell.text }], enableCodeApply, isDarkMode)}
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
                    return <div key={index} dangerouslySetInnerHTML={{ __html: decodeHtmlEntities(tokenWithText.text) }} />;

                case 'text':
                    if (!hasText(tokenWithText)) return null;
                    const decodedText = decodeHtmlEntities(tokenWithText.text);
                    // Check if this 'text' token has nested inline tokens (like strong, em, etc.)
                    if (tokenWithText.tokens && tokenWithText.tokens.length > 0) {
                        // If it has nested tokens, render them recursively
                        return <>{renderTokens(tokenWithText.tokens, enableCodeApply, isDarkMode)}</>;
                    } else {
                        // Otherwise, just render the decoded text content (use fragment)
                        return <>{decodedText}</>; // Use fragment to avoid extra spans
                    }

                // --- Handle Inline Markdown Elements (Recursively) ---
                case 'strong':
                    return <strong key={index}>{renderTokens((token as Tokens.Strong).tokens || [], enableCodeApply, isDarkMode)}</strong>;
                case 'em':
                    return <em key={index}>{renderTokens((token as Tokens.Em).tokens || [], enableCodeApply, isDarkMode)}</em>;
                case 'codespan':
                    if (!hasText(tokenWithText)) return null;
                    const decodedCode = decodeHtmlEntities(tokenWithText.text);
                    // Basic escaping for codespan content
                    return <code key={index} dangerouslySetInnerHTML={{ __html: decodedCode }} />;
                case 'br':
                    return <br key={index} />;
                case 'del':
                    return <del key={index}>{renderTokens((token as Tokens.Del).tokens || [], enableCodeApply, isDarkMode)}</del>;

                case 'link':
                    const linkToken = token as Tokens.Link;
                    return <a key={index} href={linkToken.href} title={linkToken.title ?? undefined}>{renderTokens(linkToken.tokens || [], enableCodeApply, isDarkMode)}</a>;

                case 'escape':
                    if (!hasText(tokenWithText)) return null;
                    return <>{decodeHtmlEntities(tokenWithText.text || '')}</>;

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

// Function to handle multi-file diffs
const renderMultiFileDiff = (token: TokenWithText, index: number, enableCodeApply: boolean): JSX.Element => {
    // Split the diff into separate file diffs
    const fileDiffs = splitMultiFileDiffs(token.text);

    // If there's only one diff or splitting failed, render as a single diff
    if (fileDiffs.length <= 1) {
        return (
            <DiffToken
                key={index}
                token={{ ...token, text: token.text.trim() }} // Normalize text
                index={index}
                enableCodeApply={enableCodeApply}
                isDarkMode={false} // This will be determined inside the component
            />
        );
    }

    // Render each file diff separately
    return (
        <div key={index} className="multi-file-diff">
            {fileDiffs.map((diffContent, fileIndex) => {
                const diffToken = { ...token, text: diffContent };
                return (
                    <DiffToken
                        key={`${index}-file-${fileIndex}-${Date.now()}`}
                        token={diffToken}
                        index={index * 100 + fileIndex} // Ensure unique indices
                        enableCodeApply={enableCodeApply}
                        isDarkMode={false} // This will be determined inside the component
                    />
                );
            })}
        </div>
    );
};

interface MarkdownRendererProps {
    markdown: string;
    isStreaming?: boolean;
    enableCodeApply: boolean;
}

// Configure marked options
marked.setOptions({
    gfm: true,
    breaks: false,
    pedantic: false
});

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = memo(({ markdown, enableCodeApply, isStreaming: externalStreaming = false }) => {
    const { isStreaming } = useChatContext();
    const { isDarkMode } = useTheme();
    const [isPending, startTransition] = useTransition();
    // State for the tokens that are currently displayed with stable reference
    const [displayTokens, setDisplayTokens] = useState<(Tokens.Generic | TokenWithText)[]>([]);
    const streamingCompleteRef = useRef(false);
    // Ref to store the previous set of tokens, useful for certain streaming optimizations or comparisons
    const previousTokensRef = useRef<(Tokens.Generic | TokenWithText)[]>([]);

    // Store the current view type and line numbers settings
    const [viewType, setViewType] = useState<'split' | 'unified'>(window.diffViewType || 'unified');
    const [showLineNumbers, setShowLineNumbers] = useState<boolean>(false);
    // Track if we're in a streaming response - this is for the overall component
    const isStreamingState = isStreaming;

    // Track when streaming completes
    useEffect(() => {
        if (!isStreamingState && !streamingCompleteRef.current)
            streamingCompleteRef.current = true;
    }, [isStreamingState]);

    // Memoize the parsing of markdown into tokens.
    // This is critical for stability - we need to ensure tokens don't change unnecessarily
    const lexedTokens = useMemo(() => {
        if (!markdown?.trim()) {
            return previousTokensRef.current.length > 0 ? previousTokensRef.current : [];
        }

        try {
            // During streaming, if we already have a diff being rendered, keep it stable
            if ((externalStreaming || isStreamingState) && previousTokensRef.current.length > 0 && !streamingCompleteRef.current) {
                const hasDiff = previousTokensRef.current.some(token =>
                    token.type === 'code' && (token as TokenWithText).lang === 'diff');
                if (hasDiff && false) { // Disable this optimization to allow streaming diffs
                    return previousTokensRef.current;
                }
            }

            // Use marked.lexer directly
            const lexedTokens = marked.lexer(markdown);
            return lexedTokens as (Tokens.Generic | TokenWithText)[]; // Cast for processing
        } catch (error) {
            console.error("Error lexing markdown:", error);
            // Fallback to rendering the raw markdown in a pre tag on error
            return [{ type: 'code', lang: 'text', text: markdown }] as TokenWithText[];
        }
    }, [markdown, externalStreaming, isStreamingState]);

    // Update tokens state when the memoized tokens change
    useEffect(() => {
        // Only update if streaming is complete or if we don't have tokens yet 
        if (!isStreamingState || previousTokensRef.current.length === 0) {
            previousTokensRef.current = lexedTokens;
            setDisplayTokens(lexedTokens);
        } else if (isStreamingState) {
            // During streaming, only update if we don't have any diffs yet
            const hasDiff = previousTokensRef.current.some(token =>
                token.type === 'code' && (token as TokenWithText).lang === 'diff');
            if (!hasDiff || streamingCompleteRef.current) {
                setDisplayTokens(lexedTokens);
            }
        }
    }, [lexedTokens, isStreamingState]); // This effect runs when lexedTokens (from useMemo) is updated.

    // Debug log streaming state - keep this for debugging
    // but it doesn't affect functionality
    useEffect(() => {
        console.log(`MarkdownRenderer streaming state: ${externalStreaming || isStreamingState ? 'streaming' : 'not streaming'}`);
    }, [externalStreaming, isStreamingState]);

    // Only memoize the rendered content when not streaming or when streaming completes 
    const renderedContent = useMemo(() => {
        return renderTokens(displayTokens, enableCodeApply, isDarkMode);
    }, [displayTokens, enableCodeApply, isDarkMode]); // Remove isStreamingState from dependencies

    const isMultiFileDiff = markdown?.includes('diff --git') && markdown.split('diff --git').length > 2;
    return isMultiFileDiff && displayTokens.length === 1 && displayTokens[0].type === 'code' && (displayTokens[0] as TokenWithText).lang === 'diff' ?
        <DiffViewContext.Provider value={{
            viewType,
            setViewType,
            displayMode: 'pretty',
            setDisplayMode: () => { }
        }}>
            {renderMultiFileDiff(displayTokens[0] as TokenWithText, 0, enableCodeApply)}
        </DiffViewContext.Provider> :

        <div>{renderedContent}</div>;
}, (prevProps, nextProps) => prevProps.markdown === nextProps.markdown && prevProps.enableCodeApply === nextProps.enableCodeApply);

export default MarkdownRenderer;

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
