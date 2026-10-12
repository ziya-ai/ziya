/**
 * A layered chart whose top-level transform folds columns must keep the
 * folded rows in the layer that plots them.
 *
 * Reported: "Fleet-wide SVMM transitions into ORBIT_ADJUST per UTC day"
 * rendered axes, the rule and its label, but no line or points. The spec
 * folded e/s into k/v, derived \`series\` with a calculate, and plotted
 * x=d, y=v, color=series in layer 0. FOLD-DATA-FIX saw x=d (not a folded
 * field, present in the raw rows) and gave layer 0 its own copy of the raw
 * rows. A layer with its own data does not get the parent's transform, so
 * v and series did not exist for that layer and nothing was drawn.
 * (Checked with vega + vega-lite in Node: parent data draws 6 points plus 2
 * legend swatches; the raw-row layer draws only the legend swatch. Jest
 * cannot load the ESM vega build, so that check is not repeated here.)
 *
 * Fold keeps every unfolded field, so referencing one never needed raw data.
 * The fix is kept only for a layer that plots a folded field directly and
 * uses no transform output.
 *
 * DIRECTION: against the unpatched plugin layersNeedingPreTransformData does
 * not exist and the seam test finds the old per-channel loop.
 */
import * as fs from 'fs';
import * as path from 'path';
import { layersNeedingPreTransformData } from '../vegaLitePlugin';

const rows = [
    { d: '2026-09-06', e: 301, s: 142 },
    { d: '2026-09-07', e: 328, s: 138 },
];

function reported(): any {
    return {
        data: { values: rows },
        transform: [
            { fold: ['e', 's'], as: ['k', 'v'] },
            { calculate: "datum.k=='e'?'entries':'replicas (approx)'", as: 'series' },
        ],
        layer: [
            {
                mark: { type: 'line', point: true },
                encoding: {
                    x: { field: 'd', type: 'temporal' },
                    y: { field: 'v', type: 'quantitative' },
                    color: { field: 'series', type: 'nominal' },
                },
            },
            { mark: 'rule', encoding: { y: { datum: 60 } } },
            {
                mark: 'text',
                encoding: { x: { datum: '2026-09-06' }, y: { datum: 60 }, text: { value: 'ref' } },
            },
        ],
    };
}

describe('layersNeedingPreTransformData', () => {
    it('leaves a layer that uses fold/calculate outputs on the parent data (reported spec)', () => {
        expect(layersNeedingPreTransformData(reported())).toEqual([]);
    });

    it('still picks a layer that plots a folded field directly', () => {
        const spec = reported();
        spec.layer.push({ mark: 'bar', encoding: { x: { field: 'd' }, y: { field: 'e', aggregate: 'sum' } } });
        expect(layersNeedingPreTransformData(spec)).toEqual([3]);
    });

    it('treats the default fold outputs key/value as derived', () => {
        const spec = reported();
        spec.transform = [{ fold: ['e', 's'] }];
        spec.layer[0].encoding = { x: { field: 'd' }, y: { field: 'value' }, color: { field: 'key' } };
        expect(layersNeedingPreTransformData(spec)).toEqual([]);
    });

    it('sees derived fields inside tooltip arrays and conditions', () => {
        const spec = reported();
        spec.layer.push({ mark: 'point', encoding: { x: { field: 'e' }, tooltip: [{ field: 'series' }] } });
        spec.layer.push({
            mark: 'point',
            encoding: { x: { field: 'e' }, color: { condition: { test: 'true', field: 'k' }, value: 'red' } },
        });
        expect(layersNeedingPreTransformData(spec)).toEqual([]);
    });

    it('does not touch layers that bring their own data or transform', () => {
        const spec = reported();
        spec.layer.push({ data: { values: rows }, mark: 'bar', encoding: { y: { field: 'e' } } });
        spec.layer.push({ transform: [{ filter: 'true' }], mark: 'bar', encoding: { y: { field: 'e' } } });
        expect(layersNeedingPreTransformData(spec)).toEqual([]);
    });

    it('does nothing without a fold, or when a pivot makes outputs unknowable', () => {
        const noFold = reported();
        noFold.transform = [{ calculate: '1', as: 'z' }];
        noFold.layer[0].encoding = { y: { field: 'e' } };
        expect(layersNeedingPreTransformData(noFold)).toEqual([]);
        const pivot = reported();
        pivot.transform.push({ pivot: 'k', value: 'v' });
        pivot.layer[0].encoding = { y: { field: 'e' } };
        expect(layersNeedingPreTransformData(pivot)).toEqual([]);
    });
});

describe('seam: the post-process block uses the helper', () => {
    const src = fs.readFileSync(path.join(__dirname, '../vegaLitePlugin.ts'), 'utf8');
    const start = src.indexOf("console.log('🔧 VEGA-POST-PROCESS: Fixing layered chart scales and legends')");
    const end = src.indexOf('layer.encoding?.y?.scale?.domain', start);
    const block = src.slice(start, end);

    it('finds the block', () => {
        expect(start).toBeGreaterThan(-1);
        expect(end).toBeGreaterThan(start);
        expect(block).toContain('FOLD-DATA-FIX');
    });

    it('assigns layer data only for indices the helper returns', () => {
        expect(block).toContain('layersNeedingPreTransformData(vegaSpec)');
        expect(block).not.toContain('!foldedFields.includes(channelSpec.field)');
    });
});
