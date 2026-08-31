# Document Authoring & Export

Ziya can author **work-product documents** — reports, memos, specs — as a
first-class artifact distinct from conversation transcripts, and export them
as high-fidelity PDFs rendered through the real chat renderer (KaTeX math,
mermaid/graphviz/vega-lite/packet diagrams, chemfig/tikz LaTeX, Prism
syntax highlighting).

## The document IR

A document is a plain markdown file with YAML front-matter, stored in
`<project>/.ziya/documents/`. The file is the single editing surface: the
model (or you) revises it with ordinary edits, and rendering happens only at
export time. The file stays portable — it renders sensibly on GitHub or any
markdown viewer.

```markdown
---
ziya-doc: 1
title: "Queue Depth Analysis — Q3"
author: "yourname"        # optional; becomes the PDF /Author
layout: report            # report = title block; plain = no chrome
page:
  margin: 18mm            # one value, or {top, bottom, left, right}
---

# Executive Summary

Ordinary Ziya markdown: math like $\rho = 0.85$, tables, code, diffs,
and diagram fences all render at full fidelity.

<!-- ziya:pagebreak -->

# Model

Starts on a new page. The pagebreak directive is an HTML comment —
invisible in every other markdown viewer.
```

Front-matter keys are all optional; invalid values fall back to defaults
rather than failing the export. Keep semantic sources in the IR (LaTeX,
diagram DSLs) — never pre-rendered images — so future export targets can
choose native mappings per construct.

## Exporting

`POST /api/export/document` returns PDF bytes:

```bash
# By stored name (relative to .ziya/documents/):
curl -X POST localhost:6969/api/export/document \
  -H 'Content-Type: application/json' \
  -d '{"name": "q3-report.md"}' -o report.pdf

# Or inline:
curl -X POST localhost:6969/api/export/document \
  -H 'Content-Type: application/json' \
  -d '{"markdown": "---\ntitle: X\n---\n# Hello"}' -o out.pdf
```

Optional body fields: `title` (overrides front-matter), `includeFooter`
(default false — work products don't carry the transcript footer).

The PDF gets:
- A **nested outline** (bookmarks) generated from the h1–h4 heading tree.
- Document metadata: front-matter `title` as /Title, front-matter `author`
  (or the model/provider) as /Author.
- Real page breaks at each `<!-- ziya:pagebreak -->`.

Requires Playwright (`pip install playwright && playwright install
chromium`); the endpoint returns HTTP 501 when it is absent.

## The `document_authoring` skill

A built-in model-discoverable skill teaches the model the IR contract and
the workflow: author into `.ziya/documents/`, revise with targeted edits
(never full regeneration), extract-and-restructure when promoting a
conversation into a document, and render via the export endpoint.

## Relationship to conversation export

Conversation export (Export Conversation modal: markdown / HTML / PDF /
paste targets) is transcript-shaped and unchanged. Document export shares
the same headless render session and `/print` route in document mode — no
message chrome, no role labels — so the two paths cannot drift in rendering
fidelity.
