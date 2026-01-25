import { useState, useEffect, useRef, useCallback } from 'react';

interface ScrollManagerOptions {
    containerRef: React.RefObject<HTMLElement>;
    sentinelRef: React.RefObject<HTMLElement>;
    isTopToBottom: boolean;
    isStreaming: boolean;
    contentLength: number; // Track content changes
}

interface ScrollManagerState {
    isAtActiveEnd: boolean;
    hasNewContentWhileAway: boolean;
    streamCompletedWhileAway: boolean;
    scrollToActiveEnd: () => void;
    clearIndicators: () => void;
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
    const lastContentLengthRef = useRef(contentLength);
    const observerRef = useRef<IntersectionObserver | null>(null);

    // Scroll to the active end (bottom for top-down, top for bottom-up)
    const scrollToActiveEnd = useCallback(() => {
        if (!containerRef.current) return;
        
        const container = containerRef.current;
        
        if (isTopToBottom) {
            // Scroll to bottom
            container.scrollTop = container.scrollHeight - container.clientHeight;
        } else {
            // Scroll to top
            container.scrollTop = 0;
        }
        
        // Clear indicators after scrolling
        setHasNewContentWhileAway(false);
        setStreamCompletedWhileAway(false);
        setFollowMode(true);
    }, [containerRef, isTopToBottom]);

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

        const handleScroll = () => {
            const currentScrollTop = container.scrollTop;
            const isScrollingAway = isTopToBottom 
                ? currentScrollTop < lastScrollTop - 10
                : currentScrollTop > lastScrollTop + 10;
            
            if (isStreaming && isScrollingAway) {
                setFollowMode(false);
            }
            
            lastScrollTop = currentScrollTop;
        };

        container.addEventListener('scroll', handleScroll, { passive: true });
        return () => container.removeEventListener('scroll', handleScroll);
    }, [containerRef, isStreaming, isTopToBottom]);

    // Auto-scroll when follow mode is active during streaming
    useEffect(() => {
        if (!isStreaming || !containerRef.current) return;
        
        if (followMode) {
            scrollToActiveEnd();
        }
    }, [contentLength, followMode, isStreaming, scrollToActiveEnd, containerRef]);

    // Track new content arrival while user is away
    useEffect(() => {
        // Only track if streaming and content actually changed
        if (!isStreaming) return;
        
        const contentGrew = contentLength > lastContentLengthRef.current;
        lastContentLengthRef.current = contentLength;
        
        if (contentGrew && !followMode) {
            setHasNewContentWhileAway(true);
        }
    }, [contentLength, followMode, isStreaming]);

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

    // On new user message (detected by contentLength resetting), scroll to active end
    useEffect(() => {
        const contentReset = contentLength < lastContentLengthRef.current;
        
        if (contentReset) {
            // Content was cleared - likely new user message
            // This is the ONE exception where we force scroll
            setTimeout(() => scrollToActiveEnd(), 100);
            clearIndicators();
        }
    }, [contentLength, scrollToActiveEnd, clearIndicators]);

    return {
        isAtActiveEnd,
        hasNewContentWhileAway,
        streamCompletedWhileAway,
        scrollToActiveEnd,
        clearIndicators
    };
}
