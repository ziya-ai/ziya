/**
 * @jest-environment jsdom
 *
 * handoffDraftModel — the drafting turn's model choice: default `medium`
 * (the standard model), resolved through /api/model-tiers for the active
 * endpoint, remembered per browser, and null-on-failure so the caller can
 * fall back to the conversation's pin.
 *
 * Also pins the seam in HandoffDrawer: the selector's value is what the
 * draft request is made with, and the choice is written back.
 */
import fs from 'fs';
import path from 'path';
import {
    DEFAULT_DRAFT_TIER, DRAFT_TIER_ORDER, readDraftTier, writeDraftTier,
    fetchModelTiers, resolveDraftTierModel, _resetTierCache,
} from '../handoffDraftModel';

const tiers = [
    { tier: 'xsmall', resolved_model: 'nova-micro', exact: true },
    { tier: 'small', resolved_model: 'haiku', exact: true },
    { tier: 'medium', resolved_model: 'sonnet', exact: true },
    { tier: 'large', resolved_model: 'opus', exact: true },
    { tier: 'frontier', resolved_model: 'opus', exact: false },
];

function mockFetch(impl: (url: string) => any) {
    const f = jest.fn(async (url: string) => {
        const body = impl(url);
        if (body === undefined) return { ok: false, json: async () => null };
        return { ok: true, json: async () => body };
    });
    (global as any).fetch = f;
    return f;
}

beforeEach(() => { localStorage.clear(); _resetTierCache(); });
afterEach(() => { delete (global as any).fetch; });

describe('remembered choice', () => {
    it('defaults to medium — the standard model', () => {
        expect(DEFAULT_DRAFT_TIER).toBe('medium');
        expect(readDraftTier()).toBe('medium');
    });
    it('round-trips a tier and the "conversation model" choice', () => {
        writeDraftTier('large');
        expect(readDraftTier()).toBe('large');
        writeDraftTier(null);
        expect(readDraftTier()).toBeNull();
    });
    it('ignores a value that is not a tier', () => {
        localStorage.setItem('ZIYA_HANDOFF_DRAFT_TIER', 'gigantic');
        expect(readDraftTier()).toBe('medium');
    });
});

describe('resolution', () => {
    it('resolves a tier to the model name for the ACTIVE endpoint', async () => {
        const f = mockFetch(url => {
            if (url === '/api/config') return { endpoint: 'google' };
            if (url === '/api/model-tiers?endpoint=google') return { endpoint: 'google', tiers };
            return undefined;
        });
        expect(await resolveDraftTierModel('medium')).toBe('sonnet');
        expect(await resolveDraftTierModel('xsmall')).toBe('nova-micro');
        // Cached per page: a second resolution makes no further requests.
        expect(f).toHaveBeenCalledTimes(2);
        expect((await fetchModelTiers()).endpoint).toBe('google');
    });
    it('null choice (conversation model) resolves to null without a request', async () => {
        const f = mockFetch(() => undefined);
        expect(await resolveDraftTierModel(null)).toBeNull();
        expect(f).not.toHaveBeenCalled();
    });
    it('an unreadable table yields null and is retried on the next call', async () => {
        const f = mockFetch(() => undefined);
        expect(await resolveDraftTierModel('medium')).toBeNull();
        mockFetch(url => url === '/api/config' ? { endpoint: 'bedrock' } : { tiers });
        expect(await resolveDraftTierModel('medium')).toBe('sonnet');
        expect(f).toHaveBeenCalled();
    });
    it('every rung in the order is a tier the server knows', () => {
        expect(DRAFT_TIER_ORDER).toEqual(['xsmall', 'small', 'medium', 'large', 'frontier']);
    });
});

describe('the seam: HandoffDrawer requests the draft with the selected tier', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', '..', 'components', 'HandoffDrawer.tsx'), 'utf8');
    it('initialises from the remembered choice and writes it back', () => {
        expect(src).toMatch(/useState<DraftTierChoice>\(\(\) => readDraftTier\(\)\)/);
        expect(src).toMatch(/writeDraftTier\(t\)/);
    });
    it('passes the selector value to the draft request', () => {
        expect(src).toMatch(/requestDraft\(conversationId, \{ tier: draftTier \}\)/);
        expect(src).toMatch(/data-testid="handoff-draft-tier"/);
        expect(src).toMatch(/value=\{draftTier \?\? 'conversation'\}/);
    });
});
