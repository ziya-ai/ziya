/**
 * Regression test for D-511 — faceted Vega group-mark title reads the facet
 * key via `datum.<field>` and renders "pundefined" because a mark title's text
 * is evaluated in the TITLE's own data scope, where the facet datum is reached
 * only through `parent`.
 *
 * Imports the REAL module so it detects drift in the shipped sanitizer.
 *
 * Would this FAIL against the pre-fix code? Yes — `sanitizeVegaFacetGroupTitles`
 * did not exist (the import would throw) and, with it absent from
 * `sanitizeVegaSpec`, the title signal would remain `'p' + datum.panel`. Both
 * directions are pinned: the faceted title's `datum.<groupby-field>` is
 * rewritten to `parent.<field>`, while `encode` datum references, non-facet
 * titles, and non-groupby fields are preserved untouched.
 */
import {
  sanitizeVegaFacetGroupTitles,
  sanitizeVegaSpec,
} from '../vegaGraphSanitizer';

const facetedSpec = () => ({
  $schema: 'https://vega.github.io/schema/vega/v5.json',
  marks: [
    {
      type: 'group',
      from: { facet: { name: 'pf', data: 'raw', groupby: ['panel', 'col', 'row'] } },
      encode: {
        update: {
          x: { signal: 'datum.col * (cw + 14)' },
          y: { signal: 'datum.row * (ch + 20)' },
        },
      },
      title: { text: { signal: "'p' + datum.panel" }, fontSize: 6 },
      marks: [{ type: 'line', from: { data: 'pf' } }],
    },
  ],
});

describe('D-511 faceted group-mark title facet-datum scope', () => {
  it('rewrites datum.<groupby field> to parent.<field> in the title text signal', () => {
    const spec = facetedSpec();
    const n = sanitizeVegaFacetGroupTitles(spec);
    expect(n).toBe(1);
    expect((spec.marks[0].title.text as any).signal).toBe("'p' + parent.panel");
  });

  it('leaves the group encode datum references untouched', () => {
    const spec = facetedSpec();
    sanitizeVegaFacetGroupTitles(spec);
    expect((spec.marks[0].encode.update.x as any).signal).toBe('datum.col * (cw + 14)');
    expect((spec.marks[0].encode.update.y as any).signal).toBe('datum.row * (ch + 20)');
  });

  it('rewrites bracket-form datum access and title.encode text signals', () => {
    const spec: any = {
      marks: [
        {
          type: 'group',
          from: { facet: { name: 'f', data: 'raw', groupby: ['name'] } },
          title: {
            encode: { update: { text: { signal: "datum['name'] + ' panel'" } } },
          },
        },
      ],
    };
    const n = sanitizeVegaFacetGroupTitles(spec);
    expect(n).toBe(1);
    expect(spec.marks[0].title.encode.update.text.signal).toBe("parent['name'] + ' panel'");
  });

  it('does not touch a non-faceted group title or a non-groupby field', () => {
    const spec: any = {
      marks: [
        {
          // no facet -> title datum is genuinely its own scope, leave alone
          type: 'group',
          title: { text: { signal: "'x' + datum.panel" } },
        },
        {
          type: 'group',
          from: { facet: { name: 'f', data: 'raw', groupby: ['panel'] } },
          // 'other' is not a groupby field -> not a facet key, leave alone
          title: { text: { signal: "'x' + datum.other" } },
        },
      ],
    };
    const n = sanitizeVegaFacetGroupTitles(spec);
    expect(n).toBe(0);
    expect(spec.marks[0].title.text.signal).toBe("'x' + datum.panel");
    expect(spec.marks[1].title.text.signal).toBe("'x' + datum.other");
  });

  it('is wired into sanitizeVegaSpec', () => {
    const spec = facetedSpec();
    sanitizeVegaSpec(spec);
    expect((spec.marks[0].title.text as any).signal).toBe("'p' + parent.panel");
  });
});
