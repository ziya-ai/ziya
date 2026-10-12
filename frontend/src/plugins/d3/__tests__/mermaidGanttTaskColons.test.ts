/**
 * A colon inside a gantt task NAME (mermaidGanttTaskColons).
 *
 * Reported 2026-10-11: a task named "In service (contacts from 04:29)" failed
 * the whole chart with "Invalid date:29) :10-10 02:16".  Mermaid's gantt
 * lexer ends the task name at the FIRST colon, so the data it parsed was
 * "29) :10-10 02:16, 10-11 07:00".  The repair rewrites colons before the
 * real separator to `#58;`, which mermaid renders back as ":" (verified with
 * a live render of `... 04#58;29) :10-10 02:16, 10-11 07:00`).
 *
 * Non-vacuous: the module does not exist pre-fix, so this suite cannot
 * compile against the unpatched tree; the seam block also fails if the
 * module exists but the enhancer never registers it.
 */
import * as fs from 'fs';
import * as path from 'path';
import { escapeGanttTaskNameColons, isGanttTaskData } from '../mermaidGanttTaskColons';

const REPORTED = [
  'gantt',
  '    dateFormat MM-DD HH:mm',
  '    axisFormat %m-%d',
  '    title bli501-kaa3 LWS phase and image',
  '    section Old image 2.1.9884',
  '    In service (contacts) :done, 10-01 07:51, 10-02 20:00',
  '    MaintenanceOnline, 0 contacts :crit, 10-02 20:00, 10-10 00:38',
  '    section OTA',
  '    OutOfService + reflash :active, 10-10 00:38, 10-10 02:16',
  '    section New image 2.1.11446',
  '    In service (contacts from 04:29) :10-10 02:16, 10-11 07:00',
].join('\n');

describe('escapeGanttTaskNameColons — the reported chart', () => {
  it('escapes the colon in the task name and nothing else', () => {
    const out = escapeGanttTaskNameColons(REPORTED);
    const inLines = REPORTED.split('\n');
    const outLines = out.split('\n');
    expect(outLines).toHaveLength(inLines.length);
    expect(outLines[10]).toBe('    In service (contacts from 04#58;29) :10-10 02:16, 10-11 07:00');
    // Every other line, including dateFormat and the time-bearing data, is untouched.
    outLines.forEach((l, i) => { if (i !== 10) expect(l).toBe(inLines[i]); });
  });

  it('is idempotent', () => {
    const once = escapeGanttTaskNameColons(REPORTED);
    expect(escapeGanttTaskNameColons(once)).toBe(once);
  });
});

describe('escapeGanttTaskNameColons — lines that are already valid', () => {
  it.each([
    'A task :a1, 2024-01-01, 30d',
    'Another :after a1, 20d',
    'Done thing :done, des1, 2014-01-06, 2014-01-08',
    'Milestone :milestone, m1, 2024-01-10, 0d',
    'Clock task :04:20:06, 04:25:00',
    'Window :10-10 02:16, 10-11 07:00',
    'Until :a2, 2024-01-01, until b1',
    'No data at all',
  ])('%s', (task) => {
    const def = `gantt\n    dateFormat YYYY-MM-DD\n    ${task}`;
    expect(escapeGanttTaskNameColons(def)).toBe(def);
  });
});

describe('escapeGanttTaskNameColons — names carrying colons', () => {
  it.each([
    ['Phase 1: build :a1, 2024-01-01, 3d', 'Phase 1#58; build :a1, 2024-01-01, 3d'],
    ['Ratio 3:2 :crit, 2024-01-01, 1d', 'Ratio 3#58;2 :crit, 2024-01-01, 1d'],
    ['Run 04:29 :05:00, 06:00', 'Run 04#58;29 :05:00, 06:00'],
    ['a:b:c :after x, 2h', 'a#58;b#58;c :after x, 2h'],
  ])('%s', (input, expected) => {
    expect(escapeGanttTaskNameColons(`gantt\n    ${input}`)).toBe(`gantt\n    ${expected}`);
  });

  it('leaves a line alone when no colon yields valid task data', () => {
    const def = 'gantt\n    Note: this is prose: no data here';
    expect(escapeGanttTaskNameColons(def)).toBe(def);
  });
});

describe('escapeGanttTaskNameColons — scoping', () => {
  it('never touches directive, keyword or comment lines', () => {
    const def = [
      '%%{init: {"theme": "dark"}}%%',
      'gantt',
      '    title Release: v1 :a1, 3d',
      '    accTitle: Schedule: overview',
      '    dateFormat HH:mm',
      '    click a1 href "https://example.com:8443/x"',
      '    %% note: a1 :b, 1d',
      '    Task :a1, 01:00, 1h',
    ].join('\n');
    expect(escapeGanttTaskNameColons(def)).toBe(def);
  });

  it('returns non-gantt diagrams byte-identical', () => {
    const flow = 'flowchart TD\n    A[Step 1: go :a1, 3d] --> B';
    expect(escapeGanttTaskNameColons(flow)).toBe(flow);
  });

  it('tolerates non-string input', () => {
    expect(escapeGanttTaskNameColons(null as any)).toBeNull();
  });
});

describe('isGanttTaskData', () => {
  it('accepts mermaid task metadata and rejects name fragments', () => {
    expect(isGanttTaskData('10-10 02:16, 10-11 07:00')).toBe(true);
    expect(isGanttTaskData(' done, a1, 2024-01-01, 3d')).toBe(true);
    expect(isGanttTaskData('29) :10-10 02:16, 10-11 07:00')).toBe(false);
    expect(isGanttTaskData('29 :10-10, 1d')).toBe(false);
    expect(isGanttTaskData('')).toBe(false);
    expect(isGanttTaskData('a1, ')).toBe(false);
  });
});

describe('seam — mermaidEnhancer registers the repair', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'mermaidEnhancer.ts'), 'utf8');

  it('imports the helper', () => {
    expect(src).toMatch(/import\s*\{[^}]*escapeGanttTaskNameColons[^}]*\}\s*from\s*'\.\/mermaidGanttTaskColons'/);
  });

  it('registers it for gantt ahead of the other gantt passes', () => {
    const call = src.indexOf('escapeGanttTaskNameColons(definition)');
    expect(call).toBeGreaterThan(-1);
    const reg = src.slice(call, call + 400);
    expect(reg).toMatch(/name:\s*'gantt-task-name-colon-escape'/);
    expect(reg).toMatch(/diagramTypes:\s*\['gantt'\]/);
    const prio = Number((reg.match(/priority:\s*(\d+)/) || [])[1]);
    // gantt-year-offset-fix is 130 and the task-definition fix registers below it;
    // higher runs first, so the escape must be above 130.
    expect(prio).toBeGreaterThan(130);
  });
});
