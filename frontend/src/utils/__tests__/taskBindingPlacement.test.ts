/**
 * Unanchored task-card bindings (created by the model's task_card_stage /
 * task_card_launch mid-turn) are placed by created_at.  The turn's human
 * prompt is timestamped before the tool call, and the assistant reply is
 * timestamped when the stream finishes, AFTER the tool call.  A plain
 * "last message at or before created_at" rule therefore put the tile
 * between the prompt and the reply that built it: the start of the turn.
 * These tests pin the rule "end of the turn that built it".
 */
import { placeUnanchoredBindings } from '../taskBindingPlacement';

const b = (id: string, created_at: number) => ({ id, created_at });
const human = (t: number) => ({ role: 'human', _timestamp: t });
const ai = (t: number) => ({ role: 'assistant', _timestamp: t });

describe('placeUnanchoredBindings', () => {
    it('places a mid-turn binding after the reply, not after the prompt', () => {
        // prompt@100, tool call staged @150, reply persisted @200, next prompt @300
        const msgs = [human(100), ai(200), human(300), ai(400)];
        const p = placeUnanchoredBindings([b('x', 150)], msgs);
        expect(p.byMessageIdx.get(0)).toBeUndefined();
        expect(p.byMessageIdx.get(1)?.map(x => x.id)).toEqual(['x']);
        expect(p.tail).toEqual([]);
        expect(p.inFlight).toEqual([]);
    });

    it('sends a mid-turn binding of the LAST turn to the tail, after its reply', () => {
        const msgs = [human(100), ai(200)];
        const p = placeUnanchoredBindings([b('x', 150)], msgs);
        expect(p.tail.map(x => x.id)).toEqual(['x']);
        expect(p.byMessageIdx.size).toBe(0);
    });

    it('walks past every non-human message of the turn', () => {
        const msgs = [human(100), ai(200), { role: 'system', _timestamp: 210 }, ai(220), human(300)];
        const p = placeUnanchoredBindings([b('x', 150)], msgs);
        expect(p.byMessageIdx.get(3)?.map(x => x.id)).toEqual(['x']);
    });

    it('keeps a binding created between turns right after the reply it followed', () => {
        // User clicked Run on a tile after reply@200, before prompt@300.
        const msgs = [human(100), ai(200), human(300), ai(400)];
        const p = placeUnanchoredBindings([b('x', 250)], msgs);
        expect(p.byMessageIdx.get(1)?.map(x => x.id)).toEqual(['x']);
    });

    it('reports a binding of the turn still streaming as in-flight, not tail', () => {
        // The reply is not persisted yet; the live stream renders it.
        const msgs = [human(100), ai(200), human(300)];
        const p = placeUnanchoredBindings([b('x', 350)], msgs);
        expect(p.inFlight.map(x => x.id)).toEqual(['x']);
        expect(p.tail).toEqual([]);
        expect(p.byMessageIdx.size).toBe(0);
    });

    it('puts a binding older than every message at the head', () => {
        const p = placeUnanchoredBindings([b('x', 50)], [human(100), ai(200)]);
        expect(p.head.map(x => x.id)).toEqual(['x']);
    });

    it('skips messages without a timestamp as anchors but walks through them', () => {
        const msgs = [human(100), { role: 'assistant' }, human(300)];
        const p = placeUnanchoredBindings([b('x', 150)], msgs);
        expect(p.byMessageIdx.get(1)?.map(x => x.id)).toEqual(['x']);
    });

    it('returns empty groups for no bindings', () => {
        const p = placeUnanchoredBindings([], [human(1)]);
        expect(p.head).toEqual([]);
        expect(p.tail).toEqual([]);
        expect(p.inFlight).toEqual([]);
        expect(p.byMessageIdx.size).toBe(0);
    });
});
