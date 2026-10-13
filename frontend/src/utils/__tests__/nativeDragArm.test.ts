/**
 * @jest-environment jsdom
 *
 * The native drag helper is armed by a fire-and-forget POST on pointerdown.
 * It must be throttled (a drag gesture can emit many presses), must carry the
 * page origin (the helper rewrites only drags Chromium stamps with it), and
 * must stop asking once the server says there is no helper on this platform.
 */
import { armNativeDrag, resetNativeDragArm } from '../nativeDragArm';

function installFetch(body: any, status = 200) {
    const fetchMock = jest.fn().mockResolvedValue({
        status,
        json: () => Promise.resolve(body),
    });
    (globalThis as any).fetch = fetchMock;
    return fetchMock;
}

beforeEach(() => { resetNativeDragArm(); });
afterEach(() => { delete (globalThis as any).fetch; });

describe('armNativeDrag', () => {
    it('POSTs the origin to /api/v1/drag/arm and throttles repeats', () => {
        const fetchMock = installFetch({ armed: true, supported: true });
        expect(armNativeDrag('http://localhost:6969', 1000)).toBe(true);
        expect(fetchMock).toHaveBeenCalledTimes(1);
        const [url, init] = fetchMock.mock.calls[0];
        expect(url).toBe('/api/v1/drag/arm');
        expect(init.method).toBe('POST');
        expect(JSON.parse(init.body)).toEqual({ origin: 'http://localhost:6969' });
        // Within the throttle window: nothing sent.
        expect(armNativeDrag('http://localhost:6969', 1500)).toBe(false);
        expect(fetchMock).toHaveBeenCalledTimes(1);
        // After it: sent again.
        expect(armNativeDrag('http://localhost:6969', 2100)).toBe(true);
        expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    it('defaults to window.location.origin', () => {
        const fetchMock = installFetch({ armed: true, supported: true });
        armNativeDrag();
        expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ origin: window.location.origin });
    });

    it('stops asking once the server reports the helper is unsupported', async () => {
        const fetchMock = installFetch({ armed: false, supported: false });
        expect(armNativeDrag('http://x', 1000)).toBe(true);
        await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
        expect(armNativeDrag('http://x', 5000)).toBe(false);
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it('stops asking after a 404 (server without the route)', async () => {
        const fetchMock = installFetch(null, 404);
        armNativeDrag('http://x', 1000);
        await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
        expect(armNativeDrag('http://x', 5000)).toBe(false);
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it('is a no-op without fetch and never throws on a network failure', async () => {
        expect(armNativeDrag('http://x', 1000)).toBe(false);
        (globalThis as any).fetch = jest.fn().mockRejectedValue(new Error('offline'));
        expect(armNativeDrag('http://x', 1000)).toBe(true);
        await Promise.resolve(); await Promise.resolve();
    });
});
