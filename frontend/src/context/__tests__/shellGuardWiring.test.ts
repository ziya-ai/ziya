/**
 * Static wiring guard for the SHELL_GUARD recovery path in ChatContext.
 *
 * WHY STATIC RATHER THAN A RENDER TEST
 *
 * The decision logic lives in utils/shellRecovery.ts and is unit-tested
 * directly (see utils/__tests__/shellRecovery.test.ts, 23 cases).  Every one
 * of those cases would pass while this bug was live, because the defect was
 * never in the decision -- it was in the CALL SITE: the old guard computed
 * its own completeness test inline (`_fullMessageCount || 0 > length`), which
 * read "count unknown" and "count 0" as *complete*, so a truncated shell fell
 * through to a blind append.  Reproducing that in jsdom means driving
 * setConversations through a useCallback, an async recovery, two injected
 * data tiers and a React state commit, for assertions whose real content is
 * "this branch is reached and this line is absent".
 *
 * These tests are deliberately about STRUCTURE.  They will not catch a wrong
 * comparison inside recoverShellMessages; the unit suite does that.  They
 * catch the three ways this specific area regressed or could regress:
 *
 *   1. A helper that exists but is never invoked (the pure module imported
 *      and then bypassed by an inline predicate).
 *   2. The queued messages being DELETED on a failed recovery -- the silent
 *      message-loss defect.  The queue is the only place those messages
 *      exist; they are not in IndexedDB and not on the server.
 *   3. The shell markers surviving an append.  A record flagged `_isShell`
 *      is dropped by every `nonShells` IDB write filter, so it never
 *      persists; the next sync then finds no local row, takes
 *      shouldFetchFull's `!local` branch, and (gated solely on
 *      recentlyFetchedFullIds) never pulls again.
 *
 * Anchored on identifiers and comment markers, never on line numbers or a
 * fixed-size byte window.
 */

import * as fs from 'fs';
import * as path from 'path';

const CTX = fs.readFileSync(
  path.resolve(__dirname, '..', 'ChatContext.tsx'), 'utf8');

/**
 * Strip `//` line comments.
 *
 * Load-bearing, not cosmetic.  The guard's own comment QUOTES the old buggy
 * predicate (`_fullMessageCount || 0`) to document what changed, so a raw
 * substring search for it reports a false positive against correct code.
 * Verified: the string is present in the block and absent from its code.
 */
const codeOnly = (s: string): string =>
  s.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

/**
 * Body of the SHELL_GUARD branch: from its opening comment to the
 * "Return unchanged" marker that ends it.
 */
const guardBlock = (): string => {
  const start = CTX.indexOf('SHELL_GUARD: If the conversation is still a shell');
  expect(start).toBeGreaterThan(-1);
  const end = CTX.indexOf('Return unchanged', start);
  expect(end).toBeGreaterThan(start);
  return CTX.slice(start, end);
};

/**
 * Body of the `hold` branch inside the recovery continuation: from the
 * outcome test to the `const toApply` that begins the APPLY path.
 *
 * Anchored on `const toApply` rather than on the first `return;` because the
 * hold branch's own early return is that first `return;` -- slicing there
 * would cut the body off before the assertions below can see it.
 */
const holdBranch = (): string => {
  const g = codeOnly(guardBlock());
  const start = g.indexOf("outcome.action === 'hold'");
  expect(start).toBeGreaterThan(-1);
  const end = g.indexOf('const toApply', start);
  expect(end).toBeGreaterThan(start);
  return g.slice(start, end);
};

/**
 * The APPLY path: everything from `const toApply` to the end of the guard.
 * This is the branch that legitimately consumes the queue.
 */
const applyPath = (): string => {
  const g = codeOnly(guardBlock());
  const start = g.indexOf('const toApply');
  expect(start).toBeGreaterThan(-1);
  return g.slice(start);
};

/**
 * The normal (non-shell) append object literal -- the fall-through reached
 * when the guard does NOT arm.
 */
const appendLiteral = (): string => {
  const start = CTX.indexOf('hasUnreadResponse: shouldMarkUnread,');
  expect(start).toBeGreaterThan(-1);
  const end = CTX.indexOf('title: deriveTitle', start);
  expect(end).toBeGreaterThan(start);
  return CTX.slice(start, end);
};

describe('SHELL_GUARD wiring — module seam', () => {
  it('imports the extracted recovery module', () => {
    expect(CTX).toMatch(
      /import\s*\{[^}]*recoverShellMessages[^}]*\}\s*from\s*'\.\.\/utils\/shellRecovery'/);
    expect(CTX).toMatch(
      /import\s*\{[^}]*isKnownCompleteShell[^}]*\}\s*from\s*'\.\.\/utils\/shellRecovery'/);
  });

  it('aliases antd message, since the local `message` param shadows it', () => {
    // addMessageToConversation's first parameter is `message: Message`, so the
    // antd export is unreachable by that name inside the guard.
    expect(CTX).toMatch(/message as uiMessage.*from 'antd'/);
    const params = CTX.slice(CTX.indexOf('const addMessageToConversation'));
    expect(params.slice(0, 160)).toContain('message: Message');
  });

  it('imports antd message exactly once (no duplicate-import lint hit)', () => {
    const antdImports = CTX.match(/^import .*from 'antd';$/gm) || [];
    expect(antdImports).toHaveLength(1);
  });
});

