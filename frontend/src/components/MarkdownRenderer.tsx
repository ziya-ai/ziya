import React, { useState } from 'react';
import { parseDiff, Diff, Hunk } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import { marked, Token } from 'marked';
import { Button, message } from 'antd';
import { CheckOutlined } from '@ant-design/icons';

interface ApplyChangesButtonProps {
    diff: string;
    filePath: string;
    enabled: boolean;
}

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

const renderTokens = (tokens: Token[], enableCodeApply: boolean): React.ReactNode[] => {
    return tokens.map((token, index) => {
        if (token.type === 'code' && token.lang === 'diff') {
            let files;
            try {
                files = parseDiff(token.text);
            } catch (error) {
                return <pre key={index}><code>{token.text}</code></pre>;
            }

            return files.map((file, fileIndex) => {
                if (!file.hunks || !Array.isArray(file.hunks)) {
                    return <pre key={`${index}-${fileIndex}`}><code>{token.text}</code></pre>;
                }

                return (
                    <>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
                            <b>{renderFileHeader(file)}</b>
                            <ApplyChangesButton diff={token.text} filePath={file.newPath || file.oldPath} enabled={enableCodeApply} />
                        </div>
                        {file.type !== 'delete' &&
                            <Diff key={`${index}-${fileIndex}`} viewType="unified" gutterType="none"
                                  diffType={file.type}
                                  className="smaller-diff-view"
                                  hunks={file.hunks}>
                                {hunks => hunks.map((hunk, hunkIndex) => (
                                    <Hunk key={`${index}-${hunkIndex}`} hunk={hunk}/>
                                ))}
                            </Diff>}
                    </>
                );
            });
        }

        if (token.type === 'code') {
            return (
                <pre key={index}><code>{token.text}</code></pre>
            );
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
