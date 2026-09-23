/**
 * Wiring tests for auto-collapse (B6).
 *
 * ``autoCollapse.test.ts`` covers the POLICY; a passing pure helper
 * proves nothing about whether the tile calls it.  These assert the
 * wiring, at source level, because the behaviour under test is a
 * setTimeout whose delay depends on interaction history — driving that
 * through a rendered tile would need the whole binding/run/project
 * stack mocked to observe one timer, and would still be asserting on
 * source structure by proxy.
 *
 * Follows the convention set by runningIndicator.test.ts, which reads
 * the stylesheet directly for the same reason: some guarantees are only
 * reachable statically, and a test that cannot see them is worse than
 * one that reads the file.
 */

import * as fs from 'fs';
import * as path from 'path';

const TILE = fs.readFileSync(
  path.resolve(__dirname, '../TaskCardInlineTile.tsx'), 'utf-8',
);

describe('tile delegates the collapse decision to the helper', () => {
  it('imports the helper rather than hardcoding a delay', () => {
    expect(TILE).toMatch(/import\s*\{[^}]*decideAutoCollapse[^}]*\}\s*from\s*'\.\/autoCollapse'/);
  });

  it('no longer contains the bare 8000ms timeout', () => {
    // The original: setTimeout(() => setExpanded(false), 8000).  A
    // literal delay here means the helper is being bypassed.
    expect(TILE).not.toMatch(/setExpanded\(false\),\s*8000\)/);
  });

  it('arms the timer from the decision, not a constant', () => {
    expect(TILE).toMatch(/setTimeout\(\(\)\s*=>\s*setExpanded\(false\),\s*delayMs\)/);
  });

  it('bails when the decision says not to arm', () => {
    expect(TILE).toMatch(/if\s*\(!arm\)\s*return;/);
  });
});

describe('tile tracks engagement', () => {
  it('tracks the last interaction in a ref', () => {
    // A ref, not state: it is read only when the timer is armed, so
    // state would re-render the whole tile on every click for no
    // visible effect.
    expect(TILE).toMatch(/lastInteractionRef\s*=\s*useRef</);
  });

  it('captures interaction on the expanded container', () => {
    // Capture phase on the container, because several inner controls
    // call stopPropagation (the header's Edit button, the map's
    // resume/continue, the iteration dots) — a bubble listener would
    // miss exactly the interactions that most clearly mean "I am using
    // this".
    expect(TILE).toMatch(/onMouseDownCapture=\{noteInteraction\}/);
    expect(TILE).toMatch(/onKeyDownCapture=\{noteInteraction\}/);
  });

  it('uses mousedown rather than click so text selection counts', () => {
    // Drag-selecting trace text is reading, not clicking, and must also
    // defer the collapse.
    expect(TILE).not.toMatch(/onClickCapture=\{noteInteraction\}/);
  });

  it('re-arms the effect when an interaction happens', () => {
    // Refs do not trigger effects — the trap that makes engagement
    // tracking silently not work.  A tick in the dep list is what
    // carries the interaction through to a re-arm.
    expect(TILE).toMatch(/collapseTick/);
    const dep = TILE.match(/\}, \[isTerminal, expanded, collapseTick, run\?\.status\]\);/);
    expect(dep).not.toBeNull();
  });

  it('re-arms when the tile is re-expanded by hand', () => {
    // Without ``expanded`` in the deps, reopening a collapsed finished
    // tile would get no further auto-collapse — a bug in the other
    // direction.
    expect(TILE).toMatch(/\}, \[isTerminal, expanded,/);
  });

  it('counts a manual expand/collapse as engagement', () => {
    // The collapsed receipt's ONLY handler is onClick={toggleExpand} —
    // the onMouseDownCapture/onKeyDownCapture pair lives on the EXPANDED
    // container, so without this the receipt path never stamped the
    // interaction ref.  Opening a receipt then re-armed the effect with
    // lastInteractionAt === null (the untouched 8s delay) and the tile
    // snapped shut seconds after the user deliberately opened it.
    const fn = TILE.match(
      /const toggleExpand = useCallback\(\(\) => \{[\s\S]*?\}, \[[^\]]*\]\);/,
    );
    expect(fn).not.toBeNull();
    expect(fn![0]).toMatch(/noteInteraction\(\)/);
    expect(fn![0]).toMatch(/setExpanded\(v => !v\)/);
  });

  it('keeps the receipt wired to the engagement-aware toggle', () => {
    // If the receipt ever grew its own inline setExpanded it would
    // bypass the stamp again, so assert it still routes through the
    // shared toggle.
    const receipt = TILE.match(
      /tc-tile--receipt[\s\S]{0,400}?onClick=\{([^}]+)\}/,
    );
    expect(receipt).not.toBeNull();
    expect(receipt![1].trim()).toBe('toggleExpand');
  });
});

