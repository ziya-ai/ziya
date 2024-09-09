import React from 'react';
import {parseDiff, Diff, Hunk} from 'react-diff-view';
import 'react-diff-view/style/index.css';
import {marked} from 'marked';

const renderFileHeader = (file) => {
    if (file.type === 'rename') {
        return `Rename: ${file.oldPath} → ${file.newPath}`;
    } else if (file.type === 'delete') {
        return `Delete: ${file.oldPath}`;
    } else if (file.type === 'add') {
        return `Create: ${file.newPath}`;
    } else {
        return `File: ${file.oldPath || file.newPath}`;
    }
};

const renderTokens = (tokens) => {
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
                     <h4>{renderFileHeader(file)}</h4>
                     <Diff key={`${index}-${fileIndex}`} viewType="unified" gutterType="none" diffType={file.type}
                          hunks={file.hunks}>
                        {hunks => hunks.map((hunk, hunkIndex) => (
                            <Hunk key={`${index}-${hunkIndex}`} hunk={hunk}/>
                        ))}
                    </Diff>
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

export const MarkdownRenderer = ({markdown}) => {
    const tokens = marked.lexer(markdown);
    return <div>{renderTokens(tokens)}</div>;
};
