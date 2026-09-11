/**
 * Seam guards for the outline layout.
 *
 * The outline exists as a component and the editor has a layout prop;
 * these assert that the two are actually connected — and that the deck
 * (not the chat proposal panel) is what opts in.  Source-text guards,
 * matching deckRunLinksWiring.test.ts: a full mount of TaskCardEditor
 * drags in every block editor and the scope-status fetch, and would
 * still not pin WHICH caller passes the prop.
 */

import * as fs from 'fs';
import * as path from 'path';

const DIR = path.resolve(__dirname, '..');
const read = (f: string) => fs.readFileSync(path.join(DIR, f), 'utf8');

const editor = () => read('TaskCardEditor.tsx');
const library = () => read('TaskCardsLibrary.tsx');
const runMap = () => read('TaskRunMap.tsx');
const launch = () => fs.readFileSync(path.resolve(DIR, '..', 'TaskCardLaunchButton.tsx'), 'utf8');

describe('the editor mounts the outline when asked', () => {
  it('accepts a layout prop and branches on it', () => {
    const src = editor();
    expect(src).toMatch(/layout\?: 'tree' \| 'outline'/);
    expect(src).toMatch(/layout === 'outline'/);
    expect(src).toMatch(/<BlockOutline/);
  });

  it('edits the selected block through the shared tree mutators', () => {
    // Not a private re-implementation: the same helpers the drag layer
    // and container editors use, so the outline pane and the tree
    // layout cannot disagree about how a by-id edit lands.
    const src = editor();
    expect(src).toMatch(/updateBlockById\(card\.root, selectedBlock\.id/);
    expect(src).toMatch(/removeBlockById\(card\.root, selectedBlock\.id/);
  });

  it('offers a top-level add, since the invisible root has no row', () => {
    expect(editor()).toMatch(/appendChildBlock\(card\.root, card\.root\.id/);
  });

  it('keeps the drag provider around the pane', () => {
    // A container block's children still drag within the pane; the
    // move must apply to the root, not to a detached copy.
    const src = editor();
    const outlineBranch = src.slice(src.indexOf("layout === 'outline'"));
    expect(outlineBranch).toMatch(/<TaskCardDragProvider root=\{card\.root\}/);
  });
});

describe('who opts in', () => {
  it('the deck uses the outline layout', () => {
    expect(library()).toMatch(/layout="outline"/);
  });

  it('the chat proposal editor keeps the tree', () => {
    // Read-once cards want every block visible; only the revisit-and-edit
    // surface trades that for a spine.
    expect(launch()).not.toMatch(/layout="outline"/);
  });
});

describe('one glyph table', () => {
  it('TaskRunMap no longer owns a private STATUS_GLYPHS', () => {
    const src = runMap();
    expect(src).not.toMatch(/const STATUS_GLYPHS/);
    expect(src).toMatch(/STATUS_GLYPHS[\s\S]{0,80}from '\.\/runMapModel'/);
  });
});
