/**
 * The production build must not try to bundle satellite.js's WASM runtimes.
 *
 * satellite.js's entry re-exports an optional WASM bulk propagator whose
 * runtimes do `await import('#wasm-single-thread')` (and multi-thread).  Those
 * Emscripten builds contain import("node:module"), and webpack, which bundles
 * every dynamic import, failed the whole build with
 *   UnhandledSchemeError: Reading from "node:module" is not handled by plugins
 * (reported 2026-11-04, when the orbit plugin first imported satellite.js).
 * craco.config.js ignores those two imports from inside satellite.js; the
 * orbit plugin only uses the pure-JS SGP4 path.
 */
import * as fs from 'fs';
import * as path from 'path';

const FRONTEND = path.resolve(__dirname, '../../../..');
const read = (p: string) => fs.readFileSync(path.join(FRONTEND, p), 'utf8');

describe('satellite.js WASM runtimes are kept out of the bundle', () => {
  it('the hazard is real: the installed WASM builds import node:module via the #wasm aliases', () => {
    const pkg = JSON.parse(read('node_modules/satellite.js/package.json'));
    expect(Object.keys(pkg.imports || {})).toEqual(
      expect.arrayContaining(['#wasm-single-thread', '#wasm-multi-thread']),
    );
    const runtimes = read('node_modules/satellite.js/dist/wasm/runtimes/index.js');
    expect(runtimes).toContain("import('#wasm-single-thread')");
    const target = pkg.imports['#wasm-single-thread'].replace(/^\.\//, '');
    expect(read(`node_modules/satellite.js/${target}`)).toContain('node:module');
  });

  it('craco ignores exactly those imports, and only from inside satellite.js', () => {
    const src = read('craco.config.js');
    expect(src).toContain("const webpack = require('webpack');");
    const at = src.indexOf('new webpack.IgnorePlugin({');
    expect(at).toBeGreaterThan(-1);
    const block = src.slice(at, src.indexOf('}));', at));
    const res = /resourceRegExp:\s*(\/.*\/),/.exec(block);
    const ctx = /contextRegExp:\s*(\/.*\/),/.exec(block);
    expect(res && ctx).toBeTruthy();
    // eslint-disable-next-line no-eval
    const resourceRe: RegExp = eval(res![1]);
    // eslint-disable-next-line no-eval
    const contextRe: RegExp = eval(ctx![1]);
    expect(resourceRe.test('#wasm-single-thread')).toBe(true);
    expect(resourceRe.test('#wasm-multi-thread')).toBe(true);
    expect(resourceRe.test('satellite.js')).toBe(false);
    expect(contextRe.test('/x/node_modules/satellite.js/dist/wasm/runtimes')).toBe(true);
    expect(contextRe.test('/x/src/plugins/d3')).toBe(false);
  });

  it('orbitMath imports only the pure-JS SGP4 surface', () => {
    const src = read('src/plugins/d3/orbitMath.ts');
    expect(src).toMatch(/from 'satellite\.js'/);
    expect(src).not.toMatch(/createSingleThreadRuntime|createMultiThreadRuntime|BulkPropagator/);
  });
});
