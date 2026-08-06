/**
 * Tests for AutoGrowTextarea's height cap + scroll fallback.
 *
 * Regression: the Task Card Preview modal renders this textarea while
 * antd's Modal entrance animation is still running. The original
 * implementation had no maxHeight and set overflow: hidden
 * unconditionally, so a mount-time scrollHeight measurement taken
 * against a collapsed/animating container locked the field in at a
 * near-zero height with no way to see or scroll to its content.
 */
import React from 'react';
import { render, screen, act } from '@testing-library/react';
import { AutoGrowTextarea } from '../AutoGrowTextarea';

// jsdom does not lay out text, so scrollHeight is always 0 unless we
// stub it. Each test configures the stub to model a specific content
// height in pixels.
function stubScrollHeight(px: number) {
  Object.defineProperty(HTMLTextAreaElement.prototype, 'scrollHeight', {
    configurable: true,
    get() { return px; },
  });
}

describe('AutoGrowTextarea', () => {
  afterEach(() => {
    // @ts-ignore - remove the per-test stub so it doesn't leak
    delete (HTMLTextAreaElement.prototype as any).scrollHeight;
    jest.restoreAllMocks();
  });

  it('grows to fit content below the cap and stays non-scrolling', () => {
    stubScrollHeight(120);
    render(<AutoGrowTextarea value="short text" onChange={() => {}} maxHeight={320} />);
    const el = screen.getByRole('textbox') as HTMLTextAreaElement;
    expect(el.style.height).toBe('120px');
    expect(el.style.overflowY).toBe('hidden');
  });

  it('caps height at maxHeight and switches to a scrollable box for long content', () => {
    stubScrollHeight(900);
    render(<AutoGrowTextarea value={'x'.repeat(2000)} onChange={() => {}} maxHeight={320} />);
    const el = screen.getByRole('textbox') as HTMLTextAreaElement;
    // Height is capped, not the full (unbounded) scrollHeight.
    expect(el.style.height).toBe('320px');
    // Scrolling must be available — this is the fallback that makes the
    // content reachable even if the height measurement is later found
    // to be stale or wrong.
    expect(el.style.overflowY).toBe('auto');
    expect(el.style.maxHeight).toBe('320px');
  });

  it('re-measures on the following animation frames after mount', () => {
    // Model the antd Modal scenario: at mount time (frame 0) the
    // container is still collapsed/animating so scrollHeight reads 0.
    // By the time the animation settles (a later rAF), the real content
    // height (400px, above the 320 cap) is reported.
    let frame = 0;
    Object.defineProperty(HTMLTextAreaElement.prototype, 'scrollHeight', {
      configurable: true,
      get() { return frame === 0 ? 0 : 400; },
    });

    let rafCallbacks: FrameRequestCallback[] = [];
    const rafSpy = jest.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      rafCallbacks.push(cb);
      return rafCallbacks.length;
    });

    render(<AutoGrowTextarea value="content" onChange={() => {}} maxHeight={320} />);
    const el = screen.getByRole('textbox') as HTMLTextAreaElement;

    // Mount-time measurement saw the collapsed container.
    expect(el.style.height).toBe('0px');

    // Advance to the point where the container has "settled".
    frame = 1;
    act(() => {
      const pending = rafCallbacks;
      rafCallbacks = [];
      pending.forEach((cb) => cb(0));
    });
    // Flush the second rAF scheduled from within the first callback.
    act(() => {
      const pending = rafCallbacks;
      rafCallbacks = [];
      pending.forEach((cb) => cb(0));
    });

    // The re-measure recovered the real (capped) height instead of
    // leaving the field permanently stuck at the stale 0px reading.
    expect(el.style.height).toBe('320px');
    expect(el.style.overflowY).toBe('auto');

    rafSpy.mockRestore();
  });

  it('cancels pending animation frames on unmount', () => {
    const cancelSpy = jest.spyOn(window, 'cancelAnimationFrame');
    stubScrollHeight(50);
    const { unmount } = render(
      <AutoGrowTextarea value="text" onChange={() => {}} maxHeight={320} />,
    );
    unmount();
    // Both the outer (raf1) and inner (raf2) frame ids must be
    // cancelled — a naive "return cleanup from inside the rAF callback"
    // implementation would only ever cancel raf1, since React only
    // invokes the outer effect's own returned cleanup function.
    expect(cancelSpy).toHaveBeenCalledTimes(2);
    cancelSpy.mockRestore();
  });

  it('shrinks back down when content is deleted', () => {
    stubScrollHeight(200);
    const { rerender } = render(
      <AutoGrowTextarea value="long content here" onChange={() => {}} maxHeight={320} />,
    );
    let el = screen.getByRole('textbox') as HTMLTextAreaElement;
    expect(el.style.height).toBe('200px');

    stubScrollHeight(40);
    rerender(<AutoGrowTextarea value="short" onChange={() => {}} maxHeight={320} />);
    el = screen.getByRole('textbox') as HTMLTextAreaElement;
    expect(el.style.height).toBe('40px');
    expect(el.style.overflowY).toBe('hidden');
  });

  it('respects a custom maxHeight prop', () => {
    stubScrollHeight(500);
    render(<AutoGrowTextarea value="content" onChange={() => {}} maxHeight={150} />);
    const el = screen.getByRole('textbox') as HTMLTextAreaElement;
    expect(el.style.height).toBe('150px');
    expect(el.style.maxHeight).toBe('150px');
    expect(el.style.overflowY).toBe('auto');
  });

  it('defaults maxHeight to 320px when not provided', () => {
    stubScrollHeight(500);
    render(<AutoGrowTextarea value="content" onChange={() => {}} />);
    const el = screen.getByRole('textbox') as HTMLTextAreaElement;
    expect(el.style.height).toBe('320px');
    expect(el.style.maxHeight).toBe('320px');
  });
});
