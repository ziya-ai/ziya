import React, { useState, useEffect, memo, useMemo, Suspense, useCallback, useRef, useLayoutEffect, useTransition, useId, useContext, createContext, useDebugValue, forwardRef } from 'react';
import 'prismjs/themes/prism.css';
import { marked, Tokens } from 'marked';
import { Alert, Button, message, Space, Spin, Tooltip } from 'antd';
import { parseDiff, tokenize, RenderToken, HunkProps } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import { DiffLine } from './DiffLine';
import 'prismjs/themes/prism-tomorrow.css';  // Add dark theme support
import { D3Renderer } from './D3Renderer';
import { useChatContext } from '../context/ChatContext';
import {
    CodeOutlined, ToolOutlined, ArrowUpOutlined, ArrowDownOutlined, SplitCellsOutlined, BorderlessTableOutlined, NumberOutlined, EyeOutlined, FileTextOutlined, CodepenOutlined, CheckCircleOutlined, CloseCircleOutlined, CheckOutlined, ExclamationCircleOutlined
} from '@ant-design/icons';
import 'prismjs/themes/prism.css';
import { loadPrismLanguage, isLanguageLoaded } from '../utils/prismLoader';
import { useTheme } from '../context/ThemeContext';
import type * as PrismType from 'prismjs';
import { renderFileHeader } from './renderFileHeader';
import { detectFileOperationSyntax, renderFileOperationSafely } from '../utils/fileOperationParser';
import { FileOperationRenderer } from './FileOperationRenderer';

// Define the status interface

// Create a context for diff view settings to avoid window variable dependencies
interface DiffViewContextType {
    viewType: 'split' | 'unified';
    setViewType: (type: 'split' | 'unified') => void;
    displayMode: 'raw' | 'pretty';
    setDisplayMode: (mode: 'raw' | 'pretty') => void;
}

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

        console.log('=== normalizeGitDiff Debug ===');
        console.log('Input diff preview:', diff.split('\n').slice(0, 5));

        console.log('=== normalizeGitDiff Debug ===');
        console.log('Input diff preview:', diff.split('\n').slice(0, 5));

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

