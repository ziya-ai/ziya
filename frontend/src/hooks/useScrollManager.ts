import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { createScrollActivityTracker } from '../utils/scrollActivity';
import {
    createScrollVelocityTracker,
    setSharedScrollVelocityTracker,
    jumpToEdge,
} from '../utils/scrollVelocity';

interface ScrollManagerOptions {
    containerRef: React.RefObject<HTMLElement>;
    sentinelRef: React.RefObject<HTMLElement>;
    isTopToBottom: boolean;
    isStreaming: boolean;
    /**
     * Streamed content length.  Optional: App omits it and calls
     * notifyContentLength from StreamedContentLengthWatcher instead, because
     * reading the length in App re-rendered App on every streaming flush.
     */
    contentLength?: number;
}

interface ScrollManagerState {
    isAtActiveEnd: boolean;
    hasNewContentWhileAway: boolean;
    streamCompletedWhileAway: boolean;
    scrollToActiveEnd: () => void;
    /** Jump straight to the top of the scroll container (no reveal stalls). */
    scrollToStart: () => void;
    /** Jump straight to the bottom, re-pinning while shells settle. */
    scrollToEnd: () => void;
    clearIndicators: () => void;
    /** Report a new streamed length without re-rendering the caller.  Stable. */
    notifyContentLength: (length: number) => void;
}

/** After a stream ends, content keeps re-laying out for a moment (the final
 *  message commit replaces the streamed div, late diagrams render).  Keep
 *  following for this long so the view lands on the real end. */
export const FOLLOW_GRACE_MS = 1500;

/** A programmatic clamp (content shrank, browser lowered scrollTop) leaves
 *  the view within this many px of the active end; a user gesture does not. */
const AWAY_SLACK_PX = 4;

function distanceFromActiveEnd(container: HTMLElement, isTopToBottom: boolean): number {
    return isTopToBottom
        ? container.scrollHeight - container.scrollTop - container.clientHeight
        : container.scrollTop;
}

function pinToActiveEnd(container: HTMLElement, isTopToBottom: boolean): void {
    container.scrollTop = isTopToBottom
        ? container.scrollHeight - container.clientHeight
        : 0;
}

