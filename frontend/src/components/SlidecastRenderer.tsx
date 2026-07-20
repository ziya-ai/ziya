import React from 'react';
import { useTheme } from '../context/ThemeContext';

/**
 * Slidecast — a presentation primitive for chains of rendered blocks.
 *
 * A slidecast groups a sequence of "frames", each frame being one diagram
 * spec (drawio, mermaid, packet, graphviz, vega-lite, music, …) plus an
 * optional markdown caption. Only the active frame is mounted; navigating
 * swaps both the rendered diagram AND its surrounding narration in lockstep,
 * so an image chain reads as a narrated walkthrough rather than N stacked
 * canvases.
 *
 * This component is intentionally renderer-agnostic: it does not know how to
 * draw any diagram type itself. It receives two injected render functions
 * from MarkdownRenderer (which owns the LazyD3Renderer + renderTokens
 * machinery) so it can delegate frame bodies and captions back to the
 * existing pipeline without importing it (avoids a circular dependency).
 */

export interface SlidecastFrame {
    type: string;        // diagram token type: 'drawio' | 'mermaid' | 'packet' | ...
    spec: string;        // the raw diagram definition for that type
    caption?: string;    // optional markdown narration for this frame
    /** Reserved for a future highlight pass (parked). Ignored in v1. */
    highlight?: string[];
}

export interface SlidecastSpec {
    title?: string;
    sync?: 'caption' | 'sidebar' | 'none';
    frames: SlidecastFrame[];
}

interface SlidecastRendererProps {
    /** Parsed slidecast spec (already validated by the caller). */
    spec: SlidecastSpec;
    /** Render a single diagram frame body via the existing pipeline. */
    renderFrame: (frame: SlidecastFrame, key: string) => React.ReactNode;
    /** Render a markdown caption string via the existing renderTokens. */
    renderCaption: (markdown: string) => React.ReactNode;
}

export const SlidecastRenderer: React.FC<SlidecastRendererProps> = ({
    spec,
    renderFrame,
    renderCaption,
}) => {
    const { isDarkMode } = useTheme();
    const frames = Array.isArray(spec.frames) ? spec.frames : [];
    const count = frames.length;
    const [active, setActive] = React.useState(0);

    // Clamp the active index if the frame list shrinks (e.g. re-render mid-stream).
    const idx = count === 0 ? 0 : Math.min(Math.max(active, 0), count - 1);

    const go = React.useCallback((next: number) => {
        if (count === 0) return;
        // Wrap around at both ends.
        const wrapped = ((next % count) + count) % count;
        setActive(wrapped);
    }, [count]);

    if (count === 0) {
        return (
            <div style={{
                padding: '1em', opacity: 0.6, fontSize: '0.9em',
                border: '1px dashed rgba(128,128,128,0.35)', borderRadius: 6,
                margin: '0.5em 0',
            }}>
                Empty slidecast (no frames).
            </div>
        );
    }

    const frame = frames[idx];
    const sync = spec.sync ?? 'caption';
    const border = isDarkMode ? '#3a3a3a' : '#d9d9d9';
    const headerBg = isDarkMode ? '#1f1f1f' : '#fafafa';
    const fg = isDarkMode ? '#e0e0e0' : '#333';
    const subtleFg = isDarkMode ? '#9a9a9a' : '#666';

    const caption = frame.caption && sync !== 'none' ? frame.caption : undefined;
    const sidebar = sync === 'sidebar' && caption;

    const controls = (
        <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '6px 10px', background: headerBg,
            borderBottom: `1px solid ${border}`, borderRadius: '6px 6px 0 0',
            fontSize: '0.85em', color: fg,
        }}>
            <span style={{ fontWeight: 600, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {spec.title || 'Slidecast'}
            </span>
            <button
                type="button"
                onClick={() => go(idx - 1)}
                aria-label="Previous frame"
                style={slideBtnStyle(isDarkMode)}
            >‹ Prev</button>
            <span style={{ color: subtleFg, minWidth: 54, textAlign: 'center', fontVariantNumeric: 'tabular-nums' }}>
                {idx + 1} / {count}
            </span>
            <button
                type="button"
                onClick={() => go(idx + 1)}
                aria-label="Next frame"
                style={slideBtnStyle(isDarkMode)}
            >Next ›</button>
        </div>
    );

    // Frame dots for quick jumping (kept lightweight; no scrubber in v1).
    const dots = count > 1 ? (
        <div style={{ display: 'flex', gap: 6, justifyContent: 'center', padding: '6px 0' }}>
            {frames.map((_, i) => (
                <button
                    key={i}
                    type="button"
                    onClick={() => go(i)}
                    aria-label={`Go to frame ${i + 1}`}
                    style={{
                        width: 9, height: 9, borderRadius: '50%', padding: 0,
                        border: 'none', cursor: 'pointer',
                        background: i === idx
                            ? (isDarkMode ? '#40a9ff' : '#1890ff')
                            : (isDarkMode ? '#444' : '#ccc'),
                        transition: 'background 0.15s ease',
                    }}
                />
            ))}
        </div>
    ) : null;

    const captionBlock = caption ? (
        <div style={{
            padding: '10px 14px', color: fg,
            borderTop: sidebar ? 'none' : `1px solid ${border}`,
            fontSize: '0.95em', lineHeight: 1.5,
        }}>
            {renderCaption(caption)}
        </div>
    ) : null;

    // Key the frame body on idx so React fully remounts the diagram on
    // navigation — different specs at (often) identical geometry otherwise
    // risk stale internal renderer state.
    const frameBody = (
        <div style={{ padding: '8px 10px', minWidth: 0, overflow: 'auto' }}>
            {renderFrame(frame, `slidecast-frame-${idx}`)}
        </div>
    );

    return (
        <div
            className="ziya-slidecast"
            style={{
                border: `1px solid ${border}`,
                borderRadius: 6,
                margin: '0.75em 0',
                background: isDarkMode ? '#141414' : '#fff',
            }}
            tabIndex={0}
            onKeyDown={(e) => {
                if (e.key === 'ArrowLeft') { e.preventDefault(); go(idx - 1); }
                else if (e.key === 'ArrowRight') { e.preventDefault(); go(idx + 1); }
            }}
        >
            {controls}
            {sidebar ? (
                <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'stretch' }}>
                    <div style={{ flex: '1 1 320px', minWidth: 0 }}>{frameBody}</div>
                    <div style={{
                        flex: '1 1 240px', minWidth: 0,
                        borderLeft: `1px solid ${border}`,
                    }}>
                        {captionBlock}
                    </div>
                </div>
            ) : (
                <>
                    {frameBody}
                    {captionBlock}
                </>
            )}
            {dots}
        </div>
    );
};

function slideBtnStyle(isDarkMode: boolean): React.CSSProperties {
    return {
        padding: '2px 10px',
        border: `1px solid ${isDarkMode ? '#434343' : '#d9d9d9'}`,
        borderRadius: 4,
        background: isDarkMode ? '#262626' : '#fff',
        color: isDarkMode ? '#e0e0e0' : '#333',
        cursor: 'pointer',
        fontSize: '0.9em',
        lineHeight: 1.4,
    };
}

export default SlidecastRenderer;
