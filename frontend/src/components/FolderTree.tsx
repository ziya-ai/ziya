import React, { useEffect, useState, useCallback, useRef, lazy, Suspense } from 'react';
import { Tabs, message, Spin } from 'antd';
import { useFolderContext, FolderProvider } from '../context/FolderContext';
import { useConversationList } from '../context/ConversationListContext';
import { useActiveChat } from '../context/ActiveChatContext';
import { useProject } from '../context/ProjectContext';
import { TokenCountDisplay } from "./TokenCountDisplay";
import { FolderOutlined } from '@ant-design/icons'; // Import icons
import { ModelConfigButton } from './ModelConfigButton';
import { useResolvedModelPin } from '../hooks/useResolvedModelPin';
import { MessageOutlined } from '@ant-design/icons';
import MUIChatHistory from './MUIChatHistory';
import { MUIFileExplorer } from './MUIFileExplorer';
import { ProjectSwitcher } from './ProjectSwitcher';
import { ActiveContextBar } from './ActiveContextBar';
import { ContextsTab } from './ContextsTab';
import { useTheme } from '../context/ThemeContext';
import { FolderScanProgress } from './FolderScanProgress';

import CreateNewFolderIcon from '@mui/icons-material/CreateNewFolder';
import AddCommentIcon from '@mui/icons-material/AddComment';
import IconButton from '@mui/material/IconButton';
import Tooltip from '@mui/material/Tooltip';

// Lazy — bead backlog browser sidebar tab (see design/bead-backlog-browser.md)
const BacklogBrowser = lazy(() => import('./BacklogBrowser'));

interface FolderTreeProps {
    isPanelCollapsed: boolean;
}

const ACTIVE_TAB_KEY = 'ZIYA_ACTIVE_TAB';

