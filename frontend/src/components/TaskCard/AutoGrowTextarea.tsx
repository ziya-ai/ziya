/**
 * Textarea that auto-resizes to fit its content.
 *
 * Used for the Task instructions and the Until condition fields so the
 * editor card grows naturally as the user types longer prompts, rather
 * than forcing them into a tiny scroll box.
 *
 * Growth is capped at `maxHeight` (default 320px): beyond that the
 * textarea switches to a normal scrollable box instead of growing the
 * whole card indefinitely. This also serves as a safety net — if the
 * initial `scrollHeight` measurement ever happens against a
 * zero/collapsed layout (e.g. this textarea rendered inside an antd
 * <Modal> while its entrance animation is still running), the field
 * would otherwise lock in at a near-zero height with no way to see or
 * scroll to its content. The re-measure-on-next-frame below re-checks
 * once the modal has actually finished laying out, and the scroll
 * fallback means even a stale measurement never traps the content.
 */

import React, { useLayoutEffect, useRef } from 'react';

type Props = Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, 'ref'> & {
  /** Minimum visible rows (sets a min-height floor). */
  minRows?: number;
  /** Maximum height in pixels before switching to a scrollable box. */
  maxHeight?: number;
};

export const AutoGrowTextarea: React.FC<Props> = ({
  minRows = 2,
  maxHeight = 320,
  value,
  style,
  ...rest
}) => {
  const ref = useRef<HTMLTextAreaElement | null>(null);

  const resize = () => {
    const el = ref.current;
    if (!el) return;
    // Reset first so shrinking works when text is deleted.
    el.style.height = 'auto';
    const next = Math.min(el.scrollHeight, maxHeight);
    el.style.height = `${next}px`;
    el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden';
  };

  useLayoutEffect(() => {
    resize();
  }, [value]);

  // Also resize on mount so an initial long value isn't clipped. A
  // container that is mid-animation (e.g. an antd Modal fading/scaling
  // in) can report a collapsed scrollHeight at this exact instant, so
  // re-measure again on the next couple of animation frames once the
  // container has actually settled — cheap, and self-correcting even if
  // this component is never used inside an animated container.
  useLayoutEffect(() => {
    resize();
    let raf2 = 0;
    const raf1 = requestAnimationFrame(() => {
      resize();
      raf2 = requestAnimationFrame(resize);
    });
    return () => {
      cancelAnimationFrame(raf1);
      cancelAnimationFrame(raf2);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <textarea
      {...rest}
      ref={ref}
      value={value}
      rows={minRows}
      onInput={(e) => {
        resize();
        rest.onInput?.(e);
      }}
      style={{
        // Disable manual resize; the textarea controls its own height.
        resize: 'none',
        overflow: 'hidden',
        maxHeight,
        ...style,
      }}
    />
  );
};
