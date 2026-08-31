/**
 * The inline tiles and the shared status hook must finish the signing
 * story the proposal panel started.
 *
 * Defects pinned (observed 2026-08-24, follow-on from
 * proposalSignatureRefresh.test.ts):
 *   (a) both inline tiles' signing notices said "open Task Cards for the
 *       ziya-approve command" — the same go-to-a-second-surface dead end
 *       just fixed in the proposal panel — while the scopeStatus they
 *       already fetch carries per-block signCommand and signAllCommand,
 *       neither of which was rendered;
 *   (b) useCardSignatureStatus refreshed on mount and on the broadcast
 *       event but not on window focus, so a user returning from the
 *       terminal after signing saw every tile stale; only the proposal
 *       panel had a focus listener, and only locally — the exact
 *       per-surface drift the hook exists to prevent;
 *   (c) the proposal panel's Start gate re-read status BEFORE persisting
 *       preview-modal edits, so an edit that changed the scope after
 *       save+sign was graded against the OLD stored scope — the gate
 *       stayed silent and the run clamped later with no warning.
 *
 * Static assertions, same rationale as the sibling suites: each defect is
 * a missing call site or an unrendered field, which is the shape a mount
 * test cannot pin without reproducing the entire fetch/context harness.
 */

import * as fs from 'fs';
import * as path from 'path';

const TC = path.resolve(__dirname, '..');
const TILE = fs.readFileSync(path.join(TC, 'TaskCardInlineTile.tsx'), 'utf8');
const HOOK = fs.readFileSync(
  path.join(TC, 'useCardSignatureStatus.ts'), 'utf8');
const PROPOSAL = fs.readFileSync(
  path.resolve(TC, '..', 'TaskCardLaunchButton.tsx'), 'utf8');

/** Body of a top-level `const <Name>` component (same slicing rule as
 *  signatureSurfacingWiring.test.ts, for the same anti-leak reason). */
