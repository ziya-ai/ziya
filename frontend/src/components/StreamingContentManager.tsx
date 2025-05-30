import React, { useEffect, useRef, useState } from 'react';
import { useChatContext } from '../context/ChatContext';

/**
 * StreamingContentManager is a utility component that helps manage visibility
 * of content during streaming to prevent disappearing elements.
 */
export const StreamingContentManager: React.FC = () => {
  const { isStreaming, streamingConversations } = useChatContext();
  const [activeStreamingElements, setActiveStreamingElements] = useState<Set<HTMLElement>>(new Set());
  const observerRef = useRef<IntersectionObserver | null>(null);
  const isStreamingActive = isStreaming || streamingConversations.size > 0;

  // Set up an intersection observer to track elements that should remain visible during streaming
  useEffect(() => {
    if (!isStreamingActive) {
      // Clear any tracked elements when not streaming
      setActiveStreamingElements(new Set());
      return;
    }

    // Create an observer that will track elements going in and out of view
    observerRef.current = new IntersectionObserver(
      (entries) => {
        if (!isStreamingActive) return;

        // During streaming, we want to track elements that have been visible
        // so we can ensure they remain rendered even when scrolled out of view
        setActiveStreamingElements(prev => {
          const newSet = new Set(prev);
          entries.forEach(entry => {
            if (entry.isIntersecting) {
              // Element is visible, add to our tracking set
              newSet.add(entry.target as HTMLElement);
            }
          });
          return newSet;
        });
      },
      { threshold: 0.1, rootMargin: '200px 0px' }
    );

    // Observe all code blocks, diffs, and other content that might disappear
    const elementsToObserve = document.querySelectorAll('.diff-container, pre, .token-container');
    elementsToObserve.forEach(el => observerRef.current?.observe(el));

    return () => {
      observerRef.current?.disconnect();
    };
  }, [isStreamingActive]);

  // Apply visibility styles to tracked elements
  useEffect(() => {
    // When streaming, ensure all tracked elements remain visible
    activeStreamingElements.forEach(el => {
      el.style.visibility = 'visible';
      el.classList.add('streaming-preserved');
    });

    return () => {
      // Clean up when streaming ends
      activeStreamingElements.forEach(el => el.classList.remove('streaming-preserved'));
    };
  }, [activeStreamingElements, isStreamingActive]);

  return null; // This is a utility component with no UI
};
