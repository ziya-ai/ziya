import React, { useState } from 'react';
import {FolderTree} from "./FolderTree";
import {SendChatContainer} from "./SendChatContainer";
import {useChatContext} from '../context/ChatContext';
import {StreamedContent} from './StreamedContent';
import {Conversation} from "./Conversation";
import {Button, Tooltip } from "antd";
import {MenuFoldOutlined, MenuUnfoldOutlined, PlusOutlined} from "@ant-design/icons";

export const App = () => {
    const {streamedContent, messages, startNewChat} = useChatContext();
    const enableCodeApply = window.enableCodeApply === 'true';
    const [isPanelCollapsed, setIsPanelCollapsed] = useState(false);

    const togglePanel = () => {
        setIsPanelCollapsed(!isPanelCollapsed);
    };

    return (
        <>
	    <Button
                className={`panel-toggle ${isPanelCollapsed ? 'collapsed' : ''}`}
                type="primary"
                onClick={togglePanel}
                icon={isPanelCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            />
	    <div className={`app-header ${isPanelCollapsed ? 'panel-collapsed' : ''}`}>
                <h2>Ziya: Code Assist</h2>
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
            <div style={{position: 'fixed', right: '10px', top: '10px'}}>
                <Tooltip title="New Chat">
                    <Button icon={<PlusOutlined />} onClick={startNewChat} size={"large"}/>
                </Tooltip>
            </div>
        </>
    );
};