describe('SHELL_GUARD wiring — arming condition', () => {
  it('arms on the extracted predicate, not an inline count comparison', () => {
    const code = codeOnly(guardBlock());
    expect(code).toContain('isKnownCompleteShell(');
    // Negated: the guard must arm when completeness is NOT proven.
    expect(code).toMatch(/!\s*isKnownCompleteShell\(/);
  });

  it('no longer computes completeness with `_fullMessageCount || 0`', () => {
    // The defect: `|| 0` made an ABSENT count read as 0, and `0 > 31` is
    // false, so the guard did not arm and a truncated shell was appended to.
    expect(codeOnly(guardBlock())).not.toContain('_fullMessageCount || 0');
  });

  it('still gates on _isShell (positive control — guard not always-on)', () => {
    expect(codeOnly(guardBlock())).toContain('_isShell');
  });

  it('actually invokes the recovery helper', () => {
    // The prior failure mode in this file's sibling suites: a pure helper
    // imported, unit-tested, and never called.
    expect(codeOnly(guardBlock())).toContain('recoverShellMessages(');
  });

  it('supplies both data tiers to the recovery', () => {
    const code = codeOnly(guardBlock());
    expect(code).toContain('getIdbRecord');
    expect(code).toContain('getServerChat');
    // The server tier is reached routinely, not only on IDB corruption: a
    // _isShell record is excluded from every IDB write, so it may have no row.
    expect(code).toMatch(/getServerChat:\s*\(p,\s*id\)\s*=>\s*syncApi\.getChat\(/);
  });
});

describe('SHELL_GUARD wiring — failed recovery must not lose messages', () => {
  it('does NOT delete the pending queue on a hold outcome', () => {
    // THE defect this suite exists for.  The old code deleted the queue
    // unconditionally and re-applied only on success, so when
    // db.getConversation returned null the queued human and assistant
    // messages were discarded with no error anywhere.
    expect(holdBranch()).not.toContain('queue.delete');
  });

  it('does not silently swallow a hold — the user is told', () => {
    expect(holdBranch()).toContain('uiMessage.error');
  });

  it('returns early on hold rather than falling into the apply path', () => {
    expect(holdBranch()).toMatch(/return;/);
  });

  it('DOES consume the queue on a successful recovery (positive control)', () => {
    // Pairs with the "does not happen" assertion above: proves the queue is
    // genuinely consumed somewhere, so the hold assertion cannot pass by the
    // deletion having simply been removed altogether.
    expect(applyPath()).toContain('queue.delete');
  });

  it('tracks in-flight recovery in a dedicated ref, not via queue length', () => {
    // Deriving "already loading" from a non-empty queue (the old
    // `alreadyLoading = pending.length > 0`) is incompatible with holding:
    // held messages would make the queue permanently non-empty and block
    // every subsequent recovery attempt that could deliver them.
    expect(CTX).toContain('shellRecoveryInFlight');
    const code = codeOnly(guardBlock());
    expect(code).not.toContain('alreadyLoading');
    expect(code).toMatch(/inFlight\.has\(/);
    expect(code).toMatch(/inFlight\.add\(/);
  });

  it('clears the in-flight marker on every exit path', () => {
    // Without a finally, a thrown recovery latches the flag and no later
    // send can ever retry -- the same one-way-latch class as the plugin
    // load-latch defect.
    expect(codeOnly(guardBlock())).toMatch(/\.finally\(\s*\(\)\s*=>\s*\{\s*inFlight\.delete\(/);
  });

  it('still queues the arriving message (positive control)', () => {
    expect(codeOnly(guardBlock())).toContain('queue.set(conversationId');
  });

  it('dedupes recovered-vs-queued messages on role+content', () => {
    // The recovered body can already contain a queued message (another
    // window pushed it), which would otherwise double it.
    expect(applyPath()).toMatch(/b\.role === m\.role && b\.content === m\.content/);
  });
});

describe('SHELL_GUARD wiring — markers cleared so the record can persist', () => {
  it('clears both shell markers on the normal append', () => {
    // Reaching the append means the guard did not arm, so the messages are
    // full.  Keeping `_isShell` excludes the record from every nonShells IDB
    // write -- it never persists, and the next sync has no local row to
    // compare against the server.
    const lit = appendLiteral();
    expect(lit).toContain('_isShell: false');
    expect(lit).toContain('_fullMessageCount: undefined');
  });

  it('clears both shell markers on the recovery apply path', () => {
    const apply = applyPath();
    expect(apply).toContain('_isShell: false');
    expect(apply).toContain('_fullMessageCount: undefined');
  });

  it('bumps _version on the recovery apply so the push filter sees the change', () => {
    expect(applyPath()).toMatch(/_version:\s*Date\.now\(\)/);
  });
});
