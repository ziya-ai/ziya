import React, { useState, useRef, useEffect, Suspense } from 'react';
import { FolderTree } from './FolderTree';
import { SendChatContainer } from './SendChatContainer';
import { StreamedContent } from './StreamedContent';
import { Button, Tooltip, ConfigProvider, message } from "antd";
import {
    MenuFoldOutlined,
    MenuUnfoldOutlined,
    PlusOutlined,
    BulbOutlined,
    SwapOutlined,
    CodeOutlined,
    ApiOutlined,
    SettingOutlined
} from "@ant-design/icons";
import { useTheme } from '../context/ThemeContext';
import PanelResizer from './PanelResizer';
import { useChatContext } from '../context/ChatContext';
import { ProfilerWrapper } from './ProfilerWrapper';

const ShellConfigModal = React.lazy(() => import("./ShellConfigModal"));
const MCPStatusModal = React.lazy(() => import("./MCPStatusModal"));
// Lazy load the Conversation component
const Conversation = React.lazy(() => import("./Conversation"));
const AstStatusIndicator = React.lazy(() => import("./AstStatusIndicator"));

// Error boundary component to catch extension context errors
class ExtensionErrorBoundary extends React.Component<{ children: React.ReactNode }, { hasError: boolean }> {
    constructor(props: { children: React.ReactNode }) {
        super(props);
        this.state = { hasError: false };
    }
    static getDerivedStateFromError(error: Error) {
        // Only update state for extension context errors
        if (error.message.includes('Extension context invalidated')) {
            return { hasError: true };
        }
        // Let other errors propagate normally
        throw error;
    }
    componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
        // Log extension context errors silently
        if (error.message.includes('Extension context invalidated')) {
            console.debug('Extension context error suppressed:', {
                error: error.message,
                component: errorInfo.componentStack
            });
        }
    }
    render() {
        if (this.state.hasError) {
            // Render nothing for extension errors - they're usually transient
            return null;
        }
        return this.props.children;
    }
}

// Add a new error boundary specifically for layout issues
class LayoutErrorBoundary extends React.Component<{ children: React.ReactNode }, { hasError: boolean }> {
    constructor(props: { children: React.ReactNode }) {
        super(props);
        this.state = { hasError: false };
    }

    static getDerivedStateFromError(error: Error) {
        return { hasError: true };
    }

    componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
        console.error('Layout error detected:', error, errorInfo);
    }

    resetLayout = () => {
        // Reset all layout-related localStorage items
        localStorage.removeItem('ZIYA_PANEL_WIDTH');
        localStorage.removeItem('ZIYA_PANEL_COLLAPSED');
        document.documentElement.style.setProperty('--folder-panel-width', '300px');
        window.location.reload();
    }

    render() {
        return this.state.hasError ? <div className="layout-error"><h3>Layout Error Detected</h3><Button onClick={this.resetLayout} icon={<SettingOutlined />}>Reset Layout</Button></div> : this.props.children;
    }
}


const PANEL_COLLAPSED_KEY = 'ZIYA_PANEL_COLLAPSED';
const PANEL_WIDTH_KEY = 'ZIYA_PANEL_WIDTH';

