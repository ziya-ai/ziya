import React from 'react';
import { FolderTree } from "./FolderTree";
import { ChatHistory } from "./ChatHistory";
import { SendChatContainer } from "./SendChatContainer";
import { ChatProvider } from '../context/ChatContext';
import { FolderProvider } from '../context/FolderContext';
import { StreamedContent } from './StreamedContent';

export const App = () => {
    return (
        <ChatProvider>
            <FolderProvider>
                <h2 style={{textAlign: "center", marginBlock: '0.5em'}}>Ziya: Code Assist</h2>
                <div className="container">
                    <FolderTree/>
                    <SendChatContainer/>
                    <div className="chat-container">
                        <StreamedContent/>
                        <ChatHistory/>
                    </div>
                </div>
            </FolderProvider>
        </ChatProvider>
    );
};