import { foldLane, orderForLane } from '../laneFold';
import { mk } from '../benchTestUtils';

// One "off" lane at the project lens, worst-first so a stable sort alone fails.
const lane = () => [
    { ...mk('skill:builtinA', { def: 'off' }), tokens: 30 },                        // builtin, default -> always folds
    { ...mk('skill:npxB', { def: 'off', provenance: 'user' }), tokens: 0 },          // installed, default
    { ...mk('mcp:small', { user: 'off', provenance: 'user' }), tokens: 500 },         // ghost at project lens
    { ...mk('skill:mine', { def: 'off', provenance: 'project' }), tokens: 10 },      // project-installed
    { ...mk('skill:pinned', { project: 'off', def: 'ondemand' }), tokens: 40 },      // builtin, PINNED -> a card
];

it('a built-in at its default always folds, even with unlimited space', () => {
    const { shown, builtinFolded, spaceFolded } = foldLane(lane(), null);
    expect(builtinFolded.map(i => i.key)).toEqual(['skill:builtinA']);
    expect(spaceFolded).toEqual([]);
    expect(shown.map(i => i.key)).toEqual(['skill:pinned', 'skill:mine', 'mcp:small', 'skill:npxB']);
});

it('a pinned built-in is a card, not folded', () => {
    const { shown, builtinFolded } = foldLane(lane(), null);
    expect(shown.map(i => i.key)).toContain('skill:pinned');
    expect(builtinFolded.map(i => i.key)).not.toContain('skill:pinned');
});

it('a built-in pinned at another layer is a card at this lens too', () => {
    const overridden = mk('skill:b2', { conversation: 'always', def: 'off' });  // ghost here, origin=conversation
    expect(foldLane([overridden], null).builtinFolded).toEqual([]);
});

it('orders pinned > project-installed > mcp by cost > default', () => {
    expect(orderForLane(lane()).map(i => i.key)).toEqual(
        ['skill:pinned', 'skill:mine', 'mcp:small', 'skill:builtinA', 'skill:npxB']);
});

it('space folds the rest only when they do not fit, reserving slots for both fold rows', () => {
    // 4 non-builtin cards + builtin row: fits in 5, not in 4.
    expect(foldLane(lane(), 5).spaceFolded).toEqual([]);
    const { shown, spaceFolded, builtinFolded } = foldLane(lane(), 4);
    expect(builtinFolded).toHaveLength(1);
    expect(shown.map(i => i.key)).toEqual(['skill:pinned', 'skill:mine']);
    expect(spaceFolded.map(i => i.key)).toEqual(['mcp:small', 'skill:npxB']);
});

it('never space-folds exactly one card', () => {
    for (const cap of [1, 2, 3, 4, 5, 6]) {
        expect({ cap, n: foldLane(lane(), cap).spaceFolded.length }).not.toEqual({ cap, n: 1 });
    }
});

it('a pinned card is never space-folded while a default-placed one is shown', () => {
    for (const cap of [1, 2, 3, 4]) {
        const { shown, spaceFolded } = foldLane(lane(), cap);
        const bad = spaceFolded.some(i => !i.atLens.ghost) && shown.some(i => i.atLens.ghost);
        expect({ cap, bad }).toEqual({ cap, bad: false });
    }
});

it('expanded shows everything, built-ins last', () => {
    const { shown, spaceFolded, builtinFolded } = foldLane(lane(), 1, true);
    expect(shown).toHaveLength(5);
    expect(spaceFolded).toEqual([]); expect(builtinFolded).toEqual([]);
    expect(shown.map(i => i.key).slice(-2)).toEqual(['skill:builtinA', 'skill:npxB']);
});
