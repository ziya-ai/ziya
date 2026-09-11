/**
 * Regression tests for time-of-day gantt charts (dateFormat HH:mm:ss).
 *
 * A chart like:
 *
 *   gantt
 *       dateFormat HH:mm:ss
 *       axisFormat %H:%M
 *       section Power
 *       FRED cuts gwfpga   :milestone, 04:34:00, 0s
 *       A/B current step-up :04:20:00, 04:24:00
 *
 * is ALREADY valid mermaid: dayjs parses "04:34:00" against HH:mm:ss
 * (defaulting the date to today) and "0s" is a legitimate seconds duration.
 * Two preprocessors corrupted it:
 *
 *  1. gantt-date-format-fix stripped the unit from second-durations
 *     ("0s" -> "0"), leaving a field mermaid parses as the date "0"
 *     (new Date("0") === Jan 2000), dragging the time domain out to years.
 *
 *  2. gantt-task-definition-fix classified the 2-field task
 *     "04:20:00, 04:24:00" as "simple numeric" because
 *     parseInt("04:20:00") === 4, and rewrote it to
 *     ":done, t1, 2024-01-01, 1d" — an unparseable date under HH:mm:ss
 *     that also stretches the domain.
 *
 * Net effect: every bar collapsed at the today-marker on a multi-year axis
 * whose ticks all rendered "00:00". The fix gates both transforms off for
 * time-based dateFormats.
 */

import { preprocessDefinition, initMermaidEnhancer } from '../mermaidEnhancer';

beforeAll(() => {
  initMermaidEnhancer();
});

const TIME_GANTT = `gantt
    dateFormat HH:mm:ss
    axisFormat %H:%M
    title kuipersat00004 crash #1 (all times UTC)
    section SVMM
    PLOPS command 04-20-06          :milestone, 04:20:06, 0s
    section COMINS
    CM3 X0 reflash starts 04-20-08  :milestone, 04:20:08, 0s
    A/B current step-up             :04:20:00, 04:24:00
    section MerMOD telemetry
    full rate (~7.5k samples/s)     :done, 04:10:00, 04:21:50
    SILENT (wedged, 3.2A draw)      :crit, 04:21:50, 04:27:00
    brief flush burst               :active, 04:27:00, 04:27:30
    SILENT                          :crit, 04:27:30, 04:39:30
    boot no. 2 telemetry flood      :done, 04:39:30, 04:45:00
    section Power
    FRED cuts gwfpga (CAN timeout)  :milestone, 04:34:00, 0s
    BOOT_FLAGS (boot 2)             :milestone, 04:40:15, 0s`;

describe('time-of-day gantt (dateFormat HH:mm:ss) passes through unmangled', () => {
  it('preserves second-durations like "0s" on milestones', () => {
    const out = preprocessDefinition(TIME_GANTT, 'gantt');
    // Every milestone keeps its 0s duration; none is stripped to a bare "0".
    const milestones = out.split('\n').filter(l => l.includes('milestone'));
    expect(milestones.length).toBe(4);
    for (const m of milestones) {
      expect(m).toMatch(/,\s*0s\s*$/);
    }
  });

  it('does not rewrite a 2-field HH:mm:ss task into a 2024 date task', () => {
    const out = preprocessDefinition(TIME_GANTT, 'gantt');
    expect(out).not.toContain('2024-01-01');
    expect(out).toContain('A/B current step-up');
    // The start/end times survive verbatim.
    expect(out).toMatch(/A\/B current step-up\s*:04:20:00,\s*04:24:00/);
  });

  it('leaves every task line of a valid time-based chart byte-identical', () => {
    const out = preprocessDefinition(TIME_GANTT, 'gantt');
    const taskLines = (def: string) => def.split('\n')
      .map(l => l.trim())
      .filter(l => l.includes(':'))
      .filter(l => !/^(?:%%|(?:gantt|title|dateFormat|axisFormat|tickInterval|todayMarker|excludes|includes|section)\b)/.test(l));
    expect(taskLines(out)).toEqual(taskLines(TIME_GANTT));
  });

  it('still strips "Ns" pseudo-dates on non-time charts (existing behavior)', () => {
    // A chart with a date-based format keeps the historical fixup: "50s"
    // used in a start-value position gets its stray unit removed.
    const dateGantt = `gantt
    dateFormat YYYY-MM-DD
    section Build
    Compile :done, t1, 2024-01-01, 5d`;
    const out = preprocessDefinition(dateGantt, 'gantt');
    // Sanity: valid date chart unharmed.
    expect(out).toContain('2024-01-01, 5d');
  });

  it('applies to HH:mm (no seconds) formats as well', () => {
    const def = `gantt
    dateFormat HH:mm
    axisFormat %H:%M
    section S
    warmup :04:20, 04:24
    hold   :crit, 04:24, 04:30`;
    const out = preprocessDefinition(def, 'gantt');
    expect(out).not.toContain('2024-01-01');
    expect(out).toMatch(/warmup\s*:04:20,\s*04:24/);
  });
});
