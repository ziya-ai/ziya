/**
 * G-29 / D-032 — joint JSON-repair, endpoint-alias and coercion recovery.
 *
 * D-032's triage hypothesis (see .ziya/gfx-sweep/backlog.json) was itself
 * hedged: "parseJointJsonish/stripJointFence/recursive descent exist in source
 * - confirm + add endpoint aliasing and boolean coercion". On confirming
 * against source this iteration, EVERY remediation the 10 joint-w4 recovery
 * specs need is already present and wired into jointPlugin.render:
 *
 *   w4-01 trailing commas .............. JSON5 (in parseJointJsonish)     [D-139]
 *   w4-02 unquoted keys ................ JSON5                            [D-139]
 *   w4-03 single quotes (Python repr) .. JSON5                           [D-139]
 *   w4-04 ```json fence ................ stripJointFence                 [D-140]
 *   w4-05 smart/curly quotes ........... normalizeJointSmartQuotes       [D-139]
 *   w4-06 string numbers + autoLayout .. sanitizeJointGeometry(toFinite) [D-16]
 *         "false" ...................... coerceJointBoolean(spec.autoLayout) [D-143]
 *   w4-07 {graph:{cells:[...]}} wrapper . findJointGraphContainer(top,3)  [D-141]
 *   w4-08 line and block comments ...... JSON5                            [D-139]
 *   w4-10 from/to -> source/target ..... normalizeJointLink alias        [D-142]
 *   w4-15 semicolon separators ......... repairJsonSeparators            [D-139]
 *
 * Because no new source change was required, this suite is a REGRESSION GUARD
 * (green by design on the current tree, as with D-019 in this run): it runs
 * each spec's verbatim definition through the SAME recovery path the plugin
 * uses (parseJointJsonish -> findJointGraphContainer -> normalizeJointCells,
 * plus coerceJointBoolean) and pins the intended element/connection cardinality
 * so a future edit to any of these shared helpers that re-breaks a recovery
 * shape is caught here. D-032 is a recovery (structural) defect, not a theme
 * defect, so it carries no per-theme colour assertion; the shared render stage
 * discharges the both-theme obligation by re-rendering the specs in light+dark.
 */
import {
    parseJointJsonish,
    findJointGraphContainer,
    coerceJointBoolean,
} from '../jointPlugin';
import { normalizeJointCells } from '../jointShapeResolver';

