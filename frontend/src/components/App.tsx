import React, { useState } from 'react';
import {FolderTree} from "./FolderTree";
import {SendChatContainer} from "./SendChatContainer";
import {StreamedContent} from './StreamedContent';
import {Conversation} from "./Conversation";
import {Button, Tooltip, ConfigProvider, theme } from "antd";
import {MenuFoldOutlined, MenuUnfoldOutlined, PlusOutlined, BulbOutlined} from "@ant-design/icons";
import { useTheme } from '../context/ThemeContext';
import { useChatContext } from '../context/ChatContext';

export const App = () => {
    const {streamedContent, messages, startNewChat} = useChatContext();
    const enableCodeApply = window.enableCodeApply === 'true';
    const [isPanelCollapsed, setIsPanelCollapsed] = useState(false);

    const togglePanel = () => {
        setIsPanelCollapsed(!isPanelCollapsed);
    };

    const { isDarkMode, toggleTheme, themeAlgorithm } = useTheme();

    return (
        <ConfigProvider
            theme={{
                algorithm: themeAlgorithm,
                token: {
                    borderRadius: 6,
                    colorBgContainer: isDarkMode ? '#141414' : '#ffffff',
                    colorText: isDarkMode ? '#ffffff' : '#000000',
                },
            }}
        >
	    <Button
                className={`panel-toggle ${isPanelCollapsed ? 'collapsed' : ''}`}
                type="primary"
                onClick={togglePanel}
                icon={isPanelCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            />
	    <div className={`app-header ${isPanelCollapsed ? 'panel-collapsed' : ''}`}>
	        <h2 style={{
                    color: isDarkMode ? '#fff' : '#000',
                    transition: 'color 0.3s ease'
                }}>
                    Ziya: Code Assist
                </h2>
                <div style={{ position: 'absolute', right: '10px', display: 'flex', gap: '10px' }}>
                    <Tooltip title="Toggle theme">
                    <Button icon={<BulbOutlined />} onClick={toggleTheme} />
                    </Tooltip>
                    <Tooltip title="New Chat">
                    <Button icon={<PlusOutlined />} onClick={startNewChat} />
                    </Tooltip>
	        </div>
            </div>
            <div className={`container ${isPanelCollapsed ? 'panel-collapsed' : ''}`}>
                <FolderTree isPanelCollapsed={isPanelCollapsed}/>
                <SendChatContainer/>
                {(messages.length > 0 || streamedContent) && (
                    <div className="chat-container">
                        <StreamedContent/>
                        <Conversation enableCodeApply={enableCodeApply}/>
                    </div>)}
            </div>
        </ConfigProvider>
    );
};
