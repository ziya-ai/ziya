/**
 * "Revised later" markers: one where a task card's tile used to sit before
 * the model revised the card and the tile moved down (app/utils/
 * task_card_revision.py).  Placed by the same rule as the tiles.
 */
import { placeRevisionMarkers } from '../taskBindingRevisions';

const msgs = [
  { id: 'h1', role: 'human', _timestamp: 100 },
  { id: 'a1', role: 'assistant', _timestamp: 200 },
  { id: 'h2', role: 'human', _timestamp: 300 },
  { id: 'a2', role: 'assistant', _timestamp: 400 },
  { id: 'h3', role: 'human', _timestamp: 500 },
  { id: 'a3', role: 'assistant', _timestamp: 600 },
];

describe('placeRevisionMarkers', () => {
  it('marks the old spot of a tile that moved to a later turn', () => {
    // First staged during turn 1 (ends at a1 = idx 1); revised in turn 3.
    const out = placeRevisionMarkers([
      { id: 'b', created_at: 550, revisions: [{ created_at: 150 }] },
    ], msgs);
    expect(out.byMessageIdx.get(1)?.map(m => m.bindingId)).toEqual(['b']);
    expect(out.tail).toEqual([]);
    expect(out.head).toEqual([]);
  });

  it('places an anchored former spot below its anchor message', () => {
    const out = placeRevisionMarkers([
      { id: 'b', created_at: 550, revisions: [{ created_at: 0, anchor_message_id: 'a2' }] },
    ], msgs);
    expect(out.byMessageIdx.get(3)?.map(m => m.bindingId)).toEqual(['b']);
  });

  it('drops a marker that would sit where the tile now is', () => {
    // Two writes in the same turn: the earlier placement is the tile's own.
    const out = placeRevisionMarkers([
      { id: 'b', created_at: 560, revisions: [{ created_at: 550 }] },
    ], msgs);
    expect(out.byMessageIdx.size).toBe(0);
    expect(out.tail).toEqual([]);
  });

  it('shows one marker per spot, and none for a deleted anchor', () => {
    const out = placeRevisionMarkers([
      { id: 'b', created_at: 550, revisions: [
        { created_at: 150 }, { created_at: 160 }, { created_at: 0, anchor_message_id: 'gone' },
      ] },
    ], msgs);
    expect(out.byMessageIdx.get(1)).toHaveLength(1);
    expect([...out.byMessageIdx.keys()]).toEqual([1]);
  });

  it('is empty for bindings that were never revised', () => {
    const out = placeRevisionMarkers([{ id: 'b', created_at: 150 }], msgs);
    expect(out.byMessageIdx.size + out.head.length + out.tail.length).toBe(0);
  });
});
