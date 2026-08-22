/**
 * The browse list's support-level sort must not rank via indexOf().
 *
 * The previous implementation was:
 *   const supportOrder = ['Recommended','Supported','Community',
 *                         'Under assessment','Experimental'];
 *   supportOrder.indexOf(a.supportLevel) - supportOrder.indexOf(b.supportLevel)
 *
 * 'In development' is a real SupportLevel member and was absent from that
 * array, so indexOf returned -1 and those services sorted AHEAD of
 * 'Recommended'.  The backend had the inverse defect (string sort), so the two
 * surfaces disagreed and both were wrong.
 *
 * This is a source-level guard because the sort is an inline closure inside a
 * ~1700-line component and is not separately importable; the defect was the
 * ranking EXPRESSION, which is what is asserted here.
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC = path.join(__dirname, '..', 'MCPRegistryModal.tsx');
const source = fs.readFileSync(SRC, 'utf8');

describe('support level ranking', () => {
    it('does not rank by indexOf on a hand-maintained array', () => {
        expect(source).not.toMatch(/supportOrder\s*\.indexOf/);
    });

    it('declares a rank for every level the backend enum defines', () => {
        // Mirrors SupportLevel in app/mcp/registry/interface.py.
        const levels = [
            'Recommended', 'Supported', 'Under assessment',
            'In development', 'Community', 'Experimental',
        ];
        const block = source.match(
            /const SUPPORT_LEVEL_RANK[\s\S]*?\};/
        );
        expect(block).not.toBeNull();
        for (const level of levels) {
            expect(block![0]).toContain(`'${level}'`);
        }
    });

    it('maps an unknown level to the END, not to a negative sentinel', () => {
        const helper = source.match(/const supportRank[\s\S]*?;\n/);
        expect(helper).not.toBeNull();
        expect(helper![0]).toContain('MAX_SAFE_INTEGER');
    });

    it('ranks Recommended above Under assessment and In development', () => {
        const block = source.match(
            /const SUPPORT_LEVEL_RANK[\s\S]*?\};/
        )![0];
        const rankOf = (level: string): number => {
            const m = block.match(
                new RegExp(`'${level}'\\s*:\\s*(\\d+)`)
            );
            if (!m) throw new Error(`no rank for ${level}`);
            return parseInt(m[1], 10);
        };
        expect(rankOf('Recommended')).toBeLessThan(rankOf('Under assessment'));
        expect(rankOf('Recommended')).toBeLessThan(rankOf('In development'));
        expect(rankOf('Recommended')).toBeLessThan(rankOf('Experimental'));
    });

    it('uses the rank helper in the sort comparator', () => {
        expect(source).toMatch(
            /supportRank\(a\.supportLevel\)\s*-\s*supportRank\(b\.supportLevel\)/
        );
    });
});
