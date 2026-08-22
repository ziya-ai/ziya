import { mergeSkillIds, updateDefaultSkillIds } from '../skillPersistence';

describe('durable project skill activation', () => {
  it('restores project defaults when the browser lens is empty', () => {
    expect(mergeSkillIds(
      ['builtin-continuous-documentation', 'builtin-tests-for-everything'],
      [],
    )).toEqual([
      'builtin-continuous-documentation',
      'builtin-tests-for-everything',
    ]);
  });

  it('preserves browser-local selections without duplicating project defaults', () => {
    expect(mergeSkillIds(['tests', 'docs'], ['docs', 'custom'])).toEqual([
      'tests', 'docs', 'custom',
    ]);
  });

  it('adds an enabled skill to project defaults without mutating the input', () => {
    const original = ['docs'];
    expect(updateDefaultSkillIds(original, 'tests', true)).toEqual(['docs', 'tests']);
    expect(original).toEqual(['docs']);
  });

  it('does not duplicate an already-enabled project default', () => {
    expect(updateDefaultSkillIds(['docs', 'tests'], 'tests', true)).toEqual([
      'docs', 'tests',
    ]);
  });

  it('removes a disabled skill from project defaults', () => {
    expect(updateDefaultSkillIds(['docs', 'tests'], 'tests', false)).toEqual(['docs']);
  });
});
