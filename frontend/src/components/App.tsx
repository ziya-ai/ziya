import React, { useState, useRef, useEffect, Suspense, useCallback } from 'react';
import { FolderTree } from './FolderTree';
import { SendChatContainer } from './SendChatContainer';
import { StreamedContent } from './StreamedContent';
import { Button, Tooltip, ConfigProvider, theme, message } from "antd";
import {
    MenuFoldOutlined,
    ExperimentOutlined,
    MenuUnfoldOutlined,
    PlusOutlined,
    BulbOutlined,
    SwapOutlined,
    SettingOutlined
} from "@ant-design/icons";
import { useTheme } from '../context/ThemeContext';
import { DebugControls } from './DebugControls';
import { MUIFileExplorer } from './MUIFileExplorer';
import PanelResizer from './PanelResizer';
import { useChatContext } from '../context/ChatContext';
import { StreamingContentManager } from './StreamingContentManager';

// Lazy load the Conversation component
const Conversation = React.lazy(() => import("./Conversation"));
const PrismTest = React.lazy(() => import("./PrismTest"));
const SyntaxTest = React.lazy(() => import("./SyntaxTest"));
const MUIChatHistory = React.lazy(() => import("./MUIChatHistory"));
const AstStatusIndicator = React.lazy(() => import("./AstStatusIndicator"));
const ApplyDiffTest = React.lazy(() => import("./ApplyDiffTest"));

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
    const { dbError } = useChatContext();
    const bottomUpContentRef = useRef<HTMLDivElement | null>(null);

    // Set initial CSS variable on mount
    useEffect(() => {
        document.documentElement.style.setProperty('--folder-panel-width', `${getValidPanelWidth(panelWidth)}px`);
        document.documentElement.style.setProperty('--model-display-height', '35px');

        // Force initial positioning of all elements after a short delay
        setTimeout(() => {
            handlePanelResize(panelWidth);
        }, 300);
    }, []);

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
    }, [setUserHasScrolled]);

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
        // Ensure we're not making the chat area too small
        const minChatWidth = 300; // Minimum width for chat area
        const maxPanelWidth = window.innerWidth - minChatWidth - 60; // 60px for margins and padding

        // Apply constraints
        const constrainedWidth = Math.min(newWidth, maxPanelWidth);

        setPanelWidth(getValidPanelWidth(constrainedWidth));
        localStorage.setItem(PANEL_WIDTH_KEY, constrainedWidth.toString());
        document.documentElement.style.setProperty('--folder-panel-max-width', 'none');

        // Directly update any elements that might have fixed widths
        setTimeout(() => {
            const folderPanel = document.querySelector('.folder-tree-panel') as HTMLElement;
            const modelDisplay = document.querySelector('.model-id-display') as HTMLElement;
            const tokenDisplay = document.querySelector('.token-display') as HTMLElement;

            if (folderPanel && constrainedWidth > 0) {
                folderPanel.style.width = `${constrainedWidth}px`;
                folderPanel.style.minWidth = `${constrainedWidth}px`;
                folderPanel.style.maxWidth = 'none';
            }

            if (modelDisplay) {
                modelDisplay.style.width = `${constrainedWidth}px`;
                modelDisplay.style.minWidth = `${constrainedWidth}px`;
            }

            // Update CSS variable after DOM updates
            document.documentElement.style.setProperty('--folder-panel-width', `${getValidPanelWidth(constrainedWidth)}px`);

            // Dispatch resize event after a small delay to ensure all styles are applied
            setTimeout(() => {
                window.dispatchEvent(new Event('resize'));
                // Force another resize event after a bit longer to catch any late updates
                setTimeout(() => window.dispatchEvent(new Event('resize')), 100);
            }, 10);
        }, 10);
    };

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
        });
    };

    // in top-down mode autoscroll to end
    useEffect(() => {
        if (isTopToBottom) {
            const chatContainer = document.querySelector('.chat-container');
            if (!chatContainer) return;

            const scrollToBottom = (smooth = false) => {
                if (userHasScrolled) return;

                requestAnimationFrame(() => {
                    (chatContainer as Element).scrollTo({
                        top: chatContainer.scrollHeight,
                        behavior: smooth ? 'smooth' : 'auto'
                    });
                });
            };
            // Only auto-scroll when streaming is active and user hasn't scrolled up
            const isStreaming = streamingConversations.has(currentConversationId);
            if (isStreaming) {
                scrollToBottom();
                // Add a small delay for smooth scroll after content renders
                const timeoutId = setTimeout(() => scrollToBottom(true), 50);
                return () => clearTimeout(timeoutId);
            }
        }
    }, [isTopToBottom, currentMessages, streamedContentMap, userHasScrolled, streamingConversations, currentConversationId]);

    const toggleDirection = () => {
        setIsTopToBottom(prev => !prev);
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
                        left: isPanelCollapsed ? '-1px' : `${panelWidth + 2}px`, // Add 2px for border
                        backgroundColor: isDarkMode ? undefined : (isPanelCollapsed ? '#1890ff' : undefined),
                    }}
                    ghost={!isDarkMode || !isPanelCollapsed}
                >{isPanelCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}</Button>

                <PanelResizer
                    onResize={handlePanelResize}
                    isPanelCollapsed={isPanelCollapsed}
                />

                <div style={{ height: 'var(--app-header-height)' }}>
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
                            <Tooltip title="New Chat">
                                <Button icon={<PlusOutlined />} onClick={handleNewChat} />
                            </Tooltip>
                        </div>
                    </div>
                </div>
                <div className={`container ${isPanelCollapsed ? 'panel-collapsed' : ''}`}
                    style={{
                        marginTop: 'var(--app-header-height)',
                        height: 'calc(100vh - var(--app-header-height))'
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

            </ConfigProvider>
        </ExtensionErrorBoundary>
    );
};
