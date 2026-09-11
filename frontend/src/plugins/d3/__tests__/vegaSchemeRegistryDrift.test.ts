/**
 * Drift guard for KNOWN_VEGA_SCHEMES (defect D-255).
 *
 * validateColorSchemes() in vegaRecovery.ts SILENTLY DELETES any
 * `scale.scheme` whose name is not in the hardcoded KNOWN_VEGA_SCHEMES set —
 * an unknown named scheme is a fatal Vega dataflow error, so dropping it to the
 * theme default is the lesser evil. The hazard is that the allowlist is a
 * hand-maintained copy of vega-scale's scheme registry: if a dependency bump
 * adds or renames a scheme, the allowlist goes stale, newly-valid scheme names
 * get silently stripped, charts recolour to the default, and NO existing test
 * goes red (the D-255 tests only check fixed literals).
 *
 * This guard pins the allowlist to ground truth. vega-scale registers a
 * scheme() ONLY via the two apply() loops in src/schemes.js over the
 * `discrete` and `continuous` palette objects in src/palettes.js (verified: no
 * other scheme(k, …) registration exists in the package), so the set of
 * registered scheme names is exactly the union of those objects' top-level
 * keys. We read that source at test time — tracking whatever version is
 * installed, not a frozen copy.
 *
 * We locate the source by climbing to node_modules rather than
 * require.resolve('vega-scale/src/palettes.js'): vega-scale is ESM-only
 * ("type":"module") with an exports map that seals off subpaths, so
 * require.resolve of an internal file throws ERR_PACKAGE_PATH_NOT_EXPORTED
 * (and its ESM entry is not in the CRA jest transform allowlist).
 *
 * When vega-scale is upgraded and its palette set changes, THIS test fails,
 * naming the exact schemes to add to or remove from KNOWN_VEGA_SCHEMES.
 */
import * as fs from 'fs';
import * as path from 'path';

import { KNOWN_VEGA_SCHEMES } from '../vegaRecovery';

/** Locate node_modules/vega-scale/src/palettes.js by climbing from __dirname. */
function resolvePalettesSource(): string {
  let dir = __dirname;
  for (let i = 0; i < 12; i += 1) {
    const candidate = path.join(dir, 'node_modules', 'vega-scale', 'src', 'palettes.js');
    if (fs.existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error('could not locate vega-scale/src/palettes.js from ' + __dirname);
}

/** Top-level keys of an `export const <name> = { … };` object literal, lowercased. */
function paletteKeys(src: string, name: string): string[] {
  const m = src.match(new RegExp('export const ' + name + '\\s*=\\s*\\{([\\s\\S]*?)\\n\\};'));
  if (!m) throw new Error('palette object not found: ' + name);
  return [...m[1].matchAll(/^\s*([A-Za-z0-9]+)\s*:/gim)].map((x) => x[1].toLowerCase());
}

describe('KNOWN_VEGA_SCHEMES stays in sync with the vega-scale registry (D-255 drift guard)', () => {
  const src = fs.readFileSync(resolvePalettesSource(), 'utf8');
  const registered = new Set<string>([
    ...paletteKeys(src, 'discrete'),
    ...paletteKeys(src, 'continuous'),
  ]);

  it('parses a non-trivial registry from vega-scale source (sanity)', () => {
    // Guards the parser itself: if the source layout changes and the regex
    // stops matching, we must NOT silently treat the registry as empty and let
    // the assertions below pass vacuously.
    expect(registered.size).toBeGreaterThan(40);
  });

  it('has no dead entries — every allowlisted name is a real registered scheme', () => {
    const dead = [...KNOWN_VEGA_SCHEMES].filter((n) => !registered.has(n.toLowerCase()));
    // If this fails, remove these names from KNOWN_VEGA_SCHEMES in vegaRecovery.ts.
    expect(dead).toEqual([]);
  });

  it('has no silent false-deletes — every registered scheme is allowlisted', () => {
    const missing = [...registered].filter((n) => !KNOWN_VEGA_SCHEMES.has(n));
    // If this fails, add these names to KNOWN_VEGA_SCHEMES in vegaRecovery.ts:
    // validateColorSchemes is currently stripping them from valid specs.
    expect(missing).toEqual([]);
  });
});
