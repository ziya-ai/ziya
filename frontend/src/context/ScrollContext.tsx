/**
 * ScrollContext — lightweight context for scroll-related state only.
 *
 * Scroll events fire at ~60Hz during user interaction and streaming.
 * By isolating scroll state, components like FolderTree, EditSection,
 * and ProjectManagerModal are not forced to re-render on scroll changes.
 *
 * ChatProvider owns the actual state; this context is a narrow slice
 * that receives props — same pattern as StreamingContext.
 */
import React, { createContext, useContext, useMemo, Dispatch, SetStateAction } from 'react';

interface ScrollContextValue {
  scrollToBottom: () => void;
  userHasScrolled: boolean;
  setUserHasScrolled: Dispatch<SetStateAction<boolean>>;
  recordManualScroll: () => void;
  isTopToBottom: boolean;
  setIsTopToBottom: Dispatch<SetStateAction<boolean>>;
}

const ScrollContext = createContext<ScrollContextValue | undefined>(undefined);

export const ScrollProvider: React.FC<
  ScrollContextValue & { children: React.ReactNode }
> = ({ children, ...value }) => {
  const memoized = useMemo(
    () => ({
      scrollToBottom: value.scrollToBottom,
      userHasScrolled: value.userHasScrolled,
      setUserHasScrolled: value.setUserHasScrolled,
      recordManualScroll: value.recordManualScroll,
      isTopToBottom: value.isTopToBottom,
      setIsTopToBottom: value.setIsTopToBottom,
    }),
    [
      value.scrollToBottom,
      value.userHasScrolled,
      value.setUserHasScrolled,
      value.recordManualScroll,
      value.isTopToBottom,
      value.setIsTopToBottom,
    ]
  );

  return (
    <ScrollContext.Provider value={memoized}>{children}</ScrollContext.Provider>
  );
};

export function useScrollContext(): ScrollContextValue {
  const ctx = useContext(ScrollContext);
  if (!ctx) throw new Error('useScrollContext must be used within ScrollProvider');
  return ctx;
}
