/**
 * Regression tests for the force-directed node sanitizer (ledger Issue 25).
 *
 * Root defect: a node whose fixed-position pin (`fx`/`fy`) is a non-finite value
 * — most commonly the JSON strings "Infinity"/"-Infinity"/"NaN" a model emits
 * because JSON has no Infinity/NaN literal — is spread verbatim ({...n}) into the
 * d3-force node set. d3.forceManyBody builds a quadtree via cover(), which DOUBLES
 * its extent in a while-loop until every point is inside it; an Infinity coordinate
 * can never be covered, so the loop spins forever and the render hangs to a 30s
 * timeout with zero output.
 *
 * These tests import the REAL shipped helpers (no re-implementation) and pin BOTH
 * directions: non-finite pins are dropped/coerced, AND well-formed input is left
 * untouched so the sanitizer is not a catch-all.
 */
import {
  sanitizeForceNodes,
  toFiniteOrUndefined,
  FORCE_MAX_NODE_RADIUS,
} from '../forceDirectedPlugin';

describe('toFiniteOrUndefined', () => {
  it('keeps finite numbers', () => {
    expect(toFiniteOrUndefined(0)).toBe(0);
    expect(toFiniteOrUndefined(400)).toBe(400);
    expect(toFiniteOrUndefined(-12.5)).toBe(-12.5);
  });

  it('parses finite numeric strings', () => {
    expect(toFiniteOrUndefined('400')).toBe(400);
    expect(toFiniteOrUndefined('  3.5 ')).toBe(3.5);
    expect(toFiniteOrUndefined('-7')).toBe(-7);
  });

  it('rejects the non-finite JSON strings that trigger the hang', () => {
    expect(toFiniteOrUndefined('Infinity')).toBeUndefined();
    expect(toFiniteOrUndefined('-Infinity')).toBeUndefined();
    expect(toFiniteOrUndefined('NaN')).toBeUndefined();
  });

  it('rejects raw non-finite numbers, null, empty and junk', () => {
    expect(toFiniteOrUndefined(Infinity)).toBeUndefined();
    expect(toFiniteOrUndefined(-Infinity)).toBeUndefined();
    expect(toFiniteOrUndefined(NaN)).toBeUndefined();
    expect(toFiniteOrUndefined(null)).toBeUndefined();
    expect(toFiniteOrUndefined(undefined)).toBeUndefined();
    expect(toFiniteOrUndefined('')).toBeUndefined();
    expect(toFiniteOrUndefined('   ')).toBeUndefined();
    expect(toFiniteOrUndefined('not-a-number')).toBeUndefined();
    expect(toFiniteOrUndefined({})).toBeUndefined();
  });
});

describe('sanitizeForceNodes — drops non-finite pins (the hang guard)', () => {
  it('drops string "Infinity"/"-Infinity" fx/fy so no pin survives', () => {
    const [n] = sanitizeForceNodes([{ id: 'p', fx: 'Infinity', fy: '-Infinity', radius: 5 }]);
    expect('fx' in n).toBe(false);
    expect('fy' in n).toBe(false);
    // The node itself and its other fields are preserved.
    expect(n.id).toBe('p');
    expect(n.radius).toBe(5);
  });

  it('drops string "NaN" and raw NaN/Infinity pins', () => {
    const [a] = sanitizeForceNodes([{ id: 'a', fx: 'NaN', fy: 'NaN' }]);
    expect('fx' in a).toBe(false);
    expect('fy' in a).toBe(false);
    const [b] = sanitizeForceNodes([{ id: 'b', fx: Infinity, fy: NaN }]);
    expect('fx' in b).toBe(false);
    expect('fy' in b).toBe(false);
  });

  it('AFTER sanitizing, EVERY node has only finite pins (the invariant the hang violated)', () => {
    const out = sanitizeForceNodes([
      { id: 'p1', fx: 'Infinity', fy: '-Infinity' },
      { id: 'p2', fx: 'NaN', fy: 'NaN' },
      { id: 'p3', fx: 400, fy: 300 },
      { id: 'free' },
    ]);
    for (const n of out) {
      if ('fx' in n) expect(Number.isFinite(n.fx)).toBe(true);
      if ('fy' in n) expect(Number.isFinite(n.fy)).toBe(true);
    }
  });
});

describe('sanitizeForceNodes — preserves well-formed input (not a catch-all)', () => {
  it('keeps finite numeric pins unchanged', () => {
    const [n] = sanitizeForceNodes([{ id: 'ok', fx: 400, fy: 300, radius: 5 }]);
    expect(n.fx).toBe(400);
    expect(n.fy).toBe(300);
    expect(n.radius).toBe(5);
  });

  it('coerces finite numeric-string pins to numbers rather than dropping them', () => {
    const [n] = sanitizeForceNodes([{ id: 'str', fx: '250', fy: '175' }]);
    expect(n.fx).toBe(250);
    expect(n.fy).toBe(175);
  });

  it('leaves free (unpinned) nodes and their identity fields intact', () => {
    const [n] = sanitizeForceNodes([{ id: 'x', group: 2, label: 'مرحبا', color: '#abc' }]);
    expect(n).toEqual({ id: 'x', group: 2, label: 'مرحبا', color: '#abc' });
  });

  it('does not mutate the input objects', () => {
    const input = [{ id: 'p', fx: 'Infinity', fy: 'Infinity' }];
    sanitizeForceNodes(input);
    expect(input[0].fx).toBe('Infinity'); // original untouched
  });
});

describe('sanitizeForceNodes — radius clamping', () => {
  it('clamps a huge radius to FORCE_MAX_NODE_RADIUS', () => {
    const [n] = sanitizeForceNodes([{ id: 'big', radius: 1e9 }]);
    expect(n.radius).toBe(FORCE_MAX_NODE_RADIUS);
  });

  it('forces a negative radius up to 0', () => {
    const [n] = sanitizeForceNodes([{ id: 'neg', radius: -10 }]);
    expect(n.radius).toBe(0);
  });

  it('parses a finite numeric-string radius and keeps it in-range', () => {
    const [n] = sanitizeForceNodes([{ id: 's', size: '12' }]);
    expect(n.size).toBe(12);
  });

  it('drops a non-numeric radius formula so the render default applies', () => {
    const [n] = sanitizeForceNodes([{ id: 'f', radius: '10+Math.random()*5' }]);
    expect('radius' in n).toBe(false);
  });
});

describe('sanitizeForceNodes — edge inputs', () => {
  it('returns [] for a non-array', () => {
    expect(sanitizeForceNodes(undefined as any)).toEqual([]);
    expect(sanitizeForceNodes(null as any)).toEqual([]);
  });
});
