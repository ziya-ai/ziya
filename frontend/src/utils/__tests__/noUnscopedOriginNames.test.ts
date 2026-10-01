/**
 * Static seam guard: every origin-shared primitive must take its name from
 * instanceIdentity, never a bare literal.
 *
 * Web Locks and BroadcastChannels are keyed by origin, so a literal
 * 'ziya-db-write' or `ziya-project-${id}` is shared with every other Ziya
 * installation that ever served this port.  The runtime tests prove the
 * scoped names isolate; this test proves nobody added a call site that
 * bypasses them.  Same rationale as noRawIndexedDbOpen.test.ts.
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC_ROOT = path.resolve(__dirname, '..', '..');

// Per-conversation streaming lock: uuid-keyed, cross-instance collision is
// not a data-integrity risk, and it predates this guard.
const ALLOWED_LOCK_LITERALS = [/^`ziya-stream-\$\{/];

function collect(dir: string, out: string[] = []): string[] {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        if (e.name === 'node_modules' || e.name === '__tests__') continue;
        const full = path.join(dir, e.name);
        if (e.isDirectory()) collect(full, out);
        else if (/\.(tsx?|jsx?)$/.test(e.name) && !/\.(test|spec)\./.test(e.name)) out.push(full);
    }
    return out;
}

// First argument of a call, roughly: everything up to the first top-level
// comma or closing paren.  Good enough for these two call shapes.
function firstArg(src: string, callIdx: number): string {
    let i = src.indexOf('(', callIdx) + 1;
    let depth = 0, out = '';
    for (; i < src.length; i++) {
        const ch = src[i];
        if (ch === '(' || ch === '{' || ch === '[') depth++;
        if (ch === ')' || ch === '}' || ch === ']') { if (depth === 0) break; depth--; }
        if (ch === ',' && depth === 0) break;
        out += ch;
    }
    return out.trim();
}

function isLiteral(arg: string): boolean {
    return /^['"`]/.test(arg);
}

describe('origin-shared names are instance-scoped', () => {
    const files = collect(SRC_ROOT);

    it('no navigator.locks.request(<literal>) outside the allow-list', () => {
        const offenders: string[] = [];
        for (const f of files) {
            const src = fs.readFileSync(f, 'utf8');
            const re = /navigator\.locks\s*\.\s*request\s*(<[^>]*>)?\s*\(/g;
            let m: RegExpExecArray | null;
            while ((m = re.exec(src))) {
                const arg = firstArg(src, m.index);
                if (isLiteral(arg) && !ALLOWED_LOCK_LITERALS.some(r => r.test(arg))) {
                    offenders.push(`${path.relative(SRC_ROOT, f)}: ${arg}`);
                }
            }
        }
        expect(offenders).toEqual([]);
    });

    it('no new BroadcastChannel(<literal>)', () => {
        const offenders: string[] = [];
        for (const f of files) {
            const src = fs.readFileSync(f, 'utf8');
            const re = /new\s+BroadcastChannel\s*\(/g;
            let m: RegExpExecArray | null;
            while ((m = re.exec(src))) {
                const arg = firstArg(src, m.index);
                if (isLiteral(arg)) offenders.push(`${path.relative(SRC_ROOT, f)}: ${arg}`);
            }
        }
        expect(offenders).toEqual([]);
    });

    it('db.ts opens no literal database name', () => {
        const src = fs.readFileSync(path.join(SRC_ROOT, 'utils', 'db.ts'), 'utf8');
        const re = /indexedDB\s*\.\s*open\s*\(/g;
        let m: RegExpExecArray | null;
        const literals: string[] = [];
        while ((m = re.exec(src))) {
            const arg = firstArg(src, m.index);
            if (isLiteral(arg)) literals.push(arg);
        }
        expect(literals).toEqual([]);
    });

    // Positive control: the helpers do see a literal when one is there.
    it('detector catches a literal (control)', () => {
        const src = "navigator.locks.request('ziya-db-write', fn)";
        expect(isLiteral(firstArg(src, 0))).toBe(true);
        const ok = "navigator.locks.request(LOCK.write, fn)";
        expect(isLiteral(firstArg(ok, 0))).toBe(false);
    });
});
