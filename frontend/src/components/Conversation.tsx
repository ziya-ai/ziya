import React, { useEffect, useRef, memo, useCallback, useMemo, useState } from "react";
import { useActiveChat } from '../context/ActiveChatContext';
import { useConversationList } from '../context/ConversationListContext';
import { useScrollContext } from '../context/ScrollContext';
import { EditSection } from "./EditSection";
import { Spin, Button, Tooltip, Image as AntImage } from 'antd';
import { RedoOutlined, SoundOutlined, MutedOutlined, PictureOutlined, CodeOutlined, EyeOutlined } from "@ant-design/icons";

import { DocumentChip, ImageChip } from './FileChip';
import ModelChangeNotification from './ModelChangeNotification';
import { useSetQuestion } from '../context/QuestionContext';
import { useFolderContext } from '../context/FolderContext';
import { isDebugLoggingEnabled, debugLog } from '../utils/logUtils';
import { useProject } from '../context/ProjectContext';
import { useSendPayload } from '../hooks/useSendPayload';

// Lazy load the MarkdownRenderer
import { MarkdownRenderer } from "./MarkdownRenderer";
// --- Deferred markdown rendering ---------------------------------------------
// Rendering MarkdownRenderer for a single large message (heavy diffs, syntax
// highlighting, tool-blocks) can cost 1-2 seconds.  Mounting 8 of them in a
// single React render pass blocks the main thread for 15+ seconds.  We defer
// each MarkdownRenderer mount via a shared idle-time queue, with
// IntersectionObserver prioritising messages that are actually on screen.
// Off-screen messages render during idle time.  Streaming is unaffected
// because live streaming goes through StreamedContent.tsx, not this path —
// every message passed here is already settled.

type QueueEntry = { priority: number; task: () => void };
const __messageRenderQueue: QueueEntry[] = [];
let __messageQueueProcessing = false;
const __rIC: (cb: () => void, opts?: { timeout: number }) => number =
    (window as any).requestIdleCallback
        ? (window as any).requestIdleCallback.bind(window)
        : ((cb: () => void) => window.setTimeout(cb, 16) as unknown as number);
const __processMessageQueue = () => {
    if (__messageQueueProcessing) return;
    if (__messageRenderQueue.length === 0) return;
    __messageQueueProcessing = true;
    __rIC(() => {
        __messageRenderQueue.sort((a, b) => b.priority - a.priority);
        const entry = __messageRenderQueue.shift();
        __messageQueueProcessing = false;
        if (entry) {
            try { entry.task(); } catch (e) { console.warn('deferred markdown render threw:', e); }
        }
        if (__messageRenderQueue.length > 0) __processMessageQueue();
    }, { timeout: 500 });
};

// Small messages aren't worth the observer + queue overhead — render them
// directly.  Threshold picked empirically; below this size the MarkdownRenderer
// cost is negligible compared with observer machinery.
const INLINE_THRESHOLD_CHARS = 400;

const LazyMarkdownRenderer: React.FC<React.ComponentProps<typeof MarkdownRenderer>> = (props) => {
    const { markdown } = props;
    const isSmall = (markdown?.length || 0) < INLINE_THRESHOLD_CHARS;
    const [mounted, setMounted] = useState<boolean>(isSmall);
    const placeholderRef = useRef<HTMLDivElement>(null);
    const mountedRef = useRef<boolean>(isSmall);

    // Estimate final height so the placeholder doesn't cause scroll-jump
    // when the real content mounts.  Rough proxy: ~80 chars/line, ~21px/line.
    const estimatedHeight = useMemo(() => {
        if (!markdown) return 40;
        return Math.max(40, Math.min(4000, Math.round((markdown.length / 80) * 21)));
    }, [markdown?.length]);

    useEffect(() => {
        if (mountedRef.current) return;

        const entry: QueueEntry = {
            priority: 0,
            task: () => {
                if (mountedRef.current) return;
                mountedRef.current = true;
                setMounted(true);
            },
        };
        __messageRenderQueue.push(entry);
        __processMessageQueue();

        let observer: IntersectionObserver | null = null;
        if (placeholderRef.current && typeof IntersectionObserver !== 'undefined') {
            observer = new IntersectionObserver((entries) => {
                for (const e of entries) {
                    if (e.isIntersecting) {
                        entry.priority = 100;
                        __processMessageQueue();
                        observer?.disconnect();
                        break;
                    }
                }
            }, { rootMargin: '500px 0px' }); // 500px preload
            observer.observe(placeholderRef.current);
        }

        return () => {
            observer?.disconnect();
            const idx = __messageRenderQueue.indexOf(entry);
            if (idx >= 0) __messageRenderQueue.splice(idx, 1);
        };
    }, []);

    if (!mounted) {
        return (
            <div
                ref={placeholderRef}
                style={{
                    minHeight: estimatedHeight,
                    opacity: 0.3,
                    padding: '0.5em 0',
                    fontSize: '0.9em',
                    color: 'var(--text-muted, #888)',
                }}
            >…</div>
        );
    }
    return <MarkdownRenderer {...props} />;
};
// --- end deferred markdown rendering ----------------------------------------

