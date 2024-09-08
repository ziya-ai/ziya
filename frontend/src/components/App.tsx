import React from 'react';
import {ConfigProvider, theme} from 'antd';
import {FolderTree} from "./FolderTree";
import {ChatHistory} from "./ChatHistory";
import {SendChatContainer} from "./SendChatContainer";
import {useChatContext} from '../context/ChatContext';
import {StreamedContent} from './StreamedContent';
import {ThemeProvider, useTheme} from '../context/ThemeContext';
import {ThemeToggleButton} from './ThemeToggleButton'

const AppContent = () => {
    const { streamedContent, messages } = useChatContext();
    const { isDarkMode } = useTheme();

    return (
        <ConfigProvider
            theme={{
                algorithm: isDarkMode ? theme.darkAlgorithm : theme.defaultAlgorithm,
            }}
        >
            <h2 style={{textAlign: "center", marginBlock: '0.5em'}}>Ziya: Code Assist</h2>
            <ThemeToggleButton />
            <div className="container">
                <FolderTree/>
                <SendChatContainer/>
                {(messages.length > 0 || streamedContent) && (
                    <div className="chat-container">
                        <StreamedContent/>
                        <ChatHistory/>
                    </div>)}
            </div>
        </ConfigProvider>
    );
};

export const App = () => {
    return (
        <ThemeProvider>
            <AppContent />
        </ThemeProvider>
    );
};