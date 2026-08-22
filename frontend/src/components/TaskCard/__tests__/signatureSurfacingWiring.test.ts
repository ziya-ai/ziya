/**
 * Static wiring guards for signature surfacing.
 *
 * The defect these pin was invisible to behavioural tests: the status
 * endpoint, its types, and the staged tile were all correct and fully
 * covered, while the LAUNCHED tile simply never asked.  Nothing was
 * broken; a call site was missing.  These assertions therefore read the
 * source and check the call sites exist, which is the only formulation
 * that fails when someone adds a third surface and forgets again.
 *
 * A SECOND defect motivated the strengthening below.  A two-part patch
 * (add the shared hook, delete the old hand-rolled fetch) applied only
 * its delete half, leaving `const [unsignedCount, setUnsignedCount] =
 * useState(0)` with the setter never called — so the staged tile's
 * badge, its notice, and its pre-run confirmation were all permanently
 * dead while the code still compiled and read plausibly.  A dead
 * useState is the failure shape to guard against explicitly: unlike a
 * missing call site it leaves every consumer intact, so slice-based
 * "does this component mention the hook" checks can be satisfied by a
 * sibling component's reference.  `assertNoDeadUnsignedState` is
 * therefore file-wide and slice-free.
 */

import fs from 'fs';
import path from 'path';

const dir = path.resolve(__dirname, '..');
const read = (f: string) => fs.readFileSync(path.join(dir, f), 'utf8');

/**
 * Body of a top-level `const <Name>` component, up to the next one.
 *
 * Slicing to the next component keeps a sibling's call site from
 * satisfying an assertion about this one — the exact leak that let a
 * broken staged tile pass a launched-tile guard.
 */
function componentBody(src: string, name: string): string {
  const start = src.indexOf(`const ${name}`);
  if (start < 0) throw new Error(`component not found in source: ${name}`);
  const rest = src.slice(start + name.length);
  const next = rest.search(/\nconst [A-Z]\w*(?::\s*React\.FC|\s*=\s*\()/);
  return next > 0 ? rest.slice(0, next) : rest;
}

const TILES = ['LaunchedCardTile', 'StagedCardTile'] as const;

describe('every card-showing surface consults signature status', () => {
  it.each(TILES)('%s uses the shared hook', (tile) => {
    const body = componentBody(read('TaskCardInlineTile.tsx'), tile);
    expect(body).toContain('useCardSignatureStatus');
  });

  it.each(TILES)('%s does not hand-roll its own status fetch', (tile) => {
    // A per-tile copy is how the two tiles drifted in the first place.
    const body = componentBody(read('TaskCardInlineTile.tsx'), tile);
    expect(body).not.toContain('taskCardApi.scopeStatus');
  });

  it('no tile holds signature state in a never-updated useState', () => {
    // THE regression guard.  File-wide and slice-free on purpose: a
    // half-applied patch left this setter declared and never called,
    // freezing unsignedCount at 0 so every consumer silently died.
    // Scoped checks missed it because the surviving sibling's hook
    // reference satisfied them.
    const src = read('TaskCardInlineTile.tsx');
    expect(src).not.toContain('setUnsignedCount');
    expect(src).not.toMatch(/unsignedCount\s*,\s*set\w+\s*\]\s*=\s*useState/);
  });

  it('the launched tile renders a signing warning, not just a fetch', () => {
    // Fetching without rendering satisfies the hook guard above while
    // leaving the user exactly as uninformed as before.
    const body = componentBody(read('TaskCardInlineTile.tsx'), 'LaunchedCardTile');
    expect(body).toContain('needsSigning');
    expect(body).toContain('tc-staged-signing-notice');
  });

  it('the launched tile badges signature state in its collapsed header', () => {
    // The notice lives in the tile BODY, which is hidden when collapsed.
    // Without a header badge a collapsed running tile says nothing about
    // being clamped — the state this whole change exists to surface.
    const body = componentBody(read('TaskCardInlineTile.tsx'), 'LaunchedCardTile');
    const header = body.slice(body.indexOf('tc-tile__header'));
    expect(header.slice(0, header.indexOf('tc-tile__body'))).toContain('unsignedCount');
  });

  it.each(TILES)('%s surfaces the count it fetched', (tile) => {
    // Reading the hook and then never rendering the value is the same
    // user-visible outcome as not reading it at all.
    const body = componentBody(read('TaskCardInlineTile.tsx'), tile);
    expect(body).toContain('unsignedCount');
  });
});

describe('out-of-band signing is broadcast', () => {
  it('the editor fires a refresh event after re-checking', () => {
    const src = read('TaskCardEditor.tsx');
    expect(src).toContain('CARD_SCOPE_REFRESH_EVENT');
    // Must be inside the refresh path, not merely imported.
    const idx = src.indexOf('CARD_SCOPE_REFRESH_EVENT', src.indexOf('refreshScopeStatus'));
    expect(idx).toBeGreaterThan(0);
  });

  it('the editor does not broadcast for an unsaved preview spec', () => {
    // A draft has no persisted id, so a broadcast would tell other
    // surfaces to re-check a card that does not exist.
    const src = read('TaskCardEditor.tsx');
    const at = src.indexOf('CARD_SCOPE_REFRESH_EVENT', src.indexOf('refreshScopeStatus'));
    expect(src.slice(Math.max(0, at - 400), at)).toContain('!previewMode');
  });

  it('the hook listens for the refresh event it defines', () => {
    // The event is only useful if something acts on it; an emitter with
    // no listener is indistinguishable from the stale-badge defect.
    const src = read('useCardSignatureStatus.ts');
    expect(src).toContain('addEventListener(CARD_SCOPE_REFRESH_EVENT');
    expect(src).toContain('removeEventListener(CARD_SCOPE_REFRESH_EVENT');
  });
});
