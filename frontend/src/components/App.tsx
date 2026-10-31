import React, { useState, useRef, useEffect, useLayoutEffect, useCallback, Suspense } from 'react';import { FolderTree } from './FolderTree';
import { SendChatContainer } from './SendChatContainer';
import { StreamedContent } from './StreamedContent';
import { Button, Tooltip, ConfigProvider, Spin, message, Dropdown } from "antd";
import {
    MenuFoldOutlined,
    MenuUnfoldOutlined,
    PlusOutlined,
    BulbOutlined,
    SwapOutlined,
    ExportOutlined,
    SettingOutlined,
    AppstoreOutlined,
    NodeIndexOutlined,
    ToolOutlined
} from "@ant-design/icons";
import { useTheme } from '../context/ThemeContext';
import { AppearancePopover } from './AppearancePopover';
import { SCHEDULES_OPEN_EVENT } from './TaskCard/scheduleCadence';
import { useConversationList } from '../context/ConversationListContext';
import PanelResizer from './PanelResizer';
import { useActiveChat } from '../context/ActiveChatContext';
import { useScrollContext } from '../context/ScrollContext';
import { useStreamingContext } from '../context/StreamingContext';
import { StreamedContentLengthWatcher } from '../context/StreamedContentContext';
import { ProjectProvider } from '../context/ProjectContext';
import { useProject } from '../context/ProjectContext';
import { ProfilerWrapper } from './ProfilerWrapper';
import { SafariWarning } from './SafariWarning';
import { loadFormatters } from '../utils/mcpFormatterLoader';
import { useConfig } from '../context/ConfigContext'
import { ServerStatusBanner } from './ServerStatusBanner';
import { EphemeralChatBanner } from './EphemeralChatBanner';
import { useScrollManager } from '../hooks/useScrollManager';
import { TASK_CARD_OPEN_EVENT } from '../hooks/useTaskBindings';
import { ScrollIndicator } from './ScrollIndicator';
import {
    CONVERSATION_JUMP_EVENT,
    ConversationJumpDetail,
    conversationJumpEdge,
} from '../utils/conversationJump';
import { FeedbackRecoveryWatcher } from './FeedbackRecoveryWatcher';
import { ChatTurnReattachWatcher } from './ChatTurnReattachWatcher';
import HandoffDrawerHost from './HandoffDrawerHost';
import { lazyWithRetry } from '../utils/lazyWithRetry';
import { useHeaderAttention } from '../hooks/useHeaderAttention';
import { attentionButtonStyle, attentionTooltip, mergeAttention } from '../utils/headerAttention';
import { layoutViewportWidth } from '../utils/uiScale';
import Conversation from "./Conversation";
import { CollapsedPanelStrip } from './CollapsedPanelStrip';
const ShellConfigModal = lazyWithRetry(() => import("./ShellConfigModal"));
const MCPStatusModal = lazyWithRetry(() => import("./MCPStatusModal"));
const MCPRegistryModal = lazyWithRetry(() => import("./MCPRegistryModal"));
const ExportConversationModal = lazyWithRetry(() => import("./ExportConversationModal"));
const MemoryBrowser = lazyWithRetry(() => import("./MemoryBrowser"));
const AstStatusIndicator = lazyWithRetry(() => import("./AstStatusIndicator"));
const TaskCardsLibrary = lazyWithRetry(() => import("./TaskCard/TaskCardsLibrary"));
const BenchModal = lazyWithRetry(() => import("./Bench/BenchModal"));
const GraphPanel = lazyWithRetry(() => import("./ConversationGraph/GraphPanel"));
const BeadTree = lazyWithRetry(() => import("./BeadTree"));

// Suspense fallback for the lazily-loaded modals.  A null fallback makes a
// slow chunk fetch indistinguishable from a dead button: the click updates
// state, React suspends, and nothing renders.  This is deliberately a
// non-interactive indicator rather than a spinner inside a Modal -- if the
// chunk never arrives, a non-closable modal would trap the user, which is
// worse than the silent failure it replaces.
const ChunkLoadingFallback: React.FC = () => (
    <div style={{
        position: 'fixed', top: 12, left: '50%', transform: 'translateX(-50%)',
        zIndex: 2000, display: 'flex', alignItems: 'center', gap: 8,
        padding: '6px 14px', borderRadius: 6,
        background: 'rgba(0, 0, 0, 0.75)', color: '#fff', fontSize: 13,
        pointerEvents: 'none'
    }}>
        <Spin size="small" />
        <span>Loading…</span>
    </div>
);

