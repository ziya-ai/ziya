import React, { useState, useEffect, memo } from 'react';
import { parseDiff, Diff, Hunk, tokenize, RenderToken } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import { marked, Token, Tokens } from 'marked';
import { Button, message, Radio, Space } from 'antd';
import { useTheme } from '../context/ThemeContext';
import { CheckOutlined, CodeOutlined } from '@ant-design/icons';

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
                marginBottom: '10px',
                padding: '8px'
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

const hasText = (token: Token): token is Token & { text: string } => {
    return 'text' in token;
};

const isCodeToken = (token: Token): token is Tokens.Code => {
    return token.type === 'code' && 'text' in token;
};

interface DiffViewWrapperProps {
    token: Token;
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

const renderTokens = (tokens: Token[], enableCodeApply: boolean): React.ReactNode[] => {
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

        if (token.type === 'code' && isCodeToken(token)) {
            // Regular code blocks (non-diff)
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

        // Handle tables specially
        if (token.type === 'table' && 'header' in token && 'rows' in token) {
            const tableToken = token as Tokens.Table;
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
            const listToken = token as Tokens.List;
            const ListTag = listToken.ordered ? 'ol' : 'ul';
            return (
                <ListTag key={index} 
                    start={listToken.ordered ? (listToken.start || 1) : undefined}>
                    {listToken.items.map((item, itemIndex) => {
                        if ('tokens' in item && item.tokens) {
                            // Handle nested content in list items
                            return (
                                <li key={itemIndex}>
                                    {renderTokens(item.tokens, enableCodeApply)}
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
	    const escapedText = text.replace(/</g, '&lt;').replace(/>/g, '&gt;');
            return text.trim() ?
                <div key={index} dangerouslySetInnerHTML={{ __html: escapedText }} /> : null;
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
    breaks: true
});

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({ markdown, enableCodeApply }) => {
    const tokens = marked.lexer(markdown);
    return <div>{renderTokens(tokens, enableCodeApply)}</div>;
};
