/**
 * @jest-environment jsdom
 *
 * Object-definition unwrap contract, across every wrapper that takes a
 * {type, definition} envelope.
 *
 * THE BUG CLASS
 *
 * Wrappers unwrapped the envelope with
 *
 *     const definition = typeof rawSpec?.definition === 'string'
 *         ? rawSpec.definition
 *         : rawSpec;
 *
 * which is correct for the two shapes MarkdownRenderer produces (fence text is
 * always a string; a direct spec has no `definition` key) and wrong for the
 * third shape: a definition that is ALREADY AN OBJECT.  A ```d3 fence whose
 * JSON body is {type: 'railroad', definition: {...}} parses in D3Renderer
 * before any plugin sees it, so the plugin receives an object definition; the
 * ternary then falls through and hands the ENGINE the whole envelope.  The
 * failure reads as a spec error ("unknown key \"type\"", "Requires a
 * \"sections\" array") when the spec was fine and the wrapper mis-unwrapped
 * it -- or, for music, the plugin is never selected at all, which surfaces as
 * the renderer's ~30s no-plugin timeout.
 *
 * The fix was found in the timeline wrapper first (its wrapper test passes an
 * object definition; the siblings' tests never did), then applied to every
 * carrier: unwrap whenever the `definition` KEY exists, regardless of the
 * value's type.  These tests pin that contract per wrapper, at the seam where
 * each hands off to its engine.
 *
 * SEAM STRATEGY
 *
 * railroad and packet render fully in jsdom, so they are asserted end-to-end
 * (SVG out, no error marker).  wavedrom and flamegraph dynamically import
 * heavy libraries after validation, so their validators are replaced with a
 * deliberate "stop" that halts the render at the seam -- the assertion is on
 * WHAT THE VALIDATOR RECEIVED, which is exactly the value the engine would
 * get.  music's unwrap lives in resolveMusicSpec (shared by canHandle and
 * render), so it is asserted there plus at canHandle, where the pre-fix
 * failure was a silent non-selection.
 *
 * NON-VACUITY: all envelope-shaped cases fail against the pre-fix wrappers
 * (verified: the 6 envelope cases red before the fix -- one per wrapper, two
 * for music since canHandle and resolve are asserted separately -- with all 8
 * controls green both ways).
 */
import { railroadPlugin } from '../railroadPlugin';
import { packetPlugin } from '../packetPlugin';
import { musicPlugin } from '../musicPlugin';
import { wavedromPlugin } from '../wavedromPlugin';
import { flamegraphPlugin } from '../flamegraphPlugin';
import { resolveMusicSpec, isMusicSpec } from '../../../utils/d3Plugins/musicPlugin';

// Halt wavedrom/flamegraph at their validation seam so the test never reaches
// the dynamic import('wavedrom') / import('d3-flame-graph') that follows it.
// The spies still record what crossed the seam, which is the whole assertion.
const STOP = 'objectDefinitionUnwrap test stop (deliberate)';

jest.mock('../../../utils/d3Plugins/wavedromPlugin', () => {
    const actual = jest.requireActual('../../../utils/d3Plugins/wavedromPlugin');
    return { ...actual, validateWaveDromSpec: jest.fn(() => STOP) };
});
jest.mock('../../../utils/d3Plugins/flamegraphPlugin', () => {
    const actual = jest.requireActual('../../../utils/d3Plugins/flamegraphPlugin');
    return {
        ...actual,
        parseFlamegraphInput: jest.fn(actual.parseFlamegraphInput),
        validateFlamegraphNode: jest.fn(() => STOP),
    };
});
// eslint-disable-next-line @typescript-eslint/no-var-requires
const wavedromUtils = require('../../../utils/d3Plugins/wavedromPlugin');
// eslint-disable-next-line @typescript-eslint/no-var-requires
const flamegraphUtils = require('../../../utils/d3Plugins/flamegraphPlugin');

const MARKER = '[data-diagram-error]';

// packet's render draws through the d3 instance D3Renderer normally supplies
// (d3.select at its first line of drawing).  dist/d3.js is the UMD build, so
// it loads under jest untransformed -- the same route the timeline tests use.
// Deliberately real rather than a stub: a stub would prove only that the
// wrapper called *something*.
// eslint-disable-next-line @typescript-eslint/no-var-requires
const realD3 = require('d3/dist/d3.js');

function freshContainer(): HTMLElement {
    return document.createElement('div');
}

beforeEach(() => {
    jest.clearAllMocks();
});

// ---------------------------------------------------------------------------
// railroad — full render
// ---------------------------------------------------------------------------
describe('railroad', () => {
    const inner = { diagram: { sequence: ['SELECT', { nonterminal: 'columns' }] } };

    it('renders an OBJECT definition instead of rejecting the envelope', () => {
        const c = freshContainer();
        railroadPlugin.render(c, null, { type: 'railroad', definition: inner }, false);
        expect(c.querySelector(MARKER)).toBeNull();
        expect(c.querySelector('svg')).not.toBeNull();
    });

    it('control: a STRING definition still renders', () => {
        const c = freshContainer();
        railroadPlugin.render(
            c, null,
            { type: 'railroad', definition: JSON.stringify(inner) }, false);
        expect(c.querySelector(MARKER)).toBeNull();
        expect(c.querySelector('svg')).not.toBeNull();
    });

    it('control: a direct spec with no definition key still renders', () => {
        const c = freshContainer();
        railroadPlugin.render(c, null, { type: 'railroad', ...inner }, false);
        expect(c.querySelector(MARKER)).toBeNull();
        expect(c.querySelector('svg')).not.toBeNull();
    });
});

