/**
 * Tests for artifactGroups — the pure shape -> layout selection used by
 * the task-run artifact viewer.
 *
 * The contract under test is deliberately *structural*: layout is chosen
 * from the SHAPE of a group (how many parts, are they labeled, do they
 * carry an explicit sequence), never from the semantics of any label
 * string.  That is what lets artifact shapes nobody designed for still
 * render sensibly instead of falling through to nothing.
 */

import {
  groupArtifactParts,
  selectLayout,
  blobUrlForPart,
  isImagePart,
  type ArtifactLayout,
} from '../artifactGroups';
import type { ArtifactPart } from '../../types/task_card';

/** Terse part builder — only the fields a given test cares about. */
function part(over: Partial<ArtifactPart> = {}): ArtifactPart {
  return { part_type: 'text', text: 'x', ...over } as ArtifactPart;
}

describe('selectLayout — shape decides, not label semantics', () => {
  it('single part renders as a card', () => {
    expect(selectLayout([part()])).toBe<ArtifactLayout>('card');
  });

  it('two labeled parts render side by side', () => {
    const parts = [
      part({ label: 'before' } as Partial<ArtifactPart>),
      part({ label: 'after' } as Partial<ArtifactPart>),
    ];
    expect(selectLayout(parts)).toBe<ArtifactLayout>('sideBySide');
  });

  it('side-by-side is chosen for ANY two labels, not just before/after', () => {
    const parts = [
      part({ label: 'us-east-1' } as Partial<ArtifactPart>),
      part({ label: 'eu-west-1' } as Partial<ArtifactPart>),
    ];
    expect(selectLayout(parts)).toBe<ArtifactLayout>('sideBySide');
  });

  it('two parts with only ONE labeled falls through to grid', () => {
    const parts = [part({ label: 'only' } as Partial<ArtifactPart>), part()];
    expect(selectLayout(parts)).toBe<ArtifactLayout>('grid');
  });

  it('two unlabeled parts fall through to grid', () => {
    expect(selectLayout([part(), part()])).toBe<ArtifactLayout>('grid');
  });

  it('explicit seq produces a sequence layout', () => {
    const parts = [
      part({ seq: 0 } as Partial<ArtifactPart>),
      part({ seq: 1 } as Partial<ArtifactPart>),
      part({ seq: 2 } as Partial<ArtifactPart>),
    ];
    expect(selectLayout(parts)).toBe<ArtifactLayout>('sequence');
  });

  it('seq on a single part still renders as a card (N=1 wins)', () => {
    expect(selectLayout([part({ seq: 0 } as Partial<ArtifactPart>)])).toBe<ArtifactLayout>('card');
  });

  it('two labeled parts WITH seq still render side by side (pair check precedes seq)', () => {
    const parts = [
      part({ label: 'a', seq: 0 } as Partial<ArtifactPart>),
      part({ label: 'b', seq: 1 } as Partial<ArtifactPart>),
    ];
    expect(selectLayout(parts)).toBe<ArtifactLayout>('sideBySide');
  });

  it('seq on only SOME parts is enough to sequence', () => {
    const parts = [
      part({ seq: 0 } as Partial<ArtifactPart>),
      part(),
      part({ seq: 2 } as Partial<ArtifactPart>),
    ];
    expect(selectLayout(parts)).toBe<ArtifactLayout>('sequence');
  });

  it('seq of 0 counts as present (not treated as falsy)', () => {
    const parts = [
      part({ seq: 0 } as Partial<ArtifactPart>),
      part({ seq: 0 } as Partial<ArtifactPart>),
      part({ seq: 0 } as Partial<ArtifactPart>),
    ];
    expect(selectLayout(parts)).toBe<ArtifactLayout>('sequence');
  });

  it('three to six unsequenced parts render as a grid', () => {
    for (const n of [3, 4, 5, 6]) {
      const parts = Array.from({ length: n }, () => part());
      expect(selectLayout(parts)).toBe<ArtifactLayout>('grid');
    }
  });

  it('more than six parts fall back to a list', () => {
    const parts = Array.from({ length: 7 }, () => part());
    expect(selectLayout(parts)).toBe<ArtifactLayout>('list');
  });

  it('large groups fall back to list rather than an unbounded grid', () => {
    const parts = Array.from({ length: 200 }, () => part());
    expect(selectLayout(parts)).toBe<ArtifactLayout>('list');
  });

  it('empty input degrades to list rather than throwing', () => {
    expect(selectLayout([])).toBe<ArtifactLayout>('list');
  });

  it('is a pure function of shape — same shape, same answer', () => {
    const shapeA = [
      part({ label: 'broken', part_type: 'text' } as Partial<ArtifactPart>),
      part({ label: 'fixed', part_type: 'file', file_uri: '/a.png' } as Partial<ArtifactPart>),
    ];
    const shapeB = [
      part({ label: 'draft', part_type: 'data', data: {} } as Partial<ArtifactPart>),
      part({ label: 'final', part_type: 'text' } as Partial<ArtifactPart>),
    ];
    // Different part types and different labels, identical SHAPE.
    expect(selectLayout(shapeA)).toBe(selectLayout(shapeB));
  });
});

