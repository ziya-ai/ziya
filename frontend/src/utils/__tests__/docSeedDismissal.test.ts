import { resolveDocSeed } from '../docSeedDismissal';

describe('resolveDocSeed', () => {
  it('seeds everything on a genuinely fresh tab', () => {
    const { additions, nextSeeded } = resolveDocSeed(
      ['README.md', 'AGENTS.md'], [], new Set(),
    );
    expect(additions).toEqual(['README.md', 'AGENTS.md']);
    expect([...nextSeeded].sort()).toEqual(['AGENTS.md', 'README.md']);
  });

  it('does NOT re-add a file the user unchecked (the reported bug)', () => {
    // README was seeded on a previous load and is absent now -> dismissed.
    const { additions } = resolveDocSeed(
      ['README.md', 'AGENTS.md'],
      ['AGENTS.md'],
      new Set(['README.md', 'AGENTS.md']),
    );
    expect(additions).toEqual([]);
  });

  it('keeps a dismissed key in the seeded record so it stays dismissed', () => {
    const { nextSeeded } = resolveDocSeed(
      ['README.md'], [], new Set(['README.md']),
    );
    expect(nextSeeded.has('README.md')).toBe(true);
  });

  it('does not duplicate a file that is already checked', () => {
    const { additions } = resolveDocSeed(
      ['README.md'], ['README.md'], new Set(),
    );
    expect(additions).toEqual([]);
  });

  it('records a hand-checked file as seeded so a later uncheck is honoured', () => {
    // The user checked README themselves; we add nothing, but must remember
    // it, or unchecking it later would look like "never seen" and re-add.
    const first = resolveDocSeed(['README.md'], ['README.md'], new Set());
    expect(first.additions).toEqual([]);
    expect(first.nextSeeded.has('README.md')).toBe(true);

    const afterUncheck = resolveDocSeed(
      ['README.md'], [], first.nextSeeded,
    );
    expect(afterUncheck.additions).toEqual([]);
  });

  it('still seeds a NEW doc file alongside a dismissed one', () => {
    // Dismissing README must not suppress a newly-appeared AGENTS.md.
    const { additions } = resolveDocSeed(
      ['README.md', 'docs/AGENTS.md'],
      [],
      new Set(['README.md']),
    );
    expect(additions).toEqual(['docs/AGENTS.md']);
  });

  it('is idempotent when re-run against its own output', () => {
    const first = resolveDocSeed(['README.md', 'AGENTS.md'], [], new Set());
    const second = resolveDocSeed(
      ['README.md', 'AGENTS.md'], first.additions, first.nextSeeded,
    );
    expect(second.additions).toEqual([]);
    expect([...second.nextSeeded].sort()).toEqual([...first.nextSeeded].sort());
  });

  it('collapses duplicate keys in the server response', () => {
    const { additions } = resolveDocSeed(
      ['README.md', 'README.md'], [], new Set(),
    );
    expect(additions).toEqual(['README.md']);
  });

  it('coerces numeric keys (checkedKeys is React.Key[])', () => {
    const { additions } = resolveDocSeed([42], [42], new Set());
    expect(additions).toEqual([]);
  });

  it('skips empty keys rather than putting them in the selection', () => {
    const { additions, nextSeeded } = resolveDocSeed(
      ['', 'README.md'], [], new Set(),
    );
    expect(additions).toEqual(['README.md']);
    expect(nextSeeded.has('')).toBe(false);
  });

  it('returns no additions for an empty server response', () => {
    const { additions } = resolveDocSeed([], ['README.md'], new Set());
    expect(additions).toEqual([]);
  });

  it('does not mutate its inputs', () => {
    const checked = ['AGENTS.md'];
    const seeded = new Set(['README.md']);
    resolveDocSeed(['README.md', 'AGENTS.md', 'new.md'], checked, seeded);
    expect(checked).toEqual(['AGENTS.md']);
    expect([...seeded]).toEqual(['README.md']);
  });

  it('handles external-path keys unchanged', () => {
    const key = '[external]/opt/shared/AGENTS.md';
    expect(resolveDocSeed([key], [], new Set()).additions).toEqual([key]);
    expect(resolveDocSeed([key], [], new Set([key])).additions).toEqual([]);
  });

  describe('end-to-end reported scenario', () => {
    // Reproduces the full uncheck -> refresh -> re-seed cycle, and pins the
    // difference between the old union and the new decision.
    const serverKeys = ['README.md', 'AGENTS.md'];

    it('old union behaviour re-added the dismissed file', () => {
      // Negative control: the pre-fix logic, so the test demonstrates the
      // defect rather than merely asserting the fix agrees with itself.
      const checkedAfterUncheck = ['AGENTS.md'];
      const existing = new Set(checkedAfterUncheck.map(String));
      const oldAdditions = serverKeys.filter(k => !existing.has(String(k)));
      expect(oldAdditions).toEqual(['README.md']);
    });

    it('new behaviour leaves the dismissed file out', () => {
      // load 1: seed both
      const load1 = resolveDocSeed(serverKeys, [], new Set());
      expect(load1.additions).toEqual(['README.md', 'AGENTS.md']);

      // user unchecks README; sessionStorage keeps AGENTS.md + the record
      const checkedAfterUncheck = ['AGENTS.md'];

      // load 2 (refresh)
      const load2 = resolveDocSeed(
        serverKeys, checkedAfterUncheck, load1.nextSeeded,
      );
      expect(load2.additions).toEqual([]);

      // load 3: still dismissed, not resurrected by repetition
      const load3 = resolveDocSeed(
        serverKeys, checkedAfterUncheck, load2.nextSeeded,
      );
      expect(load3.additions).toEqual([]);
    });
  });
});
