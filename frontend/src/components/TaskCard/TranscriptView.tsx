/**
 * TranscriptView — one block's output as a chat turn: prose and tool
 * calls in the order they happened.
 *
 * Shared by the inline tile's run pane (RunThreadPane) and the Task
 * Cards dialog's thread (DeckRunPane), so a run reads the same in both.
 *
 * Not named RunTranscript.tsx: the model is runTranscript.ts, and on a
 * case-insensitive filesystem the two names resolve to the same import.
 */
import React, { useEffect, useState } from 'react';
import { TaskMarkdown } from './TaskMarkdown';
import { stripTaskMetaTags } from './completionCheck';
import {
  formatToolElapsed, type TailLine, type TranscriptSegment,
} from './runTranscript';
import './run-transcript.css';

const TOOL_GLYPH: Record<string, string> = {
  running: '●', done: '✓', failed: '✗', stopped: '◼',
};

interface Props {
  segments?: ReadonlyArray<TranscriptSegment>;
  /** Plain text for a block with no ordered list (a recorded run). */
  fallbackText?: string;
  /** The block is still producing output. */
  live: boolean;
}

export const RunTranscript: React.FC<Props> = ({ segments, fallbackText, live }) => {
  const segs = segments ?? [];
  const [open, setOpen] = useState<ReadonlySet<number>>(new Set());
  const [, setTick] = useState(0);

  // A call in flight shows a counting timer.
  const ticking = live && segs.some(s => s.kind === 'tool' && s.status === 'running');
  useEffect(() => {
    if (!ticking) return;
    const t = setInterval(() => setTick(x => x + 1), 1000);
    return () => clearInterval(t);
  }, [ticking]);

  if (segs.length === 0) {
    if (!fallbackText) return null;
    return (
      <TaskMarkdown
        markdown={stripTaskMetaTags(fallbackText)}
        enableCodeApply={false}
        breaks={true}
        isStreaming={live}
        isSubRender={true}
      />
    );
  }

  const lastIdx = segs.length - 1;
  return (
    <div className="tc-turn" data-testid="run-transcript">
      {segs.map((s, i) => {
        if (s.kind === 'text') {
          const md = stripTaskMetaTags(s.text);
          if (!md.trim()) return null;
          return (
            <TaskMarkdown
              key={`x-${i}`}
              markdown={md}
              enableCodeApply={false}
              breaks={true}
              isStreaming={live && i === lastIdx}
              isSubRender={true}
            />
          );
        }
        // A call still marked running under a block that has stopped
        // never returned; a timer there would claim it is still going.
        const stopped = s.status === 'running' && !live;
        const state = stopped ? 'stopped' : s.status;
        const meta = stopped ? 'did not finish' : formatToolElapsed(s, Date.now());
        const hasBody = !!s.resultPreview;
        const isOpen = open.has(i);
        return (
          <div key={`t-${s.toolId ?? i}`} className={`tc-turn__tool tc-turn__tool--${state}`}>
            <button
              type="button"
              className="tc-turn__tool-head"
              disabled={!hasBody}
              aria-expanded={hasBody ? isOpen : undefined}
              title={hasBody ? (isOpen ? 'Hide the result' : 'Show the result') : undefined}
              onClick={() => setOpen(prev => {
                const next = new Set(prev);
                if (next.has(i)) next.delete(i); else next.add(i);
                return next;
              })}
            >
              <span className="tc-turn__glyph">{TOOL_GLYPH[state] ?? '○'}</span>
              <span className="tc-turn__name">{s.toolName}</span>
              {s.header && <code className="tc-turn__args">{s.header}</code>}
              {meta && <span className="tc-turn__meta">{meta}</span>}
              {hasBody && <span className="tc-turn__caret">{isOpen ? '▾' : '▸'}</span>}
            </button>
            {isOpen && <pre className="tc-turn__result">{s.resultPreview}</pre>}
          </div>
        );
      })}
    </div>
  );
};

/**
 * The running block and its newest lines, for a collapsed tile.
 */
export const RunTranscriptTail: React.FC<{
  label: string;
  lines: ReadonlyArray<TailLine>;
}> = ({ label, lines }) => (
  <div className="tc-tail" data-testid="run-tail">
    <div className="tc-tail__row">
      <span className="tc-tail__glyph tc-tail__glyph--running">●</span>
      <span className="tc-tail__label">{label}</span>
      <span className="tc-tail__rule" />
    </div>
    {lines.map((l, i) => (
      <div key={i} className={`tc-tail__line tc-tail__line--${l.kind}`}>
        {l.kind === 'tool' && (
          <span className={`tc-tail__glyph tc-tail__glyph--${l.status ?? 'done'}`}>
            {TOOL_GLYPH[l.status ?? 'done']}
          </span>
        )}
        <span className="tc-tail__text">{l.text}</span>
      </div>
    ))}
  </div>
);

export default RunTranscript;
