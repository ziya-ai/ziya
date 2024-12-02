import React from 'react';
import {FolderTree} from "./FolderTree";
import {SendChatContainer} from "./SendChatContainer";
import {useChatContext} from '../context/ChatContext';
import {StreamedContent} from './StreamedContent';
import {Conversation} from "./Conversation";
import {Button, Tooltip } from "antd";
import {PlusOutlined} from "@ant-design/icons";
import {ThemeProvider} from '../context/ThemeContext';
import {ThemeToggleButton} from './ThemeToggleButton'

export const App = () => {
    const { streamedContent, messages, startNewChat } = useChatContext();
    const enableCodeApply = window.enableCodeApply === 'true';

    return (
        <ThemeProvider>
            <h2 style={{textAlign: "center", marginBlock: '0.5em'}}>Ziya: Code Assist</h2>
            <div className="container">
                <FolderTree/>
                <SendChatContainer/>
                {(messages.length > 0 || streamedContent) && (
                    <div className="chat-container">
                        <StreamedContent/>
                        <Conversation enableCodeApply={enableCodeApply}/>
                    </div>)}
            </div>
            <div style={{
                position: 'fixed',
                right: '10px',
                top: '10px',
                display: 'flex',
                gap: '8px'
            }}>
                <ThemeToggleButton />
                <Tooltip title="New Chat">
                    <Button icon={<PlusOutlined />} onClick={startNewChat} size={"large"}/>
                </Tooltip>
            </div>
        </ThemeProvider>
    );
};