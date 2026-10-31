/**
 * The cwd-remapping step of a task-card import: the pure helpers behind
 * ImportCwdMapModal, plus wiring assertions that TaskCardsLibrary actually
 * inspects the file and routes the user's map into importCards (the seam
 * that makes a modal without a caller useless).
 */
import * as fs from 'fs';
import * as path from 'path';
import {
  applyToAll, buildCwdMap, initialTargets, mappedCount,
} from '../importCwdMap';

const read = (rel: string) =>
  fs.readFileSync(path.join(__dirname, '..', rel), 'utf8');

describe('importCwdMap helpers', () => {
  const cwds = ['/old/one', '/old/two'];

  it('starts every path unmapped and sends nothing for blanks', () => {
    const t = initialTargets(cwds);
    expect(t).toEqual({ '/old/one': '', '/old/two': '' });
    expect(buildCwdMap(t)).toEqual({});
    expect(mappedCount(t)).toBe(0);
  });

  it('applyToAll is the 1:1 case', () => {
    const t = applyToAll(cwds, '/new/here');
    expect(buildCwdMap(t)).toEqual({ '/old/one': '/new/here', '/old/two': '/new/here' });
    expect(mappedCount(t)).toBe(2);
  });

  it('a whitespace-only target counts as unmapped (server reads "" as clear)', () => {
    expect(buildCwdMap({ '/old/one': '   ', '/old/two': ' /n ' }))
      .toEqual({ '/old/two': '/n' });
  });
});

describe('import wiring', () => {
  const lib = read('TaskCardsLibrary.tsx');
  const api = read('../../services/taskCardApi.ts');

  it('TaskCardsLibrary inspects the file before importing and mounts the modal', () => {
    expect(lib).toMatch(/taskCardApi\.inspectImport\(projectId, payload\)/);
    expect(lib).toMatch(/import \{ ImportCwdMapModal \} from '\.\/ImportCwdMapModal'/);
    expect(lib).toMatch(/<ImportCwdMapModal/);
    // The user's choice reaches the import call.
    expect(lib).toMatch(/taskCardApi\.importCards\(projectId, payload, cwdMap\)/);
  });

  it('taskCardApi wraps the body only when a map is given', () => {
    expect(api).toMatch(/\/import\/inspect/);
    expect(api).toMatch(/\{ payload, cwd_map: cwdMap \} : payload/);
  });
});
