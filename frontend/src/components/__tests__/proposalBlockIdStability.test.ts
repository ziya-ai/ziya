/**
 * A signature the user just obtained must survive clicking Start.
 *
 * The defect this pins (observed 2026-08-27): in the chat proposal panel the
 * user did Save to deck → copied the `sudo ziya-approve` command → signed in
 * a terminal → came back (focus re-check cleared the "needs signing" notice)
 * → clicked Start, and the panel flipped straight back to unsigned.
 *
 * Cause was id churn, not a stale reading.  Approvals key on PERSISTED block
 * ids, and TaskCardStorage.update mints a fresh id for any block that arrives
 * WITHOUT one.  The panel kept re-sending `root` from the tree parsed out of
 * the message (`makeDraftCard(spec)` / `spec`), which carries no ids — so
 * every Save/Start renamed all of the card's blocks and orphaned the records
 * signed against the previous generation.  Evidence at the time: one card,
 * five distinct scope hashes, and three sign rounds 19:40/19:41/19:42 that
 * produced 51 approval records — the same scopes re-signed under three
 * generations of block ids, 34 of which matched no block in any card.
 *
 * Static assertions, like the sibling proposal tests: the whole defect is
 * wiring — which tree the component hands back on each write.  A behavioural
 * render test here would have to stand up four React contexts to observe one
 * request body.
 */

import * as fs from 'fs';
import * as path from 'path';

const COMPONENTS = path.resolve(__dirname, '..');
const PROPOSAL = fs.readFileSync(
  path.join(COMPONENTS, 'TaskCardLaunchButton.tsx'), 'utf8');

describe('the proposal panel remembers the persisted block tree', () => {
  it('tracks the stored root in state', () => {
    expect(PROPOSAL).toMatch(/const\s*\[\s*persistedRoot\s*,\s*setPersistedRoot\s*\]/);
  });

  it('adopts the tree the server echoes back after a write', () => {
    // The response root is the same content plus the assigned ids, so
    // adopting it is lossless and is the only place those ids exist.
    expect(PROPOSAL).toMatch(/adoptPersistedRoot\s*=\s*useCallback/);
    expect(PROPOSAL).toMatch(/setPersistedRoot\(card\.root\)/);
  });
});

describe('every write path sends the persisted ids back', () => {
  it('Save/Update in deck adopts the update response', () => {
    // Both branches: the create branch used to adopt only the card id and
    // leave the id-less root in place, which is what made the SECOND save
    // rename everything.
    expect(PROPOSAL).toMatch(
      /const updated = await taskCardApi\.update\([\s\S]{0,300}?adoptPersistedRoot\(updated\)/);
    expect(PROPOSAL).toMatch(
      /const card = await taskCardApi\.create\([\s\S]{0,400}?adoptPersistedRoot\(card\)/);
  });

  it('the launch path adopts the card it just wrote', () => {
    expect(PROPOSAL).toMatch(
      /setSavedCardId\(card\.id\);\s*\n\s*adoptPersistedRoot\(card\);/);
  });

  it('the Start pre-check update adopts its response', () => {
    // Start persists preview edits before grading the stored card.  That
    // write is the one the user actually hit: unadopted, it renamed the
    // blocks microseconds before the status read that then said "unsigned".
    expect(PROPOSAL).toMatch(
      /const updated = await taskCardApi\.update\([\s\S]{0,300}?adoptPersistedRoot\(updated\)/);
  });

  it('currentSpec prefers the persisted root over the parsed spec root', () => {
    expect(PROPOSAL).toMatch(/persistedRoot\s*\)?\s*return\s*\{\s*\.\.\.spec,\s*root:\s*persistedRoot\s*\}/);
    expect(PROPOSAL).toMatch(/\}, \[previewCard, spec, persistedRoot\]\)/);
  });

  it('re-opening the preview rebuilds from the persisted root', () => {
    // openPreview rebuilt the draft from the message spec every time, so
    // "sign, then open the card to look at it, then Start" churned the ids
    // even when nothing was edited.
    expect(PROPOSAL).toMatch(/root:\s*persistedRoot\s*\?\?\s*draft\.root/);
  });
});
