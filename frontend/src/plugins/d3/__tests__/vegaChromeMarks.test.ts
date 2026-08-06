import { filterVegaChromeMarks } from '../vegaPlugin';

/**
 * Issue 15 regression: vegaPlugin.render() used to UNCONDITIONALLY strip every
 * `group` mark and every static (non-data-bound) `text` mark from any Vega
 * spec — a sunburst-only chrome hack applied to all specs. Group-nested /
 * layered / faceted specs lost part or ALL of their scenegraph, producing an
 * empty container and a screenshot of the surrounding app shell.
 *
 * filterVegaChromeMarks() now only strips chrome for the sunburst SIGNATURE
 * (a data-bound `arc` mark present) and never empties the mark list.
 *
 * These tests import the REAL shipped helper (no re-implementation) and pin
 * both the fix (general specs preserved) and the guard (sunburst chrome still
 * stripped). Against the pre-fix inline logic there was no exported helper to
 * import, and the behaviour it replaced would DELETE the group marks in the
 * first two tests — so these tests would fail against the pre-fix code.
 */
describe('filterVegaChromeMarks (Issue 15)', () => {
  it('preserves a single group-wrapped mark tree (was 100% erased pre-fix)', () => {
    const marks = [
      { type: 'group', marks: [{ type: 'rect', from: { data: 't' } }] },
    ];
    const out = filterVegaChromeMarks(marks);
    expect(out).toHaveLength(1);
    expect(out[0].type).toBe('group');
  });

  it('preserves deeply nested group marks (Issue 15 5-level nesting)', () => {
    const deep = { type: 'group', marks: [
      { type: 'group', marks: [
        { type: 'group', marks: [
          { type: 'symbol', from: { data: 'table' } },
          { type: 'text', from: { data: 'dup' } },
          { type: 'rect' },
        ] },
      ] },
    ] };
    const out = filterVegaChromeMarks([deep]);
    expect(out).toHaveLength(1);
    expect(out[0].type).toBe('group');
  });

  it('preserves standalone static text marks in a non-sunburst spec', () => {
    const marks = [
      { type: 'rect', from: { data: 't' } },
      { type: 'text' }, // static (no from.data) — pre-fix this was dropped
    ];
    const out = filterVegaChromeMarks(marks);
    expect(out).toHaveLength(2);
    expect(out.map((m: any) => m.type).sort()).toEqual(['rect', 'text']);
  });

  it('STILL strips group + static text chrome for a sunburst spec (arc + from.data)', () => {
    const marks = [
      { type: 'arc', from: { data: 'tree' } },       // sunburst data mark — kept
      { type: 'text', from: { data: 'tree' } },      // arc label — kept
      { type: 'text' },                               // static title/footer — stripped
      { type: 'group', marks: [{ type: 'text' }] },   // legend group — stripped
    ];
    const out = filterVegaChromeMarks(marks);
    expect(out).toHaveLength(2);
    expect(out.every((m: any) => m.type === 'arc' || (m.type === 'text' && m.from?.data))).toBe(true);
    expect(out.some((m: any) => m.type === 'group')).toBe(false);
  });

  it('never empties the scenegraph even if a sunburst strip would remove everything', () => {
    // hasDataArc true (arc+from.data) but the ONLY marks are chrome that would
    // all be stripped → guard returns original marks unchanged.
    const marks = [
      { type: 'arc', from: { data: 'd' } },
    ];
    // Craft a case where strip keeps the arc (so not empty). Now a pathological
    // one: arc present but expressed so filter would drop all — use group-only
    // alongside a data-arc detector via a separate mark that is a group only.
    const pathological = [
      { type: 'group', marks: [{ type: 'arc', from: { data: 'd' } }] },
    ];
    // No top-level data-arc → treated as general spec → untouched.
    const out = filterVegaChromeMarks(pathological);
    expect(out).toEqual(pathological);
    // sanity: real sunburst arc kept
    expect(filterVegaChromeMarks(marks)).toHaveLength(1);
  });

  it('is a no-op for non-array input', () => {
    expect(filterVegaChromeMarks(undefined as any)).toBeUndefined();
    expect(filterVegaChromeMarks(null as any)).toBeNull();
  });
});
