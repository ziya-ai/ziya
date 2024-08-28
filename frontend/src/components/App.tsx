import React from 'react';
import {FolderTree} from "./FolderTree";
import {ChatHistory} from "./ChatHistory";
import {SendChatContainer} from "./SendChatContainer";
import {useChatContext} from '../context/ChatContext';
import {StreamedContent} from './StreamedContent';

export const App = () => {
    const {messages, streamedContent} = useChatContext();
    return (
        <>
            <h2 style={{textAlign: "center", marginBlock: '0.5em'}}>Ziya: Code Assist</h2>
            <div className="container">
                <FolderTree/>
                <SendChatContainer/>
                {(messages.length > 0 || streamedContent) && <div className="chat-container">
                    <StreamedContent/>
                    <ChatHistory/>
                </div>}
            </div>
        </>
    );
};