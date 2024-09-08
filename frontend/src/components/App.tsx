import React from 'react';
import {FolderTree} from "./FolderTree";
import {SendChatContainer} from "./SendChatContainer";
import {useChatContext} from '../context/ChatContext';
import {StreamedContent} from './StreamedContent';
import {Conversation} from "./Conversation";

export const App = () => {
    const { streamedContent, messages} = useChatContext();

    return (
        <>
            <h2 style={{textAlign: "center", marginBlock: '0.5em'}}>Ziya: Code Assist</h2>
            <div className="container">
                <FolderTree/>
                <SendChatContainer/>
                {(messages.length > 0 || streamedContent) && (
                    <div className="chat-container">
                        <StreamedContent/>
                        <Conversation/>
                    </div>)}
            </div>
        </>
    );
};