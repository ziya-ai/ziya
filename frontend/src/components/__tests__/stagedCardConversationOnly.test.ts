/**
 * Cards staged by the model (`task_card_stage`) are CONVERSATION-ONLY by
 * default: persisted as unlisted drafts so their tile, run and bindings
 * resolve them, but absent from the permanent deck.  Models are pushed to
 * stage sub-task cards freely to parallelize work and keep the parent
 * context small; each of those becoming a deck entry was noise.
 *
 * This pins the wiring across the seam the change crosses:
 *   - the tool creates with draft unless asked to persist;
 *   - the inline staged tile can PROMOTE (update draft:false) rather than
 *     leaving the only route to the deck a re-create with fresh ids;
 *   - the deck editor, reached by id from a tile's Edit backlink, shows
 *     the same promote affordance for an unlisted card;
 *   - the model's bound listing includes drafts, so it can find what it
 *     staged.
 *
 * Static assertions, matching the sibling proposal*.test.ts files — the
 * behaviour is a flag declared in Python and honoured in TSX.
 */

import * as fs from 'fs';
import * as path from 'path';

const COMPONENTS = path.resolve(__dirname, '..');
const REPO = path.resolve(COMPONENTS, '..', '..', '..');

const TILE = fs.readFileSync(
  path.join(COMPONENTS, 'TaskCard', 'TaskCardInlineTile.tsx'), 'utf8');
const LIBRARY = fs.readFileSync(
  path.join(COMPONENTS, 'TaskCard', 'TaskCardsLibrary.tsx'), 'utf8');
const STAGE_PY = fs.readFileSync(
  path.join(REPO, 'app', 'mcp', 'tools', 'task_card_stage.py'), 'utf8');
const TOOLS_PY = fs.readFileSync(
  path.join(REPO, 'app', 'mcp', 'tools', 'task_card_tools.py'), 'utf8');

function section(src: string, startMarker: string): string {
  const i = src.indexOf(startMarker);
  expect(i).toBeGreaterThanOrEqual(0);
  return src.slice(i);
}

describe('task_card_stage lifecycle', () => {
  it('declares a persist flag that defaults to conversation-only', () => {
    expect(STAGE_PY).toMatch(/persist:\s*bool\s*=\s*Field\(\s*False/);
    expect(STAGE_PY).toMatch(/draft=as_draft/);
    expect(STAGE_PY).toMatch(/"conversation_only":\s*as_draft/);
  });

  it('bound listing includes drafts so the model can find what it staged', () => {
    const body = section(TOOLS_PY, 'class TaskCardListTool');
    expect(body).toMatch(/storage\.list\(include_drafts=bound_ids is not None\)/);
    expect(body).toContain('"conversation_only"');
  });
});

describe('StagedCardTile promote affordance', () => {
  const staged = section(TILE, 'const StagedCardTile');

  it('promotes in place with draft:false rather than re-creating', () => {
    expect(staged).toMatch(/taskCardApi\.update\([^)]*\{\s*draft:\s*false\s*\}/);
    expect(staged).not.toContain('taskCardApi.create(');
  });

  it('labels a conversation-only card and offers Save to deck', () => {
    expect(staged).toContain('conversation-only');
    expect(staged).toContain('Save to deck');
  });
});

describe('deck editor promote affordance', () => {
  it('offers to add an unlisted card to the deck without duplicating it', () => {
    // Scope to the promote handler: the editor's Save sends the same
    // field set WITHOUT draft (editing is not filing), so the assertion
    // is that the promote path is an UPDATE carrying draft: false — not
    // a create, which would mint fresh block ids and strand a signature.
    const start = LIBRARY.indexOf('const handleAddToDeck');
    expect(start).toBeGreaterThan(-1);
    const handler = LIBRARY.slice(start, LIBRARY.indexOf('}, [', start));
    expect(handler).toMatch(/taskCardApi\.update\(/);
    expect(handler).toMatch(/draft:\s*false/);
    expect(handler).not.toMatch(/taskCardApi\.create\(/);
    expect(LIBRARY).toContain('Add to deck');
  });
});
