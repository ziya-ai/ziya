/**
 * Cross-project binding targeting: the tile must call the project the
 * binding LIVES IN, not the project the user happens to be viewing.
 *
 * The backend half of this seam has existed for a while:
 * list_task_bindings (app/api/task_bindings.py) resolves a global
 * chat's bindings from the chat's OWNING project and stamps
 * ``binding.project_id`` on every returned binding, with a comment
 * saying it exists "so the client targets its card / run / iteration /
 * cancel / rerun calls at the OWNING project rather than the
 * possibly-different viewing project — otherwise those follow-up reads
 * 404."  The frontend half was never connected: TaskBinding.project_id
 * was typed and documented but read by nothing, and both tile
 * components hardcoded ``currentProject?.id`` — so from a non-owning
 * project every follow-up call (card fetch, signature status, run
 * stream, cancel/pause/resume/step, ask answer, resume-from, rerun)
 * 404'd.  Classic defined-but-never-called seam defect.
 *
 * Static source assertions, following stagedCopyWiring.test.ts: what
 * breaks here is the JOIN (the field being consumed at all), which no
 * amount of unit-testing either half detects.
 */

import * as fs from 'fs';
import * as path from 'path';

const read = (rel: string) =>
  fs.readFileSync(path.join(__dirname, '..', '..', '..', rel), 'utf8');

const TILE = () => read('components/TaskCard/TaskCardInlineTile.tsx');
const TYPES = () => read('types/task_binding.ts');

/** Extract one component's body from the tile file. */
const componentBody = (name: string): string => {
  const src = TILE();
  const start = src.indexOf(`const ${name}`);
  expect(start).toBeGreaterThan(-1);
  // Component bodies in this file are delimited by the next top-level
  // ``const X: React.FC`` declaration or EOF.  Good enough for a
  // source-seam assertion; we only need the projectId derivation,
  // which sits in the first few lines of each component.
  const rest = src.slice(start + 1);
  const next = rest.search(/\nconst \w+: React\.FC/);
  return src.slice(start, next === -1 ? undefined : start + 1 + next);
};

describe('TaskBinding.project_id is declared (backend stamps it)', () => {
  it('the type carries project_id', () => {
    expect(TYPES()).toMatch(/interface TaskBinding[\s\S]*?project_id\?: string/);
  });
});

describe('both tile components consume binding.project_id', () => {
  it('LaunchedCardTile prefers the binding-owning project', () => {
    expect(componentBody('LaunchedCardTile')).toMatch(
      /const projectId = binding\.project_id \?\? currentProject\?\.id \?\? ''/,
    );
  });

  it('StagedCardTile prefers the binding-owning project', () => {
    expect(componentBody('StagedCardTile')).toMatch(
      /const projectId = binding\.project_id \?\? currentProject\?\.id \?\? ''/,
    );
  });

  it('no component in the tile file still ignores the binding project', () => {
    // The historical bug shape: deriving projectId from the viewing
    // project alone.  Any reappearance of this exact derivation in
    // this file re-breaks the seam for whichever component carries it.
    expect(TILE()).not.toMatch(
      /const projectId = currentProject\?\.id \?\? ''/,
    );
  });
});
