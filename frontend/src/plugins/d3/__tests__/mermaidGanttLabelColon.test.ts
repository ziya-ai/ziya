/**
 * Tests for the Mermaid gantt task-label colon preprocessor.
 *
 * Mermaid's gantt parser splits a task line at the FIRST colon: text before
 * it is the label, text after it is the comma-separated task data. A colon
 * inside the label pushes the label remainder into the data list, so
 * parseData() sees four fields, falls through its switch without setting
 * raw.startTime, and compileTask() throws
 *   TypeError: Cannot read properties of undefined (reading 'type')
 * which surfaces as an empty SVG / "parsing failed" in the plugin.
 *
 * The preprocessor substitutes a fullwidth colon (U+FF1A) for colons that
 * appear in the label portion only, leaving the data section untouched.
 */

import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const FULLWIDTH_COLON = '\uFF1A';

// The definition from the reported failure.
const FAILING_GANTT = `gantt
    title Decision window vs. program timeline
    dateFormat YYYY-MM-DD
    axisFormat %b %Y
    todayMarker off

    section Attrition
    15 senior departures            :crit, dep, 2026-05-21, 91d

    section Your ask
    Meeting with Janet              :milestone, m1, 2026-08-21, 0d
    Window to act before launch     :active, win, 2026-08-21, 60d

    section Program
    Remaining technical deficit     :crit, td, 2026-08-21, 60d
    Launch                          :milestone, ml, 2026-10-20, 0d

    section Amazon comp cycle
    Next annual cycle (Apr 2027)    :milestone, cyc, 2027-04-01, 0d
    Gap: launch to next cycle       :done, gap, 2026-10-20, 163d`;

/**
 * Model of Mermaid's own parseData() field counting: strip leading tags from
 * the front of the list (getTaskTags only matches whole fields), then require
 * 1-3 remaining fields. Anything else leaves startTime undefined upstream.
 */
const TAGS = ['active', 'done', 'crit', 'milestone', 'vert'];
function mermaidDataFieldCount(taskLine: string): number {
  const colon = taskLine.indexOf(':');
  const fields = taskLine.slice(colon + 1).split(',').map(f => f.trim());
  while (fields.length > 0 && TAGS.includes(fields[0].toLowerCase())) {
    fields.shift();
  }
  return fields.length;
}

function taskLines(def: string): string[] {
  return def.split('\n')
    .map(l => l.trim())
    .filter(l => l.includes(':'))
    .filter(l => !/^(?:%%|(?:gantt|title|dateFormat|axisFormat|tickInterval|todayMarker|excludes|includes|section)\b)/.test(l));
}

describe('gantt task-label colon preprocessor', () => {
  it('reproduces the parser-breaking shape without the fix', () => {
    // Guards against the test passing for the wrong reason: the raw input
    // really does yield a field count Mermaid cannot handle.
    const offending = taskLines(FAILING_GANTT).find(l => l.startsWith('Gap:'));
    expect(offending).toBeDefined();
    expect(mermaidDataFieldCount(offending!)).toBeGreaterThan(3);
  });

  it('leaves every task line with a parseable field count after preprocessing', () => {
    const result = preprocessDefinition(FAILING_GANTT, 'gantt');
    const lines = taskLines(result);
    expect(lines.length).toBeGreaterThan(0);
    for (const line of lines) {
      const count = mermaidDataFieldCount(line);
      expect(count).toBeGreaterThanOrEqual(1);
      expect(count).toBeLessThanOrEqual(3);
    }
  });

  it('substitutes the label colon rather than deleting the task', () => {
    const result = preprocessDefinition(FAILING_GANTT, 'gantt');
    expect(result).toContain(`Gap${FULLWIDTH_COLON} launch to next cycle`);
    expect(result).not.toContain('Gap: launch to next cycle');
    // Data section is preserved verbatim.
    expect(result).toMatch(/:done,\s*gap,\s*2026-10-20,\s*163d/);
  });

  it('does not touch task lines whose labels are already colon-free', () => {
    const result = preprocessDefinition(FAILING_GANTT, 'gantt');
    expect(result).toContain('Launch                          :milestone, ml, 2026-10-20, 0d');
    expect(result).toContain('15 senior departures            :crit, dep, 2026-05-21, 91d');
    expect(result).not.toContain(`Launch${FULLWIDTH_COLON}`);
  });

  it('does not treat a time value inside the data section as the separator', () => {
    const input = `gantt
    dateFormat HH:mm
    axisFormat %H:%M
    section Shift
    Standup :done, su, 10:30, 15m`;
    const result = preprocessDefinition(input, 'gantt');
    expect(result).toContain('Standup :done, su, 10:30, 15m');
    expect(result).not.toContain(FULLWIDTH_COLON);
  });

  it('does not substitute a colon when the line has no data section', () => {
    // A bare "Note: ..." line has no task data, so the colon is not a
    // separator and must not be rewritten by this preprocessor. (A later
    // preprocessor completes it into a task; that is out of scope here.)
    const input = `gantt
    dateFormat YYYY-MM-DD
    section Notes
    Note: see the attached doc`;
    const result = preprocessDefinition(input, 'gantt');
    expect(result).not.toContain(FULLWIDTH_COLON);
    expect(result).toContain('Note');
  });

  it('handles a label colon with no surrounding space', () => {
    const input = `gantt
    dateFormat YYYY-MM-DD
    section Phases
    Phase1:Design work :active, d1, 2026-01-01, 5d`;
    const result = preprocessDefinition(input, 'gantt');
    expect(result).toContain(`Phase1${FULLWIDTH_COLON}Design work :active, d1, 2026-01-01, 5d`);
    expect(mermaidDataFieldCount(taskLines(result)[0])).toBe(3);
  });

  it('handles relative "after" references in the data section', () => {
    const input = `gantt
    dateFormat YYYY-MM-DD
    section Phases
    Step 2: verification :crit, s2, after s1, 10d`;
    const result = preprocessDefinition(input, 'gantt');
    expect(result).toContain(`Step 2${FULLWIDTH_COLON} verification`);
    expect(result).toMatch(/:crit,\s*s2,\s*after s1,\s*10d/);
    expect(mermaidDataFieldCount(taskLines(result)[0])).toBe(3);
  });

  it('does not alter the title directive, which may legitimately contain a colon', () => {
    const input = `gantt
    title Roadmap: FY27
    dateFormat YYYY-MM-DD
    section Work
    Task A :done, a, 2026-01-01, 5d`;
    const result = preprocessDefinition(input, 'gantt');
    expect(result).toContain('title Roadmap: FY27');
  });

  it('leaves non-gantt diagrams untouched', () => {
    const input = `sequenceDiagram
    Alice->>Bob: Status: green`;
    const result = preprocessDefinition(input, 'sequenceDiagram');
    expect(result).not.toContain(FULLWIDTH_COLON);
  });
});
