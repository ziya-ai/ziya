# Graphics Engine Stress Report

_Four-wave light/dark rendering sweep across every diagram engine wired into Ziya, the defect remediation that followed, and an honest reconciliation of what was actually verified._

- **Engines swept:** 20 &nbsp;|&nbsp; **spec cells graded:** 954 per theme (20 basic-chart cells were *not-run* and are excluded from “attempted”).
- **Raw failure clusters triaged:** 360 → **265 merged defects** (159 structural, 41 theme-only, 65 recovery; 18 of them cross-engine families).
- **Disposition of the 265 defects:** 14 open, 183 fix-applied, 68 wont-fix.
- **Verification protocol (as recorded in the backlog):** _Every fix re-rendered in BOTH themes (light+dark) from spec on disk after npm run build; engine regression_set must render unchanged in both themes._

> **Reading note.** “Before” numbers come from the Stage 1 engine sweep files (`<engine>.json`, each spec’s `light`/`dark` status). “After” verdicts come from the remediation ledger (`backlog.json`): a single-engine defect’s `status` (`fix-applied`→verified, `wont-fix`, `open`→still-broken), and for the 18 cross-engine families the per-engine member verdicts recorded in each family’s `progress_log`. Verdicts are aggregated **per theme**, so a spec can be verified in dark yet unresolved in light. No fix is claimed here that the backlog does not record as applied for that engine **and** theme.

## 1. Summary by engine

Pass rate before/after and theme parity are spec-cell measures (per theme). “Parity” = specs passing in **both** themes. “Fixed / wont-fix / open” are the single-engine defects cleanly attributable to that engine; the 18 cross-engine families are tabulated separately in §5. Engines are ordered by mean after-pass-rate.

| Engine | Specs attempted (L/D) | Before L | Before D | After L | After D | Parity before | Parity after | Fixed | Wont-fix | Open |
|---|---|---|---|---|---|---|---|---|---|---|
| **chord** | 45/45 | 31% | 29% | 100% | 100% | 10/45 | 45/45 | 12 | 0 | 0 |
| **d2** | 45/45 | 2% | 0% | 100% | 100% | 0/45 | 45/45 | 25 | 0 | 0 |
| **network** | 45/45 | 33% | 31% | 100% | 100% | 12/45 | 45/45 | 11 | 0 | 0 |
| **force-directed** | 45/45 | 9% | 0% | 100% | 98% | 0/45 | 44/45 | 12 | 0 | 0 |
| **graphviz** | 45/45 | 24% | 31% | 98% | 98% | 8/45 | 44/45 | 9 | 1 | 0 |
| **joint** | 45/45 | 11% | 9% | 96% | 96% | 4/45 | 43/45 | 14 | 2 | 0 |
| **vega-lite** | 45/45 | 33% | 27% | 93% | 93% | 11/45 | 42/45 | 14 | 3 | 0 |
| **vega** | 45/45 | 33% | 18% | 93% | 87% | 8/45 | 39/45 | 11 | 2 | 0 |
| **plotly** | 45/45 | 31% | 27% | 89% | 89% | 12/45 | 40/45 | 8 | 2 | 1 |
| **d3** | 45/45 | 31% | 33% | 87% | 87% | 14/45 | 39/45 | 12 | 2 | 0 |
| **basic-chart** | 35/35 (+10/10 n/r) | 37% | 29% | 80% | 80% | 10/45 | 28/45 | 7 | 1 | 0 |
| **chat-message** | 60/60 | 43% | 32% | 77% | 83% | 18/60 | 46/60 | 10 | 6 | 0 |
| **tikz** | 60/60 | 48% | 13% | 73% | 73% | 8/60 | 44/60 | 2 | 6 | 0 |
| **packet** | 45/45 | 49% | 47% | 71% | 71% | 21/45 | 32/45 | 4 | 7 | 0 |
| **circuitikz** | 46/46 | 37% | 15% | 70% | 72% | 6/46 | 32/46 | 1 | 1 | 3 |
| **chemfig** | 45/45 | 31% | 2% | 67% | 69% | 0/45 | 30/45 | 5 | 6 | 0 |
| **mermaid** | 63/63 | 22% | 27% | 67% | 68% | 11/63 | 41/63 | 9 | 7 | 0 |
| **drawio** | 45/45 | 24% | 18% | 58% | 67% | 8/45 | 26/45 | 7 | 5 | 0 |
| **music** | 45/45 | 18% | 18% | 49% | 49% | 8/45 | 22/45 | 4 | 14 | 0 |
| **tikz-cd** | 60/60 | 0% | 0% | 28% | 48% | 0/60 | 17/60 | 0 | 1 | 0 |
| **ALL** | 954/954 | 27% | 20% | 79% | 80% | 169/964 | 744/964 | 177 | 66 | 4 |

Across both themes the sweep moved the pass rate from **27% / 20%** (light/dark) to **79% / 80%**, and lifted both-theme parity from **169/964 (18%)** to **744/964 (77%)**. 8 light and 8 dark cells remain broken (`open`); 196 light / 177 dark are `wont-fix`.

## 2. Before vs after pass rate, per engine and theme

The chart is faceted by theme so the light/dark gap is legible at a glance. It uses `background: null` and a theme-neutral categorical palette (a mid-blue for *before* and a mid-green for *after*, both legible on white **and** on a dark panel) rather than a light-only palette — so the figure does not itself commit the readability defect this report documents.

```vega-lite
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "description": "Before vs after pass rate per engine, faceted by theme.",
  "background": null,
  "title": {
    "text": "Pass rate before vs after remediation",
    "subtitle": "faceted by theme; bar pairs = before / after",
    "color": "#888888",
    "subtitleColor": "#888888"
  },
  "data": {
    "values": [
      {
        "engine": "chord",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.3111
      },
      {
        "engine": "chord",
        "theme": "light",
        "phase": "after",
        "pass_rate": 1.0
      },
      {
        "engine": "chord",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.2889
      },
      {
        "engine": "chord",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 1.0
      },
      {
        "engine": "d2",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.0222
      },
      {
        "engine": "d2",
        "theme": "light",
        "phase": "after",
        "pass_rate": 1.0
      },
      {
        "engine": "d2",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.0
      },
      {
        "engine": "d2",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 1.0
      },
      {
        "engine": "network",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.3333
      },
      {
        "engine": "network",
        "theme": "light",
        "phase": "after",
        "pass_rate": 1.0
      },
      {
        "engine": "network",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.3111
      },
      {
        "engine": "network",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 1.0
      },
      {
        "engine": "force-directed",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.0889
      },
      {
        "engine": "force-directed",
        "theme": "light",
        "phase": "after",
        "pass_rate": 1.0
      },
      {
        "engine": "force-directed",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.0
      },
      {
        "engine": "force-directed",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 0.9778
      },
      {
        "engine": "graphviz",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.2444
      },
      {
        "engine": "graphviz",
        "theme": "light",
        "phase": "after",
        "pass_rate": 0.9778
      },
      {
        "engine": "graphviz",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.3111
      },
      {
        "engine": "graphviz",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 0.9778
      },
      {
        "engine": "joint",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.1111
      },
      {
        "engine": "joint",
        "theme": "light",
        "phase": "after",
        "pass_rate": 0.9556
      },
      {
        "engine": "joint",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.0889
      },
      {
        "engine": "joint",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 0.9556
      },
      {
        "engine": "vega-lite",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.3333
      },
      {
        "engine": "vega-lite",
        "theme": "light",
        "phase": "after",
        "pass_rate": 0.9333
      },
      {
        "engine": "vega-lite",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.2667
      },
      {
        "engine": "vega-lite",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 0.9333
      },
      {
        "engine": "vega",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.3333
      },
      {
        "engine": "vega",
        "theme": "light",
        "phase": "after",
        "pass_rate": 0.9333
      },
      {
        "engine": "vega",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.1778
      },
      {
        "engine": "vega",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 0.8667
      },
      {
        "engine": "plotly",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.3111
      },
      {
        "engine": "plotly",
        "theme": "light",
        "phase": "after",
        "pass_rate": 0.8889
      },
      {
        "engine": "plotly",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.2667
      },
      {
        "engine": "plotly",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 0.8889
      },
      {
        "engine": "d3",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.3111
      },
      {
        "engine": "d3",
        "theme": "light",
        "phase": "after",
        "pass_rate": 0.8667
      },
      {
        "engine": "d3",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.3333
      },
      {
        "engine": "d3",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 0.8667
      },
      {
        "engine": "basic-chart",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.3714
      },
      {
        "engine": "basic-chart",
        "theme": "light",
        "phase": "after",
        "pass_rate": 0.8
      },
      {
        "engine": "basic-chart",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.2857
      },
      {
        "engine": "basic-chart",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 0.8
      },
      {
        "engine": "chat-message",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.4333
      },
      {
        "engine": "chat-message",
        "theme": "light",
        "phase": "after",
        "pass_rate": 0.7667
      },
      {
        "engine": "chat-message",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.3167
      },
      {
        "engine": "chat-message",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 0.8333
      },
      {
        "engine": "tikz",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.4833
      },
      {
        "engine": "tikz",
        "theme": "light",
        "phase": "after",
        "pass_rate": 0.7333
      },
      {
        "engine": "tikz",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.1333
      },
      {
        "engine": "tikz",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 0.7333
      },
      {
        "engine": "packet",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.4889
      },
      {
        "engine": "packet",
        "theme": "light",
        "phase": "after",
        "pass_rate": 0.7111
      },
      {
        "engine": "packet",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.4667
      },
      {
        "engine": "packet",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 0.7111
      },
      {
        "engine": "circuitikz",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.3696
      },
      {
        "engine": "circuitikz",
        "theme": "light",
        "phase": "after",
        "pass_rate": 0.6957
      },
      {
        "engine": "circuitikz",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.1522
      },
      {
        "engine": "circuitikz",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 0.7174
      },
      {
        "engine": "chemfig",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.3111
      },
      {
        "engine": "chemfig",
        "theme": "light",
        "phase": "after",
        "pass_rate": 0.6667
      },
      {
        "engine": "chemfig",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.0222
      },
      {
        "engine": "chemfig",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 0.6889
      },
      {
        "engine": "mermaid",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.2222
      },
      {
        "engine": "mermaid",
        "theme": "light",
        "phase": "after",
        "pass_rate": 0.6667
      },
      {
        "engine": "mermaid",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.2698
      },
      {
        "engine": "mermaid",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 0.6825
      },
      {
        "engine": "drawio",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.2444
      },
      {
        "engine": "drawio",
        "theme": "light",
        "phase": "after",
        "pass_rate": 0.5778
      },
      {
        "engine": "drawio",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.1778
      },
      {
        "engine": "drawio",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 0.6667
      },
      {
        "engine": "music",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.1778
      },
      {
        "engine": "music",
        "theme": "light",
        "phase": "after",
        "pass_rate": 0.4889
      },
      {
        "engine": "music",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.1778
      },
      {
        "engine": "music",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 0.4889
      },
      {
        "engine": "tikz-cd",
        "theme": "light",
        "phase": "before",
        "pass_rate": 0.0
      },
      {
        "engine": "tikz-cd",
        "theme": "light",
        "phase": "after",
        "pass_rate": 0.2833
      },
      {
        "engine": "tikz-cd",
        "theme": "dark",
        "phase": "before",
        "pass_rate": 0.0
      },
      {
        "engine": "tikz-cd",
        "theme": "dark",
        "phase": "after",
        "pass_rate": 0.4833
      }
    ]
  },
  "facet": {
    "column": {
      "field": "theme",
      "type": "nominal",
      "title": null,
      "header": {
        "labelColor": "#888888",
        "titleColor": "#888888"
      }
    }
  },
  "spec": {
    "width": 300,
    "height": 430,
    "mark": {
      "type": "bar"
    },
    "encoding": {
      "y": {
        "field": "engine",
        "type": "nominal",
        "sort": [
          "chord",
          "d2",
          "network",
          "force-directed",
          "graphviz",
          "joint",
          "vega-lite",
          "vega",
          "plotly",
          "d3",
          "basic-chart",
          "chat-message",
          "tikz",
          "packet",
          "circuitikz",
          "chemfig",
          "mermaid",
          "drawio",
          "music",
          "tikz-cd"
        ],
        "title": null,
        "axis": {
          "labelColor": "#888888",
          "domainColor": "#888888",
          "tickColor": "#888888"
        }
      },
      "yOffset": {
        "field": "phase",
        "sort": [
          "before",
          "after"
        ]
      },
      "x": {
        "field": "pass_rate",
        "type": "quantitative",
        "title": "pass rate",
        "scale": {
          "domain": [
            0,
            1
          ]
        },
        "axis": {
          "format": "%",
          "labelColor": "#888888",
          "titleColor": "#888888",
          "domainColor": "#888888",
          "gridColor": "#8888884d"
        }
      },
      "color": {
        "field": "phase",
        "type": "nominal",
        "title": "phase",
        "scale": {
          "domain": [
            "before",
            "after"
          ],
          "range": [
            "#3b82c4",
            "#3aa66f"
          ]
        },
        "legend": {
          "labelColor": "#888888",
          "titleColor": "#888888"
        }
      }
    }
  },
  "config": {
    "view": {
      "stroke": null
    },
    "axis": {
      "labelFontSize": 10
    }
  }
}
```

## 3. Theme readability — theme-only defects

The backlog records **41 theme-only defects** (defects that manifest in exactly one theme); 31 carry an explicit single-theme signature. At the spec-cell level, **115 cells** fail in exactly one theme across the roster (heaviest: tikz 21, chemfig 15, circuitikz 12). These are the cases where a theme-blind colour constant, a swapped ink, or an un-rethemed author colour makes geometry that is otherwise correct effectively invisible in one theme.

**Measured vs visual.** A numeric ratio below is a *measured* WCAG contrast ratio recorded in the backlog (`worst_measured_ratio` before; `contrast_ratios`/`ratios`/`fix_contrast_ratios` after). Where the offending colour lives inside an engine’s own renderer and could not be read from the spec, the row is marked _visual_ and no number is invented. A ratio of ~1.0 means same-colour-on-same-colour — text or strokes rendered completely invisible.

