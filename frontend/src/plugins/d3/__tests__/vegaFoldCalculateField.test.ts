/**
 * @jest-environment jsdom
 *
 * A calculate that relabels a fold key (`as: "Stage"`) must survive
 * preprocessing.  FOLD-FIELD-MAP-FIX (Fix 1.6) treated only raw data columns
 * and fold outputs as real fields, so it rewrote `color.field: "Stage"` back
 * to the raw fold key `stage` and the legend showed column names instead of
 * the author's labels.
 *
 * Seam: the spec goes through the real vegaLitePlugin.render (dataset
 * resolution included, via a stubbed same-origin fetch) and the assertion is
 * on the spec handed to vega-embed.
 */
const mockEmbedded: any[] = [];
// Plain async function, not jest.fn: CRA's resetMocks would wipe a jest.fn
// implementation before each test.
jest.mock('vega-embed', () => ({
  __esModule: true,
  default: async (el: HTMLElement, spec: any) => {
    mockEmbedded.push(JSON.parse(JSON.stringify(spec)));
    const doc = (globalThis as any).document;
    el.appendChild(doc.createElementNS('http://www.w3.org/2000/svg', 'svg'));
    return {
      view: {
        addSignalListener() {}, addEventListener() {}, width: () => 600, height: () => 220,
        runAsync: async () => {}, resize() { return this; }, signal() { return this; }, data: () => [],
      },
      spec, finalize() {},
    };
  },
}));

import { vegaLitePlugin } from '../vegaLitePlugin';

const ID = 'ds_7374f6568dd6abe2';
const rows = Array.from({ length: 30 }, (_, i) => ({
  t: new Date(Date.UTC(2026, 9, 6, 0, 25, i * 10)).toISOString(),
  n: 570, loss_pct: 5 + (i % 7), dn_loss_pct: i % 3, other_loss_pct: 5,
}));

const spec = (colorField: string) => ({
  $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
  width: 'container', height: 220,
  data: { url: `ziya://dataset/${ID}` },
  transform: [
    { fold: ['dn_loss_pct', 'other_loss_pct'], as: ['stage', 'pct'] },
    { calculate: "datum.stage == 'dn_loss_pct' ? 'downlink' : 'uplink / other'", as: 'Stage' },
  ],
  mark: { type: 'bar', width: 2 },
  encoding: {
    x: { field: 't', type: 'temporal' },
    y: { field: 'pct', type: 'quantitative', stack: true },
    color: { field: colorField, type: 'nominal' },
  },
});

beforeAll(() => {
  (global as any).ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  (global as any).fetch = async () => ({
    ok: true,
    json: async () => ({ id: ID, row_count: rows.length, returned: rows.length, truncated: false, rows }),
  });
});

beforeEach(() => { mockEmbedded.length = 0; });

async function embeddedFor(s: any) {
  const c = document.createElement('div');
  document.body.appendChild(c);
  await vegaLitePlugin.render(c, null, s, false);
  return mockEmbedded[mockEmbedded.length - 1];
}

it('the dataset rows are inlined (the path ran)', async () => {
  const out = await embeddedFor(spec('Stage'));
  expect(out.data.values).toHaveLength(rows.length);
  expect(out.data.url).toBeUndefined();
});

it('keeps a color field produced by a calculate transform', async () => {
  const out = await embeddedFor(spec('Stage'));
  expect(out.encoding.color.field).toBe('Stage');
});

it('still repairs a field produced by no transform at all', async () => {
  // Positive control for the repair itself: "Series" exists nowhere, so the
  // color channel is still mapped to the fold key.
  const out = await embeddedFor(spec('Series'));
  expect(out.encoding.color.field).toBe('stage');
});