/**
 * MessageActions — memoized per-message action buttons (retry, resubmit, mute).
 *
 * Extracted from Conversation so that button callbacks don't inflate
 * Conversation's useCallback dependency arrays. Each MessageActions instance
 * subscribes to context independently and only re-renders when its own
 * props or the specific values it reads change.
 */
interface MessageActionsProps {
    message: any;
    actualIndex: number;
    isEditing: boolean;
    needsResponse: boolean;
    enableCodeApply?: boolean;
    onOpenShellConfig?: () => void;
}

const MessageActions = memo<MessageActionsProps>(({
    message, actualIndex, isEditing, needsResponse, enableCodeApply, onOpenShellConfig
}) => {
    const {
        currentConversationId,
        addStreamingConversation,
        streamingConversations,
        toggleMessageMute,
        editingMessageIndex,
    } = useActiveChat();
    const { setConversations } = useConversationList();
    const { isTopToBottom, recordManualScroll } = useScrollContext();
    const setQuestion = useSetQuestion();
    const { send } = useSendPayload();
    const activeChat = useActiveChat();
    const convList = useConversationList();

    // Refs for callback-only values — read at invocation time, not render time.
    // This avoids re-renders when currentMessages changes on every streaming chunk.
    const activeChatRef = useRef(activeChat);
    const convListRef = useRef(convList);
    activeChatRef.current = activeChat;
    convListRef.current = convList;

    const isCurrentlyStreaming = streamingConversations.has(currentConversationId);
    // Retry button — shown when a human message has no following assistant response
    const showRetry = message.role === 'human' && needsResponse;

    const handleRetry = useCallback(async () => {
        const chatContainer = document.querySelector('.chat-container');
        if (chatContainer) {
            const isAtEnd = isTopToBottom
                ? (chatContainer.scrollHeight - chatContainer.scrollTop - chatContainer.clientHeight) < 50
                : chatContainer.scrollTop < 50;
            if (!isAtEnd) recordManualScroll();
        }
        addStreamingConversation(currentConversationId);
        try {
            await send({ question: message.content, images: message.images });
        } catch (error) {
            console.error('Error retrying message:', error);
        }
    }, [currentConversationId, message.content, message.images, addStreamingConversation, isTopToBottom, recordManualScroll, send]);

    const handleResubmit = useCallback(() => {
        if (isCurrentlyStreaming) return;
        const msgs = activeChatRef.current.currentMessages;
        const truncatedMessages = msgs.slice(0, actualIndex + 1);
        const messagesToSend = truncatedMessages.filter(msg => !msg.muted);
        convListRef.current.setConversations(prev => prev.map(conv =>
            conv.id === currentConversationId
                ? { ...conv, messages: truncatedMessages, _version: Date.now() }
                : conv
        ));
        addStreamingConversation(currentConversationId);
        (async () => {
            try {
                await send({
                    messages: messagesToSend,
                    question: message.content,
                    images: message.images,
                });
            } catch (error) {
                console.error('Error resubmitting message:', error);
            }
        })();
        setQuestion('');
    }, [currentConversationId, actualIndex, message.content, message.images, isCurrentlyStreaming, addStreamingConversation, send, setQuestion]);

    if (isEditing) return null;

    return (
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            {/* Mute button */}
            {!showRetry && message.role !== 'system' && (
                <Tooltip title={message.muted ? "Unmute (include in context)" : "Mute (exclude from context)"}>
                    <Button icon={message.muted ? <MutedOutlined /> : <SoundOutlined />}
                        type="default" size="small"
                        style={{ padding: '0 8px', minWidth: '32px', height: '32px' }}
                        onClick={() => toggleMessageMute(currentConversationId, actualIndex)} />
                </Tooltip>
            )}
            {/* Resubmit button */}
            {!showRetry && message.role === 'human' && !isCurrentlyStreaming && (
                <Tooltip title="Resubmit this question">
                    <Button icon={<RedoOutlined />} type="default" size="small"
                        style={{ padding: '0 8px', minWidth: '32px', height: '32px' }}
                        onClick={handleResubmit} />
                </Tooltip>
            )}
            {/* Retry button */}
            {showRetry && (
                <Tooltip title="The AI response may have failed. Click to retry.">
                    <Button icon={<RedoOutlined />} type="primary" size="small" onClick={handleRetry}>
                        Retry AI Response
                    </Button>
                </Tooltip>
            )}
        </div>
    );
});
MessageActions.displayName = 'MessageActions';

