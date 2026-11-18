/**
 * Wiring tests for the three hold-surfacing gaps fixed in priority order.
 *
 * Static source assertions, deliberately.  Every defect in this area has
 * been a SEAM -- two correct halves with nothing verifying the join:
 *   - a recorder defined but never called
 *   - a glyph map missing one key
 *   - a derivation with no consumer
 *   - a run-scoped numerator over a loop-scoped denominator
 * In each case the logic's own unit tests were green while the feature
 * could not possibly work.  These assert the JOINS.
 *
 * Item 1: a held study must leave a sidebar trace.  'held' is terminal, so
 *         the running-task set is correctly emptied when a run holds --
 *         which left the row completely silent, indistinguishable from an
 *         idle conversation.  Worst case for an unattended multi-hour run.
 * Item 2: (retired) CalleeHoldPanel's polling.  The panel was replaced by
 *         the Task Cards dialog's run pane and outline markers; see
 *         deckPanesWiring.test.ts.
 * Item 3: the iteration-resume control was offered for parallel loops,
 *         where the server refuses it outright with a 422.
 */

import * as fs from 'fs';
import * as path from 'path';

const read = (rel: string): string =>
  fs.readFileSync(path.resolve(__dirname, rel), 'utf8');

const CHAT_CTX = () => read('../../../context/ChatContext.tsx');
const ACTIVE_CTX = () => read('../../../context/ActiveChatContext.tsx');
const CONVERSATION = () => read('../../Conversation.tsx');
const SIDEBAR = () => read('../../MUIChatHistory.tsx');
const DETAIL = () => read('../BlockDetailPanel.tsx');
const CSS = () => read('../task-card-inline-tile.css');

// ---------------------------------------------------------------- item 1

// Item 1 (a held study is visible from the sidebar) previously lived here
// as a pair of boolean Sets.  It was superseded by the per-status gear
// cluster -- a Set cannot carry a count, and generalizing to eight
// statuses would have meant eight Sets to keep mutually consistent -- so
// its assertions now live in runStatusGearWiring.test.ts against the
// bindings-derived design that replaced it.  Deleted rather than skipped:
// a retained test for a rejected design pulls the next reader back toward
// it.

describe('item 3: no control that can only ever fail', () => {
  it('detects a parallel loop from the block itself', () => {
    // Read off the block, not passed in, so no caller can forget the gate.
    const src = DETAIL();
    expect(src).toMatch(/isParallelLoop/);
    expect(src).toMatch(/block\.repeat_parallel/);
  });

  it('gates the resume buttons on it', () => {
    expect(DETAIL()).toMatch(/isIter && !isParallelLoop && \(onRetryIteration/);
  });

  it('explains the refusal instead of rendering nothing', () => {
    // Silence would leave the user hunting for a control that is absent
    // for a reason they cannot see.
    const src = DETAIL();
    expect(src).toMatch(/isIter && isParallelLoop &&/);
    expect(src).toMatch(/in parallel/);
  });

  it('names the resume path that does work', () => {
    // A dead end is worse than a redirect: block-level Retry re-runs the
    // whole loop, and stages before it still replay from record.
    const src = DETAIL();
    const block = src.match(/isIter && isParallelLoop &&[\s\S]{0,900}?\)\}/);
    expect(block).not.toBeNull();
    expect(block![0]).toMatch(/Retry/);
  });

  it('is not gated for an until loop, which is serial by construction', () => {
    const src = DETAIL();
    expect(src).toMatch(/block_type === 'repeat' && block\.repeat_parallel/);
  });

  it('styles the explanation as information, not as an offer', () => {
    // A blue actionable panel with nothing to click reads as a broken
    // control rather than as an explanation.
    const css = CSS();
    expect(css).toMatch(/\.tc-iter-resume--unavailable/);
    expect(css).toMatch(/rgba\(128, 128, 128, 0\.3\)/);
  });
});
