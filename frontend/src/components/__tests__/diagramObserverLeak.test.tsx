/**
 * PenPal #93 [CWE-401]: the DiagramRenderPage MutationObserver must be held in
 * a ref and disconnected on unmount, so an unmount mid-render doesn't orphan
 * it. The component can't be mounted in isolation (it needs ThemeContext), so
 * this is a source-contract guard — it fails if a later refactor drops the
 * ref-tracking or the unmount disconnect (the exact regression class this
 * fix addresses).
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'DiagramRenderPage.tsx'),
  'utf-8'
);

describe('DiagramRenderPage observer cleanup (PenPal #93)', () => {
  it('declares an observerRef', () => {
    expect(SRC).toMatch(/observerRef\s*=\s*useRef</);
  });

  it('assigns the live observer to the ref', () => {
    expect(SRC).toMatch(/observerRef\.current\s*=\s*observer/);
  });

  it('disconnects the observer in an unmount cleanup', () => {
    // The cleanup effect must call observerRef.current.disconnect().
    expect(SRC).toMatch(/observerRef\.current\??\.disconnect\(\)/);
  });

  it('has an empty-dep unmount effect that references observerRef', () => {
    // Guard: the disconnect must live in a cleanup return, not only the
    // complete/timeout paths (which use the local `observer`).
    const cleanupIdx = SRC.indexOf('observerRef.current.disconnect');
    const effectIdx = SRC.lastIndexOf('useEffect', cleanupIdx);
    expect(effectIdx).toBeGreaterThan(-1);
    expect(SRC.slice(effectIdx, cleanupIdx)).toContain('return () =>');
  });
});