const SPECS: Record<string, { def: string; els: number; conns: number }> = {
    'w4-01': {
        def: `{
  "elements": [
    {"id": "ingest", "type": "rect", "label": "Ingest",},
    {"id": "parse", "type": "rect", "label": "Parse",},
    {"id": "store", "type": "rect", "label": "Store",},
  ],
  "connections": [
    {"id": "e1", "source": "ingest", "target": "parse", "label": "raw",},
    {"id": "e2", "source": "parse", "target": "store", "label": "clean",},
  ],
}`, els: 3, conns: 2,
    },
    'w4-02': {
        def: `{
  elements: [
    {id: "queue", type: "rect", label: "Queue"},
    {id: "worker", type: "rect", label: "Worker"},
    {id: "sink", type: "rect", label: "Sink"}
  ],
  connections: [
    {id: "c1", source: "queue", target: "worker", label: "pop"},
    {id: "c2", source: "worker", target: "sink", label: "emit"}
  ]
}`, els: 3, conns: 2,
    },
    'w4-03': {
        def: `{'elements': [{'id': 'auth', 'type': 'rect', 'label': 'Auth'},
              {'id': 'api', 'type': 'rect', 'label': 'API'},
              {'id': 'db', 'type': 'rect', 'label': 'DB'}],
 'connections': [{'id': 'l1', 'source': 'auth', 'target': 'api', 'label': 'token'},
                 {'id': 'l2', 'source': 'api', 'target': 'db', 'label': 'query'}]}`, els: 3, conns: 2,
    },
    'w4-04': {
        def: '```json\n' + `{
  "elements": [
    {"id": "edge", "type": "rect", "label": "Edge"},
    {"id": "cache", "type": "rect", "label": "Cache"},
    {"id": "origin", "type": "rect", "label": "Origin"}
  ],
  "connections": [
    {"id": "m1", "source": "edge", "target": "cache", "label": "hit?"},
    {"id": "m2", "source": "cache", "target": "origin", "label": "miss"}
  ]
}` + '\n```', els: 3, conns: 2,
    },
    'w4-05': {
        def: `{
  \u201Celements\u201D: [
    {\u201Cid\u201D: \u201Cclient\u201D, \u201Ctype\u201D: \u201Crect\u201D, \u201Clabel\u201D: \u201CClient\u201D},
    {\u201Cid\u201D: \u201Cproxy\u201D, \u201Ctype\u201D: \u201Crect\u201D, \u201Clabel\u201D: \u201CProxy\u201D},
    {\u201Cid\u201D: \u201Cserver\u201D, \u201Ctype\u201D: \u201Crect\u201D, \u201Clabel\u201D: \u201CServer\u201D}
  ],
  \u201Cconnections\u201D: [
    {\u201Cid\u201D: \u201Cs1\u201D, \u201Csource\u201D: \u201Cclient\u201D, \u201Ctarget\u201D: \u201Cproxy\u201D, \u201Clabel\u201D: \u201CGET\u201D},
    {\u201Cid\u201D: \u201Cs2\u201D, \u201Csource\u201D: \u201Cproxy\u201D, \u201Ctarget\u201D: \u201Cserver\u201D, \u201Clabel\u201D: \u201Cfwd\u201D}
  ]
}`, els: 3, conns: 2,
    },
    'w4-06': {
        def: `{
  "elements": [
    {"id": "a", "type": "rect", "label": "Alpha", "position": {"x": "80", "y": "60"}, "size": {"width": "140", "height": "70"}},
    {"id": "b", "type": "rect", "label": "Beta", "position": {"x": "300", "y": "60"}, "size": {"width": "140", "height": "70"}},
    {"id": "c", "type": "rect", "label": "Gamma", "position": {"x": "520", "y": "60"}, "size": {"width": "140", "height": "70"}}
  ],
  "connections": [
    {"id": "n1", "source": "a", "target": "b", "label": "1"},
    {"id": "n2", "source": "b", "target": "c", "label": "2"}
  ],
  "autoLayout": "false"
}`, els: 3, conns: 2,
    },
    'w4-07': {
        def: `{
  "graph": {
    "cells": [
      {"id": "n1", "type": "standard.Rectangle", "attrs": {"label": {"text": "Load"}}},
      {"id": "n2", "type": "standard.Rectangle", "attrs": {"label": {"text": "Transform"}}},
      {"id": "n3", "type": "standard.Rectangle", "attrs": {"label": {"text": "Publish"}}},
      {"id": "k1", "type": "standard.Link", "source": {"id": "n1"}, "target": {"id": "n2"}},
      {"id": "k2", "type": "standard.Link", "source": {"id": "n2"}, "target": {"id": "n3"}}
    ]
  }
}`, els: 3, conns: 2,
    },
    'w4-08': {
        def: `{
  // pipeline stages
  "elements": [
    {"id": "read", "type": "rect", "label": "Read"},   // source
    {"id": "map", "type": "rect", "label": "Map"},
    /* terminal stage */
    {"id": "write", "type": "rect", "label": "Write"}
  ],
  "connections": [
    {"id": "j1", "source": "read", "target": "map", "label": "rows"},
    {"id": "j2", "source": "map", "target": "write", "label": "rows"}
  ]
}`, els: 3, conns: 2,
    },
    'w4-10': {
        def: `{
  "elements": [
    {"id": "spider"},
    {"id": "indexer"},
    {"id": "ranker"},
    {"id": "serp"}
  ],
  "connections": [
    {"from": "spider", "to": "indexer", "label": "html"},
    {"from": "indexer", "to": "ranker", "label": "postings"},
    {"from": "ranker", "to": "serp", "label": "top-10"}
  ]
}`, els: 4, conns: 3,
    },
    'w4-15': {
        def: `{
  "elements": [
    {"id": "plan"; "type": "rect"; "label": "Plan"},
    {"id": "apply"; "type": "rect"; "label": "Apply"},
    {"id": "verify"; "type": "rect"; "label": "Verify"}
  ];
  "connections": [
    {"id": "y1"; "source": "plan"; "target": "apply"; "label": "diff"},
    {"id": "y2"; "source": "apply"; "target": "verify"; "label": "state"}
  ]
};`, els: 3, conns: 2,
    },
};

// Mirror jointPlugin.render's structured-recovery path (jointPlugin.ts ~L2246-2270).
function recover(def: string): { elements: any[]; connections: any[] } | null {
    const parsed = parseJointJsonish(def);
    if (parsed === undefined || parsed === null) return null;
    const top = Array.isArray(parsed) ? { elements: parsed } : parsed;
    const obj = findJointGraphContainer(top, 3) || top;
    if (!obj || !(obj.elements || obj.cells)) return null;
    return normalizeJointCells(obj.elements || obj.cells, obj.connections || obj.links);
}

describe('D-032/G-29: joint recovery pipeline (regression guard)', () => {
    for (const [id, { def, els, conns }] of Object.entries(SPECS)) {
        it(`joint-${id} recovers ${els} elements + ${conns} connections`, () => {
            const r = recover(def);
            expect(r).not.toBeNull();
            expect(r!.elements.length).toBe(els);
            expect(r!.connections.length).toBe(conns);
        });
    }

    it('every recovered edge resolves canonical source/target endpoints', () => {
        // w4-10 uses from/to; all others use source/target. After recovery every
        // connection must carry both canonical endpoints, else the link creator
        // dereferences an undefined endpoint and silently drops the edge.
        for (const { def } of Object.values(SPECS)) {
            const r = recover(def);
            expect(r).not.toBeNull();
            for (const c of r!.connections) {
                expect(c.source != null).toBe(true);
                expect(c.target != null).toBe(true);
            }
        }
    });

    it('w4-06 autoLayout string "false" coerces to boolean false (manual layout kept)', () => {
        // Truthy-string trap: without coercion `"false" !== false` is true and
        // auto-layout runs, discarding the authored x=80/300/520 manual row.
        expect(coerceJointBoolean('false')).toBe(false);
        expect(coerceJointBoolean('0')).toBe(false);
        expect(coerceJointBoolean('true')).toBe(true);
        expect(coerceJointBoolean(false)).toBe(false);
        expect(coerceJointBoolean(undefined)).toBeUndefined();
    });
});
