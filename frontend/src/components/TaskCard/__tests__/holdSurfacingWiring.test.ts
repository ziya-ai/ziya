/**
 * Static wiring guards for the three chosen hold surfaces: 1A (callee
 * panel), 2B (one-line breadth strip in the recovery banner), 3A (report
 * position even when nothing is wrong).
 *
 * These read source text rather than mounting, deliberately.  Every defect
 * this whole change set has surfaced was WIRING, not logic: a helper
 * defined and never called (record_infra_fault), a glyph map with no
 * 'held' entry, a status tag with no 'held' case, a derivation with no
 * consumer.  Each time, the unit tests for the logic were green while the
 * feature could not possibly work.  A mount test would not have caught
 * any of them either, because the component renders fine — it just never
 * asks the question.
 */

import * as fs from 'fs';
import * as path from 'path';

const DIR = path.join(__dirname, '..');
const read = (f: string) => fs.readFileSync(path.join(DIR, f), 'utf8');

const banner = () => read('RunRecoveryBanner.tsx');
const panel = () => read('CalleeHoldPanel.tsx');
const library = () => read('TaskCardsLibrary.tsx');
const holdChain = () => read('holdChain.ts');
const css = () => read('task-card-inline-tile.css');

describe('2B — the recovery banner carries breadth on one line', () => {
  it('consults the hold derivation', () => {
    expect(banner()).toMatch(/deriveHoldChain/);
  });

  it('renders the breadth, which is the fact the rows cannot carry', () => {
    expect(banner()).toMatch(/describeBreadth/);
  });

  it('renders the remedy', () => {
    expect(banner()).toMatch(/describeGate/);
  });

  it('escalates only the fleet-wide badge, not the whole strip', () => {
    // The strip stays violet ("stopped, not broken"); only FLEET goes red.
    // If the strip itself were red the hold would read as a verdict on the
    // work, which is the misreading the violet convention exists to stop.
    expect(banner()).toMatch(/fleet_wide/);
    expect(css()).toMatch(/\.tc-recover__fleet\s*\{[^}]*#f85149/);
    expect(css()).toMatch(/\.tc-recover__breadth\s*\{[^}]*rgba\(137,87,229/);
  });

  it('stays one line — no second prose paragraph about the fault', () => {
    // 2B's whole premise: the map rows already say where the hold is, so
    // the banner must not restate it.  A call-path breadcrumb block would
    // be 2A.
    expect(banner()).not.toMatch(/call_path/);
  });

  it('degrades to the head line when no aggregate was recorded', () => {
    // A hold written before held_faults existed must not render "0 of 0".
    expect(banner()).toMatch(/holdBreadth\s*&&/);
  });
});

describe('1A — a callee resolves its own portion of the tree', () => {
  it('fetches its own callee context', () => {
    expect(panel()).toMatch(/getCalleeContext/);
  });

  it('derives through the callee-specific entry point', () => {
    // Not deriveHoldChain directly: the callee frame needs the
    // held_in_callee guard, and bypassing it is how a sibling's hold gets
    // drawn on a card that is fine.
    expect(panel()).toMatch(/deriveCalleeHoldChain/);
  });

  it('marks this card\'s own blocks with their position', () => {
    expect(panel()).toMatch(/positionOf/);
    expect(panel()).toMatch(/HELD HERE/);
    expect(panel()).toMatch(/blocked/);
  });

  it('names the caller, since the hold is not this card\'s own run', () => {
    expect(panel()).toMatch(/caller_card_id/);
  });

  it('prefers a hold that is actually this card\'s over a stale sibling', () => {
    // A card called twice in one deck could otherwise show the healthy
    // invocation and hide the held one.
    expect(panel()).toMatch(/find\(c => c\.held_in_callee\)/);
  });

  it('suppresses the row map for a single-node card', () => {
    // Same reason TaskRunMap does: one row restates the banner.
    expect(panel()).toMatch(/rows\.length > 1/);
  });

  it('renders nothing when the card has never been called', () => {
    expect(panel()).toMatch(/if \(!ctx\) return null/);
  });

  it('is mounted in the deck with the card\'s own root', () => {
    const lib = library();
    expect(lib).toMatch(/CalleeHoldPanel/);
    expect(lib).toMatch(/root=\{draft\?\.root\}/);
  });
});

describe('3A — position is reported even when nothing is wrong', () => {
  it('renders a live indicator for a non-held invocation', () => {
    // The alternative (silence unless held) is the bug we just fixed
    // reappearing as a design choice: a card executing inside another
        // card would look idle.
    expect(panel()).toMatch(/!chain\.isHeld/);
    expect(panel()).toMatch(/tc-callee--ok/);
  });

  it('styles the healthy case as information, not as a problem', () => {
    // Blue, not violet/red, and no heavy panel border.
    expect(css()).toMatch(/\.tc-callee--ok\s*\{[^}]*rgba\(31,111,235/);
  });

  it('reports the run status so "running in X" is legible', () => {
    expect(panel()).toMatch(/run_status/);
  });
});

describe('the deck no longer paints a held run as unremarkable', () => {
  it('gives held its own colour in the library status tag', () => {
    // It previously fell through to 'default' grey, indistinguishable at a
    // glance from queued — on the one surface a user checks to see whether
    // a long study is still alive.
    expect(library()).toMatch(/status === 'held' \? 'purple'/);
  });
});

describe('the callee derivation refuses to blame the wrong card', () => {
  it('returns inert unless the hold is in THIS callee\'s subtree', () => {
    const src = holdChain();
    expect(src).toMatch(/deriveCalleeHoldChain/);
    expect(src).toMatch(/!ctx\.held_in_callee\)\s*return EMPTY/);
  });

  it('reuses the full derivation rather than reimplementing it', () => {
    // The whole reason this is cheap: callee_root carries the callee's own
    // block ids, so held_at_block_id is meaningful in this frame and the
    // existing walk applies unchanged.
    const src = holdChain();
    const fn = src.slice(src.indexOf('export function deriveCalleeHoldChain'));
    expect(fn).toMatch(/return deriveHoldChain\(/);
  });
});
