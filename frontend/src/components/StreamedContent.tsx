import React from 'react';
import {MarkdownRenderer} from "./MarkdownRenderer";
import {useChatContext} from '../context/ChatContext';

export const StreamedContent: React.FC = () => {
    const {streamedContent} = useChatContext();
    const enableCodeApply = window.enableCodeApply === 'true';
    return (
        <>
            {streamedContent && (
                <div className="message assistant">
                    <div className="message-sender">AI:</div>
                    <MarkdownRenderer markdown={streamedContent} enableCodeApply={enableCodeApply}/>
                </div>
            )}
        </>
    );
};
