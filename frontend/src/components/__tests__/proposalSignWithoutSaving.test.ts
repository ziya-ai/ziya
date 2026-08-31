/**
 * A proposal must be signable WITHOUT being filed in the Task Cards deck.
 *
 * The defect this pins: signing requires a PERSISTED card, because an
 * approval keys on a block id and ids are assigned by
 * TaskCardStorage.create.  That requirement was conflated with deck
 * membership — handleSaveToDeck was documented as "the ONLY route to
 * signing a proposal" — so the only way to sign was to file the card
 * somewhere the user did not want it.
 *
 * The fix separates the two: the Sign path persists the card as an
 * unlisted DRAFT (signable, runnable, hidden from every deck listing),
 * and only an explicit "Save to deck" promotes it.
 *
 * Static assertions rather than a mount, matching the sibling
 * proposal*.test.ts files: every part of this is wiring across a seam —
 * a flag declared in one language and honoured in another, a create call
 * that must carry it, and an update that must promote rather than
 * duplicate.  A render test would need a project context, a message-id
 * context and three fetch mocks and would still not pin "create is
 * called with draft: true".
 */

import * as fs from 'fs';
import * as path from 'path';

const COMPONENTS = path.resolve(__dirname, '..');
const REPO = path.resolve(COMPONENTS, '..', '..', '..');

const PROPOSAL = fs.readFileSync(
  path.join(COMPONENTS, 'TaskCardLaunchButton.tsx'), 'utf8');
const TYPES = fs.readFileSync(
  path.resolve(COMPONENTS, '..', 'types', 'task_card.ts'), 'utf8');
const MODELS_PY = fs.readFileSync(
  path.join(REPO, 'app', 'models', 'task_card.py'), 'utf8');
const STORAGE_PY = fs.readFileSync(
  path.join(REPO, 'app', 'storage', 'task_cards.py'), 'utf8');
const CARDS_API_PY = fs.readFileSync(
  path.join(REPO, 'app', 'api', 'task_cards.py'), 'utf8');

/** The body of a named useCallback, up to its dependency array. */
const handlerBody = (src: string, name: string): string => {
  const start = src.indexOf(`const ${name} = useCallback`);
  expect(start).toBeGreaterThan(-1);
  const end = src.indexOf('}, [', start);
  expect(end).toBeGreaterThan(start);
  return src.slice(start, end);
};

describe('the panel offers signing that does not touch the deck', () => {
  it('has a sign-preparation handler distinct from save-to-deck', () => {
    expect(PROPOSAL).toContain('const handleSignPrep');
    expect(PROPOSAL).toContain('const handleSaveToDeck');
  });

  it('persists as a DRAFT on that path', () => {
    // ensureCard(spec, true) — the boolean IS the deck/no-deck decision.
    expect(handlerBody(PROPOSAL, 'handleSignPrep'))
      .toMatch(/ensureCard\([^)]*,\s*true\s*\)/);
  });

  it('still fetches the by-id status there, since that is what mints the command', () => {
    // The preview endpoint returns signCommand: "" by contract, and the
    // by-id endpoint additionally STAGES the decrypted scope the
    // out-of-process signer needs.  A sign path that skipped it would
    // leave the signer without input.
    expect(handlerBody(PROPOSAL, 'handleSignPrep'))
      .toContain('taskCardApi.scopeStatus(');
  });

  it('surfaces the action in the button strip', () => {
    expect(PROPOSAL).toMatch(/onClick=\{\(\)\s*=>\s*void handleSignPrep\(\)\}/);
  });
});

describe('the draft/deck distinction is honoured end to end', () => {
  it('ensureCard passes the flag through to create', () => {
    expect(handlerBody(PROPOSAL, 'ensureCard'))
      .toMatch(/draft:\s*asDraft/);
  });

  it('save-to-deck promotes the existing draft rather than creating a twin', () => {
    // A second create assigns fresh block ids, stranding the signature
    // just obtained against the draft's ids.
    const body = handlerBody(PROPOSAL, 'ensureCard');
    expect(body).toMatch(/draft:\s*false/);
    expect(body).toContain('taskCardApi.update(');
  });

  it('launch reuses ensureCard instead of its own create/update pair', () => {
    const body = handlerBody(PROPOSAL, 'handleLaunch');
    expect(body).toContain('ensureCard(');
    expect(body).not.toContain('taskCardApi.create(');
  });

  it('the TS types declare the flag the API sends', () => {
    // Sending draft: true against a type that does not declare it is the
    // silent half of this seam — tsc would reject it, but only if the
    // object literal is typed, so pin the declaration itself.
    expect(TYPES).toMatch(/export interface TaskCardCreate\s*\{[\s\S]*?draft\?:\s*boolean/);
    expect(TYPES).toMatch(/export interface TaskCardUpdate\s*\{[\s\S]*?draft\?:\s*boolean/);
    expect(TYPES).toMatch(/export interface TaskCard\s*\{[\s\S]*?draft\?:\s*boolean/);
  });

  it('the python models accept it on create and on update', () => {
    expect(MODELS_PY).toMatch(/class TaskCardCreate[\s\S]*?draft:\s*bool\s*=\s*False/);
    expect(MODELS_PY).toMatch(/class TaskCardUpdate[\s\S]*?draft:\s*Optional\[bool\]/);
  });

  it('storage persists it and hides drafts from listings', () => {
    expect(STORAGE_PY).toMatch(/draft=data\.draft/);
    expect(STORAGE_PY).toMatch(/include_drafts/);
  });

  it('the list ENDPOINT hides them too', () => {
    // A storage-only filter that the API then bypassed would put drafts
    // straight back in the deck — the exact seam a per-layer change misses.
    expect(CARDS_API_PY).toMatch(/include_drafts/);
  });
});

describe('the notice points at the control that exists', () => {
  it('does not tell the user to save to the deck in order to sign', () => {
    // The old fallback read "Use Save to deck to save it to the deck — the
    // exact ziya-approve command appears here once the card has ids".
    // That instruction is what this change removes the need for.
    const start = PROPOSAL.indexOf('Requires signing');
    expect(start).toBeGreaterThan(-1);
    const notice = PROPOSAL.slice(start);
    expect(notice).not.toMatch(/to save it to the deck/);
  });
});
