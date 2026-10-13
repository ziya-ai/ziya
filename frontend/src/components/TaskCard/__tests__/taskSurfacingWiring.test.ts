/**
 * Wiring for the task rail, the update feed, and end-of-turn placement.
 *
 * taskSurfacing.test.ts and utils/__tests__/taskBindingPlacement.test.ts
 * cover the POLICY; neither proves Conversation.tsx uses it.  The seams
 * asserted here, at source level for the same reason the other *Wiring
 * tests are:
 *
 *   - the thread places unanchored tiles through placeUnanchoredBindings
 *     (the old inline "last message <= created_at" loop is gone);
 *   - every tile mount is wrapped in a slot carrying the binding id,
 *     without which the rail's chips and the feed's "Go to task" have
 *     nothing to scroll to;
 *   - TaskRail and TaskUpdateFeed are mounted, and the feed is told which
 *     tiles already sit at the bottom;
 *   - in-flight tiles portal into the slot StreamedContent exposes.
 */

import * as fs from 'fs';
import * as path from 'path';

const read = (p: string) => fs.readFileSync(path.resolve(__dirname, p), 'utf-8');
const CONV = read('../../Conversation.tsx');
const STREAM = read('../../StreamedContent.tsx');

describe('end-of-turn placement', () => {
    it('delegates to placeUnanchoredBindings', () => {
        expect(CONV).toMatch(/import \{ placeUnanchoredBindings \} from '\.\.\/utils\/taskBindingPlacement'/);
        expect(CONV).toMatch(/placeUnanchoredBindings\(unanchoredBindings/);
    });

    it('no longer anchors to the last message at or before created_at inline', () => {
        expect(CONV).not.toMatch(/if \(typeof mt === 'number' && mt <= t\) anchorIdx = i;/);
    });

    it('renders the in-flight bucket, portalled after the live stream', () => {
        expect(CONV).toMatch(/splicedUnanchored\.inFlight/);
        expect(CONV).toMatch(/createPortal\(tiles, inFlightSlot\)/);
        expect(CONV).toMatch(/querySelector<HTMLElement>\('\.tc-inflight-slot'\)/);
        expect(STREAM).toMatch(/<div className="tc-inflight-slot" \/>/);
    });
});

describe('tile slots', () => {
    it('wraps every tile mount in a slot carrying the binding id', () => {
        expect(CONV).toMatch(/\{\.\.\.\{ \[TILE_SLOT_ATTR\]: binding\.id \}\}/);
        // head, inline, tail, in-flight: four mounts, all through tileSlot.
        const mounts = CONV.match(/<TaskCardInlineTile binding=\{binding\} \/>/g) ?? [];
        const slotted = CONV.match(/tileSlot\(binding,/g) ?? [];
        expect(mounts.length).toBe(4);
        expect(slotted.length).toBe(4);
    });
});

describe('rail and feed are mounted', () => {
    it('mounts TaskRail over the thread with the flat binding list', () => {
        expect(CONV).toMatch(/<TaskRail bindings=\{visibleBindings\}/);
    });

    it('mounts TaskUpdateFeed below the thread, told which tiles are already at the bottom', () => {
        expect(CONV).toMatch(/<TaskUpdateFeed[\s\S]*?atBottomIds=\{bottomBindingIds\}/);
        expect(CONV).toMatch(/\[\.\.\.splicedUnanchored\.tail, \.\.\.splicedUnanchored\.inFlight\]\.map\(b => b\.id\)/);
    });
});
