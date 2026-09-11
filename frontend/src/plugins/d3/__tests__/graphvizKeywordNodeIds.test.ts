/**
 * @jest-environment jsdom
 *
 * Regression: a bare node ID that collides with a DOT keyword (`EDGE`, `Node`,
 * `Graph`) is lexed as the keyword — keywords are case-insensitive — so
 * `EDGE [label=...]` becomes an attr_stmt with an illegal body and Viz.js
 * fails with `syntax error in line N near 'EDGE'`. The repair quotes the
 * colliding identifier wherever it is used as a node ID while leaving genuine
 * lowercase attr statements (`node [...]`, `edge [...]`, `graph [...]`) intact.
 */
import { quoteGraphvizKeywordNodeIds, repairGraphvizSource } from '../graphvizPlugin';

const SAMPLE = `digraph P {
  rankdir=LR; bgcolor=transparent;
  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10];
  edge [fontname="Helvetica", fontsize=8];
  KPOP [label="KPOP scheduler\\nGPS-ns SCHEDULE only", fillcolor="#e3f2fd"];
  EDGE [label="CT customer egress\\ntx_eth0_packets_acc 1s", fillcolor="#e3f2fd"];
  KPOP -> FPGA -> SAT -> CTMAC -> EDGE;
  KPOP -> EDGE [style=dashed, color="#d62728", label="1s / 4s floor:\\nNO per-packet delivery time"];
}`;

describe('quoteGraphvizKeywordNodeIds', () => {
    it('quotes an uppercase keyword-collision node id at every use site', () => {
        const out = quoteGraphvizKeywordNodeIds(SAMPLE);
        expect(out).toContain('"EDGE" [label="CT customer egress');
        expect(out).toContain('CTMAC -> "EDGE";');
        expect(out).toContain('KPOP -> "EDGE" [style=dashed');
        expect(out).not.toMatch(/(?<!")\bEDGE\b(?!")/);
    });

    it('leaves lowercase node/edge/graph attr statements untouched', () => {
        const out = quoteGraphvizKeywordNodeIds(SAMPLE);
        expect(out).toContain('node [shape=box,');
        expect(out).toContain('edge [fontname="Helvetica"');
        const withGraph = 'digraph G { graph [rankdir=TB]; a -> b; }';
        expect(quoteGraphvizKeywordNodeIds(withGraph)).toBe(withGraph);
    });

    it('does not rewrite text inside string or HTML-like labels', () => {
        const dot = 'digraph G { Node [label=<<b>EDGE</b>>]; x [label="EDGE"]; Node -> x; }';
        const out = quoteGraphvizKeywordNodeIds(dot);
        expect(out).toContain('label=<<b>EDGE</b>>');
        expect(out).toContain('label="EDGE"');
        expect(out).toContain('"Node" [label=');
        expect(out).toContain('"Node" -> x');
    });

    it('leaves a legal `a -> subgraph x {...}` edge alone', () => {
        const dot = 'digraph G { a -> subgraph x { b c } }';
        expect(quoteGraphvizKeywordNodeIds(dot)).toBe(dot);
    });

    it('is a byte-identical no-op on clean DOT and idempotent', () => {
        const clean = 'digraph G { node [shape=box]; edge [color=red]; a -> b; subgraph cluster_x { c } }';
        expect(quoteGraphvizKeywordNodeIds(clean)).toBe(clean);
        const once = quoteGraphvizKeywordNodeIds(SAMPLE);
        expect(quoteGraphvizKeywordNodeIds(once)).toBe(once);
    });

    it('is applied by the full repair pipeline', () => {
        const out = repairGraphvizSource(SAMPLE);
        expect(out).toContain('CTMAC -> "EDGE";');
        expect(out).toContain('"EDGE" [label=');
    });
});