function componentBody(src: string, name: string): string {
  const start = src.indexOf(`const ${name}`);
  if (start < 0) throw new Error(`component not found in source: ${name}`);
  const rest = src.slice(start + name.length);
  const next = rest.search(/\nconst [A-Z]\w*(?::\s*React\.FC|\s*=\s*\()/);
  return next > 0 ? rest.slice(0, next) : rest;
}

describe('inline tiles surface sign commands in place', () => {
  it('imports the shared copyable CommandBlock', () => {
    expect(TILE).toMatch(/import\s+\{\s*CommandBlock\s*\}/);
  });

  it('renders the sign-all command when the server minted one', () => {
    expect(TILE).toContain('signAllCommand');
  });

  it('renders per-block signCommand otherwise', () => {
    expect(TILE).toMatch(/\bb\.signCommand\b/);
  });

  it('guards the empty-string signCommand so no blank box renders', () => {
    expect(TILE).toMatch(/b\.signCommand\s*&&/);
  });

  it('no longer points at the deck as the only route to the command', () => {
    // "Open the card in Task Cards for the ziya-approve command" was the
    // instruction that dead-ended into a second surface.  Comments are
    // stripped first: the fix itself quotes the removed sentence in the
    // SignCommands doc comment, and a comment cannot dead-end a user.
    const stripComments = (s: string) =>
      s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
    expect(stripComments(TILE)).not.toMatch(/[Oo]pen (the card in )?Task Cards for the/);
  });

  it('the staged tile reads full status, not just the count', () => {
    // Commands live on status.blocks/signAllCommand; a count alone cannot
    // render them.
    const body = componentBody(TILE, 'StagedCardTile');
    expect(body).toMatch(/\{\s*unsignedCount\s*,\s*status/);
  });

  it('both tiles render through one shared command component', () => {
    // Two hand-rolled copies of the command markup is the drift shape
    // this file's siblings keep re-pinning.
    const launched = componentBody(TILE, 'LaunchedCardTile');
    const staged = componentBody(TILE, 'StagedCardTile');
    expect(launched).toMatch(/<SignCommands\b/);
    expect(staged).toMatch(/<SignCommands\b/);
  });
});

describe('the shared hook re-checks on window focus', () => {
  // Signing happens in a terminal: the user must leave the window to do
  // it and come back to use the result, so refocus is the natural
  // "did it land?" trigger — for EVERY consumer, not just the proposal
  // panel's local copy.
  it('registers a focus listener', () => {
    expect(HOOK).toMatch(/addEventListener\(\s*['"]focus['"]/);
  });

  it('and removes it on cleanup', () => {
    expect(HOOK).toMatch(/removeEventListener\(\s*['"]focus['"]/);
  });

  it('guarded so an idle consumer does not fetch on every alt-tab', () => {
    // The listener must not register when there is nothing to ask about.
    const focusEffect = HOOK.slice(
      HOOK.indexOf("addEventListener('focus'") - 400,
      HOOK.indexOf("addEventListener('focus'"));
    expect(focusEffect).toMatch(/if\s*\(\s*!projectId\s*\|\|\s*!cardId\s*\)\s*return/);
  });
});

describe('the staged tile Run gate matches the other launch paths', () => {
  // The proposal panel's Start and the deck's launch buttons both re-read
  // signature status at click time and confirm through Modal.confirm.
  // The staged tile gated on a possibly-stale hook reading and used
  // window.confirm — the one browser-native dialog in the whole flow.
  const staged = () => componentBody(TILE, 'StagedCardTile');

  it('no longer uses window.confirm', () => {
    // Match the CALL, not the phrase: the replacement code's own comment
    // legitimately names window.confirm when explaining why it is gone.
    expect(staged()).not.toMatch(/window\.confirm\s*\(/);
  });

  it('confirms through Modal.confirm like every other gate', () => {
    expect(staged()).toMatch(/Modal\.confirm\(/);
    expect(TILE).toMatch(/import\s*\{[^}]*\bModal\b[^}]*\}\s*from\s*'antd'/);
  });

  it('re-reads signature status at the moment Run is clicked', () => {
    // Signing happens out of band; a gate trusting the mount-time (or
    // even focus-time) reading can still scold a user who signed after
    // the last refresh.  The fresh read comes from the shared hook —
    // the wiring suite separately forbids tiles hand-rolling fetches.
    //
    // Sliced to the NEXT handler, not to the first '};' — handleRun
    // nests a doLaunch arrow function whose closing '};' comes before
    // the fetchFresh call.
    const body = staged();
    expect(body).toMatch(/fetchFresh/);
    const run = body.slice(
      body.indexOf('const handleRun'),
      body.indexOf('const handleDiscard'));
    expect(run).toMatch(/await\s+fetchFresh\(\)/);
    // And the gate DECIDES on the fresh reading, not merely fetches it.
    expect(run).toMatch(/countUnsigned\(fresh\)/);
  });

  it('the hook exposes the fresh read and returns the reading', () => {
    // A refresh() that only bumps a nonce cannot serve a click-time gate:
    // the caller needs the fresh reading NOW, not after a re-render.
    expect(HOOK).toMatch(/fetchFresh/);
    expect(HOOK).toMatch(/fetchFresh[\s\S]{0,400}return st/);
  });
});

describe('the Start gate grades the card as it would launch', () => {
  it('persists preview edits before re-reading signature status', () => {
    // Checking first grades the OLD stored scope: an edit that adds
    // escalation after save+sign passes the gate, then the launch path's
    // own update changes the scope hash and the run clamps silently.
    const start = PROPOSAL.indexOf('const handleStart');
    expect(start).toBeGreaterThan(-1);
    const body = PROPOSAL.slice(start, PROPOSAL.indexOf('Modal.confirm', start));
    const upd = body.indexOf('taskCardApi.update');
    const refresh = body.indexOf('refreshSavedScope');
    expect(upd).toBeGreaterThan(-1);
    expect(refresh).toBeGreaterThan(upd);
  });
});
