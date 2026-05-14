/**
 * Structural tests for FailureClusters.
 *
 * The real logic (signature grouping, shouldCluster threshold) lives
 * in utils/iterationClusters.ts and is exhaustively unit-tested there.
 * This file only verifies the component exports and uses the expected
 * dependencies so future refactors don't silently break the wiring.
 */

jest.mock('../../../services/taskRunApi', () => ({
  getIterationArtifact: jest.fn(),
}));

describe('FailureClusters', () => {
  it('exports the named component', async () => {
    const mod = await import('../FailureClusters');
    expect(mod.FailureClusters).toBeDefined();
    expect(mod.default).toBe(mod.FailureClusters);
    expect(typeof mod.FailureClusters).toBe('function');
  });
});

describe('analyzeFailures re-export surface', () => {
  // Sanity check that the tile's import path still resolves to the
  // expected primitive.  Broken imports would otherwise hide behind
  // the dynamic React tree.
  it('is importable from utils/iterationClusters', async () => {
    const mod = await import('../../../utils/iterationClusters');
    expect(mod.analyzeFailures).toBeDefined();
    expect(typeof mod.analyzeFailures).toBe('function');
  });
});
