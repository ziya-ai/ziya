/**
 * Static wiring guards for the deck's run links.
 *
 * Source-text guards rather than a mount, matching the convention the
 * neighbouring task-card wiring tests set (see proposalSigningNotice and
 * holdSurfacingWiring). The defect class here is wiring: the deck already
 * held everything needed to answer "is this card still running?" — it
 * simply never asked, and a run's conversation was reachable only if you
 * happened to remember which chat it was launched into. A render test
 * would need a project context, a chat context, a fetch mock per endpoint
 * and specific run state to reach the same assertions, and would still
 * not pin the requirement that the deck ASKS at all.
 */

import * as fs from 'fs';
import * as path from 'path';

const DIR = path.resolve(__dirname, '..');
const read = (f: string) => fs.readFileSync(path.join(DIR, f), 'utf8');

const library = () => read('TaskCardsLibrary.tsx');
const runList = () => read('DeckRunList.tsx');
const index = () => read('deckRunIndex.ts');

describe('the deck asks the server which runs exist', () => {
  it('lists runs project-wide, not per card', () => {
    // One request for the whole deck. A per-card fetch would be N
    // requests on every open, which is what made this too expensive to
    // add before the index existed.
    expect(library()).toMatch(/listTaskRuns/);
    expect(library()).toMatch(/indexRunsByCard/);
  });

  it('keeps polling only while something is actually live', () => {
    // An idle deck must issue no requests at all; a deck watching a
    // running card must not require a manual Refresh to notice it
    // finished.
    expect(library()).toMatch(/hasLiveRuns/);
    expect(library()).toMatch(/setInterval|setTimeout/);
  });

  it('survives a run-list failure without breaking the card list', () => {
    // Runs are supplementary: a deck that cannot render its cards
    // because the run endpoint hiccuped is strictly worse than one
    // showing no run badges.
    expect(library()).toMatch(/catch[\s\S]{0,120}setRunIndex\(new Map\(\)\)/);
  });
});

describe('a card row states whether it is live or wants attention', () => {
  it('summarizes each row from the index', () => {
    expect(library()).toMatch(/summarizeCardRuns/);
  });

  it('distinguishes live from needs-attention', () => {
    // Both can be true at once (a retry running while the failed
    // attempt it came from is still on record), so one combined badge
    // would hide whichever the user was not looking for.
    expect(library()).toMatch(/\.live\b/);
    expect(library()).toMatch(/\.attention\b/);
  });

  it('does not reimplement the status classification inline', () => {
    // Two copies of "which statuses are live" is how the deck and the
    // tile end up disagreeing about the same run.
    const lib = library();
    expect(lib).not.toMatch(/'queued'\s*,\s*'running'\s*,\s*'paused'/);
  });
});

describe('a run is clickable through to where it is running', () => {
  it('the run list exists and renders one row per run', () => {
    expect(runList()).toMatch(/export const DeckRunList/);
    expect(runList()).toMatch(/runs\.map/);
  });

  it('routes a click to the run\'s own conversation', () => {
    expect(runList()).toMatch(/source_conversation_id/);
    expect(library()).toMatch(/loadConversation/);
  });

  it('closes the deck when navigating, so the tile is visible', () => {
    // Navigating behind a modal that still covers the conversation is
    // indistinguishable from the click having done nothing.
    expect(library()).toMatch(/const handleOpenRun[\s\S]{0,600}onClose\(\)/);
  });

  it('says why a run is not clickable rather than silently ignoring it', () => {
    // A run launched with no conversation (an older unbound launch) has
    // nowhere to navigate to; a dead click reads as a bug.
    expect(runList()).toMatch(/canOpen/);
    expect(runList()).toMatch(/no conversation/i);
  });

  it('shows a status per run, using the shared colour map', () => {
    expect(runList()).toMatch(/deckStatusColor/);
  });

  it('renders an empty state instead of a blank panel', () => {
    expect(runList()).toMatch(/never run|No runs/i);
  });
});

describe('the classification lives in one place', () => {
  it('exports the sets rather than inlining literals at each use', () => {
    const src = index();
    expect(src).toMatch(/export const LIVE_STATUSES/);
    expect(src).toMatch(/export const ATTENTION_STATUSES/);
  });

  it('excludes cancelled from attention', () => {
    // The user cancelled it; badging it beside real failures teaches
    // them to ignore the badge.
    const src = index();
    const attn = src.slice(src.indexOf('ATTENTION_STATUSES'));
    expect(attn.slice(0, 200)).not.toMatch(/cancelled/);
  });
});
