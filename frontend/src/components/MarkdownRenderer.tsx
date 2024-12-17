import React, { useState, useEffect, memo } from 'react';
import { parseDiff, Diff, Hunk, tokenize } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import { marked, Token, Tokens } from 'marked';
import { Button, message, Radio, Space } from 'antd';
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
        <div className="diff-view-controls">
            <Space>
                {displayMode === 'pretty' && (
                    <>
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
                    </>
                )}
                <Radio.Group
                    value={displayMode}
                    buttonStyle="solid"
                    onChange={(e) => onDisplayModeChange(e.target.value)}
                >
                    <Radio.Button value="pretty">Pretty</Radio.Button>
                    <Radio.Button value="raw">Raw</Radio.Button>
                </Radio.Group>
            </Space>
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

const DiffView: React.FC<DiffViewProps> = ({ diff, viewType, displayMode, showLineNumbers }) => {
    let files;
    try {
        files = parseDiff(diff);
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
            <pre style={{ backgroundColor: '#f6f8fa', padding: '10px', borderRadius: '4px' }}><code>{diff}</code></pre>
        );
    }

    return files.map((file, fileIndex) => (
            <div key={fileIndex} style={{ width: '100%', overflowX: 'auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
                <b>{renderFileHeader(file)}</b>
                <ApplyChangesButton diff={diff} filePath={file.newPath || file.oldPath} enabled={window.enableCodeApply === 'true'} />
            </div>
            {file.type !== 'delete' && <Diff 
		    viewType={viewType} 
		    diffType={file.type} 
		    hunks={file.hunks}
		    gutterType={showLineNumbers ? 'default' : 'none'}
	        >
		{hunks => renderHunks(hunks)}
            </Diff>}
        </div>
    ));
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

// Type guard to check if token has text property
const hasText = (token: Token): token is Token & { text: string } => {
    return 'text' in token;
};

// Type guard to check if token is a Code token
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

const renderTokens = (tokens: Token[], enableCodeApply: boolean): React.ReactNode[] => {
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

        if (token.type === 'code' && isCodeToken(token)) {
            return <pre key={index}><code>{token.text}</code></pre>;
        }

        return <div key={index} dangerouslySetInnerHTML={{__html: marked.parser([token])}}/>;
    });
};

interface MarkdownRendererProps {
    markdown: string;
    enableCodeApply: boolean;
}

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({ markdown, enableCodeApply }) => {
    const tokens = marked.lexer(markdown);
    return <div>{renderTokens(tokens, enableCodeApply)}</div>;
};