interface ConversationProps {
    enableCodeApply: boolean;
    onOpenShellConfig?: () => void;
}

const Conversation: React.FC<ConversationProps> = memo(({ enableCodeApply, onOpenShellConfig }) => {
    const {
        currentMessages,
        streamingConversations,
        currentConversationId,
        streamedContentMap,
        currentDisplayMode,
        editingMessageIndex,
    } = useActiveChat();
    const {
        isLoadingConversation,
        isProjectSwitching,
        conversations,
    } = useConversationList();
    const {
        isTopToBottom,
        recordManualScroll,
    } = useScrollContext();

    // Refs for callback-only values — avoids re-renders when these change.
    // Callbacks read .current at invocation time, not at render time.
    const activeChatRef = useRef(useActiveChat());
    const convListRef = useRef(useConversationList());
    const scrollRef = useRef(useScrollContext());
    const folderRef = useRef(useFolderContext());
    const projectRef = useRef(useProject());

    // Keep refs current on every render (cheap assignment, no state change)
    const activeChat = useActiveChat();
    const convList = useConversationList();
    const scrollCtx = useScrollContext();
    const folderCtx = useFolderContext();
    const projectCtx = useProject();
    activeChatRef.current = activeChat;
    convListRef.current = convList;

    // Earliest possible signal that a project switch is happening
    const isSwitchingProject = projectCtx.isLoadingProject || isProjectSwitching;
    scrollRef.current = scrollCtx;
    folderRef.current = folderCtx;
    projectRef.current = projectCtx;

    const setQuestion = useSetQuestion();

    // Two-layer streaming derivation: the raw values (Map/Set) change on every
    // chunk, but the booleans only flip at stream start/end. Derive booleans
    // first, then memo on the booleans so downstream code doesn't re-render
    // 60 times/second during streaming.
    const rawIsStreaming = streamingConversations.has(currentConversationId);
    const rawHasContent = streamedContentMap.has(currentConversationId) &&
        streamedContentMap.get(currentConversationId) !== '';
    const prevStreamingRef = useRef(false);
    const prevHasContentRef = useRef(false);

    const isCurrentlyStreaming = useMemo(() => {
        prevStreamingRef.current = rawIsStreaming;
        return rawIsStreaming;
    }, [rawIsStreaming]);

    const hasStreamedContent = useMemo(() => {
        prevHasContentRef.current = rawHasContent;
        return rawHasContent;
    }, [rawHasContent]);

    // Raw markdown display mode — toggled via Ctrl+Shift+U
    // Sourced from ActiveChatContext (computed in ChatContext) to avoid subscribing
    // to the full conversations[] array, which changes on every incoming message.
    const isRawMode = currentDisplayMode === 'raw';
    const isRawModeRef = useRef(isRawMode);
    isRawModeRef.current = isRawMode;

    // Progressive rendering: on conversation switch, render only the last
    // INITIAL_WINDOW messages immediately.  Once the browser paints, expand
    // in steps so the browser can paint between batches and stay responsive.
    const INITIAL_WINDOW = 8;
    const [messageWindow, setMessageWindow] = useState<number>(INITIAL_WINDOW);
    const scrollToMessageIndexRef = useRef<number | null>(null);
    const windowConvRef = useRef(currentConversationId);
    const expandTimerRef = useRef<number | null>(null);

    useEffect(() => {
        if (windowConvRef.current !== currentConversationId) {
            windowConvRef.current = currentConversationId;
            setMessageWindow(INITIAL_WINDOW);
            if (expandTimerRef.current) cancelAnimationFrame(expandTimerRef.current);

            // Expand progressively: 8 → 20 → 50 → all
            // Each step waits for a paint so the UI stays responsive.
            const steps = [20, 50, Infinity];
            let step = 0;
            const expand = () => {
                if (step >= steps.length) return;
                expandTimerRef.current = requestAnimationFrame(() => {
                    setMessageWindow(steps[step]);
                    step++;
                    if (step < steps.length) {
                        expandTimerRef.current = requestAnimationFrame(expand);
                    }
                });
            };
            expandTimerRef.current = requestAnimationFrame(expand);
        }
        return () => {
            if (expandTimerRef.current) cancelAnimationFrame(expandTimerRef.current);
        };
    }, [currentConversationId]);

    // Refs for conversation switch overlay (direct DOM manipulation)
    const switchOverlayRef = useRef<HTMLDivElement>(null);
    const prevConversationRef = useRef(currentConversationId);

    // Keyboard shortcut: Ctrl+Shift+U toggles raw markdown view
    useEffect(() => {
        const handleRawToggle = (e: KeyboardEvent) => {
            if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'u') {
                e.preventDefault();
                activeChatRef.current.setDisplayMode(currentConversationId, isRawModeRef.current ? 'pretty' : 'raw');
            }
        };
        window.addEventListener('keydown', handleRawToggle);
        return () => window.removeEventListener('keydown', handleRawToggle);
    }, [currentConversationId]);

    // Conversation switch overlay: show a spinner immediately when the user
    // switches conversations.  The heavy MarkdownRenderer work blocks the
    // main thread so the browser never gets a chance to paint a spinner set
    // via React state.  We use direct DOM manipulation to guarantee the
    // overlay is visible before React starts its synchronous render pass.
    // Show overlay synchronously via DOM when conversation changes
    useEffect(() => {
        if (prevConversationRef.current !== currentConversationId) {
            prevConversationRef.current = currentConversationId;
            // Show overlay immediately via DOM (bypasses React render batching).
            // Skip for global conversations during project switch — they remain
            // visible across projects and don't need a loading transition.
            const skipOverlay = isSwitchingProject && currentConvIsGlobalRef.current;
            if (switchOverlayRef.current && !skipOverlay) {
                switchOverlayRef.current.style.display = 'flex';
            }
        }
    }, [currentConversationId, isSwitchingProject]);

    // Show overlay immediately when project switch starts (even before conversation changes)
    // but only if the current conversation is NOT global-scoped
    useEffect(() => {
        if (isSwitchingProject && !currentConvIsGlobalRef.current && switchOverlayRef.current) {
            switchOverlayRef.current.style.display = 'flex';
        }
    }, [isSwitchingProject]);

    // Hide overlay when project switch completes
    useEffect(() => {
        if (!isSwitchingProject && switchOverlayRef.current &&
            switchOverlayRef.current.style.display !== 'none') {
            requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                    if (switchOverlayRef.current) {
                        switchOverlayRef.current.style.display = 'none';
                    }
                });
            });
        }
    }, [isSwitchingProject]);

    // Hide overlay after messages have rendered
    useEffect(() => {
        if (switchOverlayRef.current) {
            // Use rAF to ensure at least one paint occurred with the new content
            requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                    if (switchOverlayRef.current) {
                        switchOverlayRef.current.style.display = 'none';
                    }
                });
            });
        }
    }, [currentMessages]);

    // Fallback: always hide the overlay after conversation switch completes,
    // even if currentMessages didn't change (e.g. both old and new have 0 messages).
    useEffect(() => {
        const timer = setTimeout(() => {
            if (switchOverlayRef.current) {
                switchOverlayRef.current.style.display = 'none';
            }
        }, 500);
        return () => clearTimeout(timer);
    }, [currentConversationId]);

    const previousStreamingStateRef = useRef<Set<string>>(new Set());

    // Apply progressive window: show only the tail during initial render,
    // then the full list once the transition completes.
    // Conversation-switch detection happens during render (not in effect) so
    // the clamp applies on the very first render after switch — otherwise the
    // old messageWindow (usually Infinity after prior expansion) would render
    // all N messages synchronously before the effect has a chance to clamp,
    // producing a multi-second blocked main thread.
    const effectiveWindow = windowConvRef.current !== currentConversationId
        ? INITIAL_WINDOW
        : messageWindow;
    const windowedMessages = useMemo(() => {
        if (effectiveWindow >= currentMessages.length) return currentMessages;
        // Keep the last N messages so the user sees the most recent content first
        return currentMessages.slice(-effectiveWindow);
    }, [currentMessages, effectiveWindow]);

    const displayMessages = isTopToBottom ? windowedMessages : [...windowedMessages].reverse();

    // Keep track of rendered messages for performance monitoring  
    const renderedCountRef = useRef(0);
    const renderedSystemMessagesRef = useRef<Set<string>>(new Set());

    // Track which conversations have received streaming content
    const conversationHasStreamedContent = useCallback((conversationId: string) => {
        return streamedContentMap.has(conversationId) &&
            streamedContentMap.get(conversationId) !== '';
    }, [streamedContentMap]);

    useEffect(() => {
        // Only log when message count changes significantly, not on every render
        if (Math.abs(currentMessages.length - renderedCountRef.current) > 2) {
            if (isDebugLoggingEnabled()) {
                debugLog('Conversation messages updated:', {
                    messageCount: currentMessages.length,
                    previousCount: renderedCountRef.current,
                    displayOrder: isTopToBottom ? 'top-down' : 'bottom-up'
                });
            }
            renderedCountRef.current = currentMessages.length;
        }
    }, [isTopToBottom, currentMessages.length]);

    // Detect when OTHER conversations complete streaming
    useEffect(() => {
        const previousSet = previousStreamingStateRef.current;
        const wasCurrentStreaming = previousSet.has(currentConversationId);

        // Snapshot the current set for next render
        previousStreamingStateRef.current = new Set(streamingConversations);

        if (wasCurrentStreaming && !isCurrentlyStreaming) {
            console.log('✅ Current conversation finished streaming');
            // Scroll behavior handled by scrollToBottom in ChatContext
        } else if (previousSet.size > streamingConversations.size) {
            // A background conversation finished
            console.log('📌 Background conversation finished - locking scroll position');
            recordManualScroll();
        }
    }, [isCurrentlyStreaming, streamingConversations, currentConversationId, recordManualScroll]);

    // Global conversations survive project switches — don't blank them
    const currentConvIsGlobal = conversations.find(c => c.id === currentConversationId)?.isGlobal === true;
    const currentConvIsGlobalRef = useRef(currentConvIsGlobal);
    currentConvIsGlobalRef.current = currentConvIsGlobal;


    // Listen for feedback delivery confirmation and remove the placeholder
    // feedback message from the conversation.  The model echoes feedback
    // inline in its response, so keeping the original would show it twice.
    useEffect(() => {
        const handleFeedbackDelivered = (event: CustomEvent) => {
            const { conversationId: fbConvId } = event.detail || {};
            if (!fbConvId) return;
            convListRef.current.setConversations((prev: any[]) =>
                prev.map(conv => {
                    if (conv.id !== fbConvId) return conv;
                    const filtered = conv.messages.filter(
                        (msg: any) => !(msg._isFeedback && msg._feedbackStatus === 'pending')
                    );
                    if (filtered.length === conv.messages.length) return conv;
                    return { ...conv, messages: filtered };
                })
            );
        };
        document.addEventListener('feedbackDelivered', handleFeedbackDelivered as EventListener);
        return () => document.removeEventListener('feedbackDelivered', handleFeedbackDelivered as EventListener);
    }, []);

    return (
        <div style={{ position: 'relative' }}>
            {/* Always-mounted overlay — shown/hidden via direct DOM manipulation
                to guarantee visibility before React's synchronous render blocks the thread */}
            <div
                ref={switchOverlayRef}
                style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    right: 0,
                    bottom: 0,
                    backgroundColor: 'rgba(0, 0, 0, 0.5)',
                    display: 'none',
                    justifyContent: 'center',
                    alignItems: 'center',
                    zIndex: 1000
                }}>
                <Spin size="large" tip="Loading conversation..." />
            </div>
            <div
                style={{
                    opacity: isLoadingConversation ? 0.5 : 1,
                    minHeight: '50px' // Ensure visibility detection
                }}
                className="conversation-messages-container"
            >
                {/* Raw mode indicator banner */}
                {isRawMode && (
                    <div className="raw-mode-banner">
                        <CodeOutlined style={{ marginRight: 6 }} />
                        Raw Markdown View — <kbd>Ctrl+Shift+U</kbd> to return to rendered view
                        <Button
                            type="link" size="small"
                            icon={<EyeOutlined />}
                            onClick={() => activeChatRef.current.setDisplayMode(currentConversationId, 'pretty')}
                            style={{ marginLeft: 8, color: 'inherit' }}
                        >Rendered</Button>
                    </div>
                )}
                {displayMessages?.map((msg, index) => {
                    // Convert display index to actual index for bottom-up mode
                    const windowOffset = currentMessages.length - windowedMessages.length;
                    const rawIndex = windowOffset + (isTopToBottom ? index : windowedMessages.length - 1 - index);
                    const actualIndex = rawIndex;
                    const nextActualIndex = actualIndex + 1;
                    const hasNextMessage = nextActualIndex < currentMessages.length;
                    const nextMessage = hasNextMessage ? currentMessages[nextActualIndex] : null;
                    const needsRetry = msg.role === 'human' &&
                        !isCurrentlyStreaming && !hasStreamedContent &&
                        (!hasNextMessage || nextMessage?.role !== 'assistant');
                    const needsResponse = msg.role === 'human' &&
                        !isCurrentlyStreaming &&
                        !hasStreamedContent &&
                        (actualIndex === currentMessages.length - 1 ||
                            (hasNextMessage && nextMessage?.role !== 'assistant'));

                    // Create a unique key for system messages to prevent duplicate logging
                    const systemMessageKey = msg.role === 'system' && msg.modelChange ?
                        `${msg.modelChange.from}->${msg.modelChange.to}` :
                        msg.content;

                    // Only log system messages once and only in development mode
                    if (process.env.NODE_ENV === 'development' &&
                        msg.role === 'system' &&
                        !renderedSystemMessagesRef.current.has(systemMessageKey)) {
                        renderedSystemMessagesRef.current.add(systemMessageKey);
                    }

                    return <div
                        // Use message ID as key instead of index
                        key={`message-${msg.id || actualIndex}`}
                        data-message-index={actualIndex}
                        className={`message ${msg.role || ''}${msg.muted ? ' muted' : ''}${needsResponse
                            ? ' needs-response' : ''
                            }`}
                    >
                        {/* Handle system messages with model changes first */}
                        {msg.role === 'system' && msg.modelChange ? (
                            <ModelChangeNotification
                                previousModel={msg.modelChange.from}
                                changeKey={msg.modelChange.changeKey}
                                newModel={msg.modelChange.to}
                            />
                        ) : (
                            // Skip rendering empty messages entirely
                            msg.content ? (
                                // Regular message rendering for messages with content
                                <>
                                    {msg.role === 'human' && (
                                        <div style={{ display: editingMessageIndex === actualIndex ? 'none' : 'flex', justifyContent: 'space-between', paddingRight: '8px' }}>
                                            <div className="message-sender">
                                                You{msg._isFeedback && msg._feedbackStatus === 'pending' && (
                                                    <span style={{
                                                        color: '#faad14',
                                                        fontSize: '12px',
                                                        marginLeft: '4px',
                                                        fontStyle: 'italic'
                                                    }}>(pending feedback)</span>
                                                )}:
                                            </div>
                                            <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                                <MessageActions
                                                    message={msg}
                                                    actualIndex={actualIndex}
                                                    isEditing={editingMessageIndex === actualIndex}
                                                    needsResponse={needsRetry}
                                                    enableCodeApply={enableCodeApply}
                                                    onOpenShellConfig={onOpenShellConfig}
                                                />
                                                <EditSection index={actualIndex} isInline={true} />
                                            </div>
                                        </div>
                                    )}

                                    {/* Only show edit section when editing, otherwise show message content */}
                                    {msg.role === 'human' && editingMessageIndex === actualIndex ? (
                                        <EditSection index={actualIndex} isInline={false} />
                                    ) : msg.role === 'human' && (msg.content || (msg.images && msg.images.length > 0) || (msg.documents && msg.documents.length > 0)) ? (
                                        <>
                                            {/* Display attached documents as chips */}
                                            {msg.documents && msg.documents.length > 0 && (
                                                <div style={{
                                                    marginBottom: '8px',
                                                    display: 'flex',
                                                    gap: '6px',
                                                    flexWrap: 'wrap'
                                                }}>
                                                    {msg.documents.map((doc, docIdx) => (
                                                        <DocumentChip key={docIdx} doc={doc} inline />
                                                    ))}
                                                </div>
                                            )}
                                            {/* Display attached images if present */}
                                            {msg.images && msg.images.length > 0 && (
                                                <div style={{
                                                    marginBottom: '12px',
                                                    display: 'flex',
                                                    gap: '8px',
                                                    flexWrap: 'wrap'
                                                }}>
                                                    {msg.images.map((img, imgIndex) => (
                                                        <div key={imgIndex} style={{
                                                            position: 'relative',
                                                            display: 'inline-block'
                                                        }}>
                                                            <AntImage
                                                                src={`data:${img.mediaType};base64,${img.data}`}
                                                                alt={img.filename || 'Attached image'}
                                                                width={120}
                                                                height={120}
                                                                style={{ objectFit: 'cover', borderRadius: '4px', border: '1px solid #d9d9d9' }}
                                                                preview={{ mask: <PictureOutlined /> }}
                                                            />
                                                            {img.filename && <div style={{ fontSize: '11px', textAlign: 'center', marginTop: '4px', maxWidth: '120px', overflow: 'hidden', textOverflow: 'ellipsis' }}>{img.filename}</div>}
                                                        </div>
                                                    ))}
                                                </div>
                                            )}
                                            {/* Only render message content if there's actual text content */}
                                            {msg.content && <div className="message-content">
                                                {isRawMode ? (
                                                    <pre className="raw-markdown-view">{msg.content}</pre>
                                                ) : (
                                                    <LazyMarkdownRenderer
                                                        markdown={msg.content}
                                                        enableCodeApply={enableCodeApply}
                                                        onOpenShellConfig={onOpenShellConfig}
                                                        isStreaming={false}
                                                        role={msg.role as 'human' | 'assistant' | 'system'}
                                                    />
                                                )}
                                            </div>}
                                        </>
                                    ) : msg.role === 'assistant' && msg.content ? (
                                        <>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', paddingRight: '8px' }}>
                                                <div className="message-sender">AI:</div>
                                                <MessageActions
                                                    message={msg}
                                                    actualIndex={actualIndex}
                                                    isEditing={false}
                                                    needsResponse={needsRetry}
                                                    enableCodeApply={enableCodeApply}
                                                    onOpenShellConfig={onOpenShellConfig}
                                                />
                                            </div>
                                            <div className="message-content">
                                                {isRawMode ? (
                                                    <pre className="raw-markdown-view">{msg.content}</pre>
                                                ) : (
                                                    <LazyMarkdownRenderer
                                                        markdown={msg.content}
                                                        enableCodeApply={enableCodeApply}
                                                        onOpenShellConfig={onOpenShellConfig}
                                                        isStreaming={false}
                                                        role={msg.role as 'human' | 'assistant' | 'system'}
                                                    />
                                                )}
                                            </div>
                                        </>
                                    ) : null}
                                </>
                            ) : null
                        )}
                    </div>;
                })}

                {/* Fallback for when no messages to display */}
                {(!displayMessages || displayMessages.length === 0) && (
                    <div style={{
                        textAlign: 'center',
                        padding: '2rem',
                        color: '#999'
                    }}>
                        No messages in this conversation yet.
                    </div>
                )}
            </div>
        </div>
    );
});

// Set display name for debugging
Conversation.displayName = 'Conversation';

export default Conversation;