describe('groupArtifactParts — grouping and ordering', () => {
  it('returns no groups for an empty artifact', () => {
    expect(groupArtifactParts([])).toEqual([]);
  });

  it('buckets parts by their group id', () => {
    const parts = [
      part({ name: 'a', group: 'g1' } as Partial<ArtifactPart>),
      part({ name: 'b', group: 'g2' } as Partial<ArtifactPart>),
      part({ name: 'c', group: 'g1' } as Partial<ArtifactPart>),
    ];
    const groups = groupArtifactParts(parts);
    expect(groups).toHaveLength(2);
    expect(groups[0].key).toBe('g1');
    expect(groups[0].parts.map(p => (p as any).name)).toEqual(['a', 'c']);
    expect(groups[1].key).toBe('g2');
  });

  it('preserves first-appearance order of groups', () => {
    const parts = [
      part({ group: 'zeta' } as Partial<ArtifactPart>),
      part({ group: 'alpha' } as Partial<ArtifactPart>),
    ];
    expect(groupArtifactParts(parts).map(g => g.key)).toEqual(['zeta', 'alpha']);
  });

  it('collects ungrouped parts into a single trailing bucket', () => {
    const parts = [
      part({ name: 'loose1' } as Partial<ArtifactPart>),
      part({ name: 'grouped', group: 'g' } as Partial<ArtifactPart>),
      part({ name: 'loose2' } as Partial<ArtifactPart>),
    ];
    const groups = groupArtifactParts(parts);
    const ungrouped = groups.find(g => g.key === '');
    expect(ungrouped).toBeDefined();
    expect(ungrouped!.parts).toHaveLength(2);
    // Ungrouped always sorts last so named groups lead the viewer.
    expect(groups[groups.length - 1].key).toBe('');
  });

  it('ungrouped bucket always uses the list fallback regardless of size', () => {
    const groups = groupArtifactParts([part(), part()]);
    expect(groups[0].key).toBe('');
    expect(groups[0].layout).toBe<ArtifactLayout>('list');
  });

  it('sorts parts within a group by seq when present', () => {
    const parts = [
      part({ name: 'third', group: 'g', seq: 2 } as Partial<ArtifactPart>),
      part({ name: 'first', group: 'g', seq: 0 } as Partial<ArtifactPart>),
      part({ name: 'second', group: 'g', seq: 1 } as Partial<ArtifactPart>),
    ];
    const [g] = groupArtifactParts(parts);
    expect(g.parts.map(p => (p as any).name)).toEqual(['first', 'second', 'third']);
  });

  it('preserves emit order within a group when seq is absent', () => {
    const parts = [
      part({ name: 'x', group: 'g' } as Partial<ArtifactPart>),
      part({ name: 'y', group: 'g' } as Partial<ArtifactPart>),
      part({ name: 'z', group: 'g' } as Partial<ArtifactPart>),
    ];
    const [g] = groupArtifactParts(parts);
    expect(g.parts.map(p => (p as any).name)).toEqual(['x', 'y', 'z']);
  });

  it('sorts seq-carrying parts ahead of unsequenced ones, stably', () => {
    const parts = [
      part({ name: 'noseq1', group: 'g' } as Partial<ArtifactPart>),
      part({ name: 'seq1', group: 'g', seq: 1 } as Partial<ArtifactPart>),
      part({ name: 'noseq2', group: 'g' } as Partial<ArtifactPart>),
      part({ name: 'seq0', group: 'g', seq: 0 } as Partial<ArtifactPart>),
    ];
    const [g] = groupArtifactParts(parts);
    expect(g.parts.map(p => (p as any).name)).toEqual(['seq0', 'seq1', 'noseq1', 'noseq2']);
  });

  it('assigns a layout to every group', () => {
    const parts = [
      part({ group: 'pair', label: 'a' } as Partial<ArtifactPart>),
      part({ group: 'pair', label: 'b' } as Partial<ArtifactPart>),
      part({ group: 'solo' } as Partial<ArtifactPart>),
    ];
    const groups = groupArtifactParts(parts);
    const byKey = Object.fromEntries(groups.map(g => [g.key, g.layout]));
    expect(byKey['pair']).toBe<ArtifactLayout>('sideBySide');
    expect(byKey['solo']).toBe<ArtifactLayout>('card');
  });

  it('never drops a part — every input appears in exactly one group', () => {
    const parts = [
      part({ name: '1', group: 'a' } as Partial<ArtifactPart>),
      part({ name: '2' } as Partial<ArtifactPart>),
      part({ name: '3', group: 'b', seq: 5 } as Partial<ArtifactPart>),
      part({ name: '4', group: 'a' } as Partial<ArtifactPart>),
      part({ name: '5' } as Partial<ArtifactPart>),
    ];
    const groups = groupArtifactParts(parts);
    const seen = groups.flatMap(g => g.parts.map(p => (p as any).name)).sort();
    expect(seen).toEqual(['1', '2', '3', '4', '5']);
  });

  it('tolerates a null/undefined outputs array', () => {
    expect(groupArtifactParts(null as any)).toEqual([]);
    expect(groupArtifactParts(undefined as any)).toEqual([]);
  });

  it('treats an empty-string group as ungrouped, not as a named group', () => {
    const groups = groupArtifactParts([part({ group: '' } as Partial<ArtifactPart>)]);
    expect(groups).toHaveLength(1);
    expect(groups[0].key).toBe('');
    expect(groups[0].layout).toBe<ArtifactLayout>('list');
  });
});

