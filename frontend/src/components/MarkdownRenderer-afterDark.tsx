import React, { useState, useEffect, memo } from 'react';
import { parseDiff, Diff, Hunk, tokenize, RenderToken } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import { marked, Marked } from 'marked';
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
            alignItems: 'center',
            marginBottom: '10px'
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

const DiffView: React.FC<DiffViewProps> = ({ diff, viewType, displayMode, showLineNumbers }) => {
    const { isDarkMode } = useTheme();
    let files;
    try {
        files = parseDiff(diff);
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

interface TokenWithText {
    type: string;
    text: string;
    start?: number;
    header?: TokenWithText[];
    rows?: TokenWithText[][];
    items?: any[];
    ordered?: boolean;
    lang?: string;
    tokens?: TokenWithText[];
    task?: boolean;
    checked?: boolean;
    raw?: string;
}

// Type guard to check if token has text property
const hasText = (token: any): token is TokenWithText => {
    return 'text' in token;
};

// Type guard to check if token is a Code token
const isCodeToken = (token: any): token is TokenWithText => {
    return token.type === 'code' && hasText(token);
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
        <div>
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

const renderTokens = (tokens: TokenWithText[], enableCodeApply: boolean): React.ReactNode[] => {
    return tokens.map((token, index) => {
        if (token.type === 'code' && isCodeToken(token) && token.lang === 'diff') {
            try {
                const files = parseDiff(token.text);
                if (!files || files.length === 0) {
                    return <pre key={index}><code>{token.text}</code></pre>;
                }
                return (
                    <DiffViewWrapper
                        key={index}
                        token={token}
                        index={index}
                        enableCodeApply={enableCodeApply}
                    />
                );
            } catch (error) {
                return <pre key={index}><code>{token.text}</code></pre>;
            }
        }


	// Handle tables
        if (token.type === 'table' && token.header && token.rows) {
            return (
                <table key={index} style={{
                    borderCollapse: 'collapse',
                    width: '100%',
                    marginBottom: '1em'
                }}>
                    <thead>
                        <tr>
                            {token.header.map((cell, cellIndex) => (
                                <th
                                    key={cellIndex}
                                    style={{
                                        borderBottom: '2px solid #ddd',
                                        padding: '8px',
                                        textAlign: 'left'
                                    }}
                                >
                                    {cell.text}
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {token.rows.map((row, rowIndex) => (
                            <tr key={rowIndex}>
                                {row.map((cell, cellIndex) => (
                                    <td
                                        key={cellIndex}
                                        style={{
                                            border: '1px solid #ddd',
                                            padding: '8px'
                                        }}
                                    >
                                        {cell.text}
                                    </td>
                                ))}
                            </tr>
                        ))}
                    </tbody>
                </table>
            );
        }

        // Decode HTML entities
        const decodeHTML = (html: string) => {
            const txt = document.createElement('textarea');
            txt.innerHTML = html;
            return txt.value;
        };

	// Handle list items that might be tasks
        if (token.type === 'list_item') {
            // Check if this is a task list item by looking at the text content
            const taskMatch = token.text?.match(/^\[([ xX])\] (.*)/);
            if (taskMatch) {
                const isChecked = taskMatch[1].toLowerCase()=== 'x';
                const textContent = taskMatch[2];
                return (
                    <li key={index} style={{ listStyle: 'none' }}>
                        <input
                            type="checkbox"
                            checked={isChecked}
                            readOnly
                            style={{
                                marginRight: '0.5em',
                                verticalAlign: 'middle'
                            }}
                        />
                        <span>{textContent}</span>
                    </li>
                );
            }
            // Regular list item
            return <li key={index}>{token.text}</li>;
        }

        if (token.type === 'code' && isCodeToken(token)) {
            return <pre key={index}><code>{token.text}</code></pre>;
        }

        // Handle ordered and unordered lists
        if (token.type === 'list' && token.items) {
            const ListTag = token.ordered ? 'ol' : 'ul';
	    return (
                <ListTag
                    key={index}
                    style={{
                        marginTop: '0.5em',
                        marginBottom: '0.5em',
                        paddingLeft: '2em'
                    }}
                    start={token.ordered ? (token.start || 1) : undefined}
                >
                    {token.items.map((item, itemIndex) => {
                        // For list items that contain nested content
                        if (item.tokens && item.tokens.length > 0) {
                            return (
                                <li key={itemIndex}>
                                    {renderTokens(item.tokens, enableCodeApply)}
                                </li>
                            );
                        }
                        return <li key={itemIndex}>{item.text}</li>;
                    })}
                </ListTag>
            );
        }

        // Handle list items that might contain nested tokens
        if (token.type === 'list_item' && token.tokens) {
            return renderTokens(token.tokens, enableCodeApply);
        }

        // Handle paragraphs that might contain other inline tokens
        if (token.type === 'paragraph' && token.tokens && token.tokens.length > 0) {
            return <p key={index}>
                {renderTokens(token.tokens, enableCodeApply)}
            </p>;
        }

        // Handle tables specially
        if (token.type === 'table' && token.header && token.rows) {
            return (
                <table key={index}>
                    <thead>
                        <tr>
                            {token.header.map((cell, cellIndex) => (
                                <th key={cellIndex}>{decodeHTML(cell.text)}</th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {token.rows.map((row, rowIndex) => (
                            <tr key={rowIndex}>
                                {row.map((cell, cellIndex) => (
                                    <td key={cellIndex}>{decodeHTML(cell.text)}</td>
                                ))}
                            </tr>
                        ))}
                    </tbody>
                </table>
            );
        }

        // Handle regular HTML content
        if (token.type === 'html') {
            return <div key={index} dangerouslySetInnerHTML={{ __html: decodeHTML(token.text) }} />;
        }

	// Handle regular text, only if it has content
        const text = token.text || '';
        return text.trim() ? <div key={index}>{decodeHTML(text)}</div> : null;

    });

    const elements = tokens.map((token, index) => renderTokens([token], enableCodeApply));
    // Filter out null/undefined values and flatten the array
    return elements.flat().filter(Boolean);
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
    const tokens = (typeof markdown === 'string' ? marked.lexer(markdown) : []) as TokenWithText[];
    return <div>{renderTokens(tokens, enableCodeApply)}</div>;
};
