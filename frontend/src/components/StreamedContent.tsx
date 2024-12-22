import React, { useEffect, useRef } from 'react';
import {MarkdownRenderer} from "./MarkdownRenderer";
import {useChatContext} from '../context/ChatContext';

export const StreamedContent: React.FC = () => {
    const {streamedContent, scrollToBottom, isTopToBottom} = useChatContext();

    useEffect(() => {
        if (streamedContent) {
            // Focus the input after content updates
            const textarea = document.querySelector('.input-textarea') as HTMLTextAreaElement;
            if (textarea) {
                textarea.focus();
            }
        }

    }, [streamedContent]);

    const enableCodeApply = window.enableCodeApply === 'true';
    return (
        <>
            {streamedContent && (
                <div className="message assistant streamed-message">
                    <div className="message-sender" style={{ marginTop: 0 }}>AI:</div>
                    <MarkdownRenderer markdown={streamedContent} enableCodeApply={enableCodeApply}/>
                </div>
            )}
        </>
    );
};
