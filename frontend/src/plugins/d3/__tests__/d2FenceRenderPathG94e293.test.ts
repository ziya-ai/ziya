/**
 * G-94e293 / D-074 (regression) — markdown-fence-not-stripped, engine d2, spec
 * d2-w4-01. THEME-INVARIANT (parse runs before any colour is resolved).
 *
 * D-074 has cycled verified -> regression repeatedly. The parse-layer fix
 * (stripD2CodeFence, wired into D2Parser.parse) is present and covered by
 * d2ParseGD2PARSE.test.ts, but that suite calls the parser DIRECTLY. The
 * headless renderer instead runs the fenced definition through the FULL
 * pre-parse chain the d2 plugin uses in render():
 *
 *     extractDefinitionFromYAML(spec.definition, 'd2')   // diagramUtils.ts
 *       -> looksLikeJson / looksLikeMermaid guards        // must NOT misfire
 *       -> new D2Parser().parse(extracted)                // strips the fence
 *
 * This guard reproduces that exact chain on the real d2-w4-01 payload, so a
 * future change to either diagramUtils.extractDefinitionFromYAML OR the parse
 * fence-strip that reintroduced the phantom-backtick-node defect fails here,
 * not silently in a rebuilt bundle.
 *
 * DIRECTION: with stripD2CodeFence removed from parse, the ```d2 / ``` lines
 * fall through to the bare-node-id branch and become phantom boxes labelled
 * with the backtick run while the first real node's label is lost — the
 * assertions below (3 clean nodes, 2 edges, no backtick node) then fail.
 */
import { D2Parser, looksLikeJson, looksLikeMermaid } from '../d2Plugin';
import { extractDefinitionFromYAML } from '../../../utils/diagramUtils';

// d2-w4-01 verbatim: whole D2 source wrapped in a markdown ```d2 fence.
const SPEC_D2_W4_01 =
  '```d2\nweb: Web Server\napi: API Service\ndb: Database\nweb -> api\napi -> db\n```\n';

// Mirror of d2Plugin.render()'s pre-parse pipeline.
const renderPathParse = (definition: string) => {
  const extracted = extractDefinitionFromYAML(definition, 'd2');
  // The renderer bails out early if either alien-dialect guard fires; a
  // fenced-but-genuine d2 definition must pass both so it reaches the parser.
  expect(looksLikeJson(extracted)).toBe(false);
  expect(looksLikeMermaid(extracted)).toBe(false);
  return new D2Parser().parse(extracted);
};

describe('D-074 fenced d2 survives the full render-entry chain (G-94e293)', () => {
  test('extractDefinitionFromYAML leaves the un-YAML-wrapped fenced payload intact', () => {
    // No `type: d2` / `definition:` envelope -> returned unchanged, still fenced,
    // so the fence strip must happen inside parse (not here).
    expect(extractDefinitionFromYAML(SPEC_D2_W4_01, 'd2')).toBe(SPEC_D2_W4_01);
  });

  test('render-path parse yields the 3 real nodes and 2 edges, no phantom fence node', () => {
    const { nodes, edges } = renderPathParse(SPEC_D2_W4_01);
    expect(nodes).toHaveLength(3);
    expect(nodes.map((n: any) => n.label)).toEqual(
      expect.arrayContaining(['Web Server', 'API Service', 'Database'])
    );
    // The first real node's label ('Web Server') is not lost to the fence line.
    expect(nodes.find((n: any) => n.id === 'web')?.label).toBe('Web Server');
    // No ```d2 / ``` box survives as a node.
    expect(nodes.some((n: any) => /`/.test(n.label) || /`/.test(n.id))).toBe(false);
    expect(edges.map((e: any) => [e.source, e.target])).toEqual(
      expect.arrayContaining([['web', 'api'], ['api', 'db']])
    );
  });

  test('a YAML-wrapped-AND-fenced d2 definition also strips both layers', () => {
    // Defence in depth: `definition: |` envelope whose body is itself fenced.
    const wrapped =
      'type: d2\ndefinition: |\n  ```d2\n  web: Web Server\n  api: API Service\n  web -> api\n  ```\n';
    const { nodes, edges } = renderPathParse(wrapped);
    expect(nodes.map((n: any) => n.id).sort()).toEqual(['api', 'web']);
    expect(nodes.some((n: any) => /`/.test(n.label) || /`/.test(n.id))).toBe(false);
    expect(edges.map((e: any) => [e.source, e.target])).toEqual([['web', 'api']]);
  });
});
