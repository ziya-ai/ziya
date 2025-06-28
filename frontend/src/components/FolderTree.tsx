import React, { useEffect, useState, useCallback, useRef } from 'react';
import { Tabs, message } from 'antd';
import { useFolderContext } from '../context/FolderContext';
import { useChatContext } from '../context/ChatContext';
import { TokenCountDisplay } from "./TokenCountDisplay";
import { FolderOutlined } from '@ant-design/icons'; // Import icons
import { ModelConfigButton } from './ModelConfigButton';
import { MessageOutlined } from '@ant-design/icons';
import MUIChatHistory from './MUIChatHistory';
import { MUIFileExplorer } from './MUIFileExplorer';
import { useTheme } from '../context/ThemeContext';
import { FolderScanProgress } from './FolderScanProgress';

import CreateNewFolderIcon from '@mui/icons-material/CreateNewFolder';
import AddCommentIcon from '@mui/icons-material/AddComment';
import IconButton from '@mui/material/IconButton';
import Tooltip from '@mui/material/Tooltip';

interface FolderTreeProps {
    isPanelCollapsed: boolean;
}

const ACTIVE_TAB_KEY = 'ZIYA_ACTIVE_TAB';

export const FolderTree = React.memo(({ isPanelCollapsed }: FolderTreeProps) => {
    // We only need minimal context now since MUIFileExplorer handles its own state
    // Extract only the specific values needed from ChatContext
    // to prevent unnecessary re-renders
    const [modelId, setModelId] = useState<string>('');
    const { isDarkMode } = useTheme();

    // Use a more selective approach to extract only what we need from ChatContext
    const chatContext = useChatContext();
    const { startNewChat, createFolder } = useChatContext();
    const { isScanning, scanError } = useFolderContext();
    const currentFolderId = chatContext.currentFolderId;

    // Add state to track panel width
    const [panelWidth, setPanelWidth] = useState<number>(300);
    const [modelDisplayName, setModelDisplayName] = useState<string>('');

    // Add ref for the panel element
    const [showActionButtons, setShowActionButtons] = useState(true);
    const panelRef = useRef<HTMLDivElement>(null);
    const [activeTab, setActiveTab] = useState(() => localStorage.getItem(ACTIVE_TAB_KEY) || '1');

    // Add effect to track panel width
    useEffect(() => {
        if (!panelRef.current) return;

        const resizeObserver = new ResizeObserver(entries => {
            for (const entry of entries) {
                setPanelWidth(entry.contentRect.width);
                // Dispatch custom event for other components to react to width change
                window.dispatchEvent(new CustomEvent('folderPanelResize', {
                    detail: { width: entry.contentRect.width }
                }));

                // Hide action buttons when panel gets too narrow (less than 280px)
                setShowActionButtons(entry.contentRect.width >= 280);
            }
        });

        resizeObserver.observe(panelRef.current);
        return () => resizeObserver.disconnect();
    }, []);

    // Handle creating a new folder at current level
    const handleCreateFolderAtCurrentLevel = useCallback(async () => {
        try {
            await createFolder('New Folder', currentFolderId);
            message.success('New folder created successfully');
        } catch (error) {
            console.error('Error creating folder:', error);
            message.error('Failed to create folder');
        }
    }, [createFolder, currentFolderId]);

    // Handle creating a new chat at current folder level
    const handleCreateChatAtCurrentLevel = useCallback(async () => {
        try {
            await startNewChat(currentFolderId);
            message.success('New chat created successfully');
        } catch (error) {
            console.error('Error creating chat:', error);
            message.error('Failed to create new chat');
        }
    }, [startNewChat, currentFolderId]);

    // Handle scan cancellation
    const handleCancelScan = useCallback(async () => {
        try {
            // The cancellation logic is handled in FolderContext
        } catch (error) {
            console.error('Error cancelling scan:', error);
        }
    }, []);

    useEffect(() => {
        localStorage.setItem(ACTIVE_TAB_KEY, activeTab);
    }, [activeTab]);

    // Update model info when it changes
    const updateModelInfo = useCallback(async () => {
        try {
            const response = await fetch('/api/current-model');
            const data = await response.json();
            setModelId(data.model_id);
            setModelDisplayName(data.display_model_id || data.model_alias || data.model_id);
            console.info(`Updated model info: ${data.model_id} (${data.display_model_id || 'no display name'})`);
        } catch (error) {
            console.error('Error fetching model info:', error);
            // Fallback to basic model ID if detailed info fails
            fetchModelId();
        }
    }, []);

    const fetchModelId = useCallback(async () => {
        try {
            const response = await fetch('/api/model-id');
            const data = await response.json();
            setModelId(data.model_id);
            if (!modelDisplayName) {
                setModelDisplayName(data.model_id);
            }
        } catch (error) {
            console.error('Error fetching model ID:', error);
        }
    }, []);

    useEffect(() => {
        fetchModelId();
        updateModelInfo();

        // Listen for model changes
        const handleModelChange = () => {
            console.log("Model change detected, updating model info");
            // Use setTimeout to ensure the backend has updated
            updateModelInfo();
            // And check again after a delay to ensure we have the latest
            setTimeout(updateModelInfo, 500);
        };
        window.addEventListener('modelSettingsChanged', handleModelChange);
        return () => {
            window.removeEventListener('modelSettingsChanged', handleModelChange);
        };
    }, [fetchModelId, updateModelInfo]);

    return (
        <div ref={panelRef} className={`folder-tree-panel ${isPanelCollapsed ? 'collapsed' : ''}`}>
            <TokenCountDisplay />
            <FolderScanProgress onCancel={handleCancelScan} />
            <Tabs
                activeKey={activeTab}
                defaultActiveKey="1"
                destroyInactiveTabPane={false}
                style={{
                    height: '100%',
                    display: 'flex',
                    flexDirection: 'column',
                    color: isDarkMode ? '#ffffff' : undefined,
                    overflow: 'hidden',
                    margin: '0 -2px'  // Reduced from -4px to -2px
                }}
                onChange={setActiveTab}
                items={[
                    {
                        key: '1',
                        label: (
                            <div style={{
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                                width: '100%',
                                minWidth: 0
                            }}>
                                <span style={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    minWidth: 0,
                                    overflow: 'hidden'
                                }}>
                                    <FolderOutlined style={{ marginRight: 8 }} />
                                    File Explorer
                                </span>
                            </div>
                        ),
                        children: (
                            <div style={{ position: 'relative' }}>
                                <MUIFileExplorer />
                                {(isScanning || scanError) && <div style={{ opacity: 0.6, pointerEvents: 'none', position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, zIndex: 1 }} />}
                            </div>
                        )
                    },
                    {
                        key: '2',
                        label: (
                            <div style={{
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                                width: '100%',
                                minWidth: 0
                            }}>
                                <span style={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    minWidth: 0,
                                    overflow: 'hidden'
                                }}>
                                    <MessageOutlined style={{ marginRight: 8 }} />
                                    Chat History
                                </span>
                                {showActionButtons && activeTab === '2' && (
                                    <div style={{ display: 'flex', gap: 4, marginLeft: 8, flexShrink: 0 }}>
                                        <Tooltip title="Create new folder">
                                            <IconButton size="small" onClick={handleCreateFolderAtCurrentLevel}
                                                sx={{ color: '#1890ff', border: '1px solid #1890ff', width: 24, height: 24 }}>
                                                <CreateNewFolderIcon sx={{ fontSize: 14 }} />
                                            </IconButton>
                                        </Tooltip>
                                        <Tooltip title="Create new chat">
                                            <IconButton size="small" onClick={handleCreateChatAtCurrentLevel}
                                                sx={{ color: '#1890ff', border: '1px solid #1890ff', width: 24, height: 24 }}>
                                                <AddCommentIcon sx={{ fontSize: 14 }} />
                                            </IconButton>
                                        </Tooltip>
                                    </div>
                                )}
                            </div>
                        ),
                        children: <MUIChatHistory />
                    },
                ]}
            />
            <div className="model-id-display" style={{
                display: 'flex',
                alignItems: 'center',
            }}>
                {modelId && <span style={{ flex: 1 }}>Model: {modelDisplayName || modelId}</span>}
                {modelId && <ModelConfigButton modelId={modelId} />}
            </div>
        </div>
    );
});
