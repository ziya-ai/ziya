/**
 * Short relative timestamps for the Chats tab trailing slot
 * (design/visual-refresh.md, stage 2).  The buckets are what the row shows;
 * a wrong bucket is a wrong label on every row in the sidebar.
 */
import { formatAbsolute, formatRelativeShort } from '../relativeTime';

// Fixed "now": 2026-10-18 03:30 local.
const NOW = new Date(2026, 9, 18, 3, 30).getTime();
const ago = (ms: number) => NOW - ms;

describe('formatRelativeShort', () => {
    it('returns empty for missing or non-positive input', () => {
        expect(formatRelativeShort(0, NOW)).toBe('');
        expect(formatRelativeShort(undefined, NOW)).toBe('');
        expect(formatRelativeShort(NaN, NOW)).toBe('');
    });

    it('buckets minutes and hours', () => {
        expect(formatRelativeShort(ago(20_000), NOW)).toBe('now');
        expect(formatRelativeShort(ago(4 * 60_000), NOW)).toBe('4m');
        expect(formatRelativeShort(ago(59 * 60_000), NOW)).toBe('59m');
        expect(formatRelativeShort(ago(60 * 60_000), NOW)).toBe('1h');
        expect(formatRelativeShort(ago(23 * 3_600_000), NOW)).toBe('23h');
    });

    it('says "yest" for the previous calendar day beyond 24h, then month-day', () => {
        // 03:30 now; 26h ago is yesterday 01:30.
        expect(formatRelativeShort(ago(26 * 3_600_000), NOW)).toBe('yest');
        expect(formatRelativeShort(new Date(2026, 9, 16, 12).getTime(), NOW)).toBe('Oct 16');
    });

    it('adds the year only when it differs from the current one', () => {
        expect(formatRelativeShort(new Date(2025, 11, 4).getTime(), NOW)).toBe('Dec 4 2025');
    });

    it('accepts epoch seconds as well as milliseconds', () => {
        const secs = Math.floor(ago(4 * 60_000) / 1000);
        expect(formatRelativeShort(secs, NOW)).toBe('4m');
    });
});

describe('formatAbsolute', () => {
    it('is empty for missing input and non-empty for a timestamp', () => {
        expect(formatAbsolute(0)).toBe('');
        expect(formatAbsolute(NOW).length).toBeGreaterThan(0);
    });
});
