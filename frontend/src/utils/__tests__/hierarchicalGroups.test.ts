import { groupHierarchicalRows, splitRowTitle } from '../hierarchicalGroups';

const row = (title: string, group?: string) => ({ title, content: '', group });

describe('groupHierarchicalRows', () => {
    it('collects a group at its first appearance and keeps input indices', () => {
        const groups = groupHierarchicalRows([row('A/a', 'A'), row('B/b', 'B'), row('A/c', 'A')]);
        expect(groups.map(g => g.name)).toEqual(['A', 'B']);
        expect(groups[0].items.map(i => i.index)).toEqual([0, 2]);
        expect(groups[1].items.map(i => i.index)).toEqual([1]);
    });

    it('keeps runs of ungrouped rows in place as unnamed groups', () => {
        const groups = groupHierarchicalRows([row('x'), row('y'), row('A/a', 'A'), row('z')]);
        expect(groups.map(g => [g.name, g.items.map(i => i.index)])).toEqual([
            [null, [0, 1]], ['A', [2]], [null, [3]],
        ]);
    });

    it('treats an empty group as ungrouped', () => {
        expect(groupHierarchicalRows([row('x', '')])[0].name).toBeNull();
    });
});

describe('splitRowTitle', () => {
    it('drops the group prefix and splits directory from file', () => {
        expect(splitRowTitle('Repo/src/hugo/fpga.rs', 'Repo')).toEqual({ dir: 'src/hugo/', base: 'fpga.rs' });
    });

    it('keeps the prefix when it is not the group', () => {
        expect(splitRowTitle('Other/a.rs', 'Repo')).toEqual({ dir: 'Other/', base: 'a.rs' });
    });

    it('does not split titles containing whitespace', () => {
        expect(splitRowTitle('Pkg — a/b description', null)).toEqual({ dir: '', base: 'Pkg — a/b description' });
    });

    it('leaves a bare name whole', () => {
        expect(splitRowTitle('README', null)).toEqual({ dir: '', base: 'README' });
    });
});