describe('isImagePart', () => {
  it('detects an image by media_type', () => {
    expect(isImagePart(part({
      part_type: 'file', file_uri: '/x/y.png', media_type: 'image/png',
    } as Partial<ArtifactPart>))).toBe(true);
  });

  it('rejects a non-image file part', () => {
    expect(isImagePart(part({
      part_type: 'file', file_uri: '/x/y.md', media_type: 'text/markdown',
    } as Partial<ArtifactPart>))).toBe(false);
  });

  it('rejects text and data parts outright', () => {
    expect(isImagePart(part({ part_type: 'text', text: 'hi' }))).toBe(false);
    expect(isImagePart(part({ part_type: 'data', data: {} } as Partial<ArtifactPart>))).toBe(false);
  });

  it('does NOT treat SVG as an inline image (matches server policy)', () => {
    // The blob route refuses to serve svg inline (script execution risk),
    // so the viewer must not try to <img> it either.
    expect(isImagePart(part({
      part_type: 'file', file_uri: '/x/y.svg', media_type: 'image/svg+xml',
    } as Partial<ArtifactPart>))).toBe(false);
  });

  it('falls back to the file extension when media_type is missing', () => {
    expect(isImagePart(part({
      part_type: 'file', file_uri: '/runs/r1/artifacts/chart.png',
    } as Partial<ArtifactPart>))).toBe(true);
  });

  it('returns false for a file part with no uri', () => {
    expect(isImagePart(part({ part_type: 'file' } as Partial<ArtifactPart>))).toBe(false);
  });
});

describe('blobUrlForPart — maps a stored path to the serving route', () => {
  const PID = 'proj-1';
  const RID = 'run-9';

  it('builds the artifacts route from the blob basename', () => {
    const url = blobUrlForPart(
      part({ part_type: 'file', file_uri: '/home/u/.ziya/projects/p/task_runs/run-9/artifacts/chart.png' } as Partial<ArtifactPart>),
      PID, RID,
    );
    expect(url).toBe(
      `/api/v1/projects/${PID}/task-runs/${RID}/artifacts/chart.png`,
    );
  });

  it('URI-encodes the filename component', () => {
    const url = blobUrlForPart(
      part({ part_type: 'file', file_uri: '/a/b/my chart (1).png' } as Partial<ArtifactPart>),
      PID, RID,
    );
    expect(url).toContain('my%20chart%20(1).png');
    expect(url).not.toContain('my chart');
  });

  it('returns null without a file_uri', () => {
    expect(blobUrlForPart(part({ part_type: 'text', text: 'x' }), PID, RID)).toBeNull();
  });

  it('returns null when project or run id is missing', () => {
    const p = part({ part_type: 'file', file_uri: '/a/b.png' } as Partial<ArtifactPart>);
    expect(blobUrlForPart(p, '', RID)).toBeNull();
    expect(blobUrlForPart(p, PID, '')).toBeNull();
  });

  it('never emits a traversal segment even if the stored uri is odd', () => {
    const url = blobUrlForPart(
      part({ part_type: 'file', file_uri: '/a/b/../../etc/passwd' } as Partial<ArtifactPart>),
      PID, RID,
    );
    // basename of the path is 'passwd' — no '..' can survive into the URL.
    expect(url).not.toContain('..');
    expect(url).toBe(`/api/v1/projects/${PID}/task-runs/${RID}/artifacts/passwd`);
  });
});