// ---------------------------------------------------------------------------
// packet — full render
// ---------------------------------------------------------------------------
describe('packet', () => {
    const inner = { sections: [{ label: 'S', rows: [[['flags', 4], ['seq', 4]]] }] };

    it('renders an OBJECT definition instead of "Requires a sections array"', () => {
        const c = freshContainer();
        packetPlugin.render(c, realD3, { type: 'packet', definition: inner }, false);
        expect(c.querySelector(MARKER)).toBeNull();
        expect(c.querySelector('svg')).not.toBeNull();
    });

    it('control: a STRING definition still renders', () => {
        const c = freshContainer();
        packetPlugin.render(
            c, realD3,
            { type: 'packet', definition: JSON.stringify(inner) }, false);
        expect(c.querySelector(MARKER)).toBeNull();
        expect(c.querySelector('svg')).not.toBeNull();
    });

    it('control: a direct spec with no definition key still renders', () => {
        const c = freshContainer();
        packetPlugin.render(c, realD3, { type: 'packet', ...inner }, false);
        expect(c.querySelector(MARKER)).toBeNull();
        expect(c.querySelector('svg')).not.toBeNull();
    });
});

// ---------------------------------------------------------------------------
// wavedrom — seam assertion at the validator
// ---------------------------------------------------------------------------
describe('wavedrom', () => {
    const inner = { signal: [{ name: 'clk', wave: 'p....' }] };

    it('the validator receives the INNER object, not the envelope', async () => {
        await wavedromPlugin.render(
            freshContainer(), null,
            { type: 'wavedrom', definition: inner }, false);
        const spy = wavedromUtils.validateWaveDromSpec as jest.Mock;
        expect(spy).toHaveBeenCalledTimes(1);
        const received = spy.mock.calls[0][0];
        expect(received).toEqual(inner);
        // The distinguishing property: the envelope carries `definition`.
        expect(received.definition).toBeUndefined();
    });

    it('control: a STRING definition reaches the validator parsed', async () => {
        await wavedromPlugin.render(
            freshContainer(), null,
            { type: 'wavedrom', definition: JSON.stringify(inner) }, false);
        const spy = wavedromUtils.validateWaveDromSpec as jest.Mock;
        expect(spy.mock.calls[0][0]).toEqual(inner);
    });
});

// ---------------------------------------------------------------------------
// flamegraph — seam assertion at the parser
// ---------------------------------------------------------------------------
describe('flamegraph', () => {
    const inner = { name: 'root', value: 10, children: [{ name: 'leaf', value: 10 }] };

    it('the parser receives the INNER object, not the envelope', async () => {
        await flamegraphPlugin.render(
            freshContainer(), null,
            { type: 'flamegraph', definition: inner }, false);
        const spy = flamegraphUtils.parseFlamegraphInput as jest.Mock;
        expect(spy).toHaveBeenCalledTimes(1);
        const received = spy.mock.calls[0][0];
        expect(received).toEqual(inner);
        expect(received.definition).toBeUndefined();
    });

    it('control: a STRING definition reaches the parser as text', async () => {
        await flamegraphPlugin.render(
            freshContainer(), null,
            { type: 'flamegraph', definition: JSON.stringify(inner) }, false);
        const spy = flamegraphUtils.parseFlamegraphInput as jest.Mock;
        expect(typeof spy.mock.calls[0][0]).toBe('string');
    });
});

// ---------------------------------------------------------------------------
// music — the unwrap lives in resolveMusicSpec, shared by canHandle and render
// ---------------------------------------------------------------------------
describe('music', () => {
    const inner = { notes: ['C4/q', 'D4/q', 'E4/h'] };

    it('resolveMusicSpec lifts an OBJECT definition and stamps the type', () => {
        const resolved = resolveMusicSpec({ type: 'music', definition: inner });
        expect(isMusicSpec(resolved)).toBe(true);
        expect(resolved.notes).toEqual(inner.notes);
    });

    it('canHandle selects an object-definition spec (pre-fix: 30s no-plugin timeout)', () => {
        expect(musicPlugin.canHandle({ type: 'music', definition: inner })).toBe(true);
    });

    it('guard: a NON-music object definition is not hijacked', () => {
        const foreign = { type: 'music', definition: { nodes: [], links: [] } };
        expect(resolveMusicSpec(foreign)).toBe(foreign);
        expect(musicPlugin.canHandle(foreign)).toBe(false);
    });

    it('control: the string-definition recovery is unchanged', () => {
        const resolved = resolveMusicSpec({
            type: 'music', definition: JSON.stringify(inner),
        });
        expect(isMusicSpec(resolved)).toBe(true);
    });
});
