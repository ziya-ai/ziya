/**
 * Tests for DeterminedTokenType union completeness.
 *
 * Verifies:
 *   - Every value returned by determineTokenType is in the union
 *   - Every case arm in renderTokens corresponds to a union member
 *   - No duplicate members in the union
 *   - No dead members (in union but never returned and never handled)
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC_PATH = path.resolve(__dirname, '../../frontend/src/components/MarkdownRenderer.tsx');
const src = fs.readFileSync(SRC_PATH, 'utf-8');

// Extract the DeterminedTokenType union members from source
function extractUnionMembers(): string[] {
    // Match the full type declaration across multiple lines
    const match = src.match(/type DeterminedTokenType\s*=\s*([\s\S]*?);/);
    if (!match) throw new Error('Could not find DeterminedTokenType declaration');

    const members: string[] = [];
    const re = /'([^']+)'/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(match[1])) !== null) {
        members.push(m[1]);
    }
    return members;
}

// Extract all string literals returned by determineTokenType
function extractReturnedTypes(): string[] {
    const fnStart = src.indexOf('function determineTokenType(');
    if (fnStart === -1) throw new Error('Could not find determineTokenType function');

    // Find the end of the function (next top-level function or const at same indentation)
    const fnBody = src.slice(fnStart, src.indexOf('\nconst ', fnStart + 100));

    const returned: Set<string> = new Set();
    const re = /return '([^']+)'/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(fnBody)) !== null) {
        returned.add(m[1]);
    }
    return Array.from(returned);
}

// Extract case arms from renderTokens switch
function extractCaseArms(): string[] {
    const fnStart = src.indexOf('const renderTokens = ');
    if (fnStart === -1) throw new Error('Could not find renderTokens');

    // The switch is inside renderTokens — grab a large enough chunk
    const fnBody = src.slice(fnStart, fnStart + 30000);

    const arms: Set<string> = new Set();
    const re = /case '([^']+)':/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(fnBody)) !== null) {
        arms.add(m[1]);
    }
    return Array.from(arms);
}

describe('DeterminedTokenType union integrity', () => {
    const unionMembers = extractUnionMembers();
    const uniqueMembers = [...new Set(unionMembers)];
    const returnedTypes = extractReturnedTypes();
    const caseArms = extractCaseArms();

    it('has no duplicate members', () => {
        const seen = new Set<string>();
        const duplicates: string[] = [];
        for (const member of unionMembers) {
            if (seen.has(member)) duplicates.push(member);
            seen.add(member);
        }
        expect(duplicates).toEqual([]);
    });

    it('includes every type returned by determineTokenType', () => {
        const memberSet = new Set(uniqueMembers);
        const missing = returnedTypes.filter(t => !memberSet.has(t));
        expect(missing).toEqual([]);
    });

    it('includes every case arm used in renderTokens', () => {
        const memberSet = new Set(uniqueMembers);
        // 'unknown' and 'default' are handled by the default arm
        const missing = caseArms.filter(t => t !== 'unknown' && !memberSet.has(t));
        expect(missing).toEqual([]);
    });

    it('has no dead members (neither returned nor handled)', () => {
        const returnedSet = new Set(returnedTypes);
        const caseSet = new Set(caseArms);
        // 'unknown' is special — it's the fallback default case
        const dead = uniqueMembers.filter(
            m => m !== 'unknown' && !returnedSet.has(m) && !caseSet.has(m)
        );
        expect(dead).toEqual([]);
    });

    it('drawio is in the union (regression)', () => {
        expect(uniqueMembers).toContain('drawio');
    });
});
