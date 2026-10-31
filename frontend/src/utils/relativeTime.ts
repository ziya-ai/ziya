/**
 * Short relative timestamps for dense rows (design/visual-refresh.md,
 * Chats tab trailing slot): "now", "4m", "23h", "yest", "Oct 16",
 * "Oct 16 2025".  Deliberately terse — the slot shares a 26px row with
 * state glyphs and bead counts, and the exact time is in the tooltip.
 */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/**
 * @param ts   epoch milliseconds (server summaries) or seconds (legacy);
 *             anything <= 0 or non-finite yields ''.
 * @param now  epoch milliseconds, injectable for tests.
 */
export function formatRelativeShort(ts: number | null | undefined, now: number = Date.now()): string {
    if (!ts || !Number.isFinite(ts) || ts <= 0) return '';
    const ms = ts < 1e12 ? ts * 1000 : ts;
    const diff = now - ms;
    if (diff < 60_000) return 'now';
    const mins = Math.floor(diff / 60_000);
    if (mins < 60) return `${mins}m`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h`;
    const then = new Date(ms);
    const today = new Date(now);
    const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
    if (ms >= startOfToday - 86_400_000) return 'yest';
    const label = `${MONTHS[then.getMonth()]} ${then.getDate()}`;
    return then.getFullYear() === today.getFullYear() ? label : `${label} ${then.getFullYear()}`;
}

/** Full timestamp for the tooltip behind a short label. */
export function formatAbsolute(ts: number | null | undefined): string {
    if (!ts || !Number.isFinite(ts) || ts <= 0) return '';
    const ms = ts < 1e12 ? ts * 1000 : ts;
    return new Date(ms).toLocaleString();
}
