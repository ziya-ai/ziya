# Force-Directed Graph Visualization

Ziya supports force-directed graph rendering using D3's force simulation.
These graphs automatically lay out nodes connected by links, with physics-
based positioning that spreads connected clusters apart.

## Usage

Use a `d3` code fence with a spec object containing `type: "force-directed"`.
Both JSON and JavaScript expression syntax are supported:````

````
```d3
({
  type: "force-directed",
  width: 700,
  height: 500,
  data: {
    nodes: [
      { id: "Server", group: 1, size: 20 },
      { id: "Client A", group: 2, size: 12 },
      { id: "Client B", group: 2, size: 12 },
      { id: "Database", group: 3, size: 16 }
    ],
    links: [
      { source: "Client A", target: "Server", value: 3 },
      { source: "Client B", target: "Server", value: 3 },
      { source: "Server", target: "Database", value: 5 }
    ]
  },
  style: {
    background: "#1a1a2e",
    nodeColors: { "1": "#ff6b6b", "2": "#4ecdc4", "3": "#ffe66d" },
    linkColor: "#ffffff33",
    labelColor: "#cccccc",
    fontSize: 11
  }
})
```
````

## Spec Reference

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"force-directed"` or `"force"` | Required identifier |
| `width`, `height` | number | Canvas dimensions (default 700×500) |
| `data.nodes` | array | Nodes with `id`, optional `group`, `size`, `color` |
| `data.links` | array | Links with `source`, `target`, optional `value` |
| `style.background` | string | SVG background color |
| `style.nodeColors` | object | Map of group number → hex color |
| `style.linkColor` | string | Default link stroke color |
| `style.linkOpacity` | number | Link opacity (0–1, default 0.6) |
| `style.labelColor` | string | Text label color |
| `style.fontSize` | number | Label font size in px (default 10) |
