/**
 * Tests for the Mermaid timeline label-colon preprocessor.
 *
 * Mermaid's timeline lexer defines
 *   ":"\s[^#:\n;]+   -> 'event'
 *   [^#:\n;]+        -> 'period'
 * so a colon that is not followed by whitespace is INVALID anywhere on a
 * "period : event" line. A time-of-day like "00:00" therefore fails with
 *   Expecting 'EOF', 'SPACE', ..., 'period', 'event', got 'INVALID'
 *
 * The preprocessor substitutes the ratio symbol (U+2236) for every colon in
 * the period and for colons not followed by whitespace in the event text,
 * leaving the " : " event separators intact.
 */

import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const RATIO = '\u2236';

// The definition from the reported failure: no `section` lines, timestamps
// in both the period and (last line) the event text.
const FAILING_TIMELINE = `timeline
  title The Midnight Extraction — one night
  00:00 : Night shift starts<br>Vega takes post
  00:30 : Guard patrol A<br>East corridor, 22 min
  02:30 : Extraction window<br>Locked room, 12 min
  03:00 : Exit through vent<br>Alarm armed at 03:00`;

/** Every colon on a period/event line must be followed by whitespace. */
function lexableLines(def: string): string[] {
  return def
    .split('\n')
    .filter(l => l.includes(' : '))
    .filter(l => !/:(?!\s)/.test(l));
}

describe('timeline label colon preprocessor', () => {
  it('sanitizes timestamp colons in top-level periods (no section)', () => {
    const out = preprocessDefinition(FAILING_TIMELINE, 'timeline');
    expect(out).toContain(`00${RATIO}00 : Night shift starts`);
    expect(out).toContain(`02${RATIO}30 : Extraction window`);
    expect(out).not.toMatch(/^\s*00:00 /m);
  });

  it('sanitizes colons inside event text while keeping the " : " separator', () => {
    const out = preprocessDefinition(FAILING_TIMELINE, 'timeline');
    expect(out).toContain(`03${RATIO}00 : Exit through vent<br>Alarm armed at 03${RATIO}00`);
  });

  it('leaves no colon-without-whitespace on any period/event line', () => {
    const out = preprocessDefinition(FAILING_TIMELINE, 'timeline');
    const eventLines = out.split('\n').filter(l => l.includes(' : '));
    expect(eventLines.length).toBe(4);
    expect(lexableLines(out).length).toBe(eventLines.length);
  });

  it('preserves multiple " : " event separators on one line', () => {
    const def = `timeline
  section Ops
  09:15 : Deploy v1.2 : Rollback at 09:40 : Postmortem`;
    const out = preprocessDefinition(def, 'timeline');
    const line = out.split('\n').find(l => l.includes('Deploy'))!;
    expect(line.split(' : ').length).toBe(4);
    expect(line).toContain(`09${RATIO}15 : Deploy v1.2 : Rollback at 09${RATIO}40 : Postmortem`);
  });

  it('handles the unspaced "period: event" delimiter form', () => {
    // No space before the colon: the grammar accepts ":"\s as the separator,
    // so the boundary is the first colon followed by whitespace.
    const def = `timeline
  00:00: Night shift starts
  02:30: Extraction window at 02:45`;
    const out = preprocessDefinition(def, 'timeline');
    expect(out).toContain(`00${RATIO}00: Night shift starts`);
    expect(out).toContain(`02${RATIO}30: Extraction window at 02${RATIO}45`);
    // The separator itself must survive as a real colon on both lines.
    expect(out.split('\n').filter(l => /:\s/.test(l)).length).toBe(2);
    expect(out).not.toMatch(/:(?!\s)/);
  });

  it('sanitizes a bare period line with no event', () => {
    const def = `timeline
  section Night
  03:00
  03:30 : Exit`;
    const out = preprocessDefinition(def, 'timeline');
    expect(out).toContain(`03${RATIO}00`);
    expect(out).toContain(`03${RATIO}30 : Exit`);
    expect(out).not.toMatch(/^\s*03:00\s*$/m);
  });

  it('is gated on the resolved diagram type, not a prefix scan', () => {
    // Same body, dispatched as a gantt: the timeline pass must not run and
    // must not rewrite the colons.
    const def = `gantt
  title X
  section S
  Task 09:30 : a, 2024-01-01, 1d`;
    const out = preprocessDefinition(def, 'gantt');
    expect(out).not.toContain(`09${RATIO}30`);
  });

  it('does not touch lines without a timestamp', () => {
    const def = `timeline
  title Plain
  2024 : Launch
  2025 : Growth`;
    const out = preprocessDefinition(def, 'timeline');
    expect(out).toContain('2024 : Launch');
    expect(out).toContain('2025 : Growth');
    expect(out).not.toContain(RATIO);
  });
});
