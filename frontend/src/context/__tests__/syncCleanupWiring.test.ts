/**
 * Structural guard on syncWithServer's `finally` block.
 *
 * WHY STATIC RATHER THAN A RENDER TEST
 *
 * The defect is a control-flow shape, not a computation: a bare `return`
 * inside a `finally` skips the statements below it in that same block.
 * Three cleanup statements sit at the bottom of this one:
 *
 *   setIsProjectSwitching(false)              — releases the sidebar spinner
 *   setHasLoadedConversations(true)           — releases the loading state
 *   periodicSyncInFlightRef.current = false   — releases the poll re-entry gate
 *
 * The last is the damaging one.  It is armed before the `try` and cleared
 * only here, so any path that returns out of the `finally` leaves it armed
 * forever: every later 30s tick then returns at the in-flight guard and
 * cross-instance updates stop arriving for the life of the page.  The only
 * log is a `console.debug`, invisible at the default console level.
 *
 * Reproducing that in jsdom would mean driving a full project switch, an
 * epoch bump racing an awaited IDB read, and 60s of interval ticks — for an
 * assertion whose real content is "no statement returns out of this block".
 * Asserting the shape is both cheaper and more direct, and unlike a
 * behavioural test it also catches a *future* return being added.
 */

import * as fs from 'fs';
import * as path from 'path';

const CTX = fs.readFileSync(
  path.resolve(__dirname, '..', 'ChatContext.tsx'), 'utf8');

/**
 * The body of syncWithServer's `finally`, from the rehydrate comment that
 * opens it through the last cleanup statement.
 *
 * Anchored on the cleanup statement itself rather than a closing brace: the
 * block contains several nested closures, so a brace-counting slice would
 * end at the first inner `}` and cut the cleanup out of view — which would
 * make the assertions below pass against code that had lost it.
 */
const syncFinallyBlock = (): string => {
  const start = CTX.indexOf('Re-hydrate the active conversation if it');
  expect(start).toBeGreaterThan(-1);
  const marker = 'periodicSyncInFlightRef.current = false;';
  const end = CTX.indexOf(marker, start);
  expect(end).toBeGreaterThan(start);
  return CTX.slice(start, end + marker.length);
};

/** Same block with `//` line comments stripped, so prose cannot match. */
const syncFinallyCode = (): string =>
  syncFinallyBlock()
    .split('\n')
    .filter((l) => !l.trim().startsWith('//'))
    .join('\n');

describe('syncWithServer finally — cleanup is unconditional', () => {
  it('contains no bare `return` that could skip the cleanup', () => {
    // The defect, stated directly.  Pre-patch this block held exactly one
    // (`if (isStale()) return;`) sitting above all three cleanup statements.
    expect(syncFinallyCode()).not.toMatch(/\breturn\s*;/);
  });

  it('contains no value-returning statement either', () => {
    // A `return x;` in a finally is the same hazard wearing a different
    // shape, and would additionally override the outer completion value.
    expect(syncFinallyCode()).not.toMatch(/\breturn\s+[^;\n]/);
  });

  it('still clears the periodic in-flight gate', () => {
    // Positive control for the two absence checks above: deleting the
    // cleanup entirely would satisfy them.
    expect(syncFinallyCode()).toMatch(/periodicSyncInFlightRef\.current\s*=\s*false/);
  });

  it('still releases the project-switch spinner and the loaded flag', () => {
    expect(syncFinallyCode()).toContain('setIsProjectSwitching(false)');
    expect(syncFinallyCode()).toContain('setHasLoadedConversations(true)');
  });

  it('still performs the post-sync rehydrate (block is not merely empty)', () => {
    // Second positive control: the absence assertions must be passing
    // because the returns were converted, not because the work vanished.
    const code = syncFinallyCode();
    // Anchored ONLY on parts that predate this fix.  An earlier revision
    // also asserted syncApi.getChat (the server-fallback tier added by the
    // separate hydration-wedge change), which made this control fail against
    // a tree lacking that change -- a control that fails for a reason
    // unrelated to what it guards is not a control.
    expect(code).toContain('db.getConversation');
    expect(code).toContain('setConversations');
  });

  it('keeps the server-side fallback tier in the rehydrate', () => {
    // NOT a control: asserts the hydration-wedge fix specifically
    // (IDB-unusable -> ask the server).  Expected to fail on a tree where
    // that separate change is absent, so its failure meaning stays distinct
    // from the control above.
    expect(syncFinallyCode()).toContain('syncApi.getChat');
  });


  it('still guards the rehydrate against a stale epoch', () => {
    // The `return`s existed for a reason — a stale sync must not rehydrate
    // the previous project's conversation.  Converting them to conditions
    // must preserve that guard, not drop it.
    expect(syncFinallyCode()).toMatch(/isStale\(\)/);
  });

  it('gates every isStale check as a condition, never as a return', () => {
    const code = syncFinallyCode();
    // Each occurrence must be part of a boolean test (`!isStale()` in an
    // `if`/`&&`), not the predicate of an early exit.
    expect(code).not.toMatch(/isStale\(\)\s*\)\s*return/);
    expect(code).not.toMatch(/if\s*\(\s*isStale\(\)\s*\)\s*\{?\s*return/);
  });

  it('arms the in-flight gate before the try, not inside it', () => {
    // If the flag were set inside the `try`, a throw before that line would
    // reach the `finally` and clear a flag that was never set — masking the
    // re-entrancy the guard exists to provide.  Ordering is the invariant.
    const armIdx = CTX.indexOf('if (isPeriodicTick) periodicSyncInFlightRef.current = true;');
    const tryIdx = CTX.indexOf('try {', armIdx);
    expect(armIdx).toBeGreaterThan(-1);
    expect(tryIdx).toBeGreaterThan(armIdx);
  });
});
