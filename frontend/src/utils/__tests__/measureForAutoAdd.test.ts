/**
 * measureForAutoAdd resolves a TokenMeasure for each auto-add candidate
 * BEFORE the limit filters run, so "not measured yet" can never be mistaken
 * for "small".  Also asserts the seam with the accurate-count endpoint's
 * response shape ({results: {path: {accurate_count, timestamp} | {error}}}).
 */
import { measureForAutoAdd, filterByAutoAddTokenLimit } from '../autoAddTokenLimit';

const cached = {
    'known.ts': { count: 500, timestamp: 1 },
    'tool.pdf': { count: -1, timestamp: 1 },
    'stale-zero.tsx': { count: 0, timestamp: 1 },
};

describe('measureForAutoAdd', () => {
    it('uses positive cached counts and the -1 tool-backed marker without fetching', async () => {
        const fetcher = jest.fn();
        const { measure, fetched } = await measureForAutoAdd(['known.ts', 'tool.pdf'], cached, fetcher);
        expect(fetcher).not.toHaveBeenCalled();
        expect(measure('known.ts')).toBe(500);
        expect(measure('tool.pdf')).toBeNull();
        expect(fetched).toEqual({});
    });

    it('re-measures a cached zero (the old 50k-ceiling "valid zero") and uncached paths in one request', async () => {
        const fetcher = jest.fn(async () => ({
            'stale-zero.tsx': { accurate_count: 96787, timestamp: 42 },
            'public/app/mcp/manager.py': { accurate_count: 49216, timestamp: 42 },
        }));
        const { measure, fetched } = await measureForAutoAdd(
            ['stale-zero.tsx', 'public/app/mcp/manager.py', 'public/app/mcp/manager.py'], cached, fetcher);
        expect(fetcher).toHaveBeenCalledTimes(1);
        expect(fetcher.mock.calls[0][0]).toEqual(['stale-zero.tsx', 'public/app/mcp/manager.py']); // deduped
        expect(measure('stale-zero.tsx')).toBe(96787);
        expect(measure('public/app/mcp/manager.py')).toBe(49216);
        expect(fetched).toEqual({
            'stale-zero.tsx': { count: 96787, timestamp: 42 },
            'public/app/mcp/manager.py': { count: 49216, timestamp: 42 },
        });
    });

    it('maps a per-path server error to unmeasurable and a -1 to unmeasurable', async () => {
        const fetcher = async () => ({
            'gone.py': { accurate_count: 0, error: 'File not found' },
            'doc.pdf': { accurate_count: -1, timestamp: 5 },
        });
        const { measure } = await measureForAutoAdd(['gone.py', 'doc.pdf'], {}, fetcher);
        expect(measure('gone.py')).toBeNull();
        expect(measure('doc.pdf')).toBeNull();
    });

    it('leaves a path UNMEASURED when the request fails and the fallback has nothing', async () => {
        const fetcher = async () => { throw new Error('502'); };
        const { measure } = await measureForAutoAdd(['public/x.py'], {}, fetcher, () => 0);
        expect(measure('public/x.py')).toBeUndefined();
        // and the filter therefore holds it rather than admitting it
        const r = filterByAutoAddTokenLimit(['public/x.py'], 12500, measure);
        expect(r.allowed).toEqual([]);
        expect(r.unmeasured).toEqual(['public/x.py']);
    });

    it('uses a positive fallback estimate only when the request fails', async () => {
        const fetcher = async () => { throw new Error('502'); };
        const { measure } = await measureForAutoAdd(['internal/a.py'], {}, fetcher, () => 800);
        expect(measure('internal/a.py')).toBe(800);
    });

    it('treats a path missing from a successful response as unmeasured', async () => {
        const { measure } = await measureForAutoAdd(['a.py', 'b.py'], {}, async () => ({ 'a.py': { accurate_count: 10 } }));
        expect(measure('a.py')).toBe(10);
        expect(measure('b.py')).toBeUndefined();
    });

    it('end to end: the incident shape — no tree node, count not yet cached — is now measured and skipped', async () => {
        // Before: cache miss -> tree fallback 0 -> "unknown" -> allowed.
        const fetcher = async () => ({
            'public/frontend/src/components/MarkdownRenderer.tsx': { accurate_count: 96787, timestamp: 1 },
        });
        const { measure } = await measureForAutoAdd(
            ['public/frontend/src/components/MarkdownRenderer.tsx'], {}, fetcher, () => 0);
        const r = filterByAutoAddTokenLimit(['public/frontend/src/components/MarkdownRenderer.tsx'], 12500, measure);
        expect(r.allowed).toEqual([]);
        expect(r.skipped).toEqual([{ path: 'public/frontend/src/components/MarkdownRenderer.tsx', tokens: 96787 }]);
    });
});
