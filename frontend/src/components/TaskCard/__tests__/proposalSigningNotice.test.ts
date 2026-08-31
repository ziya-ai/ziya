/**
 * Static wiring guards for the task-card signing affordances.
 *
 * Why static rather than render tests: the defects were WIRING, not logic.
 * Every unit test on the scope-status endpoint passed while the proposal
 * block never asked about escalation at all, and while the preview modal
 * asked the wrong endpoint and swallowed the 404. Reproducing that in a
 * render test needs a mounted MarkdownRenderer subtree, a project context
 * and a fetch mock per surface, and would still not pin the specific
 * requirement — that these four launch paths cannot start an unsigned card
 * silently. The structural facts are cheaper to pin directly and fail
 * loudly if someone re-routes a launch around the gate.
 */

import * as fs from 'fs';
import * as path from 'path';

const readSrc = (rel: string) =>
  fs.readFileSync(path.resolve(__dirname, '..', '..', rel), 'utf8');
const readHere = (f: string) =>
  fs.readFileSync(path.resolve(__dirname, '..', f), 'utf8');

const PROPOSAL = readSrc('TaskCardLaunchButton.tsx');
const EDITOR = readHere('TaskCardEditor.tsx');
const LIBRARY = readHere('TaskCardsLibrary.tsx');
const TILE = readHere('TaskCardInlineTile.tsx');
const API = fs.readFileSync(
  path.resolve(__dirname, '..', '..', '..', 'services', 'taskCardApi.ts'), 'utf8');

describe('escalation is resolved server-side, never guessed client-side', () => {
  it('the api client exposes scopePreview', () => {
    expect(API).toContain('async scopePreview(');
    expect(API).toContain('/scope-preview');
  });

  it('no surface reimplements the floor subtraction', () => {
    // `.ziya/` and `/tmp/` are floor safe-write paths and the shell floor
    // is the base allowlist minus destructive/interpreter commands. A
    // client-side copy of either would false-alarm on floor-covered
    // grants — or miss a real one, which is the direction that matters.
    for (const src of [PROPOSAL, EDITOR, LIBRARY, TILE]) {
      expect(src).not.toContain('safe_write_paths');
      expect(src).not.toContain('destructive_commands');
    }
  });
});

describe('the proposal block declares signing before the user commits', () => {
  it('asks the preview endpoint for the parsed spec', () => {
    expect(PROPOSAL).toContain('taskCardApi.scopePreview(');
  });

  it('renders a needs-signing notice outside the Preview modal', () => {
    // Load-bearing: a user who trusts the proposal and clicks Start never
    // opens Preview, so a notice only inside the modal is invisible to
    // exactly the person who most needs it.
    expect(PROPOSAL).toContain('Requires signing');
    expect(PROPOSAL).toMatch(/needsSigning\s*&&/);
  });

  it('routes Start through the confirm gate, not straight to launch', () => {
    expect(PROPOSAL).toContain('const handleStart');
    expect(PROPOSAL).toContain('Run without signed permissions?');
    // The primary button must call the gate. If this regresses to
    // handleLaunch the warning becomes decorative.
    expect(PROPOSAL).toMatch(/onClick=\{handleStart\}/);
  });

  it('the gate warns but does not refuse', () => {
    // authorize_scope clamps to the floor rather than failing the launch,
    // so refusing here would be a behavior change, not a warning.
    expect(PROPOSAL).toContain('Run anyway');
  });
});

