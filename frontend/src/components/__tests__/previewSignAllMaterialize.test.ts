/**
 * A proposal's PREVIEW must offer the same working "sign all" the deck does.
 *
 * The defect this pins (reported 2026-09-01): the Preview of an unsaved,
 * AI-authored task card showed "signatures required" but no single sign-all
 * command — while viewing the same card in the Task Cards deck DID. The cause
 * was architectural, not cosmetic:
 *
 *   - The sign-all command is `sudo ziya-approve --task <card_id> ... --all`
 *     and the out-of-process signer READS the card file off disk, so it needs
 *     a persisted card_id AND persisted block ids.
 *   - An unsaved preview has neither (ids are assigned by
 *     TaskCardStorage.create), so the stateless /scope-preview endpoint
 *     returns `signAllCommand: ""` BY CONTRACT — there is nothing runnable to
 *     show.
 *
 * The fix (Option 1): opening Preview on an ESCALATING unsaved proposal first
 * materializes the card behind the scenes (create → by-id scope-status),
 * silently, so the modal opens with a real id in non-preview mode and renders
 * the deck's sign-all surface with a runnable command. This is the SEAM —
 * "the preview materializes before it opens" — that a per-surface unit test
 * of either half would miss.
 *
 * Static assertions rather than a mount, matching proposalSignCommandSurfacing:
 * every part of this is wiring across surfaces (an endpoint that stages + mints
 * vs. one that cannot, a preview that must persist before it can display),
 * which a render test would obscure behind context and fetch mocks.
 */

import * as fs from 'fs';
import * as path from 'path';

const COMPONENTS = path.resolve(__dirname, '..');
const readComponent = (f: string) =>
  fs.readFileSync(path.join(COMPONENTS, f), 'utf8');

const PROPOSAL = readComponent('TaskCardLaunchButton.tsx');
const EDITOR = readComponent('TaskCard/TaskCardEditor.tsx');
const CARDS_API_PY = fs.readFileSync(
  path.resolve(COMPONENTS, '..', '..', '..', 'app', 'api', 'task_cards.py'), 'utf8');

describe('the preview endpoint genuinely cannot mint a sign-all command', () => {
  it('scope-preview returns an empty signAllCommand by contract', () => {
    // This is WHY the preview must persist before it can offer sign-all —
    // not a bug to be flipped on in the frontend. If this ever stops being
    // "", revisit whether materialize-on-preview is still necessary.
    const preview = CARDS_API_PY.slice(CARDS_API_PY.indexOf('preview_card_scope'));
    expect(preview).toMatch(/"signAllCommand":\s*""/);
  });
});

describe('opening Preview materializes an escalating proposal', () => {
  it('has a dedicated silent materialize path (no "Saved to deck" toast)', () => {
    expect(PROPOSAL).toContain('materializeForSigning');
    const fn = PROPOSAL.slice(
      PROPOSAL.indexOf('const materializeForSigning'),
      PROPOSAL.indexOf('const openPreview'));
    // It creates the card and fetches the by-id status (which mints the
    // runnable signAllCommand AND stages the scope the signer reads)...
    expect(fn).toMatch(/taskCardApi\.create\(/);
    expect(fn).toMatch(/taskCardApi\.scopeStatus\(/);
    // ...but does NOT announce a save — the whole point is "still preview".
    expect(fn).not.toMatch(/message\.success/);
  });

  it('openPreview persists BEFORE opening, gated on escalation', () => {
    const open = PROPOSAL.slice(
      PROPOSAL.indexOf('const openPreview'),
      PROPOSAL.indexOf('const openPreview')
        + PROPOSAL.slice(PROPOSAL.indexOf('const openPreview')).indexOf('setShowPreview(true)'));
    // Only escalating (needsSigning) proposals materialize — a clean card
    // must not be silently dropped into the deck just for being previewed.
    expect(open).toMatch(/needsSigning/);
    expect(open).toMatch(/await materializeForSigning\(\)/);
    // The modal opens with whatever id materialize produced, so the editor
    // reads by-id status rather than the preview reading.
    expect(open).toMatch(/id:\s*cardId/);
  });

  it('reuses savedCardId so it never creates a second copy', () => {
    // Approvals key on block id; a fresh create assigns fresh ids and would
    // strand the signature the user just obtained on the first card.
    expect(PROPOSAL).toMatch(/if\s*\(savedCardId\)\s*return savedCardId;/);
  });

  it('surfaces materialization as button loading, not a frozen click', () => {
    expect(PROPOSAL).toMatch(/setPreparing\(true\)/);
    expect(PROPOSAL).toMatch(/setPreparing\(false\)/);
    expect(PROPOSAL).toMatch(/loading=\{preparing\}/);
  });
});

describe('the materialized preview shows the deck sign-all surface', () => {
  it('the modal editor leaves preview mode once a real id exists', () => {
    // previewMode={!savedCardId}: materialize sets savedCardId, so the
    // editor switches to by-id status and its non-preview branch renders.
    expect(PROPOSAL).toMatch(/previewMode=\{!savedCardId\}/);
  });

  it('the editor renders signAllCommand outside preview mode', () => {
    // This is the exact block the deck shows; the fix routes the preview
    // through it rather than duplicating a second sign-all surface.
    expect(EDITOR).toContain('signAllCommand');
    expect(EDITOR).toMatch(/scopeStatus\.signAllCommand/);
  });
});