export function useScrollManager({
    containerRef,
    sentinelRef,
    isTopToBottom,
    isStreaming,
    contentLength
}: ScrollManagerOptions): ScrollManagerState {
    const [isAtActiveEnd, setIsAtActiveEnd] = useState(true);
    const [followMode, setFollowMode] = useState(true);
    const [hasNewContentWhileAway, setHasNewContentWhileAway] = useState(false);
    const [streamCompletedWhileAway, setStreamCompletedWhileAway] = useState(false);
    const wasAwayDuringStreamRef = useRef(false);
    const wasStreamingRef = useRef(isStreaming);
    const lastContentLengthRef = useRef(contentLength ?? 0);
    const observerRef = useRef<IntersectionObserver | null>(null);
    const scrollRafRef = useRef<number | null>(null);
    // Read inside the ResizeObserver callback below without re-subscribing
    // it on every render.
    const followModeRef = useRef(true);
    const isStreamingRef = useRef(isStreaming);
    const streamEndedAtRef = useRef(-Infinity);
    followModeRef.current = followMode;
    isStreamingRef.current = isStreaming;

    // Scroll to the active end (bottom for top-down, top for bottom-up)
    const scrollToActiveEnd = useCallback(() => {
        if (!containerRef.current) return;
        
        const container = containerRef.current;
        
        // Coalesce into one scroll per frame to avoid layout thrashing
        if (scrollRafRef.current !== null) {
            cancelAnimationFrame(scrollRafRef.current);
        }
        scrollRafRef.current = requestAnimationFrame(() => {
            scrollRafRef.current = null;
            if (!containerRef.current) return;
            
            if (isTopToBottom) {
                containerRef.current.scrollTop = containerRef.current.scrollHeight - containerRef.current.clientHeight;
            } else {
                containerRef.current.scrollTop = 0;
            }
            
            setHasNewContentWhileAway(false);
            setStreamCompletedWhileAway(false);
            setFollowMode(true);
        });
    }, [containerRef, isTopToBottom]);

    // Direct edge jumps.  Unlike wheel/trackpad travel these do not pass
    // through every placeholder shell on the way, so they are the fast path
    // to either end of a very long conversation.
    const scrollToStart = useCallback(() => {
        if (!containerRef.current) return;
        jumpToEdge(containerRef.current, 'start');
    }, [containerRef]);

    const scrollToEnd = useCallback(() => {
        if (!containerRef.current) return;
        jumpToEdge(containerRef.current, 'end');
    }, [containerRef]);

    // Clear all indicators
    const clearIndicators = useCallback(() => {
        setHasNewContentWhileAway(false);
        setStreamCompletedWhileAway(false);
    }, []);

    // Set up IntersectionObserver on the sentinel div
    useEffect(() => {
        if (!sentinelRef.current || !containerRef.current) return;

        // Clean up existing observer
        if (observerRef.current) {
            observerRef.current.disconnect();
        }

        // Create new observer
        observerRef.current = new IntersectionObserver(
            (entries) => {
                entries.forEach(entry => {
                    const atEnd = entry.isIntersecting;
                    setIsAtActiveEnd(atEnd);
                    
                    // If user scrolled to active end, clear indicators
                    if (atEnd) {
                        setFollowMode(true);
                        setHasNewContentWhileAway(false);
                        setStreamCompletedWhileAway(false);
                    }
                });
            },
            {
                root: containerRef.current,
                threshold: 0.5, // Consider "at end" when 50% of sentinel is visible
                rootMargin: '50px'
            }
        );

        observerRef.current.observe(sentinelRef.current);

        return () => {
            if (observerRef.current) {
                observerRef.current.disconnect();
            }
        };
    }, [containerRef, sentinelRef, isTopToBottom]);

    // Detect manual scroll away from end (disables follow mode)
    useEffect(() => {
        if (!containerRef.current) return;

        const container = containerRef.current;
        let lastScrollTop = container.scrollTop;

        // Brighten the scrollbar thumb while the user is actively scrolling.
        // A class (not :hover) is required because wheel/trackpad scrolling
        // never places the cursor over the thumb. The tracker reverts the
        // class ~800ms after scrolling stops. Most useful on very long
        // conversations where the thumb shrinks toward its min-height floor
        // and a small grey rectangle is easy to lose against the dark pane.
        const activityTracker = createScrollActivityTracker(container);

        // Publish a velocity tracker so the deferred-mount code in
        // Conversation.tsx can hold off revealing placeholder shells while
        // the user is flinging through the conversation.  Revealing
        // mid-gesture stalls the scroll and reflows the document under the
        // user; deferring until they stop makes fast travel actually fast.
        const velocityTracker = createScrollVelocityTracker(container);
        setSharedScrollVelocityTracker(velocityTracker);

        const handleScroll = () => {
            const currentScrollTop = container.scrollTop;
            // Movement away from the active end that LANDS away from it.  A
            // scrollTop drop caused by content shrinking (a thinking block
            // collapsing) is clamped by the browser to the new bottom, so
            // the distance test tells it apart from a wheel-up.
            const moved = isTopToBottom
                ? currentScrollTop < lastScrollTop - 10
                : currentScrollTop > lastScrollTop + 10;
            const isScrollingAway = moved && distanceFromActiveEnd(container, isTopToBottom) > AWAY_SLACK_PX;
            
            if (isStreaming && isScrollingAway) {
                setFollowMode(false);
            }

            activityTracker.notify();
            
            lastScrollTop = currentScrollTop;
        };

        container.addEventListener('scroll', handleScroll, { passive: true });
        return () => {
            container.removeEventListener('scroll', handleScroll);
            activityTracker.dispose();
            velocityTracker.dispose();
            setSharedScrollVelocityTracker(null);
        };
    }, [containerRef, isStreaming, isTopToBottom]);

    // Re-pin when the CONTENT grows, not only when the streamed text does.
    // The rAF pin below runs the frame contentLength changes, but a tool
    // fence or thinking block renders its body a frame or more later, and
    // between a tool result and the next model chunk contentLength does not
    // change at all -- so the view sat on the top of the last block's header
    // while its body expanded beneath the sticky composer dock.  A
    // ResizeObserver on the content box fires after every layout change,
    // whoever caused it, and runs post-layout so the pin sees the final
    // height.  Gated to follow mode and streaming (plus a short grace after
    // the stream ends) so expanding a block in an idle conversation never
    // yanks the view.
    useEffect(() => {
        const container = containerRef.current;
        if (!container || typeof ResizeObserver === 'undefined') return;
        const content = container.querySelector('.chat-content-stabilizer') ?? container.firstElementChild;
        if (!content) return;
        const ro = new ResizeObserver(() => {
            if (!followModeRef.current) return;
            const live = isStreamingRef.current
                || Date.now() - streamEndedAtRef.current < FOLLOW_GRACE_MS;
            if (live) pinToActiveEnd(container, isTopToBottom);
        });
        ro.observe(content);
        return () => ro.disconnect();
    }, [containerRef, isTopToBottom]);

    // A content-length change: follow, and note growth while away.  Reads
    // streaming/follow state through refs (assigned during render), so it
    // is stable and can be called from a child's effect.  While streaming
    // the length ref tracks every value, so a decrease is only ever seen
    // after streaming has stopped.
    const handleContentLength = useCallback((len: number) => {
        const streaming = isStreamingRef.current;
        if (streaming && containerRef.current && followModeRef.current) {
            scrollToActiveEnd();
        }
        if (streaming) {
            const contentGrew = len > lastContentLengthRef.current;
            lastContentLengthRef.current = len;
            if (contentGrew && !followModeRef.current) {
                setHasNewContentWhileAway(true);
            }
        }
        if (len < lastContentLengthRef.current) {
            // The finished turn's entry was deleted at stream end.  This used
            // to be read as "a new user message was sent" and forced a scroll
            // to the end, yanking a reader who had scrolled up and clearing
            // the "completed while away" indicator as it appeared.  New-message
            // and conversation-switch scrolling are App.tsx's effects; a
            // follow-mode user is kept pinned by the FOLLOW_GRACE_MS observer.
            // Re-baseline only, so the next turn's first chunk counts as growth.
            lastContentLengthRef.current = len;
        }
    }, [containerRef, scrollToActiveEnd]);

    // Follow mode re-engaging, or a stream starting, pins as before.
    useEffect(() => {
        if (!isStreaming || !containerRef.current) return;
        if (followMode) scrollToActiveEnd();
    }, [followMode, isStreaming, scrollToActiveEnd, containerRef]);

    // The contentLength option, when a caller still passes one.
    useEffect(() => {
        if (contentLength !== undefined) handleContentLength(contentLength);
    }, [contentLength, handleContentLength]);

    // Track if user was away at any point during streaming
    useEffect(() => {
        if (isStreaming && !isAtActiveEnd) {
            // User scrolled away during streaming - remember this
            wasAwayDuringStreamRef.current = true;
        } else if (!isStreaming) {
            // Reset when not streaming
            wasAwayDuringStreamRef.current = false;
        }
    }, [isStreaming, isAtActiveEnd]);

    // Track stream completion while user is away
    useEffect(() => {
        const wasStreaming = wasStreamingRef.current;
        const streamJustEnded = wasStreaming && !isStreaming;
        wasStreamingRef.current = isStreaming;
        if (streamJustEnded) streamEndedAtRef.current = Date.now();
        
        if (streamJustEnded && (wasAwayDuringStreamRef.current || !isAtActiveEnd)) {
            // Stream completed and user was away at some point during streaming
            console.log('🎯 Stream completed while user was away - showing green indicator');
            setStreamCompletedWhileAway(true);
            setHasNewContentWhileAway(false); // Upgrade to "completed" state
            wasAwayDuringStreamRef.current = false; // Reset for next stream
        } else if (streamJustEnded && isAtActiveEnd && !wasAwayDuringStreamRef.current) {
            // Stream completed and user was always at the bottom - no need to show indicator
            console.log('🎯 Stream completed while user at bottom - clearing indicators');
            clearIndicators();
        }
    }, [isStreaming, isAtActiveEnd, clearIndicators]);

    return {
        isAtActiveEnd,
        hasNewContentWhileAway,
        streamCompletedWhileAway,
        scrollToActiveEnd,
        scrollToStart,
        scrollToEnd,
        clearIndicators,
        notifyContentLength: handleContentLength,
    };
}
