import React, { useEffect, useState, useCallback, useRef, useMemo, useLayoutEffect } from 'react';
import { Tabs, message } from 'antd';
import { useFolderContext } from '../context/FolderContext';
import { useChatContext } from '../context/ChatContext';
import { TokenCountDisplay } from "./TokenCountDisplay";
import { FolderOutlined, FileOutlined } from '@ant-design/icons'; // Import icons
import { debounce } from 'lodash';
import { ChatHistory } from "./ChatHistory";
import { ModelConfigButton } from './ModelConfigButton';
import { ReloadOutlined, MessageOutlined, PlusOutlined } from '@ant-design/icons';
import { convertToTreeData } from '../utils/folderUtil';
import MUIChatHistory from './MUIChatHistory';
import { MUIFileExplorer } from './MUIFileExplorer';
import { useTheme } from '../context/ThemeContext';

interface FolderTreeProps {
    isPanelCollapsed: boolean;
}

// Create a cache for token calculations to avoid redundant logs
const tokenCalculationCache = new Map<string, { included: number, total: number }>();
const DEBUG_LOGGING_ENABLED = false; // Set to false to disable verbose logging

const ACTIVE_TAB_KEY = 'ZIYA_ACTIVE_TAB';

export const FolderTree = React.memo(({ isPanelCollapsed }: FolderTreeProps) => {
    // We only need minimal context now since MUIFileExplorer handles its own state
    // Extract only the specific values needed from ChatContext
    // to prevent unnecessary re-renders
    const [modelId, setModelId] = useState<string>('');
    const { } = useChatContext();
    const { isDarkMode } = useTheme();

    // Use a more selective approach to extract only what we need from ChatContext
    const chatContext = useChatContext();
    const { startNewChat, createFolder } = useChatContext();
    const currentFolderId = chatContext.currentFolderId;
    const chatFolders = chatContext.folders;

    // Add state to track panel width
    const [panelWidth, setPanelWidth] = useState<number>(300);
    const [modelDisplayName, setModelDisplayName] = useState<string>('');

    // Add ref for the panel element
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
            }
        });

        resizeObserver.observe(panelRef.current);
        return () => resizeObserver.disconnect();
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
                    margin: '0 -8px'
                }}
                onChange={setActiveTab}
                items={[
                    {
                        key: '1',
                        label: (
                            <span>
                                <FolderOutlined style={{ marginRight: 8 }} />
                                File Explorer
                            </span>
                        ),
                        children: <MUIFileExplorer />
                    },
                    {
                        key: '2',
                        label: (
                            <span>
                                <MessageOutlined style={{ marginRight: 8 }} />
                                Chat History
                            </span>
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
