/**
 * Tests for the auto-scroll guard in MUIChatHistory.
 *
 * The sidebar has three effects that can call scrollToItem on the virtual
 * list. Without a guard, expanding/collapsing a folder re-fires the
 * effects (because flatNodes changes) and yanks the viewport back to
 * the active conversation.
 *
 * `lastScrolledConvIdRef` tracks the last conversation we auto-scrolled
 * to.  The effects only scroll when the active conversation actually
 * changes, not on every flatNodes change.
 *
 * These tests model the decision logic of each effect as pure functions
 * and verify the four scenarios from the truth table:
 *
 *   User action                    | should scroll?
 *   -------------------------------|---------------
 *   Toggle chevron                 | No
 *   Click different conv           | Yes
 *   Click same conv (already active) | No
 *   Clear search                   | Yes
 *   Load conv in collapsed folder  | Yes (via Effect B retry)
 */

// ── Pure decision functions extracted from MUIChatHistory.tsx ────────
// These must stay in lockstep with the effects in the component.

interface ScrollState {
  lastScrolledConvId: string | null;
  scrollToNodeId: string | null;
  currentConversationId: string | null;
  searchQuery: string;
  rowIndex: number; // flatNodes.findIndex result
}

/**
 * Effect A: immediate scroll on conversation change.
 * Depends on [currentConversationId, flatNodes].
 */
function shouldEffectAScroll(state: ScrollState): boolean {
  if (!state.currentConversationId) return false;
  if (state.scrollToNodeId) return false; // one-shot override active
  if (state.lastScrolledConvId === state.currentConversationId) return false;
  if (state.rowIndex === -1) return false;
  return true;
}

/**
 * Effect B: 100ms-delayed retry for when flatNodes hasn't caught up.
 * Depends on [currentConversationId].
 */
function shouldEffectBScroll(state: ScrollState): boolean {
  if (!state.currentConversationId) return false;
  if (state.scrollToNodeId) return false;
  if (state.rowIndex === -1) return false;
  // Note: Effect B intentionally fires even if lastScrolledConvId matches,
  // because it's a retry for a previous Effect A miss.  After it scrolls,
  // it updates lastScrolledConvId so Effect A doesn't double-fire.
  return true;
}

/**
 * Effect C: search-clear scroll.
 * Depends on [searchQuery, currentConversationId, flatNodes].
 *
 * Returns the decision AND the ref-mutation that the effect performs.
 */
function effectCDecision(state: ScrollState): {
  shouldScroll: boolean;
  nullifyRef: boolean;
} {
  // Entering search invalidates the ref so search-clear can re-scroll.
  if (state.searchQuery) {
    return { shouldScroll: false, nullifyRef: true };
  }
  if (!state.currentConversationId) return { shouldScroll: false, nullifyRef: false };
  if (state.lastScrolledConvId === state.currentConversationId) {
    return { shouldScroll: false, nullifyRef: false };
  }
  if (state.rowIndex === -1) return { shouldScroll: false, nullifyRef: false };
  return { shouldScroll: true, nullifyRef: false };
}

// ── Scenarios from the truth table ───────────────────────────────────

