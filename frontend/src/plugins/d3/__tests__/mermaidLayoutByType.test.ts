/**
 * Mermaid layout engine chosen per diagram type (Mermaid 12).
 *
 * Mermaid 12 bundles ELK and makes it the default for graph diagrams; the
 * plugin pins the layout per type from the dagre/ELK comparison in
 * scripts/mermaid_layout_eval.py (results in .ziya/layout-eval/).  ELK won or
 * tied on every state, flowchart and ER spec; class diagrams showed no
 * benefit and stay on dagre, as does requirementDiagram (not measured).
 *
 * The seam asserted here is the config that actually reaches
 * mermaid.initialize, not a table the test reads.  The classic look is
 * asserted too: our post-render passes and goldens assume it.
 */
import { mermaidPlugin, effectiveMermaidLayout, waitUntilConnected } from '../mermaidPlugin';
import { shouldRerouteEdges } from '../mermaidEdgeRerouter';

// The rerouter is mocked so the seam test can see whether the plugin asked
// for it.  It only applies to dagre output: ELK already routes around nodes,
// and arcing an ELK edge would also leave its label behind.
jest.mock('../mermaidEdgeRerouter', () => ({
  shouldRerouteEdges: jest.fn(() => false),
  rerouteSkipEdges: jest.fn(() => ({ totalEdges: 0, reroutedEdges: 0, skippedEdges: 0, details: [] })),
}));

const initialize = jest.fn();
const render = jest.fn(async () => ({
  svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    + '<g class="node"><rect width="10" height="10"></rect></g>'
    + '<!--' + ' '.repeat(120) + '--></svg>',
}));

beforeAll(() => {
  (window as any).mermaid = { initialize, render };
  (window as any).__mermaidLoaded = true;
  jest.spyOn(console, 'log').mockImplementation(() => {});
  jest.spyOn(console, 'info').mockImplementation(() => {});
  jest.spyOn(console, 'warn').mockImplementation(() => {});
  jest.spyOn(console, 'error').mockImplementation(() => {});
});

const layoutFor = async (definition: string) => {
  initialize.mockClear();
  render.mockClear();
  const container = document.createElement('div');
  document.body.appendChild(container);
  await mermaidPlugin.render(container, {}, {
    type: 'mermaid', definition, isMarkdownBlockClosed: true,
  } as any, false);
  expect(initialize).toHaveBeenCalledTimes(1);
  // Positive control: the render path ran past initialize.
  expect(render).toHaveBeenCalled();
  const cfg = initialize.mock.calls[0][0];
  expect(cfg.look).toBe('classic');
  return cfg.layout;
};

describe('mermaid layout by diagram type', () => {
  it.each([
    ['stateDiagram-v2\n    [*] --> A\n    A --> B : go', 'elk'],
    ['stateDiagram\n    [*] --> A\n    A --> B', 'elk'],
    ['flowchart TD\n    A --> B\n    B --> C', 'elk'],
    ['graph LR\n    A --> B\n    B --> C', 'elk'],
    ['erDiagram\n    A ||--o{ B : has\n    B ||--|| C : owns', 'elk'],
    ['%%{init: {"theme": "default"}}%%\nflowchart LR\n    A --> B', 'elk'],
    ['classDiagram\n    A <|-- B\n    B --> C', 'dagre'],
    ['requirementDiagram\n    requirement r {\n        id: 1\n    }', 'dagre'],
  ])('%s -> %s', async (definition, expected) => {
    expect(await layoutFor(definition)).toBe(expected);
  });
});

describe('effectiveMermaidLayout', () => {
  it.each([
    ['flowchart LR\n    A --> B', 'flowchart', 'elk'],
    ['classDiagram\n    A <|-- B', 'classdiagram', 'dagre'],
    ['%%{init: {"layout": "elk"}}%%\nclassDiagram\n    A <|-- B', 'classdiagram', 'elk'],
    ["%%{init: {'layout': 'dagre'}}%%\nflowchart LR\n    A --> B", 'flowchart', 'dagre'],
    ['---\nconfig:\n  layout: dagre\n---\nflowchart LR\n    A --> B', 'flowchart', 'dagre'],
    ['unknownDiagram\n    x', 'unknowndiagram', 'dagre'],
  ])('%s', (def, type, expected) => {
    expect(effectiveMermaidLayout(def, type)).toBe(expected);
  });
});

describe('skip-edge rerouter runs only on dagre output', () => {
  const settle = () => new Promise(r => setTimeout(r, 700));
  beforeEach(() => (shouldRerouteEdges as jest.Mock).mockClear());

  it('is not consulted for an ELK flowchart', async () => {
    expect(await layoutFor('flowchart LR\n    A --> B\n    B --> C')).toBe('elk');
    await settle();
    expect(shouldRerouteEdges).not.toHaveBeenCalled();
  });

  it('is consulted when the diagram pins dagre', async () => {
    await layoutFor('%%{init: {"layout": "dagre"}}%%\nflowchart LR\n    A --> B\n    B --> C');
    await settle();
    expect(shouldRerouteEdges).toHaveBeenCalled();
  });
});

describe('waitUntilConnected', () => {
  it('resolves once the element is attached', async () => {
    const el = document.createElement('div');
    requestAnimationFrame(() => document.body.appendChild(el));
    expect(el.isConnected).toBe(false);
    expect(await waitUntilConnected(el)).toBe(true);
  });

  it('gives up after maxFrames on an element that is never attached', async () => {
    expect(await waitUntilConnected(document.createElement('div'), 3)).toBe(false);
  });
});
