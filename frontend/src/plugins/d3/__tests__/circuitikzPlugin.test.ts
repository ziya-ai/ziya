/**
 * Tests for the CircuiTikZ parser utilities (utils/d3Plugins/circuitikzPlugin.ts).
 */
import { parseCircuit, isCircuitDefinitionComplete, bounds } from '../../../utils/d3Plugins/circuitikzPlugin';

describe('parseCircuit', () => {
  it('parses a simple RC loop with labels and a ground', () => {
    const def = `\\draw (0,0) to[R, l=$R_1$] (2,0) to[C, l=$C_1$] (2,-2) -- (0,-2) -- (0,0);
\\draw (0,0) node[ground] {};`;
    const result = parseCircuit(def);

    expect(result.elements).toHaveLength(4);
    expect(result.elements[0]).toMatchObject({ kind: 'resistor', label: 'R1' });
    expect(result.elements[1]).toMatchObject({ kind: 'capacitor', label: 'C1' });
    expect(result.elements[2].kind).toBe('wire');
    expect(result.elements[3].kind).toBe('wire');
    expect(result.grounds).toHaveLength(1);
    expect(result.grounds[0].at).toEqual({ x: 0, y: 0 });
  });

  it('parses a battery + resistor loop', () => {
    const def = `\\draw (0,0) to[V, l=$V_1$] (0,2) to[R, l=$R_1$] (2,2) -- (2,0) -- (0,0);`;
    const result = parseCircuit(def);

    expect(result.elements).toHaveLength(4);
    expect(result.elements[0]).toMatchObject({ kind: 'voltage-source', label: 'V1' });
    expect(result.elements[1]).toMatchObject({ kind: 'resistor', label: 'R1' });
  });

  it('resolves relative ++ coordinates', () => {
    const def = `\\draw (0,0) to[R] ++(2,0) to[L] ++(0,-2) -- ++(-2,0) -- (0,0);`;
    const result = parseCircuit(def);

    expect(result.elements[0]).toMatchObject({ from: { x: 0, y: 0 }, to: { x: 2, y: 0 } });
    expect(result.elements[1]).toMatchObject({ from: { x: 2, y: 0 }, to: { x: 2, y: -2 } });
    expect(result.elements[2]).toMatchObject({ from: { x: 2, y: -2 }, to: { x: 0, y: -2 } });
  });

  it('resolves named coordinates declared via \\coordinate', () => {
    const def = `\\coordinate (A) at (3,3);
\\draw (0,0) to[D] (A);`;
    const result = parseCircuit(def);

    expect(result.elements).toHaveLength(1);
    expect(result.elements[0]).toMatchObject({ kind: 'diode', to: { x: 3, y: 3 } });
  });

  it('recognizes switch and diode component aliases', () => {
    const def = `\\draw (0,0) to[switch] (2,0) to[diode, l=$D_1$] (4,0);`;
    const result = parseCircuit(def);

    expect(result.elements[0].kind).toBe('switch');
    expect(result.elements[1]).toMatchObject({ kind: 'diode', label: 'D1' });
  });

  it('ignores TikZ line comments', () => {
    const def = `% this is a comment\n\\draw (0,0) to[R] (2,0); % trailing comment`;
    const result = parseCircuit(def);
    expect(result.elements).toHaveLength(1);
  });

  it('never throws on malformed input', () => {
    expect(() => parseCircuit('\\draw (garbage')).not.toThrow();
    expect(() => parseCircuit('')).not.toThrow();
    expect(() => parseCircuit('not tikz at all')).not.toThrow();
  });

  it('returns empty results for content with no draw statements', () => {
    const result = parseCircuit('just some text');
    expect(result.elements).toHaveLength(0);
    expect(result.grounds).toHaveLength(0);
    expect(result.labels).toHaveLength(0);
  });
});

describe('isCircuitDefinitionComplete', () => {
  it('is false for empty input', () => {
    expect(isCircuitDefinitionComplete('')).toBe(false);
    expect(isCircuitDefinitionComplete('   ')).toBe(false);
  });

  it('is false for an unterminated draw statement (mid-stream)', () => {
    expect(isCircuitDefinitionComplete('\\draw (0,0) to[R] (2,0)')).toBe(false);
  });

  it('is true once a draw statement is terminated with balanced parens', () => {
    expect(isCircuitDefinitionComplete('\\draw (0,0) to[R] (2,0);')).toBe(true);
  });

  it('is false when parens are unbalanced', () => {
    expect(isCircuitDefinitionComplete('\\draw (0,0 to[R] (2,0);')).toBe(false);
  });
});

describe('bounds', () => {
  it('computes the bounding box across elements, grounds, and labels', () => {
    const parsed = parseCircuit(
      `\\draw (0,0) to[R] (2,0) to[C] (2,-2) -- (0,-2) -- (0,0);`
    );
    expect(bounds(parsed)).toEqual({ minX: 0, minY: -2, maxX: 2, maxY: 0 });
  });

  it('returns a default box for an empty circuit', () => {
    expect(bounds({ elements: [], grounds: [], labels: [] })).toEqual({
      minX: 0, minY: 0, maxX: 1, maxY: 1,
    });
  });
});