const DiffView: React.FC<DiffViewProps> = ({ diff, viewType, initialDisplayMode, showLineNumbers, elementId, fileIndex }) => {
    const [isLoading, setIsLoading] = useState(true);
    const [tokenizedHunks, setTokenizedHunks] = useState<any>(null);
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
    const parseInProgressRef = useRef<boolean>(false);
    const forceRenderRef = useRef<boolean>(false);
    const componentIdRef = useRef(`diff-${Date.now()}-${Math.random()}`);

    // Use a stable ID that doesn't change on re-renders
    const diffIdRef = useRef<string>(elementId || (() => {
        const id = `diff-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
        return id;
    })());
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
    }, [diff]);

    // Listen for window-level hunk status updates with data, but don't update during streaming
    useEffect(() => {
        const handleWindowStatusUpdate = (event: CustomEvent) => {
            if (!event.detail) return;
            console.log("DiffView received window hunk status update with data:", event.detail);

            // Check if this update is for our diff element
            let isForThisDiff = false;
            // First check if the targetDiffElementId matches our diffId
            if (event.detail.targetDiffElementId === diffId) {
                isForThisDiff = true;
                console.log(`Direct match for diffId ${diffId}`);
            }

            // Also check if the request ID maps to our diff ID
            if (event.detail.requestId && diffRequestMap.get(event.detail.requestId) === diffId) {
                isForThisDiff = true;
                console.log(`direct match for diffId ${diffId}`);

                // Apply the hunk statuses directly to our component state

                // Also check if the request ID maps to our diff ID
                if (event.detail.requestId && diffRequestMap.get(event.detail.requestId) === diffId) {
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

    const renderHunks = (hunks: any[], filePath: string, fileIndex: number) => {

        const tableClassName = `diff-table ${viewType === 'split' ? 'diff-table-split' : 'diff-table-unified'}`;

        if (!hunks || hunks.length === 0) {
            return <div className="diff-empty-hunks">No changes found in this diff.</div>;
        }

        if (!hunks || hunks.length === 0) {
            return <div className="diff-empty-hunks">No changes found in this diff.</div>;
        }

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
                        const hunkId = hunkIndex + 1;
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
                                        <><CheckCircleOutlined style={{ color: '#faad14' }} /> Already Applied</> :
                                        <><CheckCircleOutlined style={{ color: '#52c41a' }} /> Applied</> :
                                    <><CloseCircleOutlined /> Failed: {statusReason}</>
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
                                        borderTop: status ? `1px solid ${isApplied ?
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
            </table>
        );
    };

    // Handle parse error case
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


    const renderContent = (hunk: any, filePath: string, status?: any, fileIndex?: number, hunkIndex?: number): JSX.Element[] => {

        // Add a status row at the top of the hunk if status is available
        const changes = [...(hunk.changes || [])];

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
                            const hunkId = hunkIndex + 1;
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
                                            <><CheckCircleOutlined style={{ color: '#faad14' }} /> Already Applied</> :
                                            <><CheckCircleOutlined style={{ color: '#52c41a' }} /> Applied</> :
                                        <><CloseCircleOutlined /> Failed: {statusReason}</>
                                    }
                                </span>
                            );

                            // Extract hunk header information
                            const hunkHeaderInfo = hunk.content?.match(/^(@@.*@@)/)?.[1] ||
                                `@@ -${hunk.oldStart},${hunk.oldLines} +${hunk.newStart},${hunk.newLines} @@`;

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

const ApplyChangesButton: React.FC<ApplyChangesButtonProps> = ({ diff, filePath, fileIndex, diffElementId, enabled, setHunkStatuses }) => {
    const [isApplied, setIsApplied] = useState(false);
    const [isProcessing, setIsProcessing] = useState(false);
    const [instanceHunkStatusMap, setInstanceHunkStatusMap] = useState<Map<string, HunkStatus>>(new Map());
    const statusUpdateCounterRef = useRef<number>(0);
    const isStreamingRef = useRef<boolean>(false);
    const stableRequestIdRef = useRef<string>(`req-${diffElementId}-${Date.now()}`);
    const appliedRef = useRef<boolean>(false);
    const buttonInstanceId = useRef(`button-${diffElementId}-${Date.now()}`).current;

    // Track processed request IDs to prevent infinite update loops
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

        // Generate a unique request ID for this specific diff application
        const requestId = `${diffElementId}-${Date.now()}`;

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
                                    applied: hunkStatus.status === 'succeeded',
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

            const targetDiffElementId = diffRequestMap.get(e.detail.requestId);
            const targetDiffElementIdFromMap = diffRequestMap.get(e.detail.requestId)?.replace(/^diff-/, 'diff-view-');
            let isRelevantUpdate = false;

            // Create a unique key for this event to prevent duplicate processing
            // Skip if we've already processed this exact event
            if (processedWindowEvents.has(eventKey)) {
                console.debug(`Skipping already processed window event: ${eventKey}`);
                return;
            }

            // Create a unique identifier for this specific button instance
            const thisButtonId = buttonInstanceId;

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
            disabled={isApplied || isProcessing}
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
    const { isStreaming } = useChatContext();
    // Check if we're in a streaming response
    const streamingCheckedRef = useRef(false);
    // Generate a unique ID once when the component mounts
    const [diffId] = useState(() =>
        `diff-${Math.random().toString(36).substring(2, 9)}-${Date.now()}`);
    const [streamingContent, setStreamingContent] = useState<string | null>(null);
    const contentRef = useRef<string | null>(null);

    // Store the content in a ref to avoid re-renders
    useEffect(() => {
        if (!contentRef.current || contentRef.current !== token.text) {
            contentRef.current = token.text;
        }
    }, [token.text]);

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
                elementId={diffId}
                enableCodeApply={enableCodeApply}
                isStreaming={isStreaming}
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
    forceRender?: boolean;
    index?: number;
    elementId?: string;
}

const DiffViewWrapper = memo(({ token, enableCodeApply, index, elementId }: DiffViewWrapperProps) => {
    const [viewType, setViewType] = useState<'unified' | 'split'>(window.diffViewType || 'unified');
    const [showLineNumbers, setShowLineNumbers] = useState<boolean>(false);
    const [displayMode, setDisplayMode] = useState<DisplayMode>('pretty');
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

        // Look for git diff header
        for (const line of lines) {
            if (line.startsWith('diff --git')) {
                const match = line.match(/diff --git a\/(.*?) b\/(.*?)$/);
                if (match) {
                    return match[2] || match[1]; // Prefer new path, fallback to old path
                }
            }
            // Look for unified diff headers
            if (line.startsWith('+++ b/')) {
                return line.substring(6);
            }
            if (line.startsWith('--- a/')) {
                return line.substring(6);
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

    // Cleanup async operations
    useEffect(() => {
        return () => {
            if (parseTimeoutRef.current) {
                clearTimeout(parseTimeoutRef.current);
            }
        };
    }, []);

    // Track component visibility
    const containerRef = useRef<HTMLDivElement>(null);

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
        if (window.diffViewType !== viewType) {
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
        }

        // During streaming, use the initial title if current extraction fails
        return (isGlobalStreaming && initialFileTitleRef.current) ? initialFileTitleRef.current : extractedTitle;
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
                        viewType={isStreamingRef.current ? 'unified' : viewType}
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
    const [isLanguageLoaded, setIsLanguageLoaded] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);
    const { isDarkMode } = useTheme();
    const [prismInstance, setPrismInstance] = useState<typeof PrismType | null>(null);
    const [debugInfo, setDebugInfo] = useState<any>({});

    const { isStreaming: isGlobalStreaming } = useChatContext();

    console.debug('CodeBlock rendering:', {
        id: index,
        tokenType: token.type,
        language: token.lang,
        isStreaming: token.text?.endsWith('\n'),
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
        if (contentRef.current) highlightCodeIfNeeded();
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
            contentRef.current.innerHTML = highlighted;
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
    const codeText = token.text;
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
type DeterminedTokenType = 'diff' | 'graphviz' | 'vega-lite' | 'd3' | 'mermaid' | 'file-operation' | 'code' | 'html' | 'text' | 'list' | 'table' | 'escape' | 'paragraph' | 'heading' | 'hr' | 'blockquote' | 'space' | 'codespan' | 'strong' | 'em' | 'del' | 'link' | 'image' | 'br' | 'list_item' | 'unknown';

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
        const hasHunkHeader = linesToCheck.some(line => line.trim().startsWith('@@ '));
        const diffMarkersFound = [hasGitHeader, hasMinusHeader, hasPlusHeader, hasHunkHeader].filter(Boolean).length;

        // More lenient check for diff --git, allowing it not to be the very first thing
        const containsDiffGit = text.includes('diff --git');

        if (containsDiffGit || diffMarkersFound >= 2) {
            return 'diff';
        }
    }

    // 2. Handle Code Blocks with explicit lang tags
    if (tokenType === 'code' && 'lang' in token && typeof token.lang === 'string' && token.lang) {
        const lang = token.lang.toLowerCase().trim();

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

        if (lang === 'mermaid') return 'mermaid';  // Check mermaid FIRST
        if (lang === 'diff') return 'diff';
        if (lang === 'graphviz' || lang === 'dot') return 'graphviz';
        if (lang === 'vega-lite' || lang === 'vegalite') return 'vega-lite';
        if (lang === 'vega-lite') return 'vega-lite';
        if (lang === 'd3') return 'd3';
        // If it has a specific lang tag but isn't special, it's 'code'
        return 'code';
    }

    // 2. Content-based detection for code blocks *without* specific lang tags
    if (tokenType === 'code' && 'text' in token && typeof token.text === 'string') {
        const text = token.text;
        const trimmedText = text.trim();

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
        const hasHunkHeader = linesToCheck.some(line => line.trim().startsWith('@@ ')); // More lenient check

        // Check for common valid diff starting patterns
        // Require at least two characteristic lines for content-based detection
        const diffMarkersFound = [hasGitHeader, hasMinusHeader, hasPlusHeader, hasHunkHeader].filter(Boolean).length;
        if (diffMarkersFound >= 2) {
            // Log which condition matched for debugging
            const matchReason = `${hasGitHeader ? 'git ' : ''}${hasMinusHeader ? '--- ' : ''}${hasPlusHeader ? '+++ ' : ''}${hasHunkHeader ? '@@ ' : ''}`.trim();
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

const renderTokens = (tokens: (Tokens.Generic | TokenWithText)[], enableCodeApply: boolean, isDarkMode: boolean, isSubRender: boolean = false, isStreaming: boolean = false): React.ReactNode => {

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

                    // Check if this is a multi-file diff and not already a sub-render
                    if (!isSubRender) {
                        const fileDiffs = splitMultiFileDiffs(cleanedDiff);
                        if (fileDiffs.length > 1) {
                            return renderMultiFileDiff(diffToken, index, enableCodeApply, isDarkMode);
                        }
                    }

                    return <DiffToken key={index} token={diffToken} index={index} enableCodeApply={enableCodeApply} isDarkMode={isDarkMode} />;

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
                    console.log(`🎯 CREATING MERMAID SPEC:`, { text: tokenWithText.text.substring(0, 100) });
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

                case 'd3':
                    if (!hasText(tokenWithText)) return null;
                    return (
                        <D3Renderer key={index} spec={tokenWithText.text} type="d3" isStreaming={isStreaming} />
                    );

                case 'code':
                    if (!isCodeToken(tokenWithText)) return null; // Type guard

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

                    // List of known/safe HTML tags that we want to actually render as HTML
                    const knownHtmlTags = [
                        'div', 'span', 'p', 'br', 'hr', 'strong', 'em', 'b', 'i', 'u', 's',
                        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                        'ul', 'ol', 'li', 'dl', 'dt', 'dd',
                        'table', 'thead', 'tbody', 'tr', 'th', 'td',
                        'a', 'img', 'video', 'audio',
                        'blockquote', 'pre', 'code',
                        'details', 'summary',
                        'math', 'mi', 'mo', 'mn', 'mrow', 'mfrac', 'msup', 'msub', 'msubsup', 'msqrt', 'mroot'

                    ];

                    // Check if the HTML content contains only known tags
                    const htmlContent = tokenWithText.text;

                    // Check if this is a MathML element and render it inline
                    if (htmlContent.match(/^<(math|mi|mo|mn|mrow|mfrac|msup|msub|msubsup|msqrt|mroot)/)) {
                        const mathWithNamespace = htmlContent.includes('xmlns=')
                            ? htmlContent
                            : htmlContent.replace('<math', '<math xmlns="http://www.w3.org/1998/Math/MathML"');
                        return <span key={index} dangerouslySetInnerHTML={{ __html: mathWithNamespace }} />;
                    }

                    // Check if this is a wrapped MathML block
                    if (htmlContent.includes('class="mathml-block"')) {
                        return <div key={index} dangerouslySetInnerHTML={{ __html: htmlContent }} />;
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

                    // If all tags are known, render as HTML
                    return <div key={index} dangerouslySetInnerHTML={{ __html: decodeHtmlEntities(htmlContent) }} />;

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

// Function to handle multi-file diffs with proper recursive rendering
const renderMultiFileDiff = (token: TokenWithText, index: number, enableCodeApply: boolean, isDarkMode: boolean): JSX.Element => {
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
}

// Configure marked options
const markedOptions = {
    gfm: true,
    breaks: false,
    pedantic: false
};

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = memo(({ markdown, enableCodeApply, isStreaming: externalStreaming = false, forceRender = false, isSubRender = false }) => {
    const { isStreaming } = useChatContext();
    const { isDarkMode } = useTheme();
    const [isPending, startTransition] = useTransition();
    // State for the tokens that are currently displayed with stable reference
    const [displayTokens, setDisplayTokens] = useState<(Tokens.Generic | TokenWithText)[]>([]);
    // Ref to store the previous set of tokens, useful for certain streaming optimizations or comparisons
    const previousTokensRef = useRef<(Tokens.Generic | TokenWithText)[]>([]);
    // Track if we're in a streaming response - this is for the overall component 
    const markdownRef = useRef<string>(markdown);
    const isStreamingState = isStreaming;

    // Memoize the parsing of markdown into tokens.
    // This is critical for stability - we need to ensure tokens don't change unnecessarily
    const lexedTokens = useMemo(() => {
        if (!markdown?.trim()) {
            return previousTokensRef.current.length > 0 ? previousTokensRef.current : [];
        }

        try {
            markdownRef.current = markdown;
            // During streaming, if we already have a diff being rendered, keep it stable
            if (false && (externalStreaming || isStreamingState) && previousTokensRef.current.length > 0) {
                const hasDiff = previousTokensRef.current?.some(token =>
                    token.type === 'code' && (token as TokenWithText).lang === 'diff');
                if (hasDiff && false) { // Disable this optimization to allow streaming diffs
                    return previousTokensRef.current;
                }
            }

            // Use marked.lexer directly
            let processedMarkdown = markdown;

            // Debug the lexer output
            // const lexedTokens = marked.lexer(processedMarkdown, markedOptions);

            // Pre-process MathML blocks to prevent fragmentation
            const mathMLRegex = /<math[^>]*>[\s\S]*?<\/math>/gi;
            const mathMLBlocks = processedMarkdown.match(mathMLRegex);

            if (mathMLBlocks) {
                mathMLBlocks.forEach((mathBlock, index) => {
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
            console.error("Error lexing markdown:", error);
            // Fallback to rendering the raw markdown in a code block on error
            return [{ type: 'code', lang: 'text', text: markdown }] as TokenWithText[];
        }
    }, [markdown, externalStreaming, isStreamingState]);

    // Update tokens state when the memoized tokens change
    useEffect(() => {
        // Only update if streaming is complete or if we don't have tokens yet
        if (lexedTokens.length > 0) {
            previousTokensRef.current = lexedTokens;
            setDisplayTokens(lexedTokens);

            // Debug log for streaming updates
            if (isStreamingState) {
                const codeBlocks = lexedTokens.filter(token =>
                    token.type === 'code' && (token as TokenWithText).lang !== 'diff'
                );
                console.debug('Streaming update:', {
                    tokenCount: lexedTokens.length,
                    codeBlockCount: codeBlocks.length
                });
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
        return renderTokens(displayTokens, enableCodeApply, isDarkMode, isSubRender, isStreaming);
    }, [displayTokens, enableCodeApply, isDarkMode, forceRender, isSubRender, isStreamingState]); // Add isStreamingState to dependencies


    const isMultiFileDiff = markdown?.includes('diff --git') && markdown.split('diff --git').length > 2;
    return isMultiFileDiff && !isSubRender && displayTokens.length === 1 && displayTokens[0].type === 'code' && (displayTokens[0] as TokenWithText).lang === 'diff' ?
        renderMultiFileDiff(displayTokens[0] as TokenWithText, 0, enableCodeApply, isDarkMode) :

        <div>{renderedContent}</div>;
}, (prevProps, nextProps) => prevProps.markdown === nextProps.markdown && prevProps.enableCodeApply === nextProps.enableCodeApply);
// Note: forceRender prop is intentionally not included in the memo comparison to ensure re-rendering during streaming
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