| Defect | Engine(s) | Failed in | Kind | Signature | Ratio before | Ratio after | File / constant fixed | Disposition |
|---|---|---|---|---|---|---|---|---|
| D-010 | 4 engines | dark | theme | `latex-theme-ink-not-injected-black-on-dark` | 1.15:1 (measured) | dark: #EDEDED ink on #1F1F1F page = 14.08:1; light: #000000 ink on #FFFFFF page = 21.00:1 | app/services/latex_profiles.py (build_document: theme-aware \pagecolor + \color injected on the PNG path); app/services… | fixed |
| D-021 | chat-message | dark | theme | `mermaid-node-label-below-wcag:dark` | 4.03:1 (measured) | _visual_ | frontend/src/styles/mermaid-theme.css (.dark .mermaid-container .node rect:not([style*="fill:"]) { fill: #5e81ac !impor… | fixed |
| D-025 | graphviz | light | theme | `edge-label-white-on-white:light` | 1.00:1 (measured) | light: authored #000000 edge label on #ffffff page = 21.0 (was forced #ffffff = 1.0); dark: #ffffff edge label on ~#1e1e1e panel = 16.67 (unchanged) | frontend/src/plugins/d3/graphvizPlugin.ts; frontend/src/plugins/d3/__tests__/graphvizG64.test.ts | fixed |
| D-034 | graphviz | dark | theme | `authored-fill-overridden-by-node-palette:dark` | 1.89:1 (measured) | light: unaffected (author fills honoured verbatim); dark: label text driven >=4.5:1 against the resolved (darkened) fill; e.g. white on #4361ee = 5.0… | frontend/src/plugins/d3/graphvizPlugin.ts; frontend/src/plugins/d3/__tests__/graphvizG16.test.ts | fixed |
| D-049 | drawio | dark | theme | `swimlane-fillopacity-crushes-label-contrast:d…` | 1.78:1 (measured) | _visual_ | frontend/src/plugins/d3/drawioPlugin.ts (~1618: styleObj['fillOpacity'] = 20 in the isSwimlane branch) | fixed |
| D-055 | vega-lite | dark | theme | `color-chosen-outside-theme-system-invisible-i…` | 1.00:1 (measured) | _visual_ | frontend/src/plugins/d3/vegaLitePlugin.ts (arc-label colour from spec.background only ~L2532; isDarkMode computed for e… | fixed |
| D-069 | drawio | dark | theme | `default-fill-vs-theme-fontcolor:dark` | 1.08:1 (measured) | dark_fixed_label_#000000_on_default_fill_#C3D9FF: 14.69; dark_old_label_#e0e0e0_on_#C3D9FF: 1.083; light_label_#000000_on_#C3D9FF_unchanged: 14.69; n… | frontend/src/plugins/d3/drawioPlugin.ts | fixed |
| D-091 | joint | dark | theme | `bogus-theme-token-overrides-render-theme:dark` | 15.28:1 (measured) | _visual_ | frontend/src/plugins/d3/jointPlugin.ts:1728 (obj.theme lifted into spec.theme unvalidated) | fixed |
| D-100 | packet | dark | theme | `transparent-fill-assumes-white-page:dark` | 1.26:1 (measured) | _visual_ | frontend/src/plugins/d3/packetPlugin.ts (new effectiveCellBackdrop; wired at the field-label draw so getOptimalTextColo… | fixed |
| D-126 | chat-message | light | theme | `syntax-keyword-below-wcag:light` | 6.32:1 (measured) | _visual_ | frontend/src/index.css (body:not(.dark) .token.keyword { color: #d73a49 } at line 1234) | fixed |
| D-131 | d2 | dark | theme | `edge-color-dominates-at-density:dark` | 1.33:1 (measured) | _visual_ | frontend/src/plugins/d3/d2Plugin.ts: colors.edge dark '#f72585' at line 457 | fixed |
| D-159 | basic-chart | dark | theme | `hardcoded-axis-color-does-not-adapt:dark` | 1.97:1 (measured) | _visual_ | frontend/src/plugins/d3/basicChart.ts (both bubble-branch axes: .selectAll('text').style('fill', style.axisColor \|\| n… | fixed |
| D-161 | chat-message | dark | theme | `katex-error-red-below-wcag:dark` | 5.45:1 (measured) | _visual_ | frontend/src/components/MarkdownRenderer.tsx (git diff in response) | fixed |
| D-163 | chat-message | light | recovery | `transparent-fill-label-invisible:light` | 1.10:1 (measured) | _visual_ | frontend/src/plugins/d3/__tests__/chatMessageMermaidRecovery.test.ts (new test only) | fixed |
| D-186 | d3 | dark | theme | `marker-white-stroke-swamps-series:dark` | 3.92:1 (measured) | _visual_ | frontend/src/plugins/d3/basicChart.ts (render: point/bubble circles .attr('stroke', '#fff')) | fixed |
| D-187 | d3 | dark | theme | `label-over-node-fill-illegible:dark` | 1.51:1 (measured) | _visual_ | frontend/src/plugins/d3/forceDirectedPlugin.ts (render: labelColor dark default '#cccccc'; DEFAULT_GROUP_COLORS index 1… | fixed |
| D-195 | graphviz | light | theme | `nested-cluster-border-invisible-on-cluster-fi…` | 1.07:1 (measured) | light_border_#6e6e6e_vs_white: 5.10 (>=3.0 floor; old #cccccc=1.61); light_border_#6e6e6e_vs_lightgrey_#d3d3d3_cluster_fill: 3.41 (>=3.0; old #cccccc… | frontend/src/plugins/d3/graphvizPlugin.ts; frontend/src/plugins/d3/__tests__/graphvizG46.test.ts | fixed |
| D-196 | graphviz | dark | theme | `unfilled-node-keeps-authored-black-fontcolor:…` | 1.26:1 (measured) | light: unaffected (author dark text on white page ~21.0); dark: #ffffff label on ~#1e1e1e panel = 16.67 (was #000000 = 1.26) | frontend/src/plugins/d3/graphvizPlugin.ts; frontend/src/plugins/d3/__tests__/graphvizG46.test.ts | fixed |
| D-205 | mermaid | dark | theme | `timeline-light-fill-light-text:dark` | 1.61:1 (measured) | DARK: internally-consistent palette, label #1a1a1a on every section fill >=8.44:1 (text floor cleared) and every fill on dark page #2e3440 >=6.06:1 (… | frontend/src/plugins/d3/mermaidEnhancer.ts (TIMELINE_DARK_SECTION_FILLS/LABEL, buildTimelineDarkThemeVariables); mermai… | fixed |
| D-206 | mermaid | dark | theme | `linkstyle-stroke-override-dropped:dark` | 2.15:1 (measured) | DARK: honour-then-lighten via ensureReadableFill against #1e1e1e — author #aa0000 (2.15:1, sub-floor) lightened to #cc6666 = 4.49:1 (>=3 graphical fl… | frontend/src/plugins/d3/mermaidEnhancer.ts (parseLinkStyleStrokes, reapplyLinkStyleStrokes); mermaidPlugin.ts (wired at… | fixed |
| D-218 | network | light | recovery | `styles-plural-dialect-silently-ignored:light` | 1.61:1 (measured) | _visual_ | frontend/src/plugins/d3/networkDiagram.ts:16 (styles declared in interface) | fixed |
| D-233 | vega-lite | dark | theme | `boxplot-whisker-rule-low-contrast:dark` | 1.66:1 (measured) | _visual_ | frontend/src/plugins/d3/vegaLitePlugin.ts (no dark override for boxplot composite sub-mark strokes; theme applied via e… | fixed |
| D-242 | d2 | dark | theme | `dark-arrow-invisible-on-node-fill:dark` | 1.33:1 (measured) | _visual_ | frontend/src/plugins/d3/d2Plugin.ts: colors.edge dark '#f72585' at line 457; arrowhead marker fill (inherits edge colou… | fixed |
| D-244 | chat-message | light | theme | `table-gridline-below-3to1:light` | 3.12:1 (measured) | _visual_ | frontend/src/index.css (git diff in response) | fixed |
| D-245 | network | dark | theme | `hardcoded-group-color-lowcontrast:dark` | 2.87:1 (measured) | _visual_ | frontend/src/plugins/d3/networkDiagram.ts:330 (group rect stroke '#666') | fixed |
| D-263 | plotly | dark | theme | `annotation-arrow-unthemed:dark` | 1.43:1 (measured) | _visual_ | frontend/src/plugins/d3/plotlyPlugin.ts (applyPlotlyTheme does not touch layout.annotations arrowcolor/font or layout.s… | fixed |
| D-211 | mermaid | light | recovery | `init-directive-recovery-triggers-oversize-can…` | _visual_ | — | frontend/src/plugins/d3/mermaidEnhancer.ts (smart-quote init-directive sanitisation and the light-vs-dark theme-honouri… | wont-fix |
| D-216 | music | light | theme | `stave-line-vanishes-at-scale:light` | 2.85:1 (measured) | — | frontend/src/utils/d3Plugins/musicPlugin.ts (DARK_COLOR_REMAP lines 2381-2387, which intentionally omits #999999; apply… | wont-fix |
| D-241 | vega | dark | theme | `sequential-scheme-dark-end-below-floor:dark` | 1.21:1 (measured) | — | frontend/src/plugins/d3/vegaPlugin.ts (embedOptions.theme='dark' does not re-anchor continuous scheme ranges; no scheme… | wont-fix |
| D-256 | mermaid | dark | theme | `er-relationship-marker-filled-occludes-entity…` | _visual_ | — | frontend/src/plugins/d3/mermaidEnhancer.ts (er diagram dark theme marker styling) | wont-fix |
| D-257 | mermaid | dark | theme | `mindmap-root-label-low-contrast:dark` | _visual_ | — | frontend/src/plugins/d3/mermaidEnhancer.ts (mindmap dark label colour - root node label not receiving the theme foregro… | wont-fix |

The four defects with fully recorded before→after colour pairs are the clearest wins: **D-025** graphviz light edge labels went from forced `#ffffff` on `#ffffff` (1.0:1, invisible) to authored `#000000` on white (21.0:1); **D-069** drawio dark default-fill labels from `#e0e0e0` on `#C3D9FF` (1.08:1) to `#000000` on `#C3D9FF` (14.69:1); **D-196** graphviz dark unfilled-node text from `#000000` (1.26:1) to `#ffffff` on the `~#1e1e1e` panel (16.67:1); **D-195** graphviz light cluster borders from `#cccccc` (1.07:1 on the cluster fill) to `#6e6e6e` (3.41:1, clearing the 3.0 graphical floor). **D-010** (LaTeX theme-ink, 4 engines, 58 specs) injects `#EDEDED` ink on the `#1F1F1F` dark page (14.08:1) instead of black-on-dark (~1.15:1).

## 4. Per-engine detail

Each engine was stressed across four waves: **W1** canonical/typical diagrams, **W2** scale and density (large graphs, long labels, deep nesting), **W3** where present adversarial styling and author-supplied colours, and **W4** malformed/LLM-slippage input exercising the recovery path (trailing commas, smart quotes, markdown fences, orphaned tags). Tables below list every single-engine defect for the engine; cross-engine families that also touch it are named beneath. Disposition is the backlog `status` verbatim.

### chord

Before **31%/29%** (L/D) → after **100%/100%**; parity 10→45 of 45. 12 single-engine defects (12 fixed, 0 wont-fix, 0 open; kinds: 8 structural, 1 theme, 3 recovery). Worst measured contrast (any cell): 1.00:1 before; worst *remaining* 1.07:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-036 | `padangle-sum-starves-arc-width` | structural | chordLayout.padAngle(0.05) (chordPlugin.ts line 409) is a fixed per-GROUP constant with no N-awareness, so total inter-arc padding is 0.05*N radians … | frontend/src/plugins/d3/chordPlugin.ts | — | fixed |
| D-045 | `stroke-color-matches-background` | theme | Arc/segment stroke colour is set equal to the background, erasing the arc outline (1.0:1). Fix: resolve the stroke from the theme so it separates fro… | frontend/src/plugins/d3/chordPlugin.ts | — | fixed |
| D-046 | `subpixel-ribbons-at-high-edge-count` | structural | Ribbon width is starved by EDGE count independently of the node/padAngle axis: each ribbon's share of the total flow falls below the pixel grid, and … | frontend/src/plugins/d3/chordPlugin.ts | — | fixed |
| D-061 | `custom-height-clipped-by-fixed-container` | structural | spec.width/spec.height are written straight onto the SVG (lines 371-372) while the plugin declares sizingStrategy 'fixed' + containerStyles overflow:… | frontend/src/plugins/d3/chordPlugin.ts | — | fixed |
| D-083 | `negative-radius-below-120px-render-time…` | structural | outerRadius = Math.min(width,height)*0.5 - 60 (chordPlugin.ts line 415) is never clamped, so min(w,h) <= 120 yields a zero/negative radius (innerRadi… | frontend/src/plugins/d3/chordPlugin.ts | — | fixed |
| D-128 | `long-label-clipped-by-fixed-60px-gutter` | structural | The label gutter is a hardcoded 60px (outerRadius = min(w,h)*0.5 - 60, line 415) with text drawn at outerRadius+8 (line 452) and no truncation/wrappi… | frontend/src/plugins/d3/chordPlugin.ts | — | fixed |
| D-168 | `label-crowding-illegible-at-density` | structural | At N=100 arcs still have non-zero width (3.08px) so this brackets the N=126 padangle collapse from below, but the label band fails: pitch 15.58px for… | frontend/src/plugins/d3/chordPlugin.ts | — | fixed |
| D-169 | `radius-from-min-dimension-wastes-extrem…` | structural | outerRadius derives from Math.min(width,height) (line 415), so an extreme aspect wastes the long axis: at 1800x200 the ring is a ~55px postage stamp … | frontend/src/plugins/d3/chordPlugin.ts | — | fixed |
| D-170 | `fontsize-not-scaled-to-canvas` | structural | fontSize defaults to a fixed 11px irrespective of canvas (chordPlugin.ts line 375, applied line 456), so it falls from 1.83% of width at 600px to 0.5… | frontend/src/plugins/d3/chordPlugin.ts | — | fixed |
| D-171 | `unknown-wrapper-key-not-unwrapped` | recovery | The graph is nested one level too deep under a `spec` key. resolveChordSpec probes nodes/links at the top level or under `data` ONLY (chordPlugin.ts … | frontend/src/plugins/d3/chordPlugin.ts | — | fixed |
| D-172 | `names-colors-dropped-on-length-mismatch` | recovery | The matrix branch accepts `names`/`colors` only when `.length === n` EXACTLY (chordPlugin.ts lines 389 and 392), so a 5-entry list against a 6x6 matr… | frontend/src/plugins/d3/chordPlugin.ts | — | fixed |
| D-173 | `numeric-strings-in-size-clip-canvas` | recovery | Two independent coercion gaps. (1) width/height supplied as STRINGS "600" escape numeric coercion (used directly at lines 371-372), the SVG comes bac… | frontend/src/plugins/d3/chordPlugin.ts | — | fixed |

_Cross-engine families touching this engine:_ D-001 (`json-no-lenient-repair-30s-timeout`, open), D-002 (`label-color-hardcoded-not-theme-resolved`, open), D-004 (`markdown-fence-not-stripped`, open), D-005 (`low-opacity-marks-below-graphical-floor`, open), D-008 (`invalid-color-token-black-or-transparen…`, open), D-009 (`default-palette-swatch-below-graphical-…`, open).

### d2

Before **2%/0%** (L/D) → after **100%/100%**; parity 0→45 of 45. 25 single-engine defects (25 fixed, 0 wont-fix, 0 open; kinds: 17 structural, 6 recovery, 2 theme). Worst measured contrast (any cell): 1.14:1 before; worst *remaining* 18.39:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-020 | `elk-labelmanager-throws-grid-fallback` | structural | elk-labelmanager-throws-grid-fallback: buildElkNodeLabels() (d2Plugin.ts:161) emits bare {text} labels with no elk.labelManager:none layoutOption; el… | frontend/src/plugins/d3/d2Plugin.ts | frontend/src/plugins/d3/__tests__/d2G12.test.ts | fixed |
| D-030 | `arrowheads-hidden-under-node` | structural | arrowheads-hidden-under-node: trimEdgeToNodes() (d2Plugin.ts:32) runs edges border-to-border with a +6px gap so the head clears the target rect; mark… | frontend/src/plugins/d3/d2Plugin.ts | frontend/src/plugins/d3/__tests__/d2G12.test.ts | fixed |
| D-037 | `edge-label-parsed-as-node` | structural | edge-label-parsed-as-node: parseConnection() (d2Plugin.ts:957) splits the trailing ":label" off the last endpoint BEFORE matching endpoints and split… | frontend/src/plugins/d3/d2Plugin.ts | frontend/src/plugins/d3/__tests__/d2G12.test.ts | fixed |
| D-038 | `nested-container-infinity-rect` | structural | nested-container-infinity-rect: d2ContainerBounds() (d2Plugin.ts:123) collects member nodes by walking the container/parent chain and returns null fo… | frontend/src/plugins/d3/d2Plugin.ts | frontend/src/plugins/d3/__tests__/d2G12.test.ts | fixed |
| D-047 | `style-key-and-block-parsed-as-node-or-c…` | structural | Any 'x.style.y: value' line falls through parseLine() to parseSimpleNode and becomes a NODE whose label is the quoted value (boxes labelled '#8b0000'… | frontend/src/plugins/d3/d2Plugin.ts | d2G13.test.ts | fixed |
| D-048 | `attr-block-body-becomes-phantom-nodes` | recovery | WIDEST recovery gap by damage. 'id: Label {' ends with '{' so it is promoted to a CONTAINER named 'id: Label' and the node's own label is DISCARDED (… | frontend/src/plugins/d3/d2Plugin.ts | d2G25.test.ts | fixed |
| D-064 | `label-never-wraps-overflows-rect-and-ca…` | structural | calculateNodeHeight adds height for estimated wrapped lines, but the renderer emits one flat .text() with no tspans, so labels run straight out of th… | frontend/src/plugins/d3/d2Plugin.ts: calculateNodeWidth() at line 329 (Math.min clamp to 200); calculateNodeH… | — | fixed |
| D-065 | `grid-spacing-150-narrower-than-node-wid…` | structural | simpleGridLayout uses nodeSpacing=150 while calculateNodeWidth can return 200, so any label over ~21 chars makes every box overlap its right neighbou… | frontend/src/plugins/d3/d2Plugin.ts: simpleGridLayout() nodeSpacing=150 at line 346; calculateNodeWidth() max… | — | fixed |
| D-066 | `explicit-height-silently-truncates-node…` | structural | The svg height attribute is fixed at max(400, maxY+100) PIXELS while width is 100%, and nothing reads a requested size. Requesting 3000x300 on 60 nod… | frontend/src/plugins/d3/d2Plugin.ts: svg height attribute max(400, maxY+100) at line 450; viewBox at lines 44… | — | fixed |
| D-084 | `json-payload-mangled-to-bracket-nodes` | recovery | Worst image in the sweep: a silent, confident, totally wrong picture. A JSON graph payload has no d2 detection; the only lines accepted are '"nodes":… | frontend/src/plugins/d3/d2Plugin.ts | d2G25.test.ts | fixed |
| D-116 | `style-line-becomes-node-color-never-app…` | recovery | NO COLOUR PATH AT ALL. Nothing in d2Plugin.ts ever reads a colour from the definition — node/nodeStroke/edge/text are unconditional theme constants (… | frontend/src/plugins/d3/d2Plugin.ts | d2G13.test.ts + d2G45.test.ts | fixed |
| D-129 | `container-unlabeled-and-overbounded` | structural | Container name is parsed and stored but no <text> is ever emitted, so every group is anonymous. Bounds are computed by independent min/max passes ove… | frontend/src/plugins/d3/d2Plugin.ts: container render block at line 461 (renders <rect> only, no label <text>… | — | fixed |
| D-130 | `shape-keyword-ignored` | structural | parseNodeWithProperties() stores node.shape but the renderer unconditionally emits <rect rx=5>; there is no shape dispatch. circle/cylinder/queue/sql… | frontend/src/plugins/d3/d2Plugin.ts | d2G43.test.ts | fixed |
| D-131 | `edge-color-dominates-at-density:dark` | theme | The dark edge colour #f72585 is 4.36:1 on the #1f1f1f page — the brightest element on the canvas — so from ~200 edges up the magenta starburst/hairba… | frontend/src/plugins/d3/d2Plugin.ts: colors.edge dark '#f72585' at line 457 | — | fixed |
| D-175 | `edge-direction-reversed-ignored` | structural | Parser sets edge.reversed / edge.bidirectional but the renderer never reads them: x1/y1 stay on match[1] and marker-end stays on match[3], so 'a <- b… | frontend/src/plugins/d3/d2Plugin.ts | d2G25.test.ts | fixed |
| D-176 | `chained-connection-drops-edges` | structural | 'a -> b -> c' yields only ONE edge because the connection regex's greedy first/third groups match a and b and discard the rest; c becomes a disconnec… | frontend/src/plugins/d3/d2Plugin.ts | d2G43.test.ts | fixed |
| D-177 | `trailing-comment-not-stripped` | structural | Only line.startsWith('#') is filtered; a trailing '# comment' lands inside the node label ('Build # compiles sources'), blowing the box width past th… | frontend/src/plugins/d3/d2Plugin.ts | d2G43.test.ts | fixed |
| D-178 | `deep-nesting-renders-as-flat-overlappin…` | structural | 12 nested containers render as 12 mutually indistinguishable, unlabelled, overlapping dashed rects all positioned from the flat grid; depth 0 and dep… | frontend/src/plugins/d3/d2Plugin.ts | d2G44.test.ts | fixed |
| D-179 | `container-bounds-slice-through-unrelate…` | structural | Container bounds are min/max over child grid cells, but the grid assigns positions in declaration order with no regard to grouping, so at 20 containe… | frontend/src/plugins/d3/d2Plugin.ts | d2G44.test.ts | fixed |
| D-180 | `grid-layout-forces-square-aspect-ignore…` | structural | cols = ceil(sqrt(n)) is unconditional, so a 120-node linear chain becomes an 11x11 square read via 11 long right-to-left diagonal wrap edges — and wi… | frontend/src/plugins/d3/d2Plugin.ts | d2G44.test.ts | fixed |
| D-181 | `edges-crisscross-topology-agnostic-grid` | structural | 260 edges drawn as straight centre-to-centre lines across a grid whose positions have nothing to do with adjacency produce an undifferentiated hairba… | frontend/src/plugins/d3/d2Plugin.ts | d2G44.test.ts | fixed |
| D-182 | `semicolon-separator-not-split` | recovery | Semicolons are not statement separators. 'web -> api; api -> db; db -> cache' collapses to source 'web' / target 'api; api' (greedy connection regex)… | frontend/src/plugins/d3/d2Plugin.ts | d2G45.test.ts | fixed |
| D-183 | `mermaid-dialect-bracket-labels-become-n…` | recovery | No dialect detection. Mermaid bracket/brace/stadium label syntax ('A[Web Server]', 'B{API Gateway}', 'C[(Database)]') is treated as part of the node … | frontend/src/plugins/d3/d2Plugin.ts | d2G80.test.ts | fixed |
| D-242 | `dark-arrow-invisible-on-node-fill:dark` | theme | LATENT dark defect, currently masked. The dark edge/arrowhead colour #f72585 has only 1.33:1 (computed) against the dark node fill #4361ee it termina… | frontend/src/plugins/d3/d2Plugin.ts: colors.edge dark '#f72585' at line 457; arrowhead marker fill (inherits … | — | fixed |
| D-251 | `smart-quotes-not-normalized` | recovery | U+201C/201D/2018/2019 pass through as literal glyphs, so labels render with visible curly quotes where real D2 would treat them as string delimiters … | frontend/src/plugins/d3/d2Plugin.ts | d2G80.test.ts | fixed |

_Cross-engine families touching this engine:_ D-007 (`downscale-to-fit-shrinks-text-below-leg…`, open).

### network

Before **33%/31%** (L/D) → after **100%/100%**; parity 12→45 of 45. 11 single-engine defects (11 fixed, 0 wont-fix, 0 open; kinds: 6 structural, 4 recovery, 1 theme). Worst measured contrast (any cell): 1.00:1 before; worst *remaining* 1.99:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-027 | `viewport-clamp-edge-pileup-coincident-n…` | structural | The force layout is forceLink(distance 80) + forceManyBody(-200) + forceCenter with a 300-tick cap and NO bounding or collision force (charge set at … | frontend/src/plugins/d3/networkDiagram.ts:193 (clampNodePositionsToViewport) | — | fixed |
| D-052 | `tall-canvas-clipped-by-fixed-400px-cont…` | structural | sizingConfig.containerStyles pins height to '400px' (networkDiagram.ts:273-274) while the SVG is emitted at its natural height, so the container show… | frontend/src/plugins/d3/networkDiagram.ts:273-274 (containerStyles.height '400px') | — | fixed |
| D-096 | `missing-endpoint-field-rejects-entire-g…` | recovery | VALID JSON; semantic defect. One link omits `target`, one uses the obvious alias `to`, one node carries `name` instead of `id`. isNetworkDiagramSpec … | frontend/src/plugins/d3/networkDiagram.ts:259-261 (every() all-or-nothing detector) | — | fixed |
| D-097 | `numeric-id-string-endpoint-mismatch-dro…` | recovery | Renders and reports SUCCESS with 100% of edges silently gone - the dangerous case. String coordinates/sizes are coerced by Number() so all 5 nodes la… | frontend/src/plugins/d3/networkDiagram.ts:151 (new Set of raw ids) | — | fixed |
| D-146 | `long-label-clipped-and-overlapping` | structural | Node labels are single <text> elements with text-anchor 'middle' and no width measurement, wrapping, truncation or ellipsis (label render ~networkDia… | frontend/src/plugins/d3/networkDiagram.ts:402-405 (label <text>, text-anchor middle, no wrap) | — | fixed |
| D-147 | `label-overlap-at-high-node-count` | structural | There is no label collision avoidance, halo, background plate or leader line, and labels sit at a fixed dy = -(size)-5 (networkDiagram.ts:~405). In a… | frontend/src/plugins/d3/networkDiagram.ts:~405 (fixed dy label placement, no collision avoidance) | — | fixed |
| D-148 | `font-size-below-legible-floor` | structural | Effective label size = fontSize x (container width / spec width) and the engine enforces no minimum. A 3000px viewBox downscales 12px to ~4.9px (w2-0… | frontend/src/plugins/d3/networkDiagram.ts (fontSize applied verbatim, no effective-size floor) | — | fixed |
| D-149 | `group-rects-collapse-to-one-hardcoded-p…` | structural | The `groups` feature is a stub: rect geometry comes from a hardcoded ternary `d.id === 'modem_board' ? 180/350 : 680/200` with fixed y=50 h=500 (netw… | frontend/src/plugins/d3/networkDiagram.ts:325-336 (group rect/label hardcoded modem_board ternary) | — | fixed |
| D-217 | `unrecognized-graph-envelope-off-by-one-…` | recovery | VALID JSON, zero output. resolveNetworkSpec probes exactly two shapes - top-level nodes/links and `data.nodes`/`data.links` (lift logic ~networkDiagr… | frontend/src/plugins/d3/networkDiagram.ts:62-72 (two-path field lifting, no envelope search) | — | fixed |
| D-218 | `styles-plural-dialect-silently-ignored:…` | recovery | THEME-SPLIT recovery gap. `styles` is the plural keyed form actually DECLARED in the NetworkDiagramSpec interface (networkDiagram.ts:16) and resolveN… | frontend/src/plugins/d3/networkDiagram.ts:16 (styles declared in interface) | — | fixed |
| D-245 | `hardcoded-group-color-lowcontrast:dark` | theme | The `groups` rect stroke and group-label fill are hardcoded '#666' (networkDiagram.ts:330 and :336) with no theme adaptation. On dark #666 measures 2… | frontend/src/plugins/d3/networkDiagram.ts:330 (group rect stroke '#666') | — | fixed |

_Cross-engine families touching this engine:_ D-001 (`json-no-lenient-repair-30s-timeout`, open), D-002 (`label-color-hardcoded-not-theme-resolved`, open), D-003 (`user-fill-color-no-contrast-guard`, open), D-005 (`low-opacity-marks-below-graphical-floor`, open), D-009 (`default-palette-swatch-below-graphical-…`, open).

### force-directed

Before **9%/0%** (L/D) → after **100%/98%**; parity 0→44 of 45. 12 single-engine defects (12 fixed, 0 wont-fix, 0 open; kinds: 10 structural, 2 recovery). Worst measured contrast (any cell): 1.04:1 before; worst *remaining* 1.59:1 after (engine files carry no post-fix ratios — see §6). ⚠️ Reconciliation gap: force-directed-w4-06 (see §5).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-022 | `no-fit-to-extent-fixed-canvas-clips-con…` | structural | The dominant structural defect. The simulation hardcodes forceManyBody.strength(-200) and forceLink.distance(80), runs 300 warm-up ticks, and does NO… | frontend/src/plugins/d3/forceDirectedPlugin.ts | forceDirectedTheme.test.ts | fixed |
| D-071 | `container-shape-unrecognised-silent-tim…` | recovery | Container-shape recognition is exactly two probes wide: resolveForceDirectedSpec accepts only `parsed.nodes` and `parsed.data?.nodes`. Valid JSON wit… | frontend/src/plugins/d3/forceDirectedPlugin.ts | forceDirectedG05.test.ts | fixed |
| D-072 | `unresolved-link-endpoints-drop-edges-an…` | recovery | The most dangerous MODE: partial recovery that renders a confident wrong picture. Both specs pass canHandle and render, but the render path's link fi… | frontend/src/plugins/d3/forceDirectedPlugin.ts | forceDirectedG08.test.ts | fixed |
| D-089 | `charge-linkDistance-collideRadius-silen…` | structural | resolveForceDirectedSpec lifts charge/collideRadius/linkDistance onto the resolved spec (they survive normalization), but render() hardcodes `.distan… | frontend/src/plugins/d3/forceDirectedPlugin.ts | forceDirectedG05.test.ts | fixed |
| D-122 | `labels-blind-offset-no-collision-or-wrap` | structural | Labels use a blind fixed offset `.attr('x', (d.size\|\|8)+4).attr('y', 3)` with no collision avoidance, no wrapping, no ellipsis and no max-width. At… | frontend/src/plugins/d3/forceDirectedPlugin.ts | forceDirectedG05.test.ts,forceDirectedG67.test.ts | fixed |
| D-137 | `declared-style-option-not-read` | structural | Two options declared in the plugin's own interfaces are never applied. style.nodeColor (uniform fill, in ForceStyle) is not read by getNodeColor, whi… | frontend/src/plugins/d3/forceDirectedPlugin.ts | forceDirectedG08.test.ts,forceDirectedG05.test.ts | fixed |
| D-138 | `fontsize-not-scaled-to-viewbox-no-floor` | structural | `const fontSize = style.fontSize \|\| 10` is applied verbatim as `${fontSize}px` and is never scaled against the viewBox and has no minimum floor. A … | frontend/src/plugins/d3/forceDirectedPlugin.ts | forceDirectedG67.test.ts | fixed |
| D-190 | `group-palette-recycles-at-10-groups` | structural | getNodeColor indexes with `(d.group ?? 0) % DEFAULT_GROUP_COLORS.length` and the palette has 10 members, so distinct groups collide from the 11th onw… | frontend/src/plugins/d3/forceDirectedPlugin.ts | forceDirectedG08.test.ts | fixed |
| D-191 | `arrowhead-scales-with-strokewidth` | structural | The arrow marker is declared markerWidth/markerHeight 6 and inherits SVG's default markerUnits='strokeWidth', so the arrowhead scales with link weigh… | frontend/src/plugins/d3/forceDirectedPlugin.ts | forceDirectedG08.test.ts | fixed |
| D-192 | `node-radius-clamp-exceeds-canvas` | structural | FORCE_MAX_NODE_RADIUS = 200 is a constant unrelated to canvas size, permitting a 400px-diameter disc inside a 500px-tall default canvas. At 150 nodes… | frontend/src/plugins/d3/forceDirectedPlugin.ts | forceDirectedG67.test.ts | fixed |
| D-252 | `extreme-aspect-ratio-collapses-to-illeg…` | structural | A 3000x200 (15:1) viewBox scaled to fit the ~1280px frame (~0.43x) with only 200px of vertical extent leaves nodes at ~3px and labels at ~4.27px effe… | frontend/src/plugins/d3/forceDirectedPlugin.ts | forceDirectedTheme.test.ts,forceDirectedG67.test.ts | fixed |
| D-253 | `link-hairball-occludes-labels` | structural | K28 (378 links) was the only structurally complete render at scale, but the link layer overprints the interior node labels. Measured for honesty this… | frontend/src/plugins/d3/forceDirectedPlugin.ts | forceDirectedG05.test.ts | fixed |

_Cross-engine families touching this engine:_ D-001 (`json-no-lenient-repair-30s-timeout`, open), D-002 (`label-color-hardcoded-not-theme-resolved`, open), D-003 (`user-fill-color-no-contrast-guard`, open), D-004 (`markdown-fence-not-stripped`, open), D-005 (`low-opacity-marks-below-graphical-floor`, open), D-006 (`hardcoded-light-background-ignores-dark…`, open), D-008 (`invalid-color-token-black-or-transparen…`, open), D-009 (`default-palette-swatch-below-graphical-…`, open).

### graphviz

Before **24%/31%** (L/D) → after **98%/98%**; parity 8→44 of 45. 10 single-engine defects (9 fixed, 1 wont-fix, 0 open; kinds: 5 theme, 2 recovery, 3 structural). Worst measured contrast (any cell): 1.00:1 before; worst *remaining* 1.02:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-025 | `edge-label-white-on-white:light` | theme | The single highest-frequency rendering defect: every directed graph carrying edge-attached text loses that text in light mode while dark renders it p… | frontend/src/plugins/d3/graphvizPlugin.ts; frontend/src/plugins/d3/__tests__/graphvizG64.test.ts | frontend/src/plugins/d3/__tests__/graphvizG64.test.ts | fixed |
| D-026 | `no-lexical-repair-stage:parse-error-del…` | recovery | Eight lexically-broken but trivially-repairable specs never reach layout, and the failure is delivered as a 30s timeout with svg:0 canvas:0 img:0 and… | frontend/src/plugins/d3/graphvizPlugin.ts; frontend/src/plugins/d3/__tests__/graphvizG16.test.ts | frontend/src/plugins/d3/__tests__/graphvizG16.test.ts | fixed |
| D-034 | `authored-fill-overridden-by-node-palett…` | theme | In dark mode the plugin destroys author-chosen fills. graphvizPlugin.ts's dark node loop (lines ~443-497) inspects every ellipse/polygon whose fill!=… | frontend/src/plugins/d3/graphvizPlugin.ts; frontend/src/plugins/d3/__tests__/graphvizG16.test.ts | frontend/src/plugins/d3/__tests__/graphvizG16.test.ts | fixed |
| D-139 | `record-port-name-leaks-into-label` | structural | Record port declarations render as literal visible text ('<f0> left' instead of 'left') for every port in the graph, in both themes. Plain records wi… | frontend/src/plugins/d3/graphvizPlugin.ts; frontend/src/plugins/d3/__tests__/graphvizG46.test.ts | frontend/src/plugins/d3/__tests__/graphvizG46.test.ts | fixed |
| D-193 | `justification-escape-literal` | structural | \n is translated correctly (the label->HTML rewrite maps \n and \\n to <br/> at graphvizPlugin.ts line ~365) but \l (left-justify) and \r (right-just… | frontend/src/plugins/d3/graphvizPlugin.ts; frontend/src/plugins/d3/__tests__/graphvizG46.test.ts | frontend/src/plugins/d3/__tests__/graphvizG46.test.ts | fixed |
| D-194 | `node-label-subpixel-at-high-density` | structural | Density, not aspect ratio: a 120-node circo ring is roughly square but must fit 1280px, giving ~10px nodes and sub-pixel labels; the ring is addition… | frontend/src/plugins/d3/graphvizPlugin.ts; frontend/src/plugins/d3/__tests__/graphvizG56.test.ts | frontend/src/plugins/d3/__tests__/graphvizG56.test.ts | fixed |
| D-195 | `nested-cluster-border-invisible-on-clus…` | theme | Light-only, and the cleanest theme-parity defect in the sweep: dark renders all 12 nested clusters perfectly (cyan border 3.91, labels 7.53) while li… | frontend/src/plugins/d3/graphvizPlugin.ts; frontend/src/plugins/d3/__tests__/graphvizG46.test.ts | frontend/src/plugins/d3/__tests__/graphvizG46.test.ts | fixed |
| D-196 | `unfilled-node-keeps-authored-black-font…` | theme | A structurally PERFECT recovery that is illegible in dark — the theme-parity class this sweep prizes. fillcolor=transparent/none are honoured correct… | frontend/src/plugins/d3/graphvizPlugin.ts; frontend/src/plugins/d3/__tests__/graphvizG46.test.ts | frontend/src/plugins/d3/__tests__/graphvizG46.test.ts | fixed |
| D-254 | `deprecated-setlinewidth-silently-dropped` | recovery | style="setlinewidth(N)" (pre-2011 DOT idiom, common in training data) is silently discarded so nodes named 'Thick A'/'Thick B'/'Thin C' get identical… | frontend/src/plugins/d3/graphvizPlugin.ts; frontend/src/plugins/d3/__tests__/graphvizSetlinewidth.test.ts | frontend/src/plugins/d3/__tests__/graphvizSetlinewidth.test… | fixed |
| D-140 | `gradient-fill-unparsed-text-forced-white` | theme | gradient-fill-unparsed-text-forced-white. The only suspect is the shared frontend/src/utils/colorUtils.ts (getOptimalTextColor returns white when a f… | frontend/src/utils/colorUtils.ts:calculateContrastRatio (returns 1 / the parse fails when a paint is url(#...… | — | wont-fix |

_Cross-engine families touching this engine:_ D-002 (`label-color-hardcoded-not-theme-resolved`, open), D-007 (`downscale-to-fit-shrinks-text-below-leg…`, open), D-008 (`invalid-color-token-black-or-transparen…`, open), D-011 (`capture-container-crops-tall-oversize-c…`, open).

### joint

Before **11%/9%** (L/D) → after **96%/96%**; parity 4→43 of 45. 16 single-engine defects (14 fixed, 2 wont-fix, 0 open; kinds: 11 structural, 2 theme, 3 recovery). Worst measured contrast (any cell): 1.00:1 before; worst *remaining* 6.40:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-028 | `content-exceeds-viewport-no-scale-to-fit` | structural | There is no scale-to-fit. fitContentToPaper (~jointPlugin.ts:2085) sets finalWidth = max(contentWidth, containerWidth) and writes viewBox='0 0 finalW… | frontend/src/plugins/d3/jointPlugin.ts:~2085 (fitContentToPaper — finalWidth=max(content,container), viewBox … | — | fixed |
| D-031 | `raw-dia-element-missing-type:cell-dropp…` | structural | Every creator that builds a bare `new dia.Element({...})` with no `type` string is rejected by @joint/core v4 ('dia.Graph: cell type must be a string… | frontend/src/plugins/d3/jointPlugin.ts (createCylinderElement/createElectricalElement/createDocumentElement/c… | — | fixed |
| D-040 | `shape-palette-label-contrast` | theme | The default palette puts white/near-white bold 13px labels on mid-saturation fills. It is one palette that fails in BOTH directions (per the rubric t… | frontend/src/plugins/d3/jointPlugin.ts:340 (diamond fill #ebcb8b/#f39c12) | — | fixed |
| D-050 | `label-geometry-not-fitted-to-node` | structural | No textWrap and no ellipsis exist anywhere in jointPlugin.ts (grep-confirmed zero occurrences), so label geometry is never reconciled with node geome… | frontend/src/plugins/d3/jointPlugin.ts (element label attrs — no textWrap/ellipsis; hardcoded font sizes 14/1… | — | fixed |
| D-090 | `link-endpoint-alias-from-to-unmapped:li…` | recovery | The most dangerous failure because it fails PLAUSIBLY. Bare {id:x} elements degrade gracefully into default rects labelled with their id, but the fro… | frontend/src/plugins/d3/jointShapeResolver.ts (normalizeJointCells — endpoint key mapping) | — | fixed |
| D-091 | `bogus-theme-token-overrides-render-them…` | theme | The only theme-asymmetric result in the sweep (ok light, fail dark). jointPlugin.ts:1728 lifts obj.theme off the JSON definition into spec.theme when… | frontend/src/plugins/d3/jointPlugin.ts:1728 (obj.theme lifted into spec.theme unvalidated) | — | fixed |
| D-117 | `element-attrs-override-ignored` | structural | Author-supplied element `attrs` are advertised in the JointElement interface but never merged by any creator (grep confirms only attrs.line.strokeDas… | frontend/src/plugins/d3/jointPlugin.ts (createEnhancedRectElement et al — build attrs from scratch, never mer… | — | fixed |
| D-197 | `nesting-depth-off-by-one:no-descent` | recovery | Body is valid JSON so JSON.parse succeeds, but the cells array sits one level down under obj.graph.cells. The guard at jointPlugin.ts:1721 is `if (ob… | frontend/src/plugins/d3/jointPlugin.ts:1721 (depth-1-only guard obj.elements\|\|obj.cells) | — | fixed |
| D-198 | `string-boolean-not-coerced:manual-layou…` | recovery | Coercion is half-present. String NUMBERS are tolerated ({width:'140',height:'70'} yields correct 140x70 geometry) but the string BOOLEAN autoLayout:'… | frontend/src/plugins/d3/jointPlugin.ts:1725 (spec.autoLayout lift — no boolean coercion) and the autoLayout b… | — | fixed |
| D-199 | `link-overdraw-erases-node-labels-at-den…` | structural | Links are drawn ABOVE elements and are never routed around intervening nodes, so at avg degree 14 (280 links / 40 nodes) horizontal edge bundles pass… | frontend/src/plugins/d3/jointPlugin.ts (link z-order / graph.addCells ordering) | — | fixed |
| D-200 | `link-label-collision-overdraw+router-ig…` | structural | Three linked link defects. (1) appendLabel declares a backing rect (fill #ffffff / #3b4252) that is never drawn, so labels sit directly on lines/arro… | frontend/src/plugins/d3/jointPlugin.ts (appendLabel — undrawn backing rect; createEnhancedLink — connectionSt… | — | fixed |
| D-202 | `nested-container-label-occluded-by-chil…` | structural | Geometry and z-order via cells parent/embeds work, but every container label is centre-anchored (textVerticalAnchor 'middle' in every creator) and is… | frontend/src/plugins/d3/jointPlugin.ts:240 (label fill ternary) and creator label attrs (textVerticalAnchor '… | — | fixed |
| D-203 | `network-shapes-flattened-to-plain-rect` | structural | createNetworkElement (jointPlugin.ts:~512) unconditionally returns shapes.standard.Rectangle for router/switch/server/firewall/cloud alike, so the ne… | frontend/src/plugins/d3/jointPlugin.ts:~512 (createNetworkElement — returns standard.Rectangle for all networ… | — | fixed |
| D-204 | `port-position-string-invalid-layoutCall…` | structural | A port declared as position:'left' (the plain-string form the Port interface documents) is passed straight into element.addPort; JointJS then throws … | frontend/src/plugins/d3/jointPlugin.ts (port handling — element.addPort with string position; getPortCenter/l… | — | fixed |
| D-201 | `directed-graph-layout-throws-at-scale` | structural | DirectedGraph.layout throws 'TypeError: Cannot read properties of undefined (reading x)' from importElement inside DirectedGraph.fromGraphLib with NO… | frontend/src/plugins/d3/jointPlugin.ts (DirectedGraph.layout call + catch->grid fallback; dagre/graphlib brid… | — | wont-fix |
| D-255 | `explicit-width-height-ignored` | structural | spec.width/height are silently discarded in BOTH directions: 4000x3000 declared over a small graph (w2-08) and 160x120 declared over a large one (w2-… | frontend/src/plugins/d3/jointPlugin.ts:~2085 (fitContentToPaper — spec.width/height never read) | — | wont-fix |

_Cross-engine families touching this engine:_ D-001 (`json-no-lenient-repair-30s-timeout`, open), D-004 (`markdown-fence-not-stripped`, open).

### vega-lite

Before **33%/27%** (L/D) → after **93%/93%**; parity 11→42 of 45. 17 single-engine defects (14 fixed, 3 wont-fix, 0 open; kinds: 12 structural, 2 theme, 3 recovery). Worst measured contrast (any cell): 1.00:1 before; worst *remaining* 1.04:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-035 | `nominal-axis-labels-forced-90deg` | structural | The widest-breadth structural defect (5 specs, both themes, 10 render-failures). A TOP-LEVEL or FACET nominal x encoding has its tick labels rotated … | frontend/src/plugins/d3/vegaLayerDefaults.ts (AXIS_DEFAULTS x.labelAngle:0 ~L186-190; applySharedAxisDefaults… | — | fixed |
| D-055 | `color-chosen-outside-theme-system-invis…` | theme | The canonical theme-parity cluster: a colour resolved WITHOUT consulting isDarkMode lands on the dark background. Three loci, one root cause. (1) Tex… | frontend/src/plugins/d3/vegaLitePlugin.ts (arc-label colour from spec.background only ~L2532; isDarkMode comp… | — | fixed |
| D-079 | `facet-layout-sizing-broken` | structural | Two facet failures, one helper - though w1-11's severity was REVISED iteration 2. w1-11 (3-column column facet) NO LONGER shows off-canvas clipping o… | frontend/src/plugins/d3/vegaFacetLayout.ts (describeFacetLayout L125-150 column detection; resolveFacetCellWi… | — | fixed |
| D-104 | `unknown-color-scheme-crashes-render-bla…` | recovery | The definitive 'an image coming back is NOT success' case: render_diagram reports a normal successful render but the 2.6KB PNG contains only three ve… | frontend/src/plugins/d3/vegaLitePlugin.ts (fixInvalidColorSchemeInArcs guards only ARC scheme; a general scal… | — | fixed |
| D-105 | `bare-array-data-not-normalised-empty-ch…` | recovery | Silent empty chart, the most dangerous non-hang recovery outcome: axes, both axis titles ('city','pop') and the plot frame render, but there are NO b… | frontend/src/plugins/d3/vegaLitePlugin.ts (sanitizeSpec; no bare-array data coercion) | — | fixed |
| D-106 | `arc-labels-stacked-at-center` | structural | The ARC-TEXT-FIX path injects per-slice text labels but positions ALL of them at the single centre point of the donut instead of radially per slice, … | frontend/src/plugins/d3/vegaLitePlugin.ts (ARC-TEXT-FIX text layer construction ~L2520-2560; radial label pla… | — | fixed |
| D-107 | `dual-axis-independent-scale-collapsed` | structural | A layered bar+line with resolve.scale.y='independent' draws TWO y axes ('Signups' left, 'Conversion %' right) but the right axis inherits the left's … | frontend/src/plugins/d3/vegaLitePlugin.ts (resolve handling near sanitizeSpec; independent y-scale not preser… | — | fixed |
| D-151 | `categorical-palette-recycles-at-11-seri…` | structural | The categorical colour encoding stops being injective at >10 series because both active palettes are exactly 10 long: theme-excel.ts range.category h… | frontend/node_modules/vega-themes/src/theme-excel.ts (range.category, 10 entries) | — | fixed |
| D-152 | `legend-truncated-at-30-entry-cap` | structural | The symbol legend is silently capped at exactly 30 entries in both themes (bounded precisely: w2-13's 30-entry legend renders complete with no warnin… | frontend/src/plugins/d3/vegaLitePlugin.ts (no legend.symbolLimit override; Vega default 30 applies) | — | fixed |
| D-153 | `authored-height-inflated-to-min` | structural | A SMALL authored top-level height is DISCARDED and replaced by the ~305px default: height 40 -> ~305px (w2-06), height 28 -> ~310px (w2-08), silently… | frontend/src/plugins/d3/vegaSizing.ts (resolveAutosize pad/fit-x) | — | fixed |
| D-233 | `boxplot-whisker-rule-low-contrast:dark` | theme | Light-perfect/dark-degraded: the boxplot composite mark's whisker and cap RULES keep a near-black stroke in dark instead of adapting, so the thinnest… | frontend/src/plugins/d3/vegaLitePlugin.ts (no dark override for boxplot composite sub-mark strokes; theme app… | — | fixed |
| D-234 | `dialect-mismatch-v2-schema-drops-legend` | recovery | Partial recovery under a v2 $schema carrying v5-only syntax: the 3 bars, per-category colours and the encoding-level title 'Yield' all render, but th… | frontend/src/plugins/d3/vegaLitePlugin.ts (sanitizeSpec / $schema handling) | — | fixed |
| D-236 | `temporal-axis-duplicate-tick-labels` | structural | A temporal x axis emits roughly one tick per DATA ROW rather than one per distinct date, so 'Jan 2024'..'Apr 2024' each repeat ~4x and the final mont… | frontend/src/plugins/d3/vegaLitePlugin.ts (temporal axis tick generation; no tickCount/timeUnit normalisation… | — | fixed |
| D-237 | `dense-nominal-labels-overprint-no-thinn…` | structural | All 200 bars render, but all 200 nominal tick labels are emitted (rotated 90deg, ~5.9px band pitch) and overprint into a solid illegible smear in bot… | frontend/src/plugins/d3/vegaLayerDefaults.ts (AXIS_DEFAULTS sets labelLimit/labelFontSize but no labelOverlap… | — | fixed |
| D-108 | `tall-canvas-rows-clipped-to-window` | structural | w2-07 authored height 2400 renders only a ~990px middle window (title+x-axis lost). Mechanism is a post-render container/viewport sizing interaction … | frontend/src/plugins/d3/vegaSizing.ts (resolveAutosize returns pad/fit-x; height not viewport-clamped) | — | wont-fix |
| D-235 | `nested-composite-clipped-top-and-bottom` | structural | w2-09 nested vconcat>hconcat>layer clipped top (title) + bottom (x labels/axis title). Composite vertical-extent reservation is a render-time scenegr… | frontend/src/plugins/d3/vegaSizing.ts (isCompositeSpec branch of resolveSpecWidth L52-56; no vertical extent … | — | wont-fix |
| D-238 | `text-mark-overplot-no-collision-avoidan…` | structural | w2-15 150 text marks at fontSize 9 overprint (~30 collide). Requires text-mark collision avoidance/thinning which Vega-Lite does not provide as a pri… | frontend/src/plugins/d3/vegaLitePlugin.ts (no text-mark collision avoidance; text-mark colour path differs fr… | — | wont-fix |

_Cross-engine families touching this engine:_ D-001 (`json-no-lenient-repair-30s-timeout`, open), D-005 (`low-opacity-marks-below-graphical-floor`, open), D-006 (`hardcoded-light-background-ignores-dark…`, open).

### vega

Before **33%/18%** (L/D) → after **93%/87%**; parity 8→39 of 45. 13 single-engine defects (11 fixed, 2 wont-fix, 0 open; kinds: 9 structural, 3 recovery, 1 theme). Worst measured contrast (any cell): 1.00:1 before; worst *remaining* 1.07:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-029 | `postrender-viewbox-overflow-clips-conte…` | structural | The engine's single most destructive structural defect and its scale ceiling - not mark count. postRenderSizing() in render() measures the scenegraph… | frontend/src/plugins/d3/vegaPlugin.ts (+vegaSizing.ts) | vegaG22/vegaG23 | fixed |
| D-080 | `wrong-dialect-not-handed-off-or-rewritt…` | recovery | Two dialect near-misses, one root: dialect is detected by a substring on $schema and never reconciled with the body. (w4-07) a Vega-Lite body (object… | frontend/src/plugins/d3/vegaPlugin.ts (+vegaSizing.ts) | vegaG22 | fixed |
| D-109 | `dataflow-error-escapes-catch-blank-canv…` | recovery | The most dangerous outcome in the engine and an error-handling defect, not a colour one. range {scheme:'ziyaDark'} throws 'Unrecognized scheme name: … | frontend/src/plugins/d3/vegaPlugin.ts (+vegaSizing.ts) | vegaG22 + vegaErrorPlaceholder.test.ts | fixed |
| D-110 | `v5-method-rewrite-drops-datum-prefix` | structural | The v5->v6 expression compatibility shim CORRUPTS the very specs it exists to rescue. rewriteMethodCallsInExpr() scans backwards from a `.method(` ca… | frontend/src/plugins/d3/vegaPlugin.ts (+vegaSizing.ts) | vegaG23 | fixed |
| D-111 | `geoshape-graticule-flood-and-bbox-shrink` | structural | A geographic spec (mercator projection + graticule + inline-GeoJSON geoshape + projected city points) is structurally destroyed in both themes: one m… | frontend/src/plugins/d3/vegaPlugin.ts (+vegaSizing.ts) | vegaG23 | fixed |
| D-112 | `engine-chrome-injected-footer-and-demo-…` | structural | render() unconditionally injects an HTML footer with the hardcoded literal 'Hover any section for line details' beneath EVERY Vega chart (present in … | frontend/src/plugins/d3/vegaPlugin.ts (+vegaSizing.ts) | vegaG54 | fixed |
| D-154 | `silent-mark-loss-no-defaulting` | recovery | Two unambiguous-intent specs the runtime could recover but Ziya does not pre-process. (w4-09) encode channels (x/y/fill) sit directly under `encode` … | frontend/src/plugins/d3/vegaPlugin.ts (+vegaSizing.ts) | vegaG34 | fixed |
| D-155 | `high-cardinality-band-axis-not-thinned` | structural | High-cardinality band axes emit EVERY tick label with no decimation, so at sub-legible pitch they overlap into a solid smear in which no category is … | frontend/src/plugins/d3/vegaPlugin.ts (+vegaSizing.ts) | vegaG54 | fixed |
| D-156 | `categorical-palette-recycles-silently` | structural | An ordinal scale whose domain exceeds its scheme length cycles silently, so hue stops being an identifier and the legend becomes actively misleading.… | frontend/src/plugins/d3/vegaPlugin.ts (+vegaSizing.ts) | vegaG54 | fixed |
| D-239 | `static-text-mark-stripped-by-arc-chrome…` | structural | filterVegaChromeMarks() deletes every static (non-data-bound) text mark and every group mark from any spec that contains a data-bound `arc` mark. The… | frontend/src/plugins/d3/vegaPlugin.ts (+vegaSizing.ts) | vegaG34 | fixed |
| D-240 | `long-labels-truncated-to-identical-pref…` | structural | Vega's default axis labelLimit silently truncates long category names to ~35 chars with an ellipsis, applied before any theming, so two distinct cate… | frontend/src/plugins/d3/vegaPlugin.ts (+vegaSizing.ts) | vegaG54 | fixed |
| D-241 | `sequential-scheme-dark-end-below-floor:…` | theme | w2-02 sequential-scheme dark-end below floor:dark. Fixing requires re-anchoring a built-in sequential scheme's lightness ramp per theme (or a runtime… | frontend/src/plugins/d3/vegaPlugin.ts (embedOptions.theme='dark' does not re-anchor continuous scheme ranges;… | — | wont-fix |
| D-265 | `group-title-signal-cannot-see-facet-dat…` | structural | w2-15 group-title signal cannot see facet datum. This is a Vega signal-scope/facet-context problem resolved at view runtime, not a spec pre-transform… | frontend/src/plugins/d3/vegaPlugin.ts (rewriteV5Expressions touches update/expr/signal strings but not group … | — | wont-fix |

_Cross-engine families touching this engine:_ D-001 (`json-no-lenient-repair-30s-timeout`, open), D-002 (`label-color-hardcoded-not-theme-resolved`, open), D-003 (`user-fill-color-no-contrast-guard`, open), D-005 (`low-opacity-marks-below-graphical-floor`, open), D-006 (`hardcoded-light-background-ignores-dark…`, open), D-007 (`downscale-to-fit-shrinks-text-below-leg…`, open), D-009 (`default-palette-swatch-below-graphical-…`, open).

### plotly

Before **31%/27%** (L/D) → after **89%/89%**; parity 12→40 of 45. 11 single-engine defects (8 fixed, 2 wont-fix, 1 open; kinds: 6 structural, 4 recovery, 1 theme). Worst measured contrast (any cell): 1.00:1 before; worst *remaining* 1.00:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-032 | `fixed-margins-no-automargin` | structural | The plugin writes a hardcoded layout.margin { t:40, r:20, b:40, l:60 } in render() and never enables automargin, so text that needs more room collide… | frontend/src/plugins/d3/plotlyPlugin.ts (plotlyPlugin.render -- layout.margin literal { t:40, r:20, b:40, l:6… | — | fixed |
| D-053 | `silent-drop-by-misplacement` | recovery | A family of author-label losses that produce a plausible-but-mislabelled chart with NO error. (a) plotly.js v2 removed the string-shorthand title, so… | frontend/src/plugins/d3/plotlyPreprocessor.ts (fixMultilineTitle does not persist the string->{text} coercion… | — | fixed |
| D-078 | `explicit-size-exceeds-viewport-content-…` | structural | Explicit layout.width / layout.height are honoured literally and nothing constrains them to the capture viewport, so oversized figures lose content s… | frontend/src/plugins/d3/plotlyPlugin.ts (render -- renderDiv.style.height = specHeight+'px'; layout width pas… | — | fixed |
| D-225 | `four-arg-rgb-alpha-silently-dropped` | recovery | A four-argument rgb(214,39,40,0.2) (an extremely common model slip for rgba) has its alpha SILENTLY dropped and renders fully OPAQUE, so the intended… | frontend/src/plugins/d3/plotlyPreprocessor.ts (no colour-string normalisation pass) | — | fixed |
| D-226 | `missing-x-appends-index-categories-seri…` | recovery | Missing 'type' and 'mode' default harmlessly and an absent layout block is harmless, but the fatal data-shape defect is a trace with no 'x' beside a … | frontend/src/plugins/d3/plotlyPreprocessor.ts (no missing-x back-fill pass) | — | fixed |
| D-228 | `legend-overflow-clipped` | structural | The legend has a hard ceiling of ~26 entries in the plugin's default 60vh render div; entries 27+ are clipped behind a scroll track that does not exi… | frontend/src/plugins/d3/plotlyPlugin.ts (fixed 60vh renderDiv; no legend cap/wrap; no colorway extension) | — | fixed |
| D-229 | `category-axis-date-coerced` | structural | Plotly date-coerces category strings that look like date fragments: heatmap y ['00-06','06-12','12-18','18-24'] became years 1998-2003 and the real r… | frontend/src/plugins/d3/plotlyPreprocessor.ts (no category-axis-type coercion pass) | — | fixed |
| D-263 | `annotation-arrow-unthemed:dark` | theme | Secondary dark-only defect on w1-13 (its primary failure is the structural fixed-margin right-axis overlap, listed under fixed-margins-no-automargin)… | frontend/src/plugins/d3/plotlyPlugin.ts (applyPlotlyTheme does not touch layout.annotations arrowcolor/font o… | — | fixed |
| D-054 | `webgl-unavailable-3d-not-rendered` | structural | TOTAL DATA LOSS for the whole WebGL-only trace family. The capture browser has no WebGL, so surface (w1-04), 3D scatter (w1-05) and parcoords (w2-13)… | frontend/src/plugins/d3/plotlyPreprocessor.ts (demoteWebglTracesForCapture -- /gl$/ strip cannot rescue non-g… | — | wont-fix |
| D-224 | `colorscale-min-equals-paper-cell-invisi…` | recovery | 3-digit hex parses correctly (#fff/#f80/#03a all resolved), but two colour defects surface. LIGHT: the z=1 heatmap cell is scale-minimum #fff on pape… | frontend/src/plugins/d3/plotlyPlugin.ts (applyPlotlyTheme dark branch: '...base' spread lets author paper_bgc… | — | open |
| D-227 | `node-labels-collide-no-declutter` | structural | Sankey node labels do NOT shrink and are not dropped -- they collide at full size (15-20 overlaps counted at 20 nodes per column over ~500px). Distin… | frontend/src/plugins/d3/plotlyPreprocessor.ts (no sankey label-declutter pass) | — | wont-fix |

_Cross-engine families touching this engine:_ D-001 (`json-no-lenient-repair-30s-timeout`, open), D-002 (`label-color-hardcoded-not-theme-resolved`, open), D-007 (`downscale-to-fit-shrinks-text-below-leg…`, open), D-008 (`invalid-color-token-black-or-transparen…`, open).

### d3

Before **31%/33%** (L/D) → after **87%/87%**; parity 14→39 of 45. 14 single-engine defects (12 fixed, 2 wont-fix, 0 open; kinds: 10 structural, 2 recovery, 2 theme). Worst measured contrast (any cell): 1.00:1 before; worst *remaining* 2.62:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-039 | `no-fit-to-extent-nodes-clipped-offscreen` | structural | The single largest defect in the engine. forceDirectedPlugin.render builds an SVG at a FIXED viewBox (width=spec.width\|\|700, height=spec.height\|\|… | frontend/src/plugins/d3/forceDirectedPlugin.ts (render: forceCenter + 300 warm-up ticks, no post-settle fit t… | — | fixed |
| D-067 | `band-tick-labels-collide-no-pruning` | structural | basicChart.render calls d3.axisBottom(x) on the band scale with no tickValues thinning, so EVERY category label is drawn. Bars/markers all render (no… | frontend/src/plugins/d3/basicChart.ts (render: svg.append('g').call(d3.axisBottom(x)) with no tick thinning) | — | fixed |
| D-068 | `force-label-placement-no-collision-or-c…` | structural | forceDirectedPlugin places every node label unconditionally at x=(size\|\|8)+4, y=3 with no collision resolution, no truncation, no wrapping and no i… | frontend/src/plugins/d3/forceDirectedPlugin.ts (render: node.append('text').attr('x', d => (d.size\|\|8)+4).a… | — | fixed |
| D-085 | `d3-family-no-plugin-timeout` | structural | A bare {type:'d3', nodes, links} with no layout hint (the shape a user naively writes) matches NO plugin: forceDirectedPlugin.isForceDirectedSpec req… | frontend/src/plugins/d3/registry.ts (findPluginForSpec returns undefined -> no error) | — | fixed |
| D-086 | `long-tick-labels-no-rotate-or-truncate` | structural | Distinct from the count-driven collision: basicChart draws each category label horizontally, centred on its band, with no rotation, no truncation and… | frontend/src/plugins/d3/basicChart.ts (render: axisBottom with no label rotation/truncation) | — | fixed |
| D-087 | `wrong-dialect-field-names-no-alias` | recovery | SCHEMA recovery is weaker than SYNTAX recovery and fails SILENTLY IN THE IMAGE. Rows in the wrong dialect (name/y, a Highcharts/Vega habit) instead o… | frontend/src/plugins/d3/basicChart.ts (canHandle: spec.type only; render: d3.max(spec.data, d=>d.value) and x… | — | fixed |
| D-133 | `bubble-scatter-radius-range-fixed` | structural | basicChart routes type:'scatter' with x/y data through the bubble code path, whose radius is r = d3.scaleSqrt().domain([0,maxSize]).range([4,40]) - a… | frontend/src/plugins/d3/basicChart.ts (render: 'const r = d3.scaleSqrt().domain([0, maxSize]).range([4, 40])'… | — | fixed |
| D-134 | `bubble-label-clipped-top` | structural | Bubble labels are positioned at y(d.y) - r(d.size) - 4 with no top-margin reservation, so the largest bubble's label is always cut by the top edge of… | frontend/src/plugins/d3/basicChart.ts (render bubble branch: .attr('y', (d) => y(d.y) - r(d.size \|\| 1) - 4)) | — | fixed |
| D-185 | `arrowhead-scales-with-strokewidth-overl…` | structural | The shared #fd-arrow marker uses refX:20 (a fixed offset, not radius-aware) and the SVG default markerUnits='strokeWidth' (no markerUnits override in… | frontend/src/plugins/d3/forceDirectedPlugin.ts (render defs: marker '#fd-arrow' refX 20, markerWidth/Height 6… | — | fixed |
| D-186 | `marker-white-stroke-swamps-series:dark` | theme | basicChart gives every point marker a hardcoded stroke of '#fff' (.attr('stroke', '#fff') on both the bubble circles and the line/scatter points). In… | frontend/src/plugins/d3/basicChart.ts (render: point/bubble circles .attr('stroke', '#fff')) | — | fixed |
| D-187 | `label-over-node-fill-illegible:dark` | theme | Once fan-out density forces labels onto neighbouring node fills (see force-label-placement structural cluster), only the DARK label default fails on … | frontend/src/plugins/d3/forceDirectedPlugin.ts (render: labelColor dark default '#cccccc'; DEFAULT_GROUP_COLO… | — | fixed |
| D-188 | `nesting-depth-off-by-one-no-plugin-match` | recovery | forceDirectedPlugin.isForceDirectedSpec probes exactly two depths - spec.nodes and spec.data.nodes - and resolveForceDirectedSpec only lifts fields o… | frontend/src/plugins/d3/forceDirectedPlugin.ts (isForceDirectedSpec: spec.nodes \|\| spec.data?.nodes only; r… | — | fixed |
| D-132 | `explicit-width-height-ignored-aspect-re…` | structural | basicChart's sizingConfig.sizingStrategy is 'responsive' with containerStyles.height:'400px', so spec.width/spec.height are effectively discarded: a … | frontend/src/plugins/d3/basicChart.ts (sizingConfig: sizingStrategy 'responsive' + containerStyles.height '40… | — | wont-fix |
| D-184 | `extreme-aspect-shrinks-content-to-illeg…` | structural | Opposite of the basicChart case: forceDirectedPlugin.sizingConfig.sizingStrategy is 'fixed', so it DOES honour width/height and therefore honours an … | frontend/src/plugins/d3/forceDirectedPlugin.ts (render: svg width/height/viewBox taken verbatim from spec; no… | — | wont-fix |

_Cross-engine families touching this engine:_ D-001 (`json-no-lenient-repair-30s-timeout`, open), D-002 (`label-color-hardcoded-not-theme-resolved`, open), D-003 (`user-fill-color-no-contrast-guard`, open), D-005 (`low-opacity-marks-below-graphical-floor`, open), D-006 (`hardcoded-light-background-ignores-dark…`, open), D-009 (`default-palette-swatch-below-graphical-…`, open).

### basic-chart

Before **37%/29%** (L/D) → after **80%/80%**; parity 10→28 of 45. 8 single-engine defects (7 fixed, 1 wont-fix, 0 open; kinds: 5 structural, 2 recovery, 1 theme). Worst measured contrast (any cell): 1.00:1 before; worst *remaining* 2.01:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-041 | `band-tick-labels-overlap-no-rotation` | structural | The band x-axis is drawn with a bare svg.append('g').call(d3.axisBottom(x)) and no rotation, truncation, elision or staggering, so category labels ov… | frontend/src/plugins/d3/basicChart.ts (x-axis render: svg.append('g').call(d3.axisBottom(x)) with no label fi… | — | fixed |
| D-056 | `data-object-instead-of-array-throws-tim…` | recovery | The vega-lite habit {data:{values:[...]}} carried onto a basic-chart bar spec: the plugin CLAIMS the spec (canHandle sees type 'bar') and then fails … | frontend/src/plugins/d3/basicChart.ts (render: 'Array.isArray(spec.data) ? spec.data : []' vs 'd3.max(spec.da… | — | fixed |
| D-125 | `bubble-label-clipped-above-svg-top` | structural | Bubble labels are positioned at y = 'y(d.y) - r(d.size\|\|1) - 4' with no headroom reserved in margin.top (defaultMargin.top = 20). For the highest-y… | frontend/src/plugins/d3/basicChart.ts (bubble-label: .attr('y', d => y(d.y) - r(d.size\|\|1) - 4); defaultMar… | — | fixed |
| D-157 | `missing-type-field-no-plugin-claims-spe…` | recovery | An unambiguous label/value array with no 'type' key parses cleanly as JSON, then basicChartPlugin.canHandle returns false (it requires spec.type in {… | frontend/src/plugins/d3/basicChart.ts (canHandle requires spec.type in the four names) | — | fixed |
| D-158 | `scatter-xy-missing-size-max-radius` | structural | A continuous scatter (x/y present) with no 'size' field is routed into the bubble branch, where 'const maxSize = d3.max(data, d=>d.size) \|\| 1' is 1… | frontend/src/plugins/d3/basicChart.ts (bubble/scatter branch: maxSize = d3.max(data, d=>d.size) \|\| 1; r = s… | — | fixed |
| D-159 | `hardcoded-axis-color-does-not-adapt:dark` | theme | style.axisColor is honored with zero theme awareness - basicChart.ts '.selectAll('text').style('fill', style.axisColor \|\| null)' on both bubble-bra… | frontend/src/plugins/d3/basicChart.ts (both bubble-branch axes: .selectAll('text').style('fill', style.axisCo… | — | fixed |
| D-246 | `bubble-domain-padding-ignores-radius` | structural | x/y domain padding is 10% of the VALUE extent only ('xPad = (xExtent[1]-xExtent[0])*0.1 \|\| 1', same for y) and never accounts for the bubble RADIUS… | frontend/src/plugins/d3/basicChart.ts (xPad/yPad = 0.1 * extent, no radius term; scaleLinear domains) | — | fixed |
| D-042 | `explicit-width-height-silently-ignored` | structural | spec.width and spec.height are written onto the SVG in basicChart.ts render, but basicChartPlugin.sizingConfig declares sizingStrategy:'responsive' w… | frontend/src/plugins/d3/basicChart.ts (sizingConfig: sizingStrategy 'responsive', containerStyles.height '400… | — | wont-fix |

_Cross-engine families touching this engine:_ D-001 (`json-no-lenient-repair-30s-timeout`, open), D-002 (`label-color-hardcoded-not-theme-resolved`, open), D-003 (`user-fill-color-no-contrast-guard`, open), D-004 (`markdown-fence-not-stripped`, open), D-006 (`hardcoded-light-background-ignores-dark…`, open), D-009 (`default-palette-swatch-below-graphical-…`, open).

### chat-message

Before **43%/32%** (L/D) → after **77%/83%**; parity 18→46 of 60. 16 single-engine defects (10 fixed, 6 wont-fix, 0 open; kinds: 4 theme, 7 structural, 5 recovery). Worst measured contrast (any cell): 1.01:1 before; worst *remaining* 1.01:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-021 | `mermaid-node-label-below-wcag:dark` | theme | ALREADY RESOLVED by an earlier iteration (verified vs current source). The triage's offending rule `.dark .node rect:not([style*=fill:]) { fill:#5e81… | frontend/src/styles/mermaid-theme.css (.dark .mermaid-container .node rect:not([style*="fill:"]) { fill: #5e8… | — | fixed |
| D-023 | `oversize-message-capture-clipped` | structural | ALREADY RESOLVED by an earlier iteration (verified vs current source). app/utils/chat_screenshot.py build_capture_prep_js (Stage 5a) walks the ancest… | app/utils/chat_screenshot.py (element-screenshot of the live .message node; fixed viewport/clip bounds, no fu… | — | fixed |
| D-057 | `overflow-x-clipped-no-scroll-no-wrap` | structural | Additive CSS on .message .message-content (git diff, out-of-scope file): `pre{overflow-x:auto}` (long code line scrolls instead of hard-clip), `p,li{… | frontend/src/index.css (git diff in response) | — | fixed |
| D-058 | `init-json-palette-dropped` | recovery | Fixed by the shared mermaid preprocessor pipeline (repairInitDirectives/normalizeInitDirectiveJson, wired universally at priority 820 in initMermaidE… | frontend/src/plugins/d3/__tests__/chatMessageMermaidRecovery.test.ts (new test only) | frontend/src/plugins/d3/__tests__/chatMessageMermaidRecover… | fixed |
| D-081 | `style-rgba-color-form-parse-error` | recovery | Fixed by shared mermaid convertStyleColorFunctionsToHex (flowchart/graph, priority 780). w4-09 `style A fill:rgba(74,144,217,0.85),...` -> `fill:#4a9… | frontend/src/plugins/d3/__tests__/chatMessageMermaidRecovery.test.ts (new test only) | frontend/src/plugins/d3/__tests__/chatMessageMermaidRecover… | fixed |
| D-114 | `blockquote-no-visual-affordance` | structural | Additive CSS blockquote affordance on .message .message-content blockquote (git diff): left rule + muted text, per theme. LIGHT: border #6e7781 on #f… | frontend/src/index.css (git diff in response) | — | fixed |
| D-126 | `syntax-keyword-below-wcag:light` | theme | ALREADY RESOLVED (verified vs current source + computed). index.css `body:not(.dark) .token.keyword` is now #b31d28, NOT the triage's #d73a49. Comput… | frontend/src/index.css (body:not(.dark) .token.keyword { color: #d73a49 } at line 1234) | — | fixed |
| D-161 | `katex-error-red-below-wcag:dark` | theme | THEME fix (git diff): katex errorColor resolved from the active theme instead of the hardcoded #cc0000 (2.57:1 on dark #262626, a fail). No single re… | frontend/src/components/MarkdownRenderer.tsx (git diff in response) | — | fixed |
| D-163 | `transparent-fill-label-invisible:light` | recovery | Fixed by shared mermaid sanitizeInitTransparentPrimaryColor (universal, priority 815). w4-10 `primaryColor:transparent` dropped so mermaid's default … | frontend/src/plugins/d3/__tests__/chatMessageMermaidRecovery.test.ts (new test only) | frontend/src/plugins/d3/__tests__/chatMessageMermaidRecover… | fixed |
| D-244 | `table-gridline-below-3to1:light` | theme | Additive CSS markdown-table gridlines on .message .message-content th/td (git diff), theme-resolved. LIGHT: border #8a939c on white cell = 3.12:1; DA… | frontend/src/index.css (git diff in response) | — | fixed |
| D-115 | `inline-span-style-structure-broken` | structural | WONT-FIX (this stage). Fix is a rewrite of the inline-HTML->React path (utils/domSanitize.sanitizeModelHtml + MarkdownRenderer inline conversion) so … | frontend/src/components/MarkdownRenderer.tsx (sanitizeModelHtml / inline HTML-to-React conversion splits the … | — | wont-fix |
| D-118 | `markdown-dialect-and-raw-html-unsupport…` | structural | WONT-FIX (this stage). Requires markdown PARSER feature additions in MarkdownRenderer.tsx (marked extensions for footnotes + definition lists; extra … | frontend/src/components/MarkdownRenderer.tsx (marked options: no footnote/deflist extension, math preprocessi… | — | wont-fix |
| D-160 | `hard-line-break-collapsed` | structural | WONT-FIX (this stage). The assistant `breaks:false` lexer default (MarkdownRenderer ~5978) collapses single-newline hard breaks. Flipping it is a GLO… | frontend/src/components/MarkdownRenderer.tsx (breaks default false at ~line 5831 and the component prop defau… | — | wont-fix |
| D-162 | `table-separator-column-mismatch-pipe-so…` | recovery | WONT-FIX (this stage). Compound of D-160 (breaks default) + a lenient GFM near-miss table repair in marked. Depends on the deferred D-160 and adds pa… | frontend/src/components/MarkdownRenderer.tsx (GFM table validation is strict with no near-miss repair; and th… | — | wont-fix |
| D-247 | `fence-diagram-autoscaled-labels-illegib…` | structural | WONT-FIX (this stage). Fix would change `.mermaid-container svg {max-width:100% !important}` (mermaid-theme.css) to permit horizontal scroll / a mini… | frontend/src/styles/mermaid-theme.css (.mermaid-container svg { max-width: 100% !important }) | — | wont-fix |
| D-248 | `author-style-label-pair-below-wcag` | recovery | WONT-FIX (not an engine bug). Per triage: the recovery is perfect (3-digit hexes expand, per-node style color: honoured); the 'fail' is the faithfull… |  | — | wont-fix |

_Cross-engine families touching this engine:_ D-002 (`label-color-hardcoded-not-theme-resolved`, open).

### tikz

Before **48%/13%** (L/D) → after **73%/73%**; parity 8→44 of 60. 8 single-engine defects (2 fixed, 6 wont-fix, 0 open; kinds: 7 structural, 1 theme). Worst measured contrast (any cell): 1.00:1 before; worst *remaining* 1.00:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-102 | `pgfmathresult-clobbered-by-node-coordin…` | structural | tikz-w3-05 silently-wrong-number defect. The \edef/\let snapshot is blocked by the security prescan, so \pgfmathsetmacro capture is the only legal re… | app/utils/tikz_lint.py (_capture_pgfmath_results: rewrites \pgfmathparse{E}+later \pgfmathresult, with an int… | tests/test_latex_g06_tikz_lint.py (pre-existing) | fixed |
| D-103 | `pgfmath-dimen-overflow-at-high-index` | structural | tikz-w2-14 fatal overflow (cos(\n*111) crossing the 16383.99998pt dimen ceiling at \n=148). Applied only when the argument is macro-derived, so const… | app/utils/tikz_lint.py (_clamp_trig_arguments: wraps a trig argument containing a macro in mod(...,360); pgfm… | tests/test_latex_g06_tikz_lint.py (pre-existing) | fixed |
| D-101 | `non-latin-script-fatal` | structural | tikz-w3-01 (CJK/Cyrillic/Arabic) requires switching the tikz profile from pdflatex to a Unicode engine (lualatex/xelatex + fontspec) — a profile-wide… | app/services/latex_profiles.py (tikz profile at line 156: packages=(LatexPackage('tikz'),); no fontspec/unico… | — | wont-fix |
| D-150 | `degenerate-and-coincident-primitives-lo…` | structural | tikz-w3-03/w3-06 are pgf/TeX semantics (zero-length paths, r=0 circles, empty rectangles and 1-point plots emit no ink; duplicate node names redefine… | app/services/latex_renderer.py::render (no tikz lint to detect degenerate geometry or duplicate/coincident no… | — | wont-fix |
| D-230 | `macro-definition-rejected` | structural | \newcommand is denied by a load-bearing SECURITY control in latex_renderer._DENIED (the macro-definition family def\|gdef\|edef\|xdef\|let\|newcomman… | app/services/latex_renderer.py (the input safety/sanitizer layer that denylists \newcommand) | — | wont-fix |
| D-231 | `wide-aspect-collapses-tick-labels` | structural | tikz-w2-07: a ~22:1 wide canvas rasterises \tiny rotated tick labels to ~4-5px. Now MITIGATED by D-018 — explicit width/height are honored on the LaT… | app/services/latex_renderer.py::render (rasterisation fits width; no min-glyph-size guard) | — | wont-fix |
| D-232 | `pgfmath-float-stringify-overlaps-labels` | structural | tikz-w1-11: \pgfmathsetmacro stringifies an integer as a float (blue!20.0), widening five captions until they collide. No safe automatic rewrite exis… | app/services/latex_renderer.py::render (candidate lint: warn when \pgfmathsetmacro output feeds visible text/… | — | wont-fix |
| D-264 | `hardcoded-light-palette-below-floor-and…` | theme | tikz-w3-08: author paints its own #FAFAFA plate and hardcodes #CCCCCC pale-on-pale ink (1.19-1.54:1) that is illegible in the authors OWN light theme… | author spec only (no engine palette code path); the renderer cannot override an author-painted plate + author… | — | wont-fix |

_Cross-engine families touching this engine:_ D-010 (`latex-theme-ink-not-injected-black-on-d…`, fix-applied), D-012 (`latex-colour-form-not-normalised`, fix-applied), D-013 (`latex-no-mechanical-lint-repair-pass`, fix-applied), D-014 (`latex-ts1-textcomp-font-missing-fatal`, fix-applied), D-015 (`latex-authored-palette-below-contrast-f…`, wont-fix), D-017 (`latex-low-opacity-ink-dissolves`, wont-fix), D-018 (`latex-explicit-width-height-ignored`, fix-applied).

### packet

Before **49%/47%** (L/D) → after **71%/71%**; parity 21→32 of 45. 11 single-engine defects (4 fixed, 7 wont-fix, 0 open; kinds: 3 recovery, 2 theme, 6 structural). Worst measured contrast (any cell): 1.02:1 before; worst *remaining* 1.02:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-098 | `row-nesting-off-by-one-kills-render` | recovery | normalizeSectionRows recognises three row shapes (tuple array; object with fields/cells; row-of-tuples via row.every(Array.isArray)). A row one level… | frontend/src/utils/d3Plugins/packetPlugin.ts (git diff: new isScalarTuple + normalizeRow; normalizeSectionRow… | frontend/src/plugins/d3/__tests__/packetRowNestingAndCellsA… | fixed |
| D-099 | `section-cells-alias-dropped-to-placehol…` | recovery | Alias recovery is asymmetric: `cells` is honoured as a ROW key (normalizeSectionRows reads row.fields \|\| row.cells) but not as a SECTION key. norma… | frontend/src/utils/d3Plugins/packetPlugin.ts (git diff: normalizeSection resolves a section `cells` alias alo… | frontend/src/plugins/d3/__tests__/packetRowNestingAndCellsA… | fixed |
| D-100 | `transparent-fill-assumes-white-page:dark` | theme | hexColor.ts hardcodes transparent/none -> '#ffffff' (L113-115) on the written assumption that 'a transparent fill shows the white page behind it'. Tr… | frontend/src/plugins/d3/packetPlugin.ts (new effectiveCellBackdrop; wired at the field-label draw so getOptim… | frontend/src/plugins/d3/__tests__/packetTransparentBackdrop… | fixed |
| D-219 | `dsl-relative-width-syntax-unsupported` | recovery | The `packet` header matches parsePacketBetaDsl's sniff so the bridge runs, but its only field pattern is /^(-?\d+)\s*-\s*(-?\d+)\s*:\s*(.*)$/ (absolu… | frontend/src/plugins/d3/packetPlugin.ts (parsePacketBetaDsl: `+N:` relative-width accumulator checked before … | frontend/src/plugins/d3/__tests__/packetG32Recovery.test.ts | fixed |
| D-220 | `undersize-canvas-text-illegible` | structural | (w2-10 undersize-canvas-text-illegible) Honouring an explicit width/height as a scale target and font-scaling to the requested box lives in the out-o… | frontend/src/utils/d3Plugins/packetPlugin.ts (computeDimensions - explicit width/height not consumed as scale) | — | wont-fix |
| D-221 | `section-label-clipped-at-canvas-edge` | structural | (w1-15 section-label-clipped-at-canvas-edge) Budgeting a section label's width into the viewBox/gutters (LABEL_W/estimateSectionLabelWidth/computeBra… | frontend/src/utils/d3Plugins/packetPlugin.ts (LABEL_W L215; estimateSectionLabelWidth L437; computeBracketGut… | — | wont-fix |
| D-222 | `bracket-depth-cap-collapses-labels` | structural | (w2-07 bracket-depth-cap-collapses-labels) Extending PACKET_MAX_BRACKET_DEPTH lane/label attribution for >7 co-extensive brackets is a bracket-geomet… | frontend/src/utils/d3Plugins/packetPlugin.ts (assignBracketDepths / PACKET_MAX_BRACKET_DEPTH L317,L409-422; c… | — | wont-fix |
| D-223 | `multiline-label-overflows-section-height` | structural | (w2-11 multiline-label-overflows-section-height) Deriving section height from a multi-line \n label instead of row count is out-of-scope utils height… | frontend/src/utils/d3Plugins/packetPlugin.ts (computeDimensions height math; section-height derivation) | — | wont-fix |
| D-243 | `cell-border-below-graphical-floor` | theme | (w1-05/07/w2-12 cell-border-below-graphical-floor) Systemic sub-3:1 border-vs-fill across every built-in THEME (THEMES_LIGHT/DARK/AUTO_PALETTE/darken… | frontend/src/utils/d3Plugins/packetPlugin.ts (THEMES_LIGHT/THEMES_DARK border values; AUTO_PALETTE_* borders … | — | wont-fix |
| D-261 | `rotated-bracket-labels-overlap-illegible` | structural | (w2-08 rotated-bracket-labels-overlap-illegible) Budgeting width for rotated over-long bracket labels (bracketLabelLayout/computeBracketGutters rotat… | frontend/src/utils/d3Plugins/packetPlugin.ts (bracketLabelLayout L482-484; PACKET_MAX_HORIZ_BRACKET_LABEL_W L… | — | wont-fix |
| D-262 | `oversize-field-label-lost-offcanvas` | structural | (w2-14 oversize-field-label-lost-offcanvas) Clamping a field label centred at fx+fw/2 back onto the canvas for a multi-million-px field depends on th… | frontend/src/utils/d3Plugins/packetPlugin.ts (sanitizeFieldBits / PACKET_MAX_FIELD_BITS L156-174) | — | wont-fix |

_Cross-engine families touching this engine:_ D-001 (`json-no-lenient-repair-30s-timeout`, open), D-004 (`markdown-fence-not-stripped`, open), D-007 (`downscale-to-fit-shrinks-text-below-leg…`, open), D-009 (`default-palette-swatch-below-graphical-…`, open), D-011 (`capture-container-crops-tall-oversize-c…`, open).

### circuitikz

Before **37%/15%** (L/D) → after **70%/72%**; parity 6→32 of 46. 5 single-engine defects (1 fixed, 1 wont-fix, 3 open; kinds: 4 structural, 1 recovery). Worst measured contrast (any cell): 1.02:1 before; worst *remaining* 1.04:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-063 | `silent-structural-corruption-wrapper-an…` | recovery | (a) w4-11 tikzpicture-double-wrap was ALREADY fixed on disk: LatexProfile._wrap was generalised to pass a body through when it already opens any _DRA… | app/utils/circuitikz_lint.py (new _strip_numeric_quotes pass in _autofix; added import re) | tests/test_latex_g73_circuitikz_recovery.py (D-063 quote-st… | fixed |
| D-062 | `unknown-component-name-silent-or-fatal` | structural | WONT-FIX (no minimal, verifiable, targeted fix). w1-10 uses node shapes amp/adc/dac/dsp; confirmed (circuitikz manual + tex.stackexchange 222424/1792… | app/services/latex_profiles.py (circuitikz profile: no name validation; alias mechanism exists in extra_pream… | — | wont-fix |
| D-113 | `label-and-shape-overhang-not-reserved` | structural | circuitikz label and shape geometry is invisible to surrounding TikZ layout and to bbox computation, so neighbouring text overprints and extreme labe… | circuitikz engine behaviour (bipole label placement) | — | open |
| D-174 | `bipole-label-ignores-text-color` | structural | Isolated from emitted SVG, not eyeballed: text= in an enclosing scope does NOT reach circuitikz to[] bipole labels. The SVG emits every bipole label … | circuitikz engine (bipole label inherits path/stroke colour, not text=) | — | open |
| D-250 | `self-edge-collapses-to-zero-length-glyph` | structural | A self-referencing bipole (n) to[R=$R_2$] (n) is handed zero length; the resistor zigzag collapses into a spiky vertical artifact that overdraws and … | circuitikz engine (zero-length bipole) | — | open |

_Cross-engine families touching this engine:_ D-010 (`latex-theme-ink-not-injected-black-on-d…`, fix-applied), D-012 (`latex-colour-form-not-normalised`, fix-applied), D-013 (`latex-no-mechanical-lint-repair-pass`, fix-applied), D-014 (`latex-ts1-textcomp-font-missing-fatal`, fix-applied), D-015 (`latex-authored-palette-below-contrast-f…`, wont-fix), D-016 (`latex-security-prescan-rejects-legitima…`, fix-applied).

### chemfig

Before **31%/2%** (L/D) → after **67%/69%**; parity 0→30 of 45. 11 single-engine defects (5 fixed, 6 wont-fix, 0 open; kinds: 6 structural, 5 recovery). Worst measured contrast (any cell): 1.03:1 before; worst *remaining* 1.03:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-044 | `input-shape-stripper-missing` | recovery | The two FATAL members are already recovered on disk by LatexRenderer._sanitize_input, which runs before the security prescan: w4-01 (```chemfig fence… | app/services/latex_renderer.py (_sanitize_input: strips a markdown code fence, extracts \begin{document}..\en… | verified with python3 (LatexRenderer._sanitize_input + pres… | fixed |
| D-059 | `unicode-entity-transliteration-missing` | recovery | chemfig-w4-08 (TS1 superscript/thin-space) + chemfig-w4-14 (HTML entities). w4-08: kJ.mol/superscript-minus-one, degree, middot, thin space, U+2212 n… | app/utils/latex_unicode.py (_TRANSLITERATIONS: added superscripts U+2070/00B9/00B2/00B3/2074-2079/207A-207F, … | tests/test_latex_g74_chemfig_unicode_entities.py (14 tests,… | fixed |
| D-060 | `deprecated-setter-not-rewritten-to-setc…` | recovery | Already fixed on disk by pre-sweep work: chemfig_lint.rewrite_deprecated_setters rewrites the removed legacy setters to \setchemfig keys and is wired… | app/utils/chemfig_lint.py (rewrite_deprecated_setters: \setatomsep/\setbondoffset/\setdoublesep/... -> \setch… | tests/test_latex_g01_recovery_theme.py (pre-existing: rewri… | fixed |
| D-082 | `bare-angle-charge-autofix-does-not-fire…` | recovery | Already fixed on disk by pre-sweep work (triage hypothesis at chemfig_charge.py lines 255-263 'returns item unchanged' is STALE): chemfig_charge.auto… | app/utils/chemfig_charge.py (_repair_item bare-angle branch: \charge{90} -> \charge{90=}, mandatory '=' with … | tests/test_chemfig_charge.py (pre-existing: bare-angle 'emp… | fixed |
| D-127 | `quoted-numeric-and-ring-lint-fragile` | recovery | Quoted-numeric member (w4-09) already fixed on disk: chemfig_lint.unquote_numeric_fields unquotes *"6"( -> *6(, [:"30"] -> [:30], atom sep="2.4em" ->… | app/utils/chemfig_lint.py (unquote_numeric_fields: strips quotes around a purely numeric ring size *"6"(, ang… | tests/test_latex_g68_chemfig_unquote.py (pre-existing) | fixed |
| D-043 | `charge-glyph-clipped-at-bbox-edge` | structural | Already mitigated on disk: the chemfig profile crops with border=6pt (D-042, up from the 2pt default), scoped to chemfig, which keeps the common +/-4… | app/services/latex_profiles.py (build_document: '\documentclass[border=2pt]{standalone}') | — | wont-fix |
| D-164 | `chemmove-arrow-not-drawn` | structural | The mechanism the triage prescribes is ALREADY wired on disk: requires_position_marks matches \chemmove, so render() forces min_passes>=2 and forces … | app/services/latex_renderer.py (~line 321 requires_position_marks 2-pass branch) | — | wont-fix |
| D-165 | `schemestart-arrow-missing-labels-collide` | structural | \arrow{->[above][below]} inside \schemestart drawing no shaft/head is a chemfig/arrow-package version interaction that manifests only in the rendered… | app/services/latex_profiles.py (chemfig profile packages -- arrow/chemfig version interaction) | — | wont-fix |
| D-166 | `nested-ring-fails-to-close-open-chain` | structural | Depth-3 nested rings (*5 in *6 in *5) short a closing bond: the ring lint already DETECTS and warns, but refuses to auto-close because the bond ORDER… | app/utils/chemfig_lint.py (autofix + _RING_RE line 54; ring-padding heuristic does not recurse to nested ring… | — | wont-fix |
| D-167 | `parameterised-definesubmol-arg-not-subs…` | structural | Parameterised \definesubmol{arm}[1]{...#1...} dies in pgfmath ('Unknown operator #1') -- a chemfig install/version limitation in the parameterised-su… | chemfig install (parameterised submol mechanism) | — | wont-fix |
| D-249 | `identical-branch-angle-labels-overprint` | structural | Three branches given the identical [:90] angle overprint. chemfig places exactly what it is told and offers no collision avoidance -- this is author … |  | — | wont-fix |

_Cross-engine families touching this engine:_ D-006 (`hardcoded-light-background-ignores-dark…`, open), D-010 (`latex-theme-ink-not-injected-black-on-d…`, fix-applied), D-012 (`latex-colour-form-not-normalised`, fix-applied), D-014 (`latex-ts1-textcomp-font-missing-fatal`, fix-applied), D-015 (`latex-authored-palette-below-contrast-f…`, wont-fix), D-017 (`latex-low-opacity-ink-dissolves`, wont-fix).

### mermaid

Before **22%/27%** (L/D) → after **67%/68%**; parity 11→41 of 63. 16 single-engine defects (9 fixed, 7 wont-fix, 0 open; kinds: 5 theme, 5 recovery, 6 structural). Worst measured contrast (any cell): 1.00:1 before; worst *remaining* 1.02:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-073 | `pie-palette-slices-and-swatches-indisti…` | theme | Fails contrast in BOTH themes because the pie palette is wrong regardless of background: light recycles near-white ivory #ffffe0 (1.02:1 on white) an… | frontend/src/plugins/d3/mermaidPlugin.ts (PIE_PALETTE_LIGHT/DARK, buildPieThemeVariables wired at render L499) | frontend/src/plugins/d3/__tests__/mermaidPieG79.test.ts | fixed |
| D-074 | `commas-or-parens-inside-value-render-ha…` | recovery | ONE tokenizer bug with two faces. w4-04 'A[Parse request (fast path)]' (unquoted parens in a bracket label) and w4-06 'fill:rgba(255,99,71,0.85)' (fu… | frontend/src/plugins/d3/mermaidEnhancer.ts (quoteBracketLabelsWithParens; rgb()/rgba()/hsl() style->#hex norm… | frontend/src/plugins/d3/__tests__/mermaidG24Recovery.test.t… | fixed |
| D-092 | `flowchart-subgraph-crossedge-render-tim…` | structural | A flowchart whose subgraph has BOTH an inbound cross-edge and an outbound cross-edge plus a second path rejoining the exit node hangs the renderer 30… | frontend/src/plugins/d3/mermaidEnhancer.ts (subgraph-spacing pass no longer splits subgraph_entry node id) | frontend/src/plugins/d3/__tests__/mermaidSubgraphNodeIdSpli… | fixed |
| D-093 | `missing-dateFormat-yields-NaN-scale-all…` | recovery | The most dangerous single defect: a gantt with no dateFormat SUCCEEDS into a lie. An image returns, the title renders legibly (12.63:1), and the char… | frontend/src/plugins/d3/mermaidEnhancer.ts (inject default dateFormat when a gantt declares none) | frontend/src/plugins/d3/__tests__/mermaidG24Recovery.test.t… | fixed |
| D-141 | `mechanical-syntax-repair-absent-render-…` | recovery | Two deterministic, exactly-solvable repairs are absent and both hang 30s with zero SVG. w4-11: one missing subgraph 'end' - balancing unclosed subgra… | frontend/src/plugins/d3/mermaidEnhancer.ts (balanceSubgraphEnds, coercePieDataValues) | frontend/src/plugins/d3/__tests__/mermaidG59Recovery.test.t… | fixed |
| D-205 | `timeline-light-fill-light-text:dark` | theme | Passes light (black text on pale pastel bands, ~15-20:1), fails dark: the dark timeline palette keeps a LIGHT cyan fill on the 'Early' section while … | frontend/src/plugins/d3/mermaidEnhancer.ts (TIMELINE_DARK_SECTION_FILLS/LABEL, buildTimelineDarkThemeVariable… | frontend/src/plugins/d3/__tests__/mermaidG40.test.ts (D-160… | fixed |
| D-206 | `linkstyle-stroke-override-dropped:dark` | theme | Passes light (linkStyle stroke:#ff8800 and stroke:#aa0000 honoured), fails dark: both explicit edge linkStyle strokes are silently discarded and repa… | frontend/src/plugins/d3/mermaidEnhancer.ts (parseLinkStyleStrokes, reapplyLinkStyleStrokes); mermaidPlugin.ts… | frontend/src/plugins/d3/__tests__/mermaidG40.test.ts (D-161… | fixed |
| D-209 | `gantt-gridlines-drawn-over-bars` | structural | Vertical date gridlines are painted as heavy rules ON TOP of the task bars and section bands instead of behind them, slicing through the bars - a z-o… | frontend/src/plugins/d3/mermaidEnhancer.ts (moveGanttGridBehind, recolorGanttCritLabels); mermaidPlugin.ts (w… | frontend/src/plugins/d3/__tests__/mermaidG40.test.ts (D-170… | fixed |
| D-210 | `cross-dialect-arrow-in-flowchart-render…` | recovery | A single '-->>' (a sequenceDiagram arrow) inside a flowchart hangs the render 30s and takes five perfectly legal edges (-.->, ==>, --x, '-- label -->… | frontend/src/plugins/d3/mermaidEnhancer.ts (normalizeCrossDialectArrows: degrade -->> / ->> to plain edge) | frontend/src/plugins/d3/__tests__/mermaidG59Recovery.test.t… | fixed |
| D-207 | `deep-nesting-cluster-titles-lost-conten…` | structural | Deep-nesting cluster-title loss (w2-05) is a mermaid/dagre cluster-layout internal: titles are dropped and concentric containers collapse to stripes … | frontend/src/plugins/d3/mermaidEnhancer.ts (no local normaliser registered for nested cluster titles; also em… | — | wont-fix |
| D-208 | `sequence-alt-else-branch-label-missing` | structural | sequence alt/else branch-label omission (w1-03) originates inside mermaid sequenceRenderer emission; the else condition text is not drawn by the libr… | frontend/src/plugins/d3/mermaidSequenceSemicolons.ts (sequence preprocessing) | — | wont-fix |
| D-211 | `init-directive-recovery-triggers-oversi…` | recovery | Init-directive-honoured oversize canvas (w4-10) fails because honouring the recovered forest theme reaches the SAME oversize-canvas/fit-to-viewport g… | frontend/src/plugins/d3/mermaidEnhancer.ts (smart-quote init-directive sanitisation and the light-vs-dark the… | — | wont-fix |
| D-256 | `er-relationship-marker-filled-occludes-…` | theme | ER dark relationship-marker fill occlusion (w1-06) is mermaid-internal marker geometry/fill under the dark theme; the crows-foot/double-bar markers a… | frontend/src/plugins/d3/mermaidEnhancer.ts (er diagram dark theme marker styling) | — | wont-fix |
| D-257 | `mindmap-root-label-low-contrast:dark` | theme | Mindmap dark root-label low contrast (w1-11) comes from mermaids mindmap renderer painting the root label with a fixed ink not derived from the secti… | frontend/src/plugins/d3/mermaidEnhancer.ts (mindmap dark label colour - root node label not receiving the the… | — | wont-fix |
| D-258 | `class-multiplicity-label-collision` | structural | classDiagram composition multiplicity/role-label collision (w1-04) is a mermaid class-edge label-placement defect inside the library layout. No prepr… | frontend/src/plugins/d3/mermaidClassGenerics.ts (class relationship handling) | — | wont-fix |
| D-259 | `pie-legend-truncated-and-slice-labels-c…` | structural | Pie legend truncation + hub label collision at 60 slices (w2-15) is mermaid pie-renderer layout at scale (legend not scrolled/wrapped, labels converg… | frontend/src/plugins/d3/mermaidPlugin.ts (pie legend/label layout) | — | wont-fix |

_Cross-engine families touching this engine:_ D-003 (`user-fill-color-no-contrast-guard`, open), D-004 (`markdown-fence-not-stripped`, open), D-011 (`capture-container-crops-tall-oversize-c…`, open).

### drawio

Before **24%/18%** (L/D) → after **58%/67%**; parity 8→26 of 45. 12 single-engine defects (7 fixed, 5 wont-fix, 0 open; kinds: 7 structural, 2 theme, 3 recovery). Worst measured contrast (any cell): 1.00:1 before; worst *remaining* 1.00:1 after (engine files carry no post-fix ratios — see §6). ⚠️ Reconciliation gap: drawio-w1-15 (see §5).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-049 | `swimlane-fillopacity-crushes-label-cont…` | theme | The swimlane branch forces fillOpacity=20 on every swimlane. Harmless in light; in dark the fill blends toward the canvas (#dae8fc@20% over #1c1c1c ~… | frontend/src/plugins/d3/drawioPlugin.ts (~1618: styleObj['fillOpacity'] = 20 in the isSwimlane branch) | — | fixed |
| D-069 | `default-fill-vs-theme-fontcolor:dark` | theme | G-14. Confirmed against code: styled vertex with a style string but NO fillColor is painted with maxGraph builtin default vertex fill #C3D9FF in BOTH… | frontend/src/plugins/d3/drawioPlugin.ts | frontend/src/plugins/d3/__tests__/drawioG14.test.ts | fixed |
| D-070 | `quote-style-regex-blindspots (single-qu…` | recovery | Every geometry/de-quote regex in the preprocessor is DOUBLE-QUOTE-ONLY. Single-quoted attributes are XML-legal so the doc parses, then bypasses sanit… | frontend/src/plugins/d3/drawioPlugin.ts (de-quote regexes 536-546; the numG geometry helper; sanitizeDrawioCo… | — | fixed |
| D-088 | `bare-angle-brackets-in-value-unescaped` | recovery | The ampersand fixer (lines 580-590) escapes bare & inside attribute values but nothing escapes a bare < or >. A single '<' in a label ('R&D < Ops > N… | frontend/src/plugins/d3/drawioPlugin.ts (ampersand fixer 580-590 — extend to escape bare < and > inside attri… | — | fixed |
| D-120 | `shape-not-implemented-falls-back-to-rect` | structural | Only cloud, hexagon, rhombus and ellipse have real geometry; shape=cylinder3, note, process, parallelogram and step silently degrade to plain rectang… | frontend/src/plugins/d3/drawioPlugin.ts (shape-name -> renderer dispatch) | — | fixed |
| D-135 | `label-not-clipped-to-box (long-label-ov…` | structural | Labels are never constrained to their box: a 406-char label overflows +/-190px and an 84-char nowrap token overflows ~1140px, printing through neighb… | frontend/src/plugins/d3/drawioPlugin.ts (label sizing / whiteSpace / overflow handling; edge-label placement) | — | fixed |
| D-136 | `silent-semantic-corruption (space-separ…` | recovery | Two silent corruptions worse than the hangs because they produce a plausible image. A space instead of ';' between style keys makes the whole run one… | frontend/src/plugins/d3/drawioPlugin.ts (style-string tokenizer that splits on ';'; cell-type inference in th… | — | fixed |
| D-024 | `auto-layout-label-detached-from-vertex` | structural | When no vertex carries a non-zero geometry, hasExplicitLayout is false and the plugin runs its placement optimizer + custom orthogonal router. These … | frontend/src/plugins/d3/drawioPlugin.ts (hasExplicitLayout gate ~1925-1948; the auto-layout placement optimiz… | — | wont-fix |
| D-033 | `custom-endarrow-ignored / arrowheads-mi…` | structural | Two overlapping failures with one owner (edge marker rendering). Custom endArrow values (block/open/oval/diamond, startArrow=classic, ERone/ERmany cr… | frontend/src/plugins/d3/drawioPlugin.ts (edge style parsing + marker/terminator rendering) | — | wont-fix |
| D-119 | `edge-routing-ignored (self-loop-missing…` | structural | The custom router ignores edgeStyle=orthogonalEdgeStyle, curved=1 and explicit <Array as='points'> waypoints, drops a B->B self-loop outright, collap… | frontend/src/plugins/d3/orthogonalRouter.ts | — | wont-fix |
| D-121 | `coordinate-clamp-collateral (oversized-…` | structural | sanitizeDrawioCoordinates uses dimCap = max(median*12, 6000) and MIN_POS_WINDOW=3000. These robust clamps stop the 1e9 blank-canvas but are still far… | frontend/src/plugins/d3/drawioPlugin.ts (sanitizeDrawioCoordinates: dimCap, MIN_POS_WINDOW, median/MAD window) | — | wont-fix |
| D-189 | `aws4-icon-fetch-blocked-by-csp` | structural | iconRegistry fetches every shape=mxgraph.aws4.* resIcon from raw.githubusercontent.com; the render CSP (connect-src 'self' localhost) blocks all of t… | frontend/src/plugins/d3/iconRegistry.ts | — | wont-fix |

_Cross-engine families touching this engine:_ D-002 (`label-color-hardcoded-not-theme-resolved`, open), D-003 (`user-fill-color-no-contrast-guard`, open), D-004 (`markdown-fence-not-stripped`, open), D-008 (`invalid-color-token-black-or-transparen…`, open), D-011 (`capture-container-crops-tall-oversize-c…`, open).

### music

Before **18%/18%** (L/D) → after **49%/49%**; parity 8→22 of 45. 18 single-engine defects (4 fixed, 14 wont-fix, 0 open; kinds: 14 structural, 3 recovery, 1 theme). Worst measured contrast (any cell): 2.85:1 before; worst *remaining* 2.85:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-075 | `dialect-alias-fields-silently-dropped` | recovery | The engine has no field-alias layer, and this is the most dangerous failure mode found: unrecognised note fields do not error -- a note with no recog… | frontend/src/utils/d3Plugins/musicPlugin.ts (resolveMusicSpec + new helpers stripMusicFence/normalizeMusicSma… | frontend/src/utils/d3Plugins/__tests__/musicRecoveryNormali… | fixed |
| D-077 | `voice-overprint-no-collision-resolution` | structural | Simultaneous voices get no horizontal offsetting and no stem-direction/rest-pitch assignment on the top-level spec.voices path: two voices' simultane… | frontend/src/utils/d3Plugins/musicPlugin.ts (multiVoiceRestPitch + REST_PITCH_MULTIVOICE + buildNoteString re… | frontend/src/utils/d3Plugins/__tests__/musicRests.test.ts (… | fixed |
| D-094 | `nested-notes-array-degrades-to-rests` | recovery | hasNotes tests only Array.isArray(notes) && length>0, so notes:[[...],[...]] (nesting off by one) passes the content gate; each inner ARRAY is then t… | frontend/src/utils/d3Plugins/musicPlugin.ts (resolveMusicSpec + new helpers stripMusicFence/normalizeMusicSma… | frontend/src/utils/d3Plugins/__tests__/musicRecoveryNormali… | fixed |
| D-212 | `scalar-tempo-shorthand-dropped` | recovery | spec.tempo is read only as an object {name,bpm,duration}; a scalar tempo:120 or '120' is truthy, enters the tempo block, finds no .bpm, sanitizeTempo… | frontend/src/utils/d3Plugins/musicPlugin.ts (resolveMusicSpec + new helpers stripMusicFence/normalizeMusicSma… | frontend/src/utils/d3Plugins/__tests__/musicRecoveryNormali… | fixed |
| D-051 | `canvas-height-misallocated-content-clip…` | structural | canvas-height-misallocated-content-clipped: draw loop vs vertical allocation disagreement (staves/systems dropped). music engine core frontend/src/ut… | frontend/src/utils/d3Plugins/musicPlugin.ts (the per-system draw loop and its y-advance vs the height allocat… | — | wont-fix |
| D-076 | `flat-notes-array-never-wraps` | structural | flat-notes-array-never-wraps: needs chunking a top-level flat notes[] into synthetic measures before planSystemBreaks (no chunkFlat helper present). … | frontend/src/utils/d3Plugins/musicPlugin.ts (planSystemBreaks line 3853 -- only breaks between measures; the … | — | wont-fix |
| D-095 | `beam-geometry-degenerate` | structural | beam-geometry-degenerate: 12/8 stemless eighths + detached beams; applyHouseBeamSlope unchanged from HEAD. music engine core frontend/src/utils/d3Plu… | frontend/src/utils/d3Plugins/musicPlugin.ts (beam construction feeding applyHouseBeamSlope ~line 3945+; stem … | — | wont-fix |
| D-123 | `overlay-band-position-and-negotiation` | structural | overlay-band-position-and-negotiation: annotation position/stacking + above-stave band occupancy. music engine core frontend/src/utils/d3Plugins/musi… | frontend/src/utils/d3Plugins/musicPlugin.ts (the annotation overlay helper ~line 2456+, drawLyricLayer verse … | — | wont-fix |
| D-124 | `text-layer-reserves-no-horizontal-room` | structural | text-layer-reserves-no-horizontal-room: lyric/title/tempo width reservation + wrap/ellipsis (tempo-name overlap partially guarded by drawTempoName/me… | frontend/src/utils/d3Plugins/musicPlugin.ts (drawLyricLayer horizontal packing; the title-block / tempo / reh… | — | wont-fix |
| D-142 | `explicit-width-defeats-wrapping` | structural | explicit-width-defeats-wrapping: wrapEnabled = authorWidth==null disables system breaks; needs a legibility-driven wrap decision (unchanged from HEAD… | frontend/src/utils/d3Plugins/musicPlugin.ts (`wrapEnabled = authorWidth == null` and the LEGIBILITY_WIDTH_LIM… | — | wont-fix |
| D-143 | `author-dimension-below-natural-destroys…` | structural | author-dimension-below-natural-destroys-content: sanitizeLayoutDimension clamps without reflow/compression (no reflow helper present). music engine c… | frontend/src/utils/d3Plugins/musicPlugin.ts (sanitizeLayoutDimension and the width/height resolution at ~line… | — | wont-fix |
| D-144 | `wiggle-glyph-missing` | structural | wiggle-glyph-missing: trill/arpeggio/vibrato wavy-line SMuFL glyph not emitted (no wiggle helper present). music engine core frontend/src/utils/d3Plu… | frontend/src/utils/d3Plugins/musicPlugin.ts (the trill-line / vibrato / arpeggio overlay drawing helpers -- t… | — | wont-fix |
| D-145 | `measure-barline-modifiers-dropped` | structural | measure-barline-modifiers-dropped: per-measure beginBar/endBar spellings not applied (no beginBar/endBar handling present). music engine core fronten… | frontend/src/utils/d3Plugins/musicPlugin.ts (the per-measure barline / begin-bar / end-bar application in the… | — | wont-fix |
| D-213 | `cross-staff-span-degenerate` | structural | cross-staff-span-degenerate: crossStaffBeams wedge/slur geometry + name gutter (crossStaffBeam code unchanged from HEAD). music engine core frontend/… | frontend/src/utils/d3Plugins/musicPlugin.ts (crossStaffBeams handling ~line 5760; cross-staff slur geometry; … | — | wont-fix |
| D-214 | `tuplet-position-inverted` | structural | tuplet-position-inverted: default vs explicit position flipped + degenerate tuplet beam slope (the rewrite's sanitizeTupletCounts is a NUMERIC out-of… | frontend/src/utils/d3Plugins/musicPlugin.ts (tuplet construction / location flag mapping; beam slope for tupl… | — | wont-fix |
| D-215 | `grace-note-group-geometry-broken` | structural | grace-note-group-geometry-broken: oversized acciaccatura slash, collapsed grace chord, no reserved horizontal room (the rewrite's grace change is a s… | frontend/src/utils/d3Plugins/musicPlugin.ts (grace-note group construction and its horizontal spacing reserva… | — | wont-fix |
| D-216 | `stave-line-vanishes-at-scale:light` | theme | stave-line-vanishes-at-scale:light -- VexFlow's internal Stave/barline default ink #999999 is never remapped. Computed WCAG: #999999 on white = 2.85:… | frontend/src/utils/d3Plugins/musicPlugin.ts (DARK_COLOR_REMAP lines 2381-2387, which intentionally omits #999… | — | wont-fix |
| D-260 | `extreme-register-ledger-comb` | structural | extreme-register-ledger-comb: ledger-line stroke weight/spacing relief at extreme register (no ledger-relief helper present). music engine core front… | frontend/src/utils/d3Plugins/musicPlugin.ts (ledger-line generation / vertical spacing; DARK_LEDGER handling … | — | wont-fix |

_Cross-engine families touching this engine:_ D-001 (`json-no-lenient-repair-30s-timeout`, open).

### tikz-cd

Before **0%/0%** (L/D) → after **28%/48%**; parity 0→17 of 60. 1 single-engine defects (0 fixed, 1 wont-fix, 0 open; kinds: 1 structural). Worst measured contrast (any cell): 1.00:1 before; worst *remaining* 1.00:1 after (engine files carry no post-fix ratios — see §6).

| Defect | Sig | Kind | Root cause | File / function fixed | Test | Disposition |
|---|---|---|---|---|---|---|
| D-019 | `profile-package-not-installed` | structural | Environmental package-availability blocker, not a parser/code bug (as triage predicted). tikz-cd.sty/tikz-cd.code.tex are absent from the ACTIVE dist… | app/services/latex_renderer.py:render()@278 (calls missing_for_profile then _not_installed) | — | wont-fix |

_Cross-engine families touching this engine:_ D-010 (`latex-theme-ink-not-injected-black-on-d…`, fix-applied), D-012 (`latex-colour-form-not-normalised`, fix-applied), D-013 (`latex-no-mechanical-lint-repair-pass`, fix-applied), D-015 (`latex-authored-palette-below-contrast-f…`, wont-fix), D-016 (`latex-security-prescan-rejects-legitima…`, fix-applied), D-018 (`latex-explicit-width-height-ignored`, fix-applied).

## 5. Cross-engine defects, inventory gaps, and reconciliation

18 defects span more than one engine — the highest-leverage fixes, because one theme-blind helper or one missing recovery pre-pass accounts for failures across many engines. The LaTeX families (chemfig / circuitikz / tikz / tikz-cd share `latex_profiles.py`, `latex_renderer.py`, `latex_color.py`) were largely resolved; the frontend d3-family theme/recovery families (D-001–D-009, D-011) remain **`open` at the family (umbrella) level** even though their `progress_log` records most per-engine members as fixed. The per-theme reconciliation in §1 credits those member-level fixes; the umbrella status has simply not been closed.

| Family | Signature | Kind | Engines | Specs | Themes | Status | File(s) fixed |
|---|---|---|---|---|---|---|---|
| D-001 | `json-no-lenient-repair-30s-timeout` | recovery | 11 | 66 | dark/light | open | frontend/src/components/D3Renderer.tsx (initializeVisualization: line 389 parseD3Spec plu… |
| D-002 | `label-color-hardcoded-not-theme-resolved` | theme | 10 | 35 | dark/light | open | frontend/src/plugins/d3/basicChart.ts (bubble-label .attr('fill', style.labelColor \|\| '… |
| D-003 | `user-fill-color-no-contrast-guard` | theme | 7 | 21 | dark/light | open | frontend/src/plugins/d3/basicChart.ts (bar .attr('fill', d => d.color \|\| 'steelblue'); … |
| D-004 | `markdown-fence-not-stripped` | recovery | 7 | 11 | dark/light | open | frontend/src/utils/d3SpecParser.ts (parseD3Spec - no fence/prose stripping) |
| D-005 | `low-opacity-marks-below-graphical-floor` | theme | 6 | 23 | dark/light | open | frontend/src/plugins/d3/chordPlugin.ts:376 (ribbonOpacity default/clamp) |
| D-006 | `hardcoded-light-background-ignores-dark-the…` | theme | 6 | 16 | dark/light | open | frontend/src/plugins/d3/basicChart.ts (style.background rect: .attr('fill', style.backgro… |
| D-007 | `downscale-to-fit-shrinks-text-below-legibil…` | structural | 5 | 14 | dark/light | open | frontend/src/plugins/d3/d2Plugin.ts: hardcoded '12px' label font-size in node render bloc… |
| D-008 | `invalid-color-token-black-or-transparent-fa…` | recovery | 5 | 8 | dark/light | open | frontend/src/plugins/d3/chordPlugin.ts:400-401 (node.color \|\| palette -- 'transparent' … |
| D-009 | `default-palette-swatch-below-graphical-floor` | theme | 7 | 20 | dark/light | open | frontend/src/plugins/d3/basicChart.ts (bar .attr('fill', d => d.color \|\| 'steelblue') -… |
| D-010 | `latex-theme-ink-not-injected-black-on-dark` | theme | 4 | 58 | dark | fix-applied | app/services/latex_profiles.py (build_document: theme-aware \pagecolor + \color injected … |
| D-011 | `capture-container-crops-tall-oversize-conte…` | structural | 4 | 24 | dark/light | open | frontend/src/plugins/d3/drawioPlugin.ts (fit/center scaling logic) |
| D-012 | `latex-colour-form-not-normalised` | recovery | 4 | 19 | dark/light | fix-applied | app/utils/latex_color.py (normalize_colors: hex/rgb/rgba/CSS-name/transparent + NEW theme… |
| D-013 | `latex-no-mechanical-lint-repair-pass` | recovery | 3 | 17 | dark/light | fix-applied | app/utils/tikz_lint.py (_lint_tikz: literal-\n restore (w4-15), trig clamp, pgfmathparse … |
| D-014 | `latex-ts1-textcomp-font-missing-fatal` | structural | 3 | 5 | dark/light | fix-applied | app/utils/latex_unicode.py (transliterate: U+2192 -> \ensuremath{\rightarrow}, plus micro… |
| D-015 | `latex-authored-palette-below-contrast-floor` | theme | 4 | 25 | dark/light | wont-fix | app/services/latex_profiles.py (build_document -- no per-theme ink) |
| D-016 | `latex-security-prescan-rejects-legitimate-i…` | recovery | 2 | 4 | dark/light | fix-applied | app/services/latex_renderer.py (_sanitize_input + _DOCUMENT_BODY_RE/_PREAMBLE_LINE_RE, on… |
| D-017 | `latex-low-opacity-ink-dissolves` | theme | 2 | 4 | dark/light | wont-fix | app/services/latex_profiles.py (build_document -- theme-blind ink) |
| D-018 | `latex-explicit-width-height-ignored` | structural | 2 | 4 | dark/light | fix-applied | app/services/latex_renderer.py (render() takes width/height, folds them into the cache ke… |

**The single theme-blind helper story.** D-002 (`label-color-hardcoded-not-theme-resolved`, 10 engines, 35 specs) is the archetype: multiple plugins painted labels with a hardcoded `fill` (e.g. `'#666'`, `labelColor || '#666'`) and never received `isDarkMode`. Its `progress_log` records member fixes across graphviz, vega, plotly, drawio, chord, force-directed, network, basic-chart and d3 (each routed through a `resolve*Colors` helper that now derives the label ink from the theme), with chat-message’s `%%{init}%%` themeVariables cases explicitly **deferred** — which is exactly why the umbrella stays open. D-006 (`hardcoded-light-background-ignores-dark-theme`) and D-009 (`default-palette-swatch-below-graphical-floor`) follow the same shared-plumbing pattern.

### Inventory gaps (Stage 1)

Stage 1’s inventory task classified the 20 declared engines:

- **Declared in `SUPPORTED_DIAGRAM_TYPES` but no registered plugin:** `d3`. Such a type **hangs rather than fails** (no plugin resolves, the render path waits out its timeout). `d3` is flagged here; it nonetheless produced sweep results (it renders through the generic `D3Renderer` path), so it is graded above, but the registration gap should be closed so it fails fast instead of hanging.
- **Registered but undeclared:** `none`.

**Theme-blind engines (Stage 1 `theme_blind` list).** These engines render without consulting the active theme; a diagram correct in one theme can be unreadable in the other. Whether each was given theme resolution:

| Engine | Theme-blind (Stage 1) | Theme fix applied? | Via |
|---|---|---|---|
| d2 | yes | yes (fix-applied) | D-131, D-242 |
| chord | yes | yes (fix-applied) | D-002, D-005, D-009, D-045 |
| force-directed | yes | member-level only (family open) | D-002, D-003, D-005, D-006, D-009 |
| network | yes | yes (fix-applied) | D-002, D-003, D-005, D-009, D-245 |
| music | yes | no (wont-fix) | D-216 |
| basic-chart | yes | yes (fix-applied) | D-002, D-003, D-006, D-009, D-159 |
| tikz | yes | yes (fix-applied) | D-010, D-015, D-017, D-264 |
| circuitikz | yes | yes (fix-applied) | D-010, D-015 |
| chemfig | yes | yes (fix-applied) | D-006, D-010, D-015, D-017 |
| tikz-cd | yes | yes (fix-applied) | D-010, D-015 |

The four LaTeX engines (chemfig, circuitikz, tikz, tikz-cd) carry genuine `fix-applied` theme work (D-010 theme-ink injection). The pure-frontend theme-blind engines (d2, chord, network, basic-chart) each also received engine-specific `fix-applied` theme fixes (e.g. D-131, D-045/D-245, D-159), **but their shared theme-blindness** — hardcoded label colour (D-002), light-only background (D-006), sub-floor default swatches (D-009) — **is addressed only at the member level inside families that remain `open` at the umbrella level**. force-directed is the starkest: every theme family touching it (D-002/D-003/D-005/D-006/D-009) is still `open`, so its dark theme coverage rests entirely on `progress_log` member notes.

### Reconciliation gaps (the finding)

The invariant **failed-before = verified + still-broken + wont-fix** holds for every engine/theme cell **except two**, each an unrecorded verdict — precisely the light/dark asymmetry this sweep was meant to surface:

1. **drawio-w1-15 (light):** Fails in BOTH themes; only D-069 (fix-applied) is recorded and it is DARK-only. The light failure has no verified/wont-fix/open verdict -> a light verdict went unrecorded.
1. **force-directed-w4-06 (dark):** Dark-only failure (label-over-node-fill-illegible:dark); referenced by NO backlog defect at all -> completely untriaged, verdict unrecorded.

Both are left **unadjusted** in the tables above; they are reported as `uncovered` rather than silently reclassified.

**Regressions introduced then resolved.** There is **no first-class field** for this anywhere in the data. `regression_sets` are *preventive both-theme baselines* (the verification protocol requires each engine’s baseline to re-render unchanged after every fix), not a ledger of fix-induced regressions. It is therefore honestly reported as **0** per engine/theme, not inferred. Two related inconsistencies are worth flagging: (a) six specs sit in a regression baseline (nominally both-theme passes) yet also carry a filed defect — `chat-message-w2-02` (fix-applied), `network-w1-05` (open), `packet-w1-05` (wont-fix), `vega-w1-03` (fix-applied), `vega-w1-02` (fix-applied), `vega-w1-04` (fix-applied); and (b) basic-chart’s 10 not-run specs still count against its parity denominator (45), depressing its parity figure.

## 6. Limitations

- **The “before” column is not reproducible.** Pre-fix pixels live only in the Stage 1 sweep run’s artifacts. Once a fix lands, the old render cannot be regenerated. So the *before* column is **signature + failing detail + measured ratio as recorded at sweep time**, whereas the *after* column is a live re-render from spec on disk. The two columns are evidence of different kinds and should not be diffed pixel-for-pixel.
- **Contrast was judged visually wherever engine-internal colours were not readable from the spec.** Rows and cells marked _visual_ carry no invented number. Only ratios the backlog recorded as measured (`worst_measured_ratio`, `contrast_ratios`, `ratios`, `fix_contrast_ratios`) are presented as numbers, and `worst_contrast_after` per engine is the worst *remaining* measured ratio over cells not marked verified-fixed — a conservative floor, since the engine files hold no post-fix ratios.
- **Per-theme verdicts for the cross-engine frontend families rest on `progress_log` member notes, not a closed family status.** Families D-001–D-009 and D-011 remain `open`; their member-level fixes are credited in the per-theme reconciliation but the umbrella defects are not claimed closed. No fix is asserted as verified in a theme unless the backlog records it applied for that engine and theme.
- **`not-run` cells are excluded from “attempted”** (20 basic-chart cells) but the parity denominator uses total specs, so basic-chart parity is understated.

---

_Generated from `.ziya/gfx-sweep/` — 20 engine sweep files, `backlog.json` (265 defects), `inventory.json`, and `report-data.json`._
