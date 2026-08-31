/**
 * The proposal panel must be able to LEARN that the user signed.
 *
 * The defect this pins (observed 2026-08-23): after "Save to deck" the
 * panel showed the sign command, the user ran it, and the panel — and its
 * Start gate — still said "unsigned":
 *   (a) the badge/gate derived from `editedScope ?? specScope`, both of
 *       which are PREVIEW readings.  The preview endpoint reports
 *       needsSignature: true by contract (no persisted ids → no signature
 *       can exist), so the panel literally could not register a signature;
 *   (b) `savedScope` — the only reading that can say "signed" — was
 *       fetched once at save time and used only to mint commands, never
 *       for the gate, and was never refreshed;
 *   (c) there was no re-check affordance and no listener on the
 *       cross-surface refresh event, so signing via the deck editor's
 *       re-check also never reached the panel.
 *
 * Static assertions for the same reason as proposalSignCommandSurfacing:
 * every part of this defect was wiring — a state variable that existed
 * but was not consulted, and listeners that were never registered.
 */

import * as fs from 'fs';
import * as path from 'path';

const COMPONENTS = path.resolve(__dirname, '..');
const PROPOSAL = fs.readFileSync(
  path.join(COMPONENTS, 'TaskCardLaunchButton.tsx'), 'utf8');

describe('the persisted reading is authoritative for the badge and gate', () => {
  it('activeScope prefers savedScope over the preview readings', () => {
    // Order matters: preview readings can never report "signed", so any
    // formulation where they win after save reintroduces the defect.
    expect(PROPOSAL).toMatch(
      /activeScope\s*=\s*savedScope\s*\?\?\s*editedScope\s*\?\?\s*specScope/);
  });

  it('Start re-reads the persisted status at the moment of truth', () => {
    // Signing happens out of band; a gate that trusts a reading taken
    // before the user signed scolds them for a state that no longer holds.
    // Sliced to the callback's end marker, not a fixed size: the original
    // 800-char window truncated when the persist-before-check fix grew
    // the gate, failing on code that still did exactly the right thing.
    const start = PROPOSAL.indexOf('const handleStart');
    const end = PROPOSAL.indexOf('if (!spec) return null', start);
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
    expect(PROPOSAL.slice(start, end)).toMatch(/refreshSavedScope\(\)/);
  });
});

describe('the panel refreshes after out-of-band signing', () => {
  it('re-checks when the window regains focus', () => {
    // The user signs in a terminal; returning focus to the browser is the
    // natural moment the panel should notice.
    expect(PROPOSAL).toMatch(/addEventListener\(\s*['"]focus['"]/);
  });

  it('listens for the cross-surface refresh event', () => {
    // A re-check in the deck editor must reach an open proposal panel.
    expect(PROPOSAL).toContain('CARD_SCOPE_REFRESH_EVENT');
    expect(PROPOSAL).toMatch(/addEventListener\(\s*CARD_SCOPE_REFRESH_EVENT/);
  });

  it('offers an explicit re-check control once the card is saved', () => {
    expect(PROPOSAL).toContain('Re-check (after signing)');
    expect(PROPOSAL).toMatch(/handleRecheck/);
  });

  it('a manual re-check broadcasts so tiles and deck badges follow', () => {
    expect(PROPOSAL).toMatch(/dispatchEvent\(new CustomEvent\(CARD_SCOPE_REFRESH_EVENT/);
  });
});

describe('the two surfaces offer the same signing vocabulary', () => {
  it('the panel shows per-block commands as well as sign-all', () => {
    // Sign-all replaced (rather than accompanied) the per-block commands,
    // while the deck editor showed only per-block — so neither surface
    // matched the other and users bounced between them.
    const allBranch = PROPOSAL.slice(PROPOSAL.indexOf('signAllCommand ? ('));
    expect(allBranch.slice(0, 1200)).toMatch(/sign blocks individually/);
  });

  it('the deck editor shows the sign-all command too', () => {
    const editor = fs.readFileSync(
      path.join(COMPONENTS, 'TaskCard', 'TaskCardEditor.tsx'), 'utf8');
    expect(editor).toContain('signAllCommand');
  });

  it('the preview modal leaves preview mode once the card is saved', () => {
    // previewMode pins the editor to the preview endpoint, which can never
    // report "signed" and deliberately hides the re-check button — correct
    // for an unsaved spec, wrong forever after Save to deck.
    expect(PROPOSAL).toMatch(/previewMode=\{!savedCardId\}/);
    expect(PROPOSAL).not.toMatch(/^\s*previewMode\s*$/m);
  });
});