export const App: React.FC = () => {
    const { streamedContentMap, currentMessages, startNewChat, isTopToBottom, setIsTopToBottom, setStreamedContentMap } = useChatContext();
    const enableCodeApply = window.enableCodeApply === 'true';
    const [isPanelCollapsed, setIsPanelCollapsed] = useState(() => {
        const saved = localStorage.getItem(PANEL_COLLAPSED_KEY);
        return saved ? JSON.parse(saved) : false;
    });

    // Validate panel width from localStorage
    const getValidPanelWidth = (width: number): number => {
        const minWidth = 200;
        return isNaN(width) || width <= 0 ? 300 : Math.max(minWidth, width);
    };
    const { streamingConversations, currentConversationId, isStreaming,
        userHasScrolled, setUserHasScrolled } = useChatContext();
    const lastScrollPositionRef = useRef<number>(0);
    const [panelWidth, setPanelWidth] = useState(() => {
        const saved = localStorage.getItem(PANEL_WIDTH_KEY);
        return saved ? parseInt(saved, 10) : 300; // Default width: 300px
    });
    const bottomUpContentRef = useRef<HTMLDivElement | null>(null);

    const [showShellConfig, setShowShellConfig] = useState(false);
    const [showMCPStatus, setShowMCPStatus] = useState(false);
    const [mcpEnabled, setMcpEnabled] = useState(false);

    // Check MCP status on mount
    useEffect(() => {
        // Set initial panel width to 33% of viewport width
        const initialWidth = Math.round(window.innerWidth * 0.33);
        document.documentElement.style.setProperty('--folder-panel-width', `${initialWidth}px`);
        document.documentElement.style.setProperty('--model-display-height', '35px');

        // Force initial positioning of all elements after a short delay
        setTimeout(() => {
            handlePanelResize(initialWidth);
        }, 300);
    }, []); // Add empty dependency array to run only once

    // Check MCP status on mount
    useEffect(() => {
        const checkMCPStatus = async () => {
            try {
                const response = await fetch('/api/mcp/status');
                if (response.ok) {
                    const data = await response.json();
                    // MCP is enabled only if it's not explicitly disabled
                    setMcpEnabled(!data.disabled);
                }
            } catch (error) {
                console.error('Failed to check MCP status:', error);
            }
        };
        checkMCPStatus();
    }, []);
    // Reduce logging frequency to improve performance
    // console.log('Current mcpEnabled state:', mcpEnabled);
    // Add scroll event listener to detect manual scrolling
    useEffect(() => {
        const chatContainer = document.querySelector('.chat-container');
        if (!chatContainer) return;

        const handleScroll = () => {
            const { scrollTop, scrollHeight, clientHeight } = chatContainer as HTMLElement;
            const isAtBottom = Math.abs(scrollHeight - scrollTop - clientHeight) < 20;

            // If user scrolls to bottom, reset userHasScrolled to false to re-enable auto-scrolling
            if (isAtBottom && userHasScrolled) {
                setUserHasScrolled(false);
                return;
            }

            // If we're not at the bottom and the scroll position changed significantly, mark as user scrolled
            if (!isAtBottom && Math.abs(scrollTop - lastScrollPositionRef.current) > 10) {
                setUserHasScrolled(true);
            }
            lastScrollPositionRef.current = scrollTop;
        };

        chatContainer.addEventListener('scroll', handleScroll);
        return () => chatContainer.removeEventListener('scroll', handleScroll);
    }, [userHasScrolled, setUserHasScrolled]);

    const handleNewChat = async () => {
        try {
            await startNewChat();
            setStreamedContentMap(new Map());
        } catch (error) {
            message.error('Failed to create new chat');
            console.error('Error creating new chat:', error);
        }
  };

  const handlePanelResize = (newWidth: number) => {
    const minWidth = 200;
    const maxWidth = Math.min(800, window.innerWidth - 350); // Leave at least 350px for chat
    const constrainedWidth = Math.max(minWidth, Math.min(newWidth, maxWidth));

    // Only update if width actually changed significantly (avoid micro-updates)
    if (Math.abs(constrainedWidth - panelWidth) > 2) {
      setPanelWidth(getValidPanelWidth(constrainedWidth));
      localStorage.setItem(PANEL_WIDTH_KEY, constrainedWidth.toString());

      // Update CSS variable immediately
      document.documentElement.style.setProperty('--folder-panel-width', `${constrainedWidth}px`);
      
      // Remove the forced window resize event - it's not needed and causes performance issues
      // The CSS variable update is sufficient for layout changes
    }
  };

  // Sync panelWidth state with CSS variable
    useEffect(() => {
        document.documentElement.style.setProperty('--folder-panel-width', `${panelWidth}px`);
    }, [panelWidth]);

    // Add window resize handler to update panel width when viewport changes
    useEffect(() => {
        const handleWindowResize = () => {
            // Only update if panel is not being manually resized
            const currentWidth = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--folder-panel-width'));
            if (currentWidth) {
                const newWidth = Math.min(window.innerWidth * 0.25, Math.max(currentWidth, 300));
                document.documentElement.style.setProperty('--folder-panel-width', `${newWidth}px`);
            }
        };

        window.addEventListener('resize', handleWindowResize);
        return () => window.removeEventListener('resize', handleWindowResize);
    }, []);

    const preserveScrollPosition = (action: () => void) => {

        const chatContainer = document.querySelector('.chat-container');
        if (!chatContainer) return;

        if (isTopToBottom) {
            // Get the element and its exact offset from viewport top
            const rect = chatContainer.getBoundingClientRect();
            const messages = chatContainer.querySelectorAll('.message');
            let targetMessage: Element | null = null;
            let targetOffset = 0;

            for (const msg of messages) {
                const msgRect = msg.getBoundingClientRect();
                if (msgRect.top >= rect.top) {
                    targetMessage = msg;
                    targetOffset = msgRect.top - rect.top;
                    break;
                }
            }

            action();

            requestAnimationFrame(() => {
                if (!targetMessage) return;
                const newRect = chatContainer.getBoundingClientRect();
                const newMsgRect = targetMessage.getBoundingClientRect();
                chatContainer.scrollTop += (newMsgRect.top - (newRect.top + targetOffset));
            });

            // Double-check position after transition
            setTimeout(() => {
                const finalMsgRect = targetMessage?.getBoundingClientRect();
                const finalContainerRect = chatContainer.getBoundingClientRect();
                if (finalMsgRect && Math.abs(finalMsgRect.top - (finalContainerRect.top + targetOffset)) > 1) {
                    chatContainer.scrollTop += (finalMsgRect.top - (finalContainerRect.top + targetOffset));
                }
            }, 300);
        } else {
            // Bottom-up mode handles itself correctly
            action();
        }
    };

    const togglePanel = () => {
        preserveScrollPosition(() => {
            const newState = !isPanelCollapsed;
            setIsPanelCollapsed(newState);
            localStorage.setItem(PANEL_COLLAPSED_KEY, JSON.stringify(newState));
            
            // Update CSS variable to match panel state
            const newWidth = newState ? 0 : panelWidth;
            document.documentElement.style.setProperty('--folder-panel-width', `${newWidth}px`);
        });
    };

    // in top-down mode autoscroll to end
    useEffect(() => {
        // Removed aggressive auto-scrolling that was causing cursor jumps
        // Let StreamedContent handle scrolling during streaming
    }, [isTopToBottom, currentMessages, streamedContentMap, userHasScrolled, streamingConversations, currentConversationId]);

    const toggleDirection = () => {
        // Prevent scroll jumping when toggling direction
        const chatContainer = document.querySelector('.chat-container');
        const currentScrollTop = chatContainer?.scrollTop || 0;

        setIsTopToBottom(prev => !prev);

        // Restore scroll position after a brief delay
        setTimeout(() => {
            if (chatContainer) {
                chatContainer.scrollTop = currentScrollTop;
            }
        }, 50);
    };

    useEffect(() => {
        // Handle scroll position after mode switch
        const chatContainer = document.querySelector('.chat-container');
        const bottomUpContent = document.querySelector('.bottom-up-content');

        setTimeout(() => {
            if (isTopToBottom && chatContainer) {
                chatContainer.scrollTop = chatContainer.scrollHeight;
            } else if (!isTopToBottom && bottomUpContent) {
                requestAnimationFrame(() => bottomUpContent.scrollTop = 0);
            }
        }, 100);
    }, [isTopToBottom, isStreaming, streamingConversations, currentConversationId]);

    // Add keyboard shortcut for layout reset
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.ctrlKey && e.shiftKey && e.key === 'R') {
                e.preventDefault();
                localStorage.removeItem('ZIYA_PANEL_WIDTH');
                localStorage.removeItem('ZIYA_PANEL_COLLAPSED');
                document.documentElement.style.setProperty('--folder-panel-width', '300px');
                window.location.reload();
            }
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, []);

    const { isDarkMode, toggleTheme, themeAlgorithm } = useTheme();

    const chatContainerContent = isTopToBottom ? (
        <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100%' }}>
            <div style={{
                flex: 1, overflow: 'auto', width: '100%',
                maxWidth: '100%', overflowX: 'hidden'
            }}>
                <Suspense fallback={<div>Loading conversation...</div>}>
                    <Conversation key="conv" enableCodeApply={enableCodeApply} />
                </Suspense>
                <StreamedContent key="stream" />
            </div>
            <SendChatContainer fixed={true} />
        </div>
    ) : (
        <div className="chat-content-with-fixed-input">
            <SendChatContainer fixed={true} />
            <StreamedContent key="stream" />
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
                <div className="bottom-up-content" ref={bottomUpContentRef}>
                    <Suspense fallback={<div>Loading conversation...</div>}>
                        <Conversation key="conv" enableCodeApply={enableCodeApply} />
                    </Suspense>
                </div>
            </div>
        </div>
    );

    return (
        <ExtensionErrorBoundary>
            <ProfilerWrapper id="App">
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
                        size="small"
                        style={{
                            padding: '4px 8px',
                            color: isDarkMode ? undefined : (isPanelCollapsed ? '#ffffff' : '#1890ff'),
                            backgroundColor: isDarkMode ? undefined : (isPanelCollapsed ? '#1890ff' : undefined),
                        }}
                        ghost={!isDarkMode || !isPanelCollapsed}
                    >{isPanelCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}</Button>

                    <PanelResizer
                        onResize={handlePanelResize}
                        isPanelCollapsed={isPanelCollapsed}
                    />

                    <div style={{ height: 'var(--header-height)' }}>
                        <div className={`app-header ${isPanelCollapsed ? 'panel-collapsed' : ''}`}
                            style={{
                                position: 'fixed',
                                width: '100%',
                                zIndex: 1000
                            }}>
                            <h2 style={{
                                color: isDarkMode ? '#fff' : '#000',
                                transition: 'color 0.3s ease',
                                transform: 'translateZ(0)' // force GPU accel
                            }}>
                                <div style={{ position: 'absolute', left: '10px', display: 'flex', gap: '10px' }}>
                                    <Tooltip title={`Switch to ${isTopToBottom ? 'bottom-up' : 'top-down'} view`}>
                                        <Button
                                            icon={<SwapOutlined rotate={90} />}
                                            onClick={toggleDirection}
                                            type={isTopToBottom ? 'primary' : 'default'}
                                        >
                                            {isTopToBottom ? 'Top-Down' : 'Bottom-Up'}
                                        </Button>
                                    </Tooltip>
                                </div>
                                Ziya: Code Assist
                            </h2>
                            <div style={{ position: 'absolute', right: '10px', display: 'flex', gap: '10px' }}>
                                <Tooltip title="Toggle theme">
                                    <Button icon={<BulbOutlined />} onClick={toggleTheme} />
                                </Tooltip>
                                {mcpEnabled && (
                                    <>
                                        <Tooltip title="Shell Configuration">
                                            <Button icon={<CodeOutlined />} onClick={() => setShowShellConfig(true)} />
                                        </Tooltip>
                                        <Tooltip title="MCP Servers">
                                            <Button icon={<ApiOutlined />} onClick={() => setShowMCPStatus(true)} />
                                        </Tooltip>
                                    </>
                                )}
                                <Tooltip title="New Chat">
                                    <Button icon={<PlusOutlined />} onClick={handleNewChat} />
                                </Tooltip>
                            </div>
                        </div>
                    </div>
                    <div className={`container ${isPanelCollapsed ? 'panel-collapsed' : ''}`}
                        style={{
                            marginTop: 'var(--header-height)',
                            height: 'calc(100vh - var(--header-height))',
                            display: 'flex',
                            width: '100vw',
                            overflow: 'hidden'
                        }}>
                        <FolderTree isPanelCollapsed={isPanelCollapsed} />
                        <div className="chat-container">
                            <div className="chat-content-stabilizer">
                                <LayoutErrorBoundary>
                                    {chatContainerContent}
                                </LayoutErrorBoundary>
                                {/* Add a hidden element to check layout integrity */}
                                <div id="layout-integrity-check" style={{
                                    position: 'absolute',
                                    visibility: 'hidden'
                                }}></div>
                            </div>
                        </div>
                    </div>
                    <Suspense fallback={null}>
                        <AstStatusIndicator />
                    </Suspense>

                    <Suspense fallback={null}>
                        {mcpEnabled && (
                            <>
                                <ShellConfigModal
                                    visible={showShellConfig}
                                    onClose={() => setShowShellConfig(false)}
                                />
                                <MCPStatusModal
                                    visible={showMCPStatus}
                                    onClose={() => setShowMCPStatus(false)}
                                />
                            </>
                        )}
                    </Suspense>

                </ConfigProvider>
            </ProfilerWrapper>
        </ExtensionErrorBoundary>
    );
};
