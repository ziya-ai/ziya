import React, { useState, useRef, useEffect, Suspense } from 'react';
import {FolderTree} from "./FolderTree";
import {SendChatContainer} from "./SendChatContainer";
import {StreamedContent} from './StreamedContent';
import {Button, Tooltip, ConfigProvider, theme, message } from "antd";
import {
    MenuFoldOutlined,
    ExperimentOutlined,
    MenuUnfoldOutlined,
    PlusOutlined,
    BulbOutlined,
    SwapOutlined
} from "@ant-design/icons";
import { useTheme } from '../context/ThemeContext';
import { DebugControls } from './DebugControls';
import { useChatContext } from '../context/ChatContext';

// Lazy load the Conversation component
const Conversation = React.lazy(() => import("./Conversation"));
const PrismTest = React.lazy(() => import("./PrismTest"));
const SyntaxTest = React.lazy(() => import("./SyntaxTest"));
const ApplyDiffTest = React.lazy(() => import("./ApplyDiffTest"));

// Error boundary component to catch extension context errors
class ExtensionErrorBoundary extends React.Component<{children: React.ReactNode}, {hasError: boolean}> {
    constructor(props: {children: React.ReactNode}) {
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

const PANEL_COLLAPSED_KEY = 'ZIYA_PANEL_COLLAPSED';

export const App = () => {
    const {streamedContentMap, currentMessages, startNewChat, isTopToBottom, setIsTopToBottom, setStreamedContentMap} = useChatContext();
    const enableCodeApply = window.enableCodeApply === 'true';
    const [isPanelCollapsed, setIsPanelCollapsed] = useState(() => {
        const saved = localStorage.getItem(PANEL_COLLAPSED_KEY);
        return saved ? JSON.parse(saved) : false;
    });
    const bottomUpContentRef = useRef<HTMLDivElement | null>(null);

    const handleNewChat = async () => {
        try {
            await startNewChat();
	    setStreamedContentMap(new Map());
        } catch (error) {
            message.error('Failed to create new chat');
            console.error('Error creating new chat:', error);
        }
    };

    const preserveScrollPosition = (action: () => void) =>    {

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
                requestAnimationFrame(() => {
                    chatContainer.scrollTo({
                        top: chatContainer.scrollHeight,
                        behavior: smooth ? 'smooth' : 'auto'
                    });
                });
            };
            // Scroll on initial render and when messages change
            scrollToBottom();
            // Add a small delay for smooth scroll after content renders
            const timeoutId = setTimeout(() => scrollToBottom(true), 50);
            return () => clearTimeout(timeoutId);
        }
    }, [isTopToBottom, currentMessages, streamedContentMap]);

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
    }, [isTopToBottom]);

    const { isDarkMode, toggleTheme, themeAlgorithm } = useTheme();

    const chatContainerContent = isTopToBottom ? (
        <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100%' }}>
            <div style={{ flex: 1, overflow: 'auto', width: '100%',
	                  maxWidth: '100%', overflowX: 'hidden' }}>
                <Suspense fallback={<div>Loading conversation...</div>}>
                    <Conversation key="conv" enableCodeApply={enableCodeApply}/>
                </Suspense>
                <StreamedContent key="stream"/>
            </div>
            <SendChatContainer fixed={true}/>
        </div>
    ) : (
        <div className="chat-content-with-fixed-input">
            <SendChatContainer fixed={true}/>
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
                style={{ padding: '4px 8px', color: isDarkMode ? undefined : '#000000' }}
		ghost={!isDarkMode}
            >{isPanelCollapsed ? '›' : '‹'}</Button>
            <div style={{ height: 'var(--app-header-height)' }}>
                <div className={`app-header ${isPanelCollapsed ? 'panel-collapsed' : ''}`}
                    style={{
                        position: 'fixed',
                        width: '100%',
                        zIndex: 1000}}>
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
                    height: 'calc(100vh - var(--app-header-height))'}}>
                <FolderTree isPanelCollapsed={isPanelCollapsed}/>
                <div className="chat-container">
		    <div className="chat-content-stabilizer">
		        {chatContainerContent}
                    </div>
                </div>
            </div>
            
        </ConfigProvider>
	</ExtensionErrorBoundary>
    );
};
