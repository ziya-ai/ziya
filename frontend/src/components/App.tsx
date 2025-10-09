import React, { useState, useRef, useEffect, useLayoutEffect, Suspense } from 'react';
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
    const {
        streamedContentMap, currentMessages, startNewChat, isTopToBottom, setIsTopToBottom, setStreamedContentMap,
        streamingConversations, currentConversationId, isStreaming, userHasScrolled, setUserHasScrolled
    } = useChatContext();
    const enableCodeApply = window.enableCodeApply === 'true';
    const [astEnabled, setAstEnabled] = useState(false);
    const [isPanelCollapsed, setIsPanelCollapsed] = useState(() => {
        const saved = localStorage.getItem(PANEL_COLLAPSED_KEY);
        return saved ? JSON.parse(saved) : false;
    });

    // Validate panel width from localStorage
    const getValidPanelWidth = (width: number): number => {
        const minWidth = 200;
        return isNaN(width) || width <= 0 ? 300 : Math.max(minWidth, width);
    };
    const lastScrollPositionRef = useRef<number>(0);
    const [panelWidth, setPanelWidth] = useState(() => {
        const saved = localStorage.getItem(PANEL_WIDTH_KEY);
        return saved ? parseInt(saved, 10) : 300; // Default width: 300px
    });
    const wasFollowingStreamRef = useRef<boolean>(false);
    const scrollPreservationRef = useRef<{ position: number; wasAtBottom: boolean }>({ position: 0, wasAtBottom: false });
    const isRenderingRef = useRef(false);
    const bottomUpContentRef = useRef<HTMLDivElement | null>(null);
    const chatContainerRef = useRef<HTMLDivElement | null>(null);

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

    // Check AST status on mount
    useEffect(() => {
        const checkASTStatus = async () => {
            try {
                const response = await fetch('/api/ast/status');
                if (response.ok) {
                    const data = await response.json();
                    setAstEnabled(data.enabled === true);
                }
            } catch (error) {
                setAstEnabled(false);
            }
        };
        checkASTStatus();
    }, []);

    // Enhanced scroll event listener for proper bottom-follow behavior
    useEffect(() => {
        const chatContainer = chatContainerRef.current || document.querySelector('.chat-container') as HTMLElement;
        if (!chatContainer) return;

        let scrollTimeout: NodeJS.Timeout;

        const handleScroll = () => {
            // Clear existing timeout to debounce rapid scroll events
            clearTimeout(scrollTimeout);

            scrollTimeout = setTimeout(() => {
                if (!chatContainer) return;

                const { scrollTop, scrollHeight, clientHeight } = chatContainer;
                const isAtBottom = Math.abs(scrollHeight - scrollTop - clientHeight) < 20;
                const isNearBottom = Math.abs(scrollHeight - scrollTop - clientHeight) < 100;

                // If user scrolls to bottom, reset userHasScrolled to re-enable auto-scrolling
                // But only if there's actual streaming content, not just loading indicators
                const currentStreamedContent = streamedContentMap.get(currentConversationId);
                const hasActualContent = currentStreamedContent && currentStreamedContent.trim().length > 0;
                
                if (isAtBottom && userHasScrolled && hasActualContent) {
                    console.log('📜 User scrolled back to bottom with actual content - resuming auto-scroll');
                    setUserHasScrolled(false);
                    wasFollowingStreamRef.current = true;
                    return;
                }

                // If user scrolls away from bottom significantly, mark as manual scroll
                if (!isNearBottom && Math.abs(scrollTop - lastScrollPositionRef.current) > 50) {
                    if (!userHasScrolled) {
                        console.log('📜 User scrolled away from bottom - pausing auto-scroll');
                        setUserHasScrolled(true);
                        wasFollowingStreamRef.current = false;
                    }
                } else if (isNearBottom && userHasScrolled && hasActualContent) {
                    // Only re-enable if user deliberately scrolls back AND we have actual content
                    console.log('📜 User scrolled back near bottom with content - resuming auto-scroll');
                    setUserHasScrolled(false);
                    wasFollowingStreamRef.current = true;
                }

                lastScrollPositionRef.current = scrollTop;
            }, 50); // Faster response to user scroll actions
        };

        chatContainer.addEventListener('scroll', handleScroll, { passive: true });
        
        return () => {
            chatContainer.removeEventListener('scroll', handleScroll);
            clearTimeout(scrollTimeout);
        };
    }, [userHasScrolled, setUserHasScrolled]);

    // Preserve scroll position during re-renders
    useLayoutEffect(() => {
        const chatContainer = chatContainerRef.current || document.querySelector('.chat-container') as HTMLElement;
        if (!chatContainer) return;
        
        // Check if the last message is a new user message
        const lastMessage = currentMessages[currentMessages.length - 1];
        const isNewUserMessage = lastMessage?.role === 'human';
        
        const wasStreamingBefore = streamingConversations.has(currentConversationId);
        const isStreamingNow = streamingConversations.has(currentConversationId);
        const streamingJustEnded = wasStreamingBefore && !isStreamingNow;

        // Before render: capture current position
        const { scrollTop, scrollHeight, clientHeight } = chatContainer;
        const wasAtBottom = Math.abs(scrollHeight - scrollTop - clientHeight) < 20;
        
        scrollPreservationRef.current = {
            position: scrollTop,
            wasAtBottom
        };
        
        isRenderingRef.current = true;
        
        // Track if user was following the stream
        wasFollowingStreamRef.current = wasAtBottom && !userHasScrolled;
        
        // After render: restore appropriate position
        return () => {
            if (!isRenderingRef.current) return;
            
            requestAnimationFrame(() => {
                const { wasAtBottom, position } = scrollPreservationRef.current;
                
                // For new user messages, always scroll to bottom regardless of other conditions
                if (isNewUserMessage && isTopToBottom) {
                    console.log('📜 New user message detected in layout effect - scrolling to bottom');
                    chatContainer.scrollTop = chatContainer.scrollHeight;
                    // Reset user scroll state immediately for new messages
                    setUserHasScrolled(false);
                    wasFollowingStreamRef.current = true;
                    return;
                }
                
                // Skip position preservation for new user messages to avoid interference
                if (isNewUserMessage) return;
                
                // If streaming just ended and user was following, force scroll to bottom
                if (streamingJustEnded && wasFollowingStreamRef.current && isTopToBottom) {
                    console.log('📜 Stream ended while user was following - maintaining bottom position');
                    chatContainer.scrollTop = chatContainer.scrollHeight;
                    return;
                }
                
                // If streaming just ended, handle position based on user behavior
                if (streamingJustEnded) {
                    if (userHasScrolled) {
                        console.log('📜 Stream ended but user had scrolled away - preserving current position');
                        return; // Don't restore old position
                    } else {
                        // User was following - stay at bottom
                        console.log('📜 Stream ended and user was following - staying at bottom');
                        chatContainer.scrollTop = chatContainer.scrollHeight;
                        return;
                    }
                }
                
                if (isTopToBottom) {
                    if (wasAtBottom && !userHasScrolled) {
                        // If we were at bottom and not manually scrolled, stay at bottom
                        console.log('📜 Preserving bottom position after render');
                        chatContainer.scrollTop = chatContainer.scrollHeight;
                    } else if (!isStreamingNow) {
                        // If not streaming, preserve exact position to prevent jumps
                        console.log('📜 Preserving scroll position after render:', position);
                        chatContainer.scrollTop = position;
                    }
                } else {
                    // In bottom-up mode, preserve position
                    chatContainer.scrollTop = position;
                }
                
                isRenderingRef.current = false;
            });
        };
    }, [
        // Only run scroll preservation for UI state changes, not content changes
        isTopToBottom, 
        currentConversationId,  // Only when switching conversations, not when messages change
        streamingConversations, // Add this to detect streaming state changes
        userHasScrolled        // Add this to detect user scroll state changes
    ]);

    // Auto-scroll to bottom when new messages arrive or streaming updates occur
    useEffect(() => {
        // Only auto-scroll in specific circumstances to prevent jumping
        if (!isTopToBottom || isRenderingRef.current) return;
        
        const lastMessage = currentMessages[currentMessages.length - 1];
        const isNewUserMessage = lastMessage?.role === 'human';
        
        const chatContainer = chatContainerRef.current || document.querySelector('.chat-container') as HTMLElement;
        if (!chatContainer) return;

        // For new user messages, ensure we get to bottom and enable autofollow
        if (isNewUserMessage) {
            console.log('📜 New user message - scrolling to bottom and enabling autofollow');
            setUserHasScrolled(false);
            wasFollowingStreamRef.current = true;
            
            // Use multiple approaches to ensure we get to the bottom
            const scrollToBottom = () => {
                // Force immediate scroll for new messages
                chatContainer.scrollTo({
                    top: chatContainer.scrollHeight,
                    behavior: 'auto'
                });
                // Double-ensure we're at bottom with direct assignment
                const newScrollHeight = chatContainer.scrollHeight;
                chatContainer.scrollTop = newScrollHeight;
                console.log('📜 Set scrollTop to:', newScrollHeight, 'actual:', chatContainer.scrollTop);
            };
            
            // Ensure we get to bottom immediately
            scrollToBottom();
            // Also ensure after DOM updates
            requestAnimationFrame(() => {
                scrollToBottom();
                // Final guarantee after any layout shifts
                setTimeout(scrollToBottom, 50);
            });
            return;
        }
        
        // For other messages, only scroll if user hasn't manually scrolled away
        if (userHasScrolled) return;
        
        // Skip auto-scroll during streaming for AI responses unless it's a new user message
        if (streamingConversations.has(currentConversationId) && !isNewUserMessage) return;

        // Scroll to bottom smoothly
        const scrollToBottom = () => {
            const { scrollTop, scrollHeight, clientHeight } = chatContainer as HTMLElement;
            const isAtBottom = Math.abs(scrollHeight - scrollTop - clientHeight) < 20;
            
            if (!isAtBottom) {
                // Only auto-scroll for actual content, not loading states
                const currentStreamedContent = streamedContentMap.get(currentConversationId);
                const hasActualContent = !streamingConversations.has(currentConversationId) || 
                    (currentStreamedContent && currentStreamedContent.trim().length > 0);
                
                if (hasActualContent) {
                    chatContainer.scrollTo({
                    top: chatContainer.scrollHeight,
                    behavior: 'smooth' // Smooth scroll is less jarring when user is trying to scroll away
                });
                }
            }
        };
        
        // Use requestAnimationFrame to ensure DOM has updated
        requestAnimationFrame(scrollToBottom);
    }, [isTopToBottom, currentMessages.length, userHasScrolled, streamingConversations, currentConversationId]);
    
    // Note: userHasScrolled reset for new user messages is handled in the main scroll effect above

    // Reset userHasScrolled when switching conversations
    useEffect(() => {
        // Reset scroll state when switching to a new conversation
        // This ensures auto-scroll works immediately in new conversations
        setUserHasScrolled(false);
        
        // Also scroll to bottom when switching conversations in top-down mode
        if (isTopToBottom) {
            setTimeout(() => {
                const chatContainer = chatContainerRef.current || document.querySelector('.chat-container') as HTMLElement;
                if (chatContainer) {
                    chatContainer.scrollTop = chatContainer.scrollHeight;
                    wasFollowingStreamRef.current = true;
                }
            }, 100);
        }
    }, [currentConversationId, isTopToBottom, setUserHasScrolled]);

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

    const toggleDirection = () => {
        const chatContainer = chatContainerRef.current || document.querySelector('.chat-container') as HTMLElement;

        setIsTopToBottom(prev => !prev);
        setUserHasScrolled(false);

        setTimeout(() => {
            if (chatContainer) {
                if (!isTopToBottom) {
                    console.log('📜 Switching to top-down mode - scrolling to bottom');
                    chatContainer.scrollTop = chatContainer.scrollHeight;
                    wasFollowingStreamRef.current = true;
                } else {
                    console.log('📜 Switching to bottom-up mode - scrolling to top');
                    chatContainer.scrollTop = 0;
                }
            }

            const bottomUpContent = bottomUpContentRef.current;
            if (bottomUpContent && isTopToBottom) {
                requestAnimationFrame(() => bottomUpContent.scrollTop = 0);
            }
        }, 50);
    };

    // Add keyboard shortcut handling
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.ctrlKey && e.key === 'r') {
                e.preventDefault();
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
                                    <Button icon={<PlusOutlined />} onClick={() => startNewChat()} />
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
                        <div
                            className="chat-container"
                            ref={chatContainerRef}
                        >
                            <div className="chat-content-stabilizer">
                                <LayoutErrorBoundary>
                                    {chatContainerContent}
                                </LayoutErrorBoundary>
                                <div id="layout-integrity-check" style={{
                                    position: 'absolute',
                                    visibility: 'hidden'
                                }}></div>
                            </div>
                        </div>
                    </div>
                    {astEnabled && (
                        <Suspense fallback={null}>
                            <AstStatusIndicator />
                        </Suspense>
                    )}

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
