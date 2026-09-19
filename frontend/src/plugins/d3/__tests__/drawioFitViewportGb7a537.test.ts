/**
 * Regression test for group G-b7a537 (D-094/D-095/D-096/D-098/D-099/D-100).
 *
 * These drawio defects had all been verified, then regressed together after the
 * fitCenter→safeFitCenter rework. safeFitCenter SKIPS fit/center when the
 * graphContainer measures clientWidth/clientHeight === 0. In the interactive UI
 * a later retry / ResizeObserver rescues that; in the HEADLESS capture harness
 * the screenshot is taken before the rescue, so the diagram is captured at
 * maxGraph's default ~2x scale and content overflows the capture window — the
 * shared root cause behind the "viewport-clips-content", "oversized-dimension-
 * clamp", "label-illegible-after-width-fit-downscale" and "arrowheads-missing"
 * symptoms across the w1 and w2 specs.
 *
 * resolveFitViewport is the fix: when the container is zero-size it resolves a
 * DEFINITE, positive viewport (nearest laid-out ancestor, else a canvas-sized
 * default) so fitCenter always computes a finite scale and runs, instead of
 * being skipped. The load-bearing invariant is that it never yields a zero
 * dimension — that is what prevents both the skip and fitCenter's
 * divide-by-zero (newScale=0 → NaN translate).
 *
 * Imports the REAL shipped helper so it detects drift.
 */

import { resolveFitViewport } from '../drawioPlugin';

describe('resolveFitViewport (G-b7a537 fit-skip regression)', () => {
  it('uses the container box when it is already laid out (no fallback)', () => {
    const vp = resolveFitViewport(1024, 768, 5000, 5000);
    expect(vp.width).toBe(1024);
    expect(vp.height).toBe(768);
    expect(vp.usedFallback).toBe(false);
  });

  it('falls back to the nearest laid-out ancestor when the container is zero-size', () => {
    // This is the headless-capture case that used to SKIP fit entirely.
    const vp = resolveFitViewport(0, 0, 1230, 960);
    expect(vp.width).toBe(1230);
    expect(vp.height).toBe(960);
    expect(vp.usedFallback).toBe(true);
  });

  it('resolves each axis independently when only one is zero', () => {
    const vp = resolveFitViewport(800, 0, 0, 640);
    expect(vp.width).toBe(800);
    expect(vp.height).toBe(640);
    expect(vp.usedFallback).toBe(true);
  });

  it('uses the canvas-sized default when neither container nor ancestor has a box', () => {
    const vp = resolveFitViewport(0, 0, 0, 0);
    expect(vp.width).toBeGreaterThan(0);
    expect(vp.height).toBeGreaterThan(0);
  });

  it('NEVER yields a zero dimension — the invariant that keeps fit from being skipped', () => {
    // Exhaustive over the zero/non-zero combinations that reach safeFitCenter.
    const cases: Array<[number, number, number, number]> = [
      [0, 0, 0, 0],
      [0, 0, 1230, 0],
      [0, 0, 0, 800],
      [0, 500, 0, 0],
      [500, 0, 0, 0],
      [0, 0, 1230, 800],
    ];
    for (const [cw, ch, aw, ah] of cases) {
      const vp = resolveFitViewport(cw, ch, aw, ah);
      expect(vp.width).toBeGreaterThan(0);
      expect(vp.height).toBeGreaterThan(0);
    }
  });
});