export const FolderTree = React.memo(({ isPanelCollapsed }: FolderTreeProps) => {
    // We only need minimal context now since MUIFileExplorer handles its own state
    const { contexts, activeContextIds, isLoadingProject } = useProject();
    // Extract only the specific values needed from ChatContext
    // to prevent unnecessary re-renders
    const [modelId, setModelId] = useState<string>('');
    const { isDarkMode } = useTheme();
    const projectContext = useProject();
    // Extract only the specific values needed from ChatContext
    // This reduces re-renders when unrelated ChatContext values change
    // (e.g., streamedContentMap changes during streaming won't trigger FolderTree re-renders)
    const { createFolder, currentFolderId, isProjectSwitching } = useConversationList();
    const { startNewChat } = useActiveChat();
    const { isScanning, scanError } = useFolderContext();
    // Blank the panel as soon as either the project API call or the full sync is in progress
    const isSwitchingProject = isLoadingProject || isProjectSwitching;
    // Distinguish initial load from an actual switch for the spinner label.
    // isProjectSwitching is only set for a genuine switch (it is gated on
    // isActualProjectSwitch, which is false when serverSyncedForProject is
    // still null), so on a cold start this must not claim to be "switching" —
    // there is no previous project to switch away from.
    const switchingLabel = isProjectSwitching
        ? 'Switching project…'
        : 'Loading…';
    const [panelWidth, setPanelWidth] = useState<number>(300);
    const [modelDisplayName, setModelDisplayName] = useState<string>('');

    // Add ref for the panel element
    const [showActionButtons, setShowActionButtons] = useState(true);
    const panelRef = useRef<HTMLDivElement>(null);
    const [activeTab, setActiveTab] = useState(() => localStorage.getItem(ACTIVE_TAB_KEY) || '1');
    // Lazy parked-count badge — populated when BacklogBrowser first fetches
    // and broadcasts BACKLOG_COUNT_EVENT ('ziya:backlog-count').
    const [backlogCount, setBacklogCount] = useState(0);

    // Progressive collapse logic for tabs
    // Priority: Chats (3) > Files (1) > Contexts (2)
    const getTabDisplayMode = useCallback((tabKey: string): 'full' | 'icon' => {
        const isActive = activeTab === tabKey;
        
        // Very narrow: all icons
        if (panelWidth < 140) return 'icon';
        
        // Narrow: only active tab shows text
        if (panelWidth < 200) return isActive ? 'full' : 'icon';
        
        // Medium: active + highest priority tabs show text
        if (panelWidth < 260) {
            if (isActive) return 'full';
            // Priority order: Chats > Files > Contexts
            if (tabKey === '3') return 'full'; // Chats always if space
            if (tabKey === '1' && panelWidth >= 230) return 'full'; // Files next
            return 'icon';
        }
        
        // Wide enough: show all
        return 'full';
    }, [activeTab, panelWidth]);

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

    // Listen for the backlog's parked-count broadcast to drive the tab badge.
    useEffect(() => {
        const handler = (e: Event) => {
            const detail = (e as CustomEvent).detail;
            if (detail && typeof detail.count === 'number') setBacklogCount(detail.count);
        };
        window.addEventListener('ziya:backlog-count', handler);
        return () => window.removeEventListener('ziya:backlog-count', handler);
    }, []);

    // Update model info when it changes
    const updateModelInfo = useCallback(async () => {
        console.debug('🔄 FolderTree: updateModelInfo called');
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
            setModelDisplayName(prev => {
                if (!prev) return data.model_id;
                return prev;
            });
        } catch (error) {
            console.error('Error fetching model ID:', error);
        }
    }, []);

    useEffect(() => {
        fetchModelId();
        updateModelInfo();

        // Listen for model changes
        const handleModelChange = () => {
            console.debug("FolderTree: Model change event received");
            // Single call with delay to ensure backend is ready
            updateModelInfo();
        };
        window.addEventListener('modelSettingsChanged', handleModelChange);
        return () => {
            window.removeEventListener('modelSettingsChanged', handleModelChange);
        };
    }, [fetchModelId, updateModelInfo]);

    // Effective model for the ACTIVE conversation, resolved across all
    // scopes (conversation → folder → project) and both layers (tab pin
    // over saved pref).  The hook re-resolves on pin-store mutations and
    // record changes, so no manual event wiring is needed here.
    const { pin: activePin } = useResolvedModelPin();

    return (
        <div ref={panelRef} className={`folder-tree-panel ${isPanelCollapsed ? 'collapsed' : ''}`}>
            <ProjectSwitcher />
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
                tabBarStyle={{
                    flexWrap: 'nowrap',
                    minWidth: 0,
                }}
                items={[
                    {
                        key: '1',
                        label: (
                            <span style={{ display: 'flex', alignItems: 'center', whiteSpace: 'nowrap', gap: getTabDisplayMode('1') === 'full' ? 6 : 0 }}>
                                <FolderOutlined style={{ fontSize: 16 }} />
                                {getTabDisplayMode('1') === 'full' && <span>Files</span>}
                            </span>
                        ),
                        children: (
                            <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', height: '100%' }}>
                                {isSwitchingProject ? (
                                    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', flexDirection: 'column', gap: 12, opacity: 0.7 }}>
                                        <Spin size="large" />
                                        <span style={{ fontSize: 13 }}>{switchingLabel}</span>
                                    </div>
                                ) : (
                                    <>
                                        <ActiveContextBar />
                                        <MUIFileExplorer />
                                        {(isScanning || scanError) && <div style={{ opacity: 0.6, pointerEvents: 'none', position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, zIndex: 1 }} />}
                                    </>
                                )}
                            </div>
                        )
                    },
                    {
                        key: '2',
                        label: (
                            <span style={{ display: 'flex', alignItems: 'center', whiteSpace: 'nowrap', gap: getTabDisplayMode('2') === 'full' ? 6 : 0 }}>
                                <span style={{ fontSize: 16 }}>🎓</span>
                                {getTabDisplayMode('2') === 'full' && <span>Skills</span>}
                            </span>
                        ),
                        children: (
                            <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                                {isSwitchingProject ? (
                                    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', flexDirection: 'column', gap: 12, opacity: 0.7 }}>
                                        <Spin size="large" />
                                        <span style={{ fontSize: 13 }}>{switchingLabel}</span>
                                    </div>
                                ) : (
                                    <>
                                        <ActiveContextBar />
                                        <ContextsTab />
                                    </>
                                )}
                            </div>
                        )
                    },
                    {
                        key: '3',
                        label: (
                            <span style={{ display: 'flex', alignItems: 'center', whiteSpace: 'nowrap', gap: getTabDisplayMode('3') === 'full' ? 6 : 0 }}>
                                <MessageOutlined style={{ fontSize: 16 }} />
                                {getTabDisplayMode('3') === 'full' && <span>Chats</span>}
                            </span>
                        ),
                        children: (
                            <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                                {isSwitchingProject ? (
                                    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', flexDirection: 'column', gap: 12, opacity: 0.7 }}>
                                        <Spin size="large" />
                                        <span style={{ fontSize: 13 }}>{switchingLabel}</span>
                                    </div>
                                ) : (
                                    <>
                                        <ActiveContextBar />
                                        <MUIChatHistory />
                                    </>
                                )}
                            </div>
                        )
                    },
                    {
                        key: '4',
                        label: (
                            <span style={{ display: 'flex', alignItems: 'center', whiteSpace: 'nowrap', gap: getTabDisplayMode('4') === 'full' ? 6 : 0 }}>
                                <span style={{ fontSize: 16 }}>📋</span>
                                {getTabDisplayMode('4') === 'full' && <span>Backlog</span>}
                                {backlogCount > 0 && (
                                    <span style={{ background: '#2dd4bf', color: '#111', borderRadius: 8, padding: '0 6px', fontSize: 11, marginLeft: getTabDisplayMode('4') === 'full' ? 4 : 2 }}>{backlogCount}</span>
                                )}
                            </span>
                        ),
                        children: (
                            <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                                {isSwitchingProject ? (
                                    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', flexDirection: 'column', gap: 12, opacity: 0.7 }}>
                                        <Spin size="large" />
                                        <span style={{ fontSize: 13 }}>{switchingLabel}</span>
                                    </div>
                                ) : (
                                    <Suspense fallback={<div style={{ display: 'flex', justifyContent: 'center', padding: 20 }}><Spin /></div>}>
                                        <BacklogBrowser />
                                    </Suspense>
                                )}
                            </div>
                        )
                    },
                ]}
            />
            <div className="model-id-display" style={{
                display: 'flex',
                alignItems: 'center',
            }}>
                {modelId && (
                    <span style={{ flex: 1 }}>
                        Model: {activePin ? activePin.model : (modelDisplayName || modelId)}
                        {activePin && (() => {
                            // Higher-resolution pin indicator: the scope word
                            // ({conv|folder|proj}) says which hierarchy level
                            // the effective pin lives at; the style says
                            // whether it's tab-only (dashed, ephemeral) or
                            // saved (solid, persists across tabs/restarts).
                            const scopeLabel = { conversation: 'conv', folder: 'folder', project: 'proj' }[activePin.scope];
                            const persisted = activePin.persistent;
                            const icon = persisted ? '📍' : '📌';
                            const layer = persisted ? 'saved' : 'tab';
                            const title = persisted
                                ? `Pinned to this ${activePin.scope} — saved, persists across tabs & restarts (server default: ${modelDisplayName || modelId})`
                                : `Pinned to this ${activePin.scope} — this tab only (server default: ${modelDisplayName || modelId})`;
                            return (
                                <span
                                    title={title}
                                    style={{
                                        marginLeft: 6, padding: '0 6px', fontSize: 10.5,
                                        borderRadius: 9, cursor: 'default', whiteSpace: 'nowrap',
                                        border: persisted ? '1px solid #2a5b38' : '1px dashed #6b5a1a',
                                        color: persisted ? '#4ac76a' : '#e8d44a',
                                    }}
                                >{icon} {scopeLabel} · {layer}</span>
                            );
                        })()}
                    </span>
                )}
                {modelId && <ModelConfigButton modelId={modelId} />}
            </div>
        </div>
    );
});
