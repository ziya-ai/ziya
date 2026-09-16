/**
 * Behavioural check of the classification the sidebar consistency check
 * performs, extracted verbatim in shape: given the full list, the active
 * subset and the selected id, which of {consistent, absent, inactive} is it,
 * and is a repeat render of the same state re-reported?
 *
 * Both the retired and the current shape are modelled so the test
 * demonstrates the bug (absent id → error on every render) rather than only
 * the fix.
 */
type Conv = { id: string; isActive?: boolean };

function retiredShape(convs: Conv[], currentId: string | null, log: string[]) {
  const active = convs.filter(c => c.isActive !== false);
  if (currentId && !active.find(c => c.id === currentId)) {
    log.push('error:HISTORY_CORRUPTION');
    log.push('error:state');
  }
}

const missing = new Set<string>();
const inactive = new Set<string>();
function currentShape(convs: Conv[], currentId: string | null, log: string[]) {
  const active = convs.filter(c => c.isActive !== false);
  if (currentId && !active.some(c => c.id === currentId)) {
    const record = convs.find(c => c.id === currentId);
    if (record) {
      if (!inactive.has(currentId)) { inactive.add(currentId); log.push('error:inactive'); }
    } else if (!missing.has(currentId)) {
      missing.add(currentId); log.push('debug:absent');
    }
  } else if (currentId) {
    missing.delete(currentId);
    inactive.delete(currentId);
  }
}

beforeEach(() => { missing.clear(); inactive.clear(); });

describe('blank-chat id (absent from list)', () => {
  it('retired shape: two errors per render, every render (the bug)', () => {
    const log: string[] = [];
    for (let i = 0; i < 5; i++) retiredShape([], 'blank', log);
    expect(log.filter(l => l.startsWith('error')).length).toBe(10);
  });

  it('current shape: one debug line, no error, not repeated', () => {
    const log: string[] = [];
    for (let i = 0; i < 5; i++) currentShape([], 'blank', log);
    expect(log).toEqual(['debug:absent']);
  });
});

describe('present-but-inactive id (real inconsistency)', () => {
  it('is reported as an error exactly once', () => {
    const log: string[] = [];
    const convs = [{ id: 'x', isActive: false }];
    for (let i = 0; i < 3; i++) currentShape(convs, 'x', log);
    expect(log).toEqual(['error:inactive']);
  });

  it('is reported again if it recurs after becoming consistent', () => {
    const log: string[] = [];
    currentShape([{ id: 'x', isActive: false }], 'x', log);
    currentShape([{ id: 'x', isActive: true }], 'x', log);   // consistent → clears memory
    currentShape([{ id: 'x', isActive: false }], 'x', log);
    expect(log).toEqual(['error:inactive', 'error:inactive']);
  });
});

it('consistent state logs nothing', () => {
  const log: string[] = [];
  currentShape([{ id: 'x' }], 'x', log);
  currentShape([{ id: 'x' }], null, log);
  expect(log).toEqual([]);
});
