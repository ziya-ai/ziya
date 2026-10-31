/**
 * @jest-environment jsdom
 *
 * Panel status store (design/visual-refresh.md, stage 3).  The seam: a
 * producer publishes a partial patch; every subscriber sees the merged
 * state; a patch to one key leaves the other intact.  A store that merged
 * wrongly would show a model name with no tokens (or vice versa) in the
 * collapsed strip whenever either producer re-published.
 */
import {
    _resetPanelStatusForTests, getPanelStatus, publishPanelStatus, subscribePanelStatus,
} from '../panelStatus';

beforeEach(() => _resetPanelStatusForTests());

const tokens = { files: 31200, chat: 9400, ast: 1100, tools: 400, total: 42100, limit: 200000, cacheWorking: true };
const model = { id: 'anthropic.claude-sonnet-4.5', display: 'claude-sonnet-4.5', pinned: false };

it('starts empty', () => {
    expect(getPanelStatus()).toEqual({ tokens: null, model: null });
});

it('merges patches from independent producers', () => {
    publishPanelStatus({ tokens });
    publishPanelStatus({ model });
    expect(getPanelStatus()).toEqual({ tokens, model });
    publishPanelStatus({ tokens: { ...tokens, total: 50000 } });
    expect(getPanelStatus().model).toEqual(model);
    expect(getPanelStatus().tokens?.total).toBe(50000);
});

it('notifies subscribers with the merged state and stops after unsubscribe', () => {
    const seen: number[] = [];
    const unsub = subscribePanelStatus(s => seen.push(s.tokens?.total ?? -1));
    publishPanelStatus({ tokens });
    publishPanelStatus({ model });
    expect(seen).toEqual([42100, 42100]);
    unsub();
    publishPanelStatus({ tokens: { ...tokens, total: 1 } });
    expect(seen).toEqual([42100, 42100]);
});