// A lazily-loaded modal whose chunk cannot be fetched (stale build) or whose
// recovery reload never landed (see lazyWithRetry.ts watchdog) must fail as
// ONE modal, not as the page.  Without this the rejection climbs to
// RootErrorBoundary and pressing the memory button takes the whole app down.
// Only chunk-class errors are handled here; anything else propagates as before.
function isChunkFailure(error: Error): boolean {
    return error.name === 'ChunkLoadError'
        || /Loading chunk|recovery reload did not complete/.test(error.message);
}

class LazyChunkBoundary extends React.Component<
    { children: React.ReactNode; onDismiss: () => void },
    { failed: boolean }
> {
    constructor(props: { children: React.ReactNode; onDismiss: () => void }) {
        super(props);
        this.state = { failed: false };
    }
    static getDerivedStateFromError(error: Error) {
        return isChunkFailure(error) ? { failed: true } : null;
    }
    componentDidCatch(error: Error) {
        if (!isChunkFailure(error)) return;
        message.error({ content: error.message, key: 'ziya-chunk-failure', duration: 10 });
        // Reset the caller's show-flag so the modal is not left half-open and
        // the button is usable again; then clear our own state.
        this.props.onDismiss();
        this.setState({ failed: false });
    }
    render() {
        return this.state.failed ? null : this.props.children;
    }
}

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
        // Don't handle non-extension errors — return null so React propagates to next boundary
        return null;
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
        currentMessages, startNewChat, startNewEphemeralChat, currentConversationId,
    } = useActiveChat();
    const {
        isTopToBottom, setIsTopToBottom, userHasScrolled, setUserHasScrolled, recordManualScroll,
    } = useScrollContext();
    const { streamingConversations } = useStreamingContext();
    const enableCodeApply = window.enableCodeApply === 'true';
    const { currentProject } = useProject();
    const { conversations } = useConversationList();
    const currentConversationTitle = currentConversationId
        ? conversations.find(c => c.id === currentConversationId)?.title : undefined;
    const [graphPanelOpen, setGraphPanelOpen] = useState(false);
    const [astEnabled, setAstEnabled] = useState(false);
    const [isPanelCollapsed, setIsPanelCollapsed] = useState(() => {
        const saved = localStorage.getItem(PANEL_COLLAPSED_KEY);
        return saved ? JSON.parse(saved) : false;
    });
    const { isEphemeralMode } = useConfig();
    const { version } = useConfig();
    const { memoryEnabled } = useConfig();

    // Validate panel width from localStorage
    const getValidPanelWidth = (width: number): number => {
        const minWidth = 200;
        return isNaN(width) || width <= 0 ? 300 : Math.max(minWidth, width);
    };
    const [panelWidth, setPanelWidth] = useState(() => {
        const saved = localStorage.getItem(PANEL_WIDTH_KEY);
        return saved ? parseInt(saved, 10) : 300; // Default width: 300px
    });
    const sentinelRef = useRef<HTMLDivElement | null>(null);
    const bottomUpContentRef = useRef<HTMLDivElement | null>(null);
    const chatContainerRef = useRef<HTMLDivElement | null>(null);

    const [showShellConfig, setShowShellConfig] = useState(false);
    const handleOpenShellConfig = useCallback(() => setShowShellConfig(true), []);

    const [showMCPStatus, setShowMCPStatus] = useState(false);
    // Server a Bench card asked the status panel to land on.
    const [mcpFocusServer, setMcpFocusServer] = useState<string | undefined>(undefined);
    const [showMCPRegistry, setShowMCPRegistry] = useState(false);
    const [showBench, setShowBench] = useState(false);
    // MCP Servers / Registry / Shell no longer have toolbar buttons; the Bench
    // reaches them (design/capabilities-hub.md, slice 6b).
    const benchActions = React.useMemo(() => ({
        openMcpServers: (server?: string) => { setMcpFocusServer(server); setShowMCPStatus(true); },
        openRegistry: () => setShowMCPRegistry(true),
    }), []);
    const [showExportModal, setShowExportModal] = useState(false);
    const [showTaskCards, setShowTaskCards] = useState(false);
    // Tab the deck opens on; "Manage schedules" links open it on Schedules.
    // Cleared on close so a later manual open starts on Cards.
    const [taskCardInitialView, setTaskCardInitialView] = useState<'cards' | 'schedules' | undefined>(undefined);
    // Card to focus when the deck opens via an inline tile's "Edit card"
    // backlink (TASK_CARD_OPEN_EVENT).  Cleared on close so a later manual
    // deck-open starts at the list, not the last-edited card.
    const [taskCardInitialId, setTaskCardInitialId] = useState<string | undefined>(undefined);
    const [showMemoryBrowser, setShowMemoryBrowser] = useState(false);
    const [mcpEnabled, setMcpEnabled] = useState(false);
    // Red/orange outlines on the header buttons for state the user must act
    // on (quarantined MCP servers, config errors, unsigned shell / task-card
    // escalations). Severity policy lives in utils/headerAttention.ts.
    const headerAttention = useHeaderAttention({ mcpEnabled, projectId: currentProject?.id });

    const {
        isAtActiveEnd,
        hasNewContentWhileAway,
        streamCompletedWhileAway,
        scrollToActiveEnd,
        scrollToStart,
        scrollToEnd,
        clearIndicators,
        notifyContentLength,
    } = useScrollManager({
        containerRef: chatContainerRef,
        sentinelRef,
        isTopToBottom,
        isStreaming: streamingConversations.has(currentConversationId),
        // No contentLength: reading the streamed map here re-rendered App
        // (and everything under it) on every streaming flush.  The watcher
        // rendered below reports the length instead.
    });

    // Check MCP status on mount
    useEffect(() => {
        // Load formatter plugins
        console.log('🔧 Loading external formatters...');
        loadFormatters().then(() => {
            console.log('✅ Formatters loaded successfully');
            console.log('Registered formatters:', window.FormatterRegistry?.getAllFormatters?.());
        }).catch(error => {
            console.error('Failed to load formatters:', error);
        });

        // Set initial panel width to 33% of the layout viewport (window.innerWidth
        // is unzoomed viewport px; under UI zoom the layout is wider — uiScale.ts).
        const initialWidth = Math.round(layoutViewportWidth() * 0.33);
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

    // On new user message, scroll to active end
    useEffect(() => {
        const lastMessage = currentMessages.length > 0 ? currentMessages[currentMessages.length - 1] : null;
        const isNewUserMessage = lastMessage?.role === 'human';

        if (!isNewUserMessage) return;

        const chatContainer = chatContainerRef.current || document.querySelector('.chat-container') as HTMLElement;
        if (chatContainer) {
            setUserHasScrolled(false);
            clearIndicators();
            setTimeout(() => scrollToActiveEnd(), 100);
        }
    }, [currentMessages, scrollToActiveEnd, clearIndicators, setUserHasScrolled]);

    useEffect(() => {
        setUserHasScrolled(false);
        clearIndicators();
        setTimeout(() => scrollToActiveEnd(), 100);
    }, [currentConversationId, setUserHasScrolled, clearIndicators, scrollToActiveEnd]);

    const handlePanelResize = (newWidth: number) => {
        const minWidth = 200;
        const maxWidth = Math.min(800, layoutViewportWidth() - 350); // Leave at least 350px for chat
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
                const newWidth = Math.min(layoutViewportWidth() * 0.25, Math.max(currentWidth, 300));
                document.documentElement.style.setProperty('--folder-panel-width', `${newWidth}px`);
            }
        };

        window.addEventListener('resize', handleWindowResize);
        return () => window.removeEventListener('resize', handleWindowResize);
    }, []);

    const togglePanel = () => {
        const newState = !isPanelCollapsed;
        setIsPanelCollapsed(newState);
        localStorage.setItem(PANEL_COLLAPSED_KEY, JSON.stringify(newState));

        // Enable transitions only during panel toggle animation
        document.documentElement.classList.add('animations-enabled');
        setTimeout(() => {
            document.documentElement.classList.remove('animations-enabled');
        }, 600);

        const newWidth = newState ? 0 : panelWidth;
        document.documentElement.style.setProperty('--folder-panel-width', `${newWidth}px`);
    };

    const toggleDirection = () => {
        setIsTopToBottom(prev => !prev);
        setTimeout(() => scrollToActiveEnd(), 100);
    };

    // Add keyboard shortcut handling
    useEffect(() => {
        // True when the keystroke belongs to a text field — Ctrl+Home/End
        // must keep moving the caret there, not the conversation.
        const isEditableTarget = (t: EventTarget | null): boolean => {
            const el = t as HTMLElement | null;
            if (!el || typeof el.closest !== 'function') return false;
            return !!el.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]');
        };
        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.ctrlKey && !e.shiftKey && e.key === 'r') {
                e.preventDefault();
                window.location.reload();
            }
            if (e.ctrlKey && e.shiftKey && e.key === 'G') {
                e.preventDefault();
                setGraphPanelOpen(prev => !prev);
                    }
                    // Ctrl+Shift+M → Memory Browser
                    if (e.ctrlKey && e.shiftKey && e.key === 'M' && memoryEnabled) {
                        e.preventDefault();
                        setShowMemoryBrowser(prev => !prev);
            }
            // Jump straight to the top / bottom of the conversation.  Wheel
            // travel through a long conversation has to pass every placeholder
            // shell on the way; a jump does not.
            //   Ctrl+Home / Ctrl+End  — Windows/Linux convention
            //   ⌘↑ / ⌘↓               — macOS convention (Mac laptops have no
            //                            Home/End keys; they're Fn+←/→)
            const jumpToTop =
                (e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey && e.key === 'Home') ||
                (e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey && e.key === 'ArrowUp');
            const jumpToBottom =
                (e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey && e.key === 'End') ||
                (e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey && e.key === 'ArrowDown');
            if ((jumpToTop || jumpToBottom) && !isEditableTarget(e.target)) {
                e.preventDefault();
                if (jumpToTop) scrollToStart(); else scrollToEnd();
            }
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    // scrollToStart/scrollToEnd are stable useCallbacks keyed on the container
    // ref; listing them keeps the handler honest without re-binding per render.
    }, [scrollToStart, scrollToEnd]);

    // Composer arrow keys (SendChatContainer): Left in an empty composer
    // jumps to the conversation's first message, Right to its latest.  The
    // first message's DOM edge depends on the display order.
    useEffect(() => {
        const onJump = (e: Event) => {
            const target = (e as CustomEvent<ConversationJumpDetail>).detail?.target;
            if (target === 'end') {
                scrollToActiveEnd();
            } else if (target === 'beginning') {
                if (conversationJumpEdge('beginning', isTopToBottom) === 'start') scrollToStart();
                else scrollToEnd();
            }
        };
        window.addEventListener(CONVERSATION_JUMP_EVENT, onJump);
        return () => window.removeEventListener(CONVERSATION_JUMP_EVENT, onJump);
    }, [scrollToActiveEnd, scrollToStart, scrollToEnd, isTopToBottom]);

    // Inline card tile "Edit card" backlink → open the deck on that card.
    useEffect(() => {
        const onOpenCard = (e: Event) => {
            const cardId = (e as CustomEvent).detail?.cardId;
            if (!cardId) return;
            setTaskCardInitialId(cardId);
            setShowTaskCards(true);
        };
        window.addEventListener(TASK_CARD_OPEN_EVENT, onOpenCard);
        return () => window.removeEventListener(TASK_CARD_OPEN_EVENT, onOpenCard);
    }, []);
    // "Manage schedules" link → open the deck on its Schedules tab.
    useEffect(() => {
        const onOpen = () => { setTaskCardInitialView('schedules'); setShowTaskCards(true); };
        window.addEventListener(SCHEDULES_OPEN_EVENT, onOpen);
        return () => window.removeEventListener(SCHEDULES_OPEN_EVENT, onOpen);
    }, []);

    const { isDarkMode, toggleTheme, themeAlgorithm } = useTheme();
    const chatContainerContent = isTopToBottom ? (
        <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100%' }}>
            {/* Not a scroll container: `overflow: auto` here made THIS div
                the nearest scrollport for every position:sticky descendant
                (the TaskRail chips), so they stuck to a box that scrolls
                away with the content instead of to .chat-container, the
                element that actually scrolls.  `clip` keeps the horizontal
                containment without creating a scrollport. */}
            <div style={{
                flex: 1, width: '100%', maxWidth: '100%', overflowX: 'clip'
            }}>
                <Suspense fallback={<div>Loading conversation...</div>}>
                    <Conversation key="conv" enableCodeApply={enableCodeApply} onOpenShellConfig={handleOpenShellConfig} />
                </Suspense>
                <StreamedContent key="stream" />
            </div>
            {/* Dock: the collapsed-panel strip rides with the composer so it
                never scrolls away from it (design/visual-refresh.md). */}
            <div className="ziya-composer-dock">
                {isPanelCollapsed && <CollapsedPanelStrip />}
                <SendChatContainer fixed={true} />
            </div>
            <div ref={sentinelRef} style={{ height: '1px', width: '100%' }} />
        </div>
    ) : (
        <div className="chat-content-with-fixed-input">
            <div ref={sentinelRef} style={{ height: '1px', width: '100%' }} />
            <div className="ziya-composer-dock">
                <SendChatContainer fixed={true} />
                {isPanelCollapsed && <CollapsedPanelStrip />}
            </div>
            <StreamedContent key="stream" />
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
                <div className="bottom-up-content" ref={bottomUpContentRef}>
                    <Suspense fallback={<div>Loading conversation...</div>}>
                        <Conversation key="conv" enableCodeApply={enableCodeApply} onOpenShellConfig={handleOpenShellConfig} />
                    </Suspense>
                </div>
            </div>
        </div>
    );

    return (
        <ExtensionErrorBoundary>
            <ProfilerWrapper id="App">
                <ServerStatusBanner />
                <SafariWarning />
                <FeedbackRecoveryWatcher />
                <ChatTurnReattachWatcher />
                <StreamedContentLengthWatcher
                    conversationId={currentConversationId}
                    onLength={notifyContentLength}
                />
                <ConfigProvider
                    theme={{
                        algorithm: themeAlgorithm,
                        token: {
                            borderRadius: 6,
                            // design/visual-refresh.md: teal is the one accent;
                            // surfaces follow the --z-* ladder in theme-tokens.css.
                            colorPrimary: isDarkMode ? '#2dd4bf' : '#0d9488',
                            colorBgContainer: isDarkMode ? '#252930' : '#fcfbf9',
                            colorText: isDarkMode ? '#e8eaed' : '#1a1b1e',
                        },
                    }}
                >
                    <Button
                        className={`panel-toggle ${isPanelCollapsed ? 'collapsed' : ''}`}
                        type="primary"
                        onClick={togglePanel}
                        size="small"
                        aria-label={isPanelCollapsed ? 'Expand panel' : 'Collapse panel'}
                        title={isPanelCollapsed ? 'Expand panel' : 'Collapse panel · drag the edge to resize'}
                        data-testid="panel-toggle"
                    >{isPanelCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}</Button>

                    <PanelResizer
                        onResize={handlePanelResize}
                        isPanelCollapsed={isPanelCollapsed}
                    />

                        <div className={`app-header ${isPanelCollapsed ? 'panel-collapsed' : ''}`}
                            style={{
                                position: 'fixed',
                                width: '100%',
                                zIndex: 1000
                            }}>
                            <h2 style={{
                                transform: 'translateZ(0)' // force GPU accel
                            }}>
                                <span 
                                    key={`z-${isDarkMode ? 'dark' : 'light'}`}
                                    style={{
                                    background: isDarkMode
                                        ? 'linear-gradient(90deg, #b32ca8 0%, #2dd4bf 100%)'
                                        : 'linear-gradient(90deg, #b32ca8 0%, #0d9488 100%)',
                                    WebkitBackgroundClip: 'text',
                                    WebkitTextFillColor: 'transparent',
                                    backgroundClip: 'text',
                                    color: 'transparent',
                                    filter: isDarkMode
                                        ? 'drop-shadow(0 0 12px rgba(179, 44, 168, 0.4))'
                                        : 'drop-shadow(0 0 8px rgba(179, 44, 168, 0.2))',
                                    fontWeight: 800,
                                    fontSize: '1.3em',
                                    letterSpacing: '-0.02em',
                                    fontFamily: 'Georgia, "Times New Roman", serif',
                                    verticalAlign: 'middle',
                                    lineHeight: 1,
                                }}>ℤ</span>
                                <span 
                                    key={`iya-${isDarkMode ? 'dark' : 'light'}`}
                                    style={{
                                    fontWeight: 700,
                                    letterSpacing: '0.10em',
                                    marginLeft: '4px',
                                    fontSize: '0.92em',
                                    position: 'relative' as const,
                                    top: '1px',
                                    fontFamily: "'Avenir Next', Avenir, Montserrat, 'Trebuchet MS', sans-serif",
                                    background: isDarkMode
                                        ? 'linear-gradient(90deg, #8b3aaa 0%, #2dd4bf 100%)'
                                        : 'linear-gradient(90deg, #8b2a9a 0%, #0d9488 100%)',
                                    WebkitBackgroundClip: 'text',
                                    WebkitTextFillColor: 'transparent',
                                    backgroundClip: 'text',
                                    color: 'transparent',
                                }}>iya</span>
                                {currentProject && (
                                    <span className="ziya-breadcrumb" data-testid="header-breadcrumb"
                                        title={currentConversationTitle ? `${currentProject.name} / ${currentConversationTitle}` : currentProject.name}>
                                        <span>{currentProject.name}</span>
                                        {currentConversationTitle && <span className="ziya-breadcrumb-sep">/</span>}
                                        {currentConversationTitle && <span className="ziya-breadcrumb-title">{currentConversationTitle}</span>}
                                    </span>
                                )}
                                {isEphemeralMode && (
                                    <Tooltip title="Ephemeral mode: No data persisted after closing. Each browser tab has independent, non-shared state.">
                                        <span
                                            className="ephemeral-badge"
                                            style={{
                                                marginLeft: '12px',
                                                fontSize: '12px'
                                            }}
                                        >
                                            🔒 Ephemeral
                                        </span>
                                    </Tooltip>
                                )}
                            </h2>
                            <div style={{ position: 'absolute', right: '10px', display: 'flex', gap: '6px', alignItems: 'center' }}>
                                {/* One button for skills, MCP servers, registry and shell: the
                                    Bench carries the shell + MCP attention its panels used to. */}
                                <Tooltip title={attentionTooltip('Bench — skills and MCP servers for this conversation',
                                                                 mergeAttention(headerAttention.shell, headerAttention.mcp))}>
                                    <Button className="ziya-header-btn" icon={<ToolOutlined />} onClick={() => setShowBench(true)} data-testid="bench-button"
                                        style={attentionButtonStyle(mergeAttention(headerAttention.shell, headerAttention.mcp).level)}
                                        data-attention={mergeAttention(headerAttention.shell, headerAttention.mcp).level}>
                                        <span className="ziya-header-btn-label">Bench</span>
                                    </Button>
                                </Tooltip>
                                {/* Shell Configuration keeps a direct toolbar button; folding it
                                    into the Bench left it reachable only via the environment row. */}
                                <Tooltip title={attentionTooltip('Shell Configuration', headerAttention.shell)}>
                                    <Button className="ziya-header-btn" icon={<SettingOutlined />} onClick={handleOpenShellConfig} data-testid="shell-config-button"
                                        style={attentionButtonStyle(headerAttention.shell.level)}
                                        data-attention={headerAttention.shell.level}>
                                        <span className="ziya-header-btn-label">Shell</span>
                                    </Button>
                                </Tooltip>
                                <Tooltip title={attentionTooltip('Task Cards', headerAttention.taskCards)}>
                                    <Button className="ziya-header-btn" icon={<AppstoreOutlined />} onClick={() => setShowTaskCards(true)}
                                        style={attentionButtonStyle(headerAttention.taskCards.level)}
                                        data-attention={headerAttention.taskCards.level}>
                                        <span className="ziya-header-btn-label">Tasks</span>
                                    </Button>
                                </Tooltip>
                                {memoryEnabled && (
                                    <Tooltip title="Memory Browser">
                                        <Button className="ziya-header-btn" icon={<NodeIndexOutlined />} onClick={() => setShowMemoryBrowser(true)}>
                                            <span className="ziya-header-btn-label">Memory</span>
                                        </Button>
                                    </Tooltip>
                                )}
                                {/* Message order: a low-emphasis icon beside Appearance.  It
                                    used to be a labelled primary button alone at the left edge. */}
                                <Tooltip title={`Switch to ${isTopToBottom ? 'bottom-up' : 'top-down'} view`}>
                                    <Button className="ziya-header-btn" type="text" icon={<SwapOutlined rotate={90} />}
                                        onClick={toggleDirection} aria-label="Toggle message order" data-testid="order-toggle" />
                                </Tooltip>
                                <AppearancePopover />
                                {/* Body click = new chat (the common case, one click).  The caret
                                    is the only way to reach the ephemeral variant. */}
                                <Dropdown.Button
                                    className="ziya-header-btn ziya-new-chat"
                                    type="primary"
                                    onClick={() => startNewChat()}
                                    data-testid="new-chat-button"
                                    menu={{ items: [
                                        { key: 'new', label: <span>New chat<span className="ziya-menu-sub">Saved to this project</span></span>, onClick: () => startNewChat() },
                                        { key: 'ephemeral', label: <span>New ephemeral chat<span className="ziya-menu-sub">Not saved; lost on reload. Each tab keeps its own state.</span></span>,
                                          onClick: () => startNewEphemeralChat() },
                                    ] }}
                                >
                                    <PlusOutlined /> New
                                </Dropdown.Button>
                            </div>
                        </div>
                    <div className={`container ${isPanelCollapsed ? 'panel-collapsed' : ''}`}
                        style={{
                            display: 'flex',
                            width: '100%',
                            overflow: 'hidden'
                        }}>
                        <FolderTree isPanelCollapsed={isPanelCollapsed} />
                        <div
                            className="chat-container"
                            ref={chatContainerRef}
                        >
                            <EphemeralChatBanner />
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

                    {/* Always mounted: the token bar's "⇢ Hand off…" opens it via a
                        document event, whichever sidebar tab is showing. */}
                    <HandoffDrawerHost />

                    {currentConversationId && currentProject?.id && (
                        <>
                            <button className="cgp-toggle" onClick={() => setGraphPanelOpen(true)} title="Conversation graph (Ctrl+Shift+G)">🌳</button>
                            {graphPanelOpen && (
                                <LazyChunkBoundary onDismiss={() => setGraphPanelOpen(false)}>
                                <Suspense fallback={null}>
                                    <GraphPanel
                                        projectId={currentProject.id}
                                        chatId={currentConversationId}
                                        onClose={() => setGraphPanelOpen(false)}
                                    />
                                </Suspense>
                                </LazyChunkBoundary>
                            )}
                        </>
                    )}

                    <ScrollIndicator
                        visible={hasNewContentWhileAway || streamCompletedWhileAway}
                        isCompleted={streamCompletedWhileAway}
                        onClick={scrollToActiveEnd}
                        isTopToBottom={isTopToBottom}
                    />

                    {astEnabled && (
                        <LazyChunkBoundary onDismiss={() => setAstEnabled(false)}>
                        <Suspense fallback={null}>
                            <AstStatusIndicator />
                        </Suspense>
                        </LazyChunkBoundary>
                    )}

                    <LazyChunkBoundary onDismiss={() => { setShowShellConfig(false); setShowMCPStatus(false); setShowMCPRegistry(false); }}>
                    <Suspense fallback={<ChunkLoadingFallback />}>
                        {mcpEnabled && (
                            <>
                                {showShellConfig && <ShellConfigModal
                                    visible={showShellConfig}
                                    onClose={() => { setShowShellConfig(false); headerAttention.refresh(); }}
                                />}
                                {showMCPStatus && <MCPStatusModal
                                    visible={showMCPStatus}
                                    onClose={() => { setShowMCPStatus(false); setMcpFocusServer(undefined); headerAttention.refresh(); }}
                                    onOpenShellConfig={handleOpenShellConfig}
                                    focusServer={mcpFocusServer}
                                />}
                                {showMCPRegistry && <MCPRegistryModal
                                    visible={showMCPRegistry}
                                    onClose={() => setShowMCPRegistry(false)}
                                />}
                            </>
                        )}
                    </Suspense>
                    </LazyChunkBoundary>

                    {/* Its own boundary: the MCP modals above open FROM the Bench.
                        Sharing one, a modal's lazy chunk suspended it, which hid the
                        Bench and re-appended its portal after the new modal's --
                        same z-index, so the opened panel landed behind the Bench. */}
                    <LazyChunkBoundary onDismiss={() => setShowBench(false)}>
                    <Suspense fallback={<ChunkLoadingFallback />}>
                        {showBench && <BenchModal
                            visible={showBench}
                            onClose={() => { setShowBench(false); headerAttention.refresh(); }}
                            projectId={currentProject?.id}
                            conversationId={currentConversationId || null}
                            shellAttention={headerAttention.shell}
                            mcpAttention={headerAttention.mcp}
                            onOpenShellConfig={handleOpenShellConfig}
                            actions={mcpEnabled ? benchActions : {}}
                        />}
                    </Suspense>
                    </LazyChunkBoundary>

                    <LazyChunkBoundary onDismiss={() => setShowExportModal(false)}>
                    <Suspense fallback={<ChunkLoadingFallback />}>
                        {showExportModal && <ExportConversationModal visible={showExportModal} onClose={() => setShowExportModal(false)} />}
                    </Suspense>
                    </LazyChunkBoundary>

                    {showMemoryBrowser && <LazyChunkBoundary onDismiss={() => setShowMemoryBrowser(false)}><Suspense fallback={<ChunkLoadingFallback />}>
                        <MemoryBrowser
                            visible={showMemoryBrowser}
                            onClose={() => setShowMemoryBrowser(false)} />
                    </Suspense></LazyChunkBoundary>}

                    {showTaskCards && <LazyChunkBoundary onDismiss={() => { setShowTaskCards(false); setTaskCardInitialId(undefined); setTaskCardInitialView(undefined); }}><Suspense fallback={<ChunkLoadingFallback />}>
                        <TaskCardsLibrary
                            visible={showTaskCards}
                            onClose={() => { setShowTaskCards(false); setTaskCardInitialId(undefined); setTaskCardInitialView(undefined); headerAttention.refresh(); }}
                            initialCardId={taskCardInitialId}
                            initialView={taskCardInitialView}
                            chatId={currentConversationId || undefined}
                            anchorMessageId={
                                currentMessages.length > 0
                                    ? currentMessages[currentMessages.length - 1]?.id
                                    : null
                            } />
                    </Suspense></LazyChunkBoundary>}

                    <div style={{
                        position: 'fixed',
                        // Kept clear of the window's rounded corner, which
                        // masks anything within its radius (~12px); the tail
                        // of a longer version string (e.g. .post7) was cut off.
                        bottom: '4px',
                        right: '16px',
                        fontSize: '9px',
                        color: isDarkMode ? 'rgba(255,255,255,0.165)' : 'rgba(0,0,0,0.165)',
                        fontFamily: 'monospace',
                        whiteSpace: 'nowrap',
                        zIndex: 1,
                        userSelect: 'none',
                        pointerEvents: 'none',
                    }}>v{version}</div>

                </ConfigProvider>
            </ProfilerWrapper>
        </ExtensionErrorBoundary>
    );
};