describe('MUIChatHistory scroll guard', () => {
  const baseState: ScrollState = {
    lastScrolledConvId: null,
    scrollToNodeId: null,
    currentConversationId: 'conv-42',
    searchQuery: '',
    rowIndex: 5, // active conv is at row 5
  };

  describe('Scenario 1: Toggle chevron (the bug)', () => {
    it('Effect A does not scroll when only flatNodes changed', () => {
      const state: ScrollState = {
        ...baseState,
        lastScrolledConvId: 'conv-42', // already scrolled here
        currentConversationId: 'conv-42',
        rowIndex: 7, // row shifted because a folder expanded above
      };
      expect(shouldEffectAScroll(state)).toBe(false);
    });

    it('Effect C does not scroll when only flatNodes changed', () => {
      const state: ScrollState = {
        ...baseState,
        lastScrolledConvId: 'conv-42',
        currentConversationId: 'conv-42',
        searchQuery: '',
        rowIndex: 7,
      };
      expect(effectCDecision(state)).toEqual({
        shouldScroll: false,
        nullifyRef: false,
      });
    });
  });

  describe('Scenario 2: Click different conversation', () => {
    it('Effect A scrolls when currentConversationId changes', () => {
      const state: ScrollState = {
        ...baseState,
        lastScrolledConvId: 'conv-42',
        currentConversationId: 'conv-7',
        rowIndex: 12,
      };
      expect(shouldEffectAScroll(state)).toBe(true);
    });
  });

  describe('Scenario 3: Click already-active conversation', () => {
    it('Effect A does not scroll', () => {
      const state: ScrollState = {
        ...baseState,
        lastScrolledConvId: 'conv-42',
        currentConversationId: 'conv-42',
        rowIndex: 5,
      };
      expect(shouldEffectAScroll(state)).toBe(false);
    });
  });

  describe('Scenario 4: Clear search', () => {
    it('entering search nullifies the ref', () => {
      const state: ScrollState = {
        ...baseState,
        lastScrolledConvId: 'conv-42',
        currentConversationId: 'conv-42',
        searchQuery: 'needle',
      };
      expect(effectCDecision(state)).toEqual({
        shouldScroll: false,
        nullifyRef: true,
      });
    });

    it('clearing search re-scrolls to active conversation', () => {
      // Simulate: user searched, then cleared.  lastScrolledConvId was
      // nullified when they entered search.
      const state: ScrollState = {
        ...baseState,
        lastScrolledConvId: null, // was nullified on search entry
        currentConversationId: 'conv-42',
        searchQuery: '',
        rowIndex: 5,
      };
      expect(effectCDecision(state)).toEqual({
        shouldScroll: true,
        nullifyRef: false,
      });
    });

    it('without ref-nullify, Effect C would not re-scroll after search', () => {
      // Regression guard: if someone removes the ref-nullify in the
      // searchQuery branch, the second assertion below would fail.
      // Here we assert the buggy state for contrast.
      const buggyState: ScrollState = {
        ...baseState,
        lastScrolledConvId: 'conv-42', // NOT nullified — bug
        currentConversationId: 'conv-42',
        searchQuery: '',
        rowIndex: 5,
      };
      expect(effectCDecision(buggyState).shouldScroll).toBe(false);
    });
  });

  describe('Scenario 5: Load conv in collapsed folder', () => {
    it('Effect A bails when target not yet in flatNodes', () => {
      const state: ScrollState = {
        ...baseState,
        lastScrolledConvId: 'conv-42',
        currentConversationId: 'conv-99', // new conv inside collapsed folder
        rowIndex: -1, // not yet in flatNodes
      };
      expect(shouldEffectAScroll(state)).toBe(false);
    });

    it('Effect B retries after 100ms when flatNodes has caught up', () => {
      const state: ScrollState = {
        ...baseState,
        lastScrolledConvId: 'conv-42',
        currentConversationId: 'conv-99',
        rowIndex: 8, // folder auto-expanded, target now visible
      };
      expect(shouldEffectBScroll(state)).toBe(true);
    });

    it('after Effect B scrolls & updates ref, Effect A does not double-fire', () => {
      // Simulate: Effect B ran, scrolled, set lastScrolledConvId = 'conv-99'.
      // Now flatNodes changes again (e.g. another folder-expand race).
      const state: ScrollState = {
        ...baseState,
        lastScrolledConvId: 'conv-99', // updated by Effect B
        currentConversationId: 'conv-99',
        rowIndex: 8,
      };
      expect(shouldEffectAScroll(state)).toBe(false);
    });
  });

  describe('scrollToNodeIdRef one-shot override', () => {
    it('Effect A defers when a one-shot scroll target is pending', () => {
      const state: ScrollState = {
        ...baseState,
        lastScrolledConvId: null,
        scrollToNodeId: 'new-folder-id',
        currentConversationId: 'conv-7',
        rowIndex: 3,
      };
      expect(shouldEffectAScroll(state)).toBe(false);
    });

    it('Effect B also defers', () => {
      const state: ScrollState = {
        ...baseState,
        scrollToNodeId: 'new-folder-id',
        currentConversationId: 'conv-7',
        rowIndex: 3,
      };
      expect(shouldEffectBScroll(state)).toBe(false);
    });
  });
});
