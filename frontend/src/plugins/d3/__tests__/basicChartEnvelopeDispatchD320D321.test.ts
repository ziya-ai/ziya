/**
 * Envelope dispatch for the basic-chart engine (D-320, D-321, and the D-001
 * regression that shares this root cause).
 *
 * THE BUG
 *
 * D3Renderer.initializeVisualization unwrapped a {type, definition} envelope
 * ONLY when the outer type was exactly 'd3' AND the inner spec carried a
 * truthy `type`.  Two shapes that render_diagram legitimately emits therefore
 * matched no plugin and hung to the ~30s render timeout with an empty DOM:
 *
 *   D-320  {type:'basic-chart', definition:{type:'bar', data:[...]}}
 *          outer type is the ENGINE ID, not 'd3', so no unwrap happened;
 *          basicChart.canHandle rejects the 'basic-chart' envelope, so no
 *          plugin mounted.  (45 specs; also the cause of the D-001 regression
 *          on the explicit-width w2-08..11 specs, which stopped dispatching.)
 *
 *   D-321  {type:'d3', definition:'{"data":[{"label":"A","value":1}]}'}
 *          a typeless inner: the unwrap parsed it but the `inner.type` guard
 *          declined to substitute it, so the envelope stayed wrapped and
 *          basicChart.canHandle rejected it.
 *
 * THE CONTRACT (unwrapDiagramEnvelope)
 *
 *   - unwrap 'd3' AND 'basic-chart' outer envelopes;
 *   - substitute the inner even when it has no own `type` (basicChart claims a
 *     typeless {data:[{label,value}]} by shape);
 *   - leave EVERY OTHER engine's envelope wrapped, because graphviz, d2,
 *     chord, drawio, force-directed, railroad, packet, music, wavedrom and
 *     flamegraph unwrap their own envelope inside canHandle/render keyed to
 *     their specific type -- generic unwrapping would strip that type.
 *
 * NON-VACUITY: every "unwraps"/"claims" case below is red against the pre-fix
 * logic (which unwrapped only type==='d3' with a truthy inner.type), and every
 * "left wrapped" guard is green both ways -- pinning that the widening did not
 * spill onto the self-unwrapping engines.
 */
import { unwrapDiagramEnvelope } from '../../../utils/d3EnvelopeUnwrap';
import { basicChartPlugin } from '../basicChart';

describe('unwrapDiagramEnvelope — basic-chart engine-id envelope (D-320)', () => {
    it('unwraps {type:"basic-chart", definition:{type:"bar",...}} to the inner spec', () => {
        const env = {
            type: 'basic-chart',
            definition: {
                type: 'bar',
                data: [
                    { label: 'Jan', value: 42 },
                    { label: 'Feb', value: 58 },
                ],
            },
        };
        const out = unwrapDiagramEnvelope(env);
        // Pre-fix: env was returned unchanged (type 'basic-chart' !== 'd3').
        expect(out.type).toBe('bar');
        expect(out.definition).toBeUndefined();
        // The unwrapped spec is what actually dispatches; the raw envelope did not.
        expect(basicChartPlugin.canHandle(out)).toBe(true);
        expect(basicChartPlugin.canHandle(env)).toBe(false);
    });

    it('preserves explicit width/height on the inner spec (D-001 regression)', () => {
        const env = {
            type: 'basic-chart',
            definition: {
                type: 'bar',
                width: 3200,
                height: 180,
                data: [{ label: '1000', value: 19 }],
            },
        };
        const out = unwrapDiagramEnvelope(env);
        // Dispatch broke before these specs could reach the container-dims
        // code path; once unwrapped, the explicit dims flow through untouched.
        expect(out.width).toBe(3200);
        expect(out.height).toBe(180);
        expect(basicChartPlugin.canHandle(out)).toBe(true);
    });
});

describe('unwrapDiagramEnvelope — typeless d3 envelope (D-321)', () => {
    it('substitutes a typeless inner {data:[{label,value}]} parsed from a string', () => {
        const env = {
            type: 'd3',
            definition: JSON.stringify({
                data: [
                    { label: 'Alpha', value: 30 },
                    { label: 'Beta', value: 55 },
                    { label: 'Gamma', value: 18 },
                ],
            }),
        };
        const out = unwrapDiagramEnvelope(env);
        // Pre-fix: the inner.type guard left this wrapped (out === env), so
        // basicChart declined it and the render hung.
        expect(out.type).toBeUndefined();
        expect(Array.isArray(out.data)).toBe(true);
        expect(out.data).toHaveLength(3);
        expect(basicChartPlugin.canHandle(out)).toBe(true);
        expect(basicChartPlugin.canHandle(env)).toBe(false);
    });

    it('substitutes a typeless inner supplied as an OBJECT definition', () => {
        const env = {
            type: 'd3',
            definition: { data: [{ label: 'A', value: 1 }, { label: 'B', value: 2 }] },
        };
        const out = unwrapDiagramEnvelope(env);
        expect(out.type).toBeUndefined();
        expect(basicChartPlugin.canHandle(out)).toBe(true);
    });
});

describe('unwrapDiagramEnvelope — self-unwrapping engines are left wrapped', () => {
    it('a graphviz STRING envelope is NOT unwrapped (type preserved for its own dispatch)', () => {
        const env = { type: 'graphviz', definition: 'digraph { a -> b }' };
        const out = unwrapDiagramEnvelope(env);
        expect(out).toBe(env);
        expect(out.type).toBe('graphviz');
    });

    it('a railroad OBJECT envelope is NOT unwrapped (would strip type="railroad")', () => {
        const env = { type: 'railroad', definition: { diagram: { sequence: ['SELECT'] } } };
        const out = unwrapDiagramEnvelope(env);
        expect(out).toBe(env);
        expect(out.type).toBe('railroad');
    });
});

describe('unwrapDiagramEnvelope — no-op passthrough', () => {
    it('a direct chart spec (no definition key) is returned unchanged', () => {
        const spec = { type: 'bar', data: [{ label: 'A', value: 1 }] };
        expect(unwrapDiagramEnvelope(spec)).toBe(spec);
        expect(basicChartPlugin.canHandle(spec)).toBe(true);
    });

    it('a non-object spec is returned unchanged', () => {
        expect(unwrapDiagramEnvelope('not a spec')).toBe('not a spec');
        expect(unwrapDiagramEnvelope(null)).toBeNull();
    });
});
