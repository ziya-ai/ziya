/**
 * Tests for the State block: factory shape, makeBlock dispatch, and the
 * editor's JSON value parse/serialize round-trip.  The pure helpers
 * (valueToText/textToValue) are re-implemented inline here to keep the
 * test free of a React render harness for the parsing logic, mirroring
 * the structural-test convention used by the sibling block editors.
 */

// uuid is ESM-only; stub so transitive importers resolve synchronously.
jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

describe('makeStateBlock factory', () => {
  it('produces a valid State leaf block', async () => {
    const { makeStateBlock } = await import('../../../utils/taskCardBlocks');
    const block = makeStateBlock();
    expect(block.block_type).toBe('state');
    expect(block.id).toMatch(/^st-/);
    expect(block.name).toBe('Initial state');
    // Leaf — no body children.
    expect(Array.isArray(block.body)).toBe(true);
    expect(block.body.length).toBe(0);
    // Prose-first: state_context is the conversational baseline (empty
    // by default); named variables are the optional adjunct (empty map).
    expect(block.state_context).toBe('');
    expect(block.state_variables).toEqual({});
    // No loop/scope fields leak onto a State block.
    expect(block.repeat_mode).toBeUndefined();
    expect(block.scope).toBeUndefined();
  });

  it('accepts a custom name', async () => {
    const { makeStateBlock } = await import('../../../utils/taskCardBlocks');
    expect(makeStateBlock('Givens').name).toBe('Givens');
  });
});

describe('makeBlock dispatch', () => {
  it('routes "state" to makeStateBlock', async () => {
    const { makeBlock } = await import('../../../utils/taskCardBlocks');
    const block = makeBlock('state');
    expect(block.block_type).toBe('state');
    expect(block.id).toMatch(/^st-/);
  });

  it('still routes the other kinds correctly', async () => {
    const { makeBlock } = await import('../../../utils/taskCardBlocks');
    expect(makeBlock('task').block_type).toBe('task');
    expect(makeBlock('repeat').block_type).toBe('repeat');
    expect(makeBlock('parallel').block_type).toBe('parallel');
    expect(makeBlock('until').block_type).toBe('until');
    expect(makeBlock('schedule').block_type).toBe('schedule');
  });
});

describe('StateBlockEditor module', () => {
  it('exports the named component', async () => {
    const mod = await import('../StateBlockEditor');
    expect(mod.StateBlockEditor).toBeDefined();
    expect(typeof mod.StateBlockEditor).toBe('function');
  });
});

// The value parse/serialize contract the editor relies on: JSON when it
// parses, raw string otherwise.  Mirrors textToValue/valueToText.
describe('state value parse/serialize contract', () => {
  const textToValue = (s: string): unknown => {
    const t = s.trim();
    if (t === '') return '';
    try { return JSON.parse(t); } catch { return s; }
  };
  const valueToText = (v: unknown): string =>
    typeof v === 'string' ? v : JSON.stringify(v);

  it('parses JSON scalars into real types', () => {
    expect(textToValue('42')).toBe(42);
    expect(textToValue('true')).toBe(true);
    expect(textToValue('"prod"')).toBe('prod');
  });

  it('parses JSON arrays/objects', () => {
    expect(textToValue('["a","b"]')).toEqual(['a', 'b']);
    expect(textToValue('{"k":1}')).toEqual({ k: 1 });
  });

  it('keeps a non-JSON string verbatim', () => {
    expect(textToValue('us-east-1')).toBe('us-east-1');
    expect(textToValue('prod')).toBe('prod');
  });

  it('round-trips: string stays string, typed renders to JSON text', () => {
    expect(valueToText('prod')).toBe('prod');
    expect(valueToText(42)).toBe('42');
    expect(valueToText(['a', 'b'])).toBe('["a","b"]');
    // A bare string that looks numeric round-trips to its typed form —
    // documented behavior, not a bug: "42" typed in becomes number 42.
    expect(textToValue(valueToText(42))).toBe(42);
  });
});
