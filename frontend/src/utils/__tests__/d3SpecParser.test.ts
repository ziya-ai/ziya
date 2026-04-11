import { parseD3Spec } from '../d3SpecParser';

describe('parseD3Spec', () => {
  describe('JSON format', () => {
    it('parses standard JSON', () => {
      const result = parseD3Spec('{ "type": "force-directed", "width": 700 }');
      expect(result).toEqual({ type: 'force-directed', width: 700 });
    });

    it('parses JSON with nested objects', () => {
      const spec = JSON.stringify({
        type: 'force-directed',
        data: { nodes: [{ id: 'A' }], links: [] },
        style: { background: '#000' }
      });
      const result = parseD3Spec(spec);
      expect(result.type).toBe('force-directed');
      expect(result.data.nodes).toHaveLength(1);
    });
  });

  describe('JS expression format', () => {
    it('strips outer parentheses', () => {
      const result = parseD3Spec('({ "type": "force", "width": 500 })');
      expect(result).toEqual({ type: 'force', width: 500 });
    });

    it('handles unquoted keys', () => {
      const result = parseD3Spec('({ type: "force-directed", width: 700 })');
      expect(result).toEqual({ type: 'force-directed', width: 700 });
    });

    it('handles nested unquoted keys', () => {
      const input = `({
        type: "force-directed",
        data: {
          nodes: [{ id: "A", group: 1 }],
          links: [{ source: "A", target: "B", value: 5 }]
        },
        style: { background: "#000" }
      })`;
      const result = parseD3Spec(input);
      expect(result.type).toBe('force-directed');
      expect(result.data.nodes[0].id).toBe('A');
      expect(result.data.links[0].value).toBe(5);
      expect(result.style.background).toBe('#000');
    });

    it('handles trailing commas', () => {
      const result = parseD3Spec('({ type: "force", nodes: [1, 2, 3,], })');
      expect(result).toEqual({ type: 'force', nodes: [1, 2, 3] });
    });

    it('handles single-quoted strings', () => {
      const result = parseD3Spec("({ type: 'force-directed', style: { bg: '#fff' } })");
      expect(result.type).toBe('force-directed');
      expect(result.style.bg).toBe('#fff');
    });
  });

  describe('comment stripping', () => {
    it('strips line comments', () => {
      const input = `({
        type: "force-directed", // this is the type
        width: 700
      })`;
      const result = parseD3Spec(input);
      expect(result.type).toBe('force-directed');
      expect(result.width).toBe(700);
    });

    it('strips block comments', () => {
      const input = `({
        /* graph type */
        type: "force-directed",
        width: 700
      })`;
      const result = parseD3Spec(input);
      expect(result.type).toBe('force-directed');
    });
  });

  describe('real-world spec from documentation', () => {
    it('parses the stellar colony spec from the bug report', () => {
      const input = `({
  type: "force-directed",
  width: 700,
  height: 550,
  data: {
    nodes: [
      {id: "Star", group: 1, size: 40},
      {id: "Colony", group: 2, size: 25}
    ],
    links: [
      {source: "Star", target: "Colony", value: 10}
    ]
  },
  style: {
    background: "#030306",
    nodeColors: {"1": "#FFD700", "2": "#228b22"},
    linkColor: "#ff222244",
    linkOpacity: 0.5,
    labelColor: "#aaaaaa",
    fontSize: 9
  }
})`;
      const result = parseD3Spec(input);
      expect(result).not.toBeNull();
      expect(result.type).toBe('force-directed');
      expect(result.width).toBe(700);
      expect(result.height).toBe(550);
      expect(result.data.nodes).toHaveLength(2);
      expect(result.data.links).toHaveLength(1);
      expect(result.style.nodeColors['1']).toBe('#FFD700');
    });
  });

  describe('error handling', () => {
    it('returns null for empty string', () => {
      expect(parseD3Spec('')).toBeNull();
    });

    it('returns null for non-string input', () => {
      expect(parseD3Spec(null as any)).toBeNull();
      expect(parseD3Spec(undefined as any)).toBeNull();
    });

    it('returns null for unparseable garbage', () => {
      expect(parseD3Spec('not valid at all {{')).toBeNull();
    });
  });
});