describe('a manual expand pins the tile open', () => {
  it('tracks the pin in a ref', () => {
    expect(TILE).toMatch(/manuallyExpandedRef\s*=\s*useRef</);
  });

  it('passes the pin to the collapse decision', () => {
    // The whole mechanism is inert unless the flag actually reaches the
    // helper, and the helper defaults it to false — so a missing
    // argument here fails open (tile keeps collapsing) rather than
    // loudly.
    const call = TILE.match(
      /decideAutoCollapse\(\s*run,[\s\S]*?\);/,
    );
    expect(call).not.toBeNull();
    expect(call![0]).toMatch(/manuallyExpandedRef\.current/);
  });

  it('sets the pin from the toggle, in both directions', () => {
    // ``= !expanded`` rather than ``= true``: a manual COLLAPSE must
    // clear the pin, or a tile closed by hand and later re-rendered
    // open would still be treated as pinned.
    const fn = TILE.match(
      /const toggleExpand = useCallback\(\(\) => \{[\s\S]*?\}, \[[^\]]*\]\);/,
    );
    expect(fn).not.toBeNull();
    expect(fn![0]).toMatch(/manuallyExpandedRef\.current\s*=\s*!expanded/);
  });

  it('keeps the ref write out of the state updater', () => {
    // A setState updater can run twice under StrictMode, so it must stay
    // free of side effects; the ref is written before setExpanded.
    expect(TILE).not.toMatch(
      /setExpanded\(v\s*=>\s*\{[\s\S]*?manuallyExpandedRef/,
    );
  });
});

describe('a user-launched attempt stays open across the tile remount', () => {
  // Retry / Continue create a new run AND a new binding; lineage
  // collapse supersedes the old binding and Conversation.tsx keys tiles
  // by binding.id, so the attempt renders in a FRESH tile whose refs are
  // empty.  Every per-instance rule (engagement, manual expand) is blind
  // to the click that launched it, and a continue that finishes in a
  // second folded the tile away under the user.

  it('imports both halves of the registry', () => {
    expect(TILE).toMatch(/import\s*\{[^}]*markUserLaunched[^}]*\}\s*from\s*'\.\/autoCollapse'/);
    expect(TILE).toMatch(/import\s*\{[^}]*takeUserLaunched[^}]*\}\s*from\s*'\.\/autoCollapse'/);
  });

  it('registers the new run from the block-level resume handler', () => {
    const fn = TILE.match(
      /const handleResumeFrom = useCallback\(async \([\s\S]*?\}, \[[^\]]*\]\);/,
    );
    expect(fn).not.toBeNull();
    expect(fn![0]).toMatch(/markUserLaunched\(res\.run\.id\)/);
    // Before the binding event: the tile that event mounts is the one
    // that claims the entry, so it must already be there.
    const mark = fn![0].indexOf('markUserLaunched(res.run.id)');
    const evt = fn![0].indexOf('TASK_BINDING_EVENT');
    expect(mark).toBeGreaterThan(-1);
    expect(evt).toBeGreaterThan(mark);
  });

  it('registers the new run from the mid-loop resume handler too', () => {
    // The two handlers are one user-facing idea at two granularities; a
    // pin that only worked for block-level resume would be a divergence
    // the user could see and not explain.
    const fn = TILE.match(
      /const handleResumeIteration = useCallback\(async \([\s\S]*?\}, \[[^\]]*\]\);/,
    );
    expect(fn).not.toBeNull();
    expect(fn![0]).toMatch(/markUserLaunched\(res\.run\.id\)/);
  });

  it('claims the launch on mount keyed to the binding run, and pins', () => {
    const eff = TILE.match(
      /useEffect\(\(\) => \{\s*if \(takeUserLaunched\(binding\.run_id\)\) \{[\s\S]*?\}, \[binding\.run_id\]\);/,
    );
    expect(eff).not.toBeNull();
    expect(eff![0]).toMatch(/manuallyExpandedRef\.current = true/);
    // Refs do not re-run effects: without the tick the collapse effect
    // could keep a timer it armed in the same commit.
    expect(eff![0]).toMatch(/setCollapseTick\(t => t \+ 1\)/);
  });

  it('claims against the BINDING run id, not the shown run id', () => {
    // The OLD tile switches its shownRunId to the new attempt before it
    // unmounts; if the claim were keyed on that, the doomed instance
    // would consume the entry and the fresh tile would find nothing.
    expect(TILE).not.toMatch(/takeUserLaunched\(shownRunId\)/);
    expect(TILE).not.toMatch(/takeUserLaunched\(run\?\.id\)/);
  });
});

describe('receipt distinguishes waiting-on-user from finished', () => {
  it('gates the held chip on awaitsUser, not controls.isHeld', () => {
    // The inverted guard this change fixes: controls.isHeld is false for
    // status==='held', so the chip never rendered for the infra-held run
    // its own comment said it was protecting.
    expect(TILE).toMatch(/\{awaitsUser\(run\) && \(/);
  });

  it('imports awaitsUser', () => {
    expect(TILE).toMatch(/import\s*\{[^}]*awaitsUser[^}]*\}\s*from\s*'\.\/autoCollapse'/);
  });

  it('no longer labels a paused run "held"', () => {
    // Two unrelated states must not share one word in the same header:
    // the status Tag already reads "held" for the infra case.
    expect(TILE).toMatch(/controls\.isAtBoundary \? 'paused' : 'pausing'/);
  });
});