describe('a proposal can be persisted without being run', () => {
  it('offers a save-without-launch action', () => {
    expect(PROPOSAL).toContain('const handleSaveToDeck');
    expect(PROPOSAL).toMatch(/Save to deck/);
  });

  it('reuses the saved card on a later Start', () => {
    // Save-then-Start must not leave two copies: the user would sign one
    // and run the other, and the run would still be clamped to the floor
    // with nothing on screen explaining why.
    //
    // Asserted on the BRANCH, not on a helper name.  A previous revision
    // pinned `const ensureCard` — an extraction that was never made — so
    // this test failed while the behaviour it protects was present and
    // correct, which is worse than no test: it trains the next reader to
    // ignore the red.  Written this way it holds whether the reuse rule
    // stays inline or is later hoisted into a shared helper, and still
    // fails the moment any create becomes unconditional.
    expect(PROPOSAL).toContain('savedCardId');

    // Every create in this file is the fallback leg of a savedCardId
    // branch, with the reuse (update) path as its sibling.  The length
    // check is the positive control: without it, deleting every create
    // would satisfy the loop vacuously.
    const creates = [...PROPOSAL.matchAll(/taskCardApi\.create\(/g)];
    expect(creates.length).toBeGreaterThan(0);
    for (const m of creates) {
      const before = PROPOSAL.slice(Math.max(0, m.index! - 600), m.index!);
      expect(before).toMatch(/savedCardId/);
      expect(before).toMatch(/taskCardApi\.update\(/);
    }

    // And the launch path resolves the card before binding, rather than
    // binding an id it never reconciled against the saved copy.
    const launch = PROPOSAL.match(
      /const handleLaunch = useCallback\([\s\S]*?\n  \}, \[[^\]]*\]\);/);
    expect(launch).not.toBeNull();
    expect(launch![0]).toMatch(/savedCardId[\s\S]{0,140}taskCardApi\.update\(/);
    expect(launch![0]).toMatch(/createBinding\(/);
  });

  it('tags proposals from ONE shared constant so the deck can group them', () => {
    // Two independent literals would silently stop matching if either
    // changed, and the failure mode is invisible: proposals just quietly
    // stop grouping.
    expect(PROPOSAL).toContain("export const PROPOSED_TAG = 'proposed'");
    expect(LIBRARY).toMatch(/import\s*\{\s*PROPOSED_TAG\s*\}\s*from\s*'\.\.\/TaskCardLaunchButton'/);
    expect(LIBRARY).not.toContain("PROPOSED_TAG = 'proposed'");
  });
});

describe('the live preview reports escalation instead of 404-ing', () => {
  it('the editor accepts previewMode and branches on it', () => {
    expect(EDITOR).toContain('previewMode');
    expect(EDITOR).toMatch(/previewMode\s*\n?\s*\?\s*await taskCardApi\.scopePreview/);
  });

  it('the guard no longer lets a synthetic draft id reach the by-id endpoint', () => {
    // 'draft' is truthy, so the old `!card.id` guard passed it straight
    // through to /task-cards/draft/scope-status.
    expect(EDITOR).toContain('(!previewMode && !card.id)');
  });

  it('the proposal modal passes previewMode', () => {
    expect(PROPOSAL).toMatch(/previewMode/);
  });

  it('the editor re-checks when card-level scope changes', () => {
    // A card-level grant is a layer of every leaf's effective scope, so
    // editing it changes the escalation; keying only on root left the
    // banner stale.
    expect(EDITOR).toContain('JSON.stringify(card.scope)');
  });

  it('the preview hides the re-check affordance', () => {
    // No approval can exist for ids that were never assigned, so re-check
    // could only ever say "still unsigned" and would read as breakage.
    expect(EDITOR).toContain('!scopeStatus.preview');
  });

  it('an absent sign command renders nothing, not an empty code box', () => {
    // The preview endpoint returns signCommand: "" because no persisted
    // block id exists to sign against. Rendering that unguarded produces an
    // empty monospace box that reads as a command the UI failed to load.
    expect(EDITOR).toMatch(/\{b\.signCommand\s*&&\s*\(/);
  });
});

describe('every launch path is gated', () => {
  it('the deck confirms before launching an unsigned card', () => {
    expect(LIBRARY).toContain('const confirmIfUnsigned');
    expect(LIBRARY).toContain('Run without signed permissions?');
    expect(LIBRARY).toMatch(/handleLaunchCurrent[\s\S]{0,400}confirmIfUnsigned/);
    expect(LIBRARY).toMatch(/handleLaunchNew[\s\S]{0,400}confirmIfUnsigned/);
  });

  it('the staged goal tile warns and confirms', () => {
    // Staging exists precisely so permissions can be granted before work
    // starts; a Run that does not mention signing defeats its purpose.
    //
    // Updated 2026-08-24 to follow the gate's refactor: it now re-reads
    // signature status at click time (fetchFresh) and confirms through
    // Modal.confirm like the proposal panel and deck launch buttons,
    // replacing the stale-count window.confirm this originally pinned.
    expect(TILE).toContain('tc-staged-signing-notice');
    expect(TILE).toMatch(/gateUnsigned === 0[\s\S]{0,400}Modal\.confirm/);
  });

  it('all surfaces read needsSignature rather than each deriving it', () => {
    for (const src of [PROPOSAL, LIBRARY, TILE]) {
      expect(src).toContain('needsSignature');
    }
  });
});
