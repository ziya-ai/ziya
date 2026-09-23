/**
 * The staged tile must show the FULL card — the block tree plus a
 * per-block definition pane — and offer editing before launch, rather
 * than the old thin collapsed-instructions blob.
 *
 * Source-assertion test (mirrors tileSignCommandSurfacing.test.ts): the
 * defect this guards against is purely a rendering-wiring one, so we
 * assert against the StagedCardTile body in source rather than mounting
 * the whole polling/streaming tile.
 */
import fs from 'fs';
import path from 'path';

const TILE = fs.readFileSync(
  path.join(__dirname, '..', 'TaskCardInlineTile.tsx'), 'utf8');

/** Slice one component's body out of the file by its declaration. */
function componentBody(src: string, name: string): string {
  const start = src.indexOf(`const ${name}:`);
  expect(start).toBeGreaterThanOrEqual(0);
  // Bounded by the next top-level `const X: React.FC` or the default export.
  const rest = src.slice(start + name.length + 10);
  const nextDecl = rest.search(/\nconst \w+: React\.FC|\nexport default/);
  return rest.slice(0, nextDecl >= 0 ? nextDecl : undefined);
}

describe('StagedCardTile full inspection', () => {
  const staged = componentBody(TILE, 'StagedCardTile');

  it('renders the shared BlockOutline over the card root', () => {
    // The whole structure, via the same renderer the editor/run views use
    // (can't drift) — not a name + collapsed instructions blob.
    expect(TILE).toMatch(/import\s*\{\s*BlockOutline\s*\}\s*from '\.\/BlockOutline'/);
    expect(staged).toMatch(/<BlockOutline\b/);
    expect(staged).toMatch(/root=\{card\.root\}/);
    // edit mode: a not-yet-run card has no per-block status to paint.
    expect(staged).toMatch(/mode="edit"/);
  });

  it('drives a definition pane from the selected block', () => {
    expect(staged).toMatch(/blockConfigLines\(selectedBlock\)/);
    expect(staged).toMatch(/selectedBlockId/);
    expect(staged).toMatch(/findBlockById\(card\.root, selectedBlockId\)/);
  });

  it('offers Edit before Go via the existing card-open backlink', () => {
    expect(staged).toMatch(/TASK_CARD_OPEN_EVENT/);
    expect(staged).toMatch(/cardId:\s*binding\.card_id/);
    expect(staged).toMatch(/>\s*Edit\s*</);
  });

  it('keeps Run / Discard as the launch gate', () => {
    expect(staged).toMatch(/onClick=\{handleRun\}/);
    expect(staged).toMatch(/onClick=\{handleDiscard\}/);
  });

  it('no longer falls back to the thin collapsed-instructions view', () => {
    // The old presentation: a lone <details> wrapping card instructions.
    expect(staged).not.toMatch(/<summary><strong>Instructions<\/strong><\/summary>/);
  });
});
