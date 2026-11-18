"""app/utils/html_report.py: one self-contained HTML file from an authored
body fragment, with every diagram placeholder replaced by the rendered
diagram as an SVG ``data:`` image.

Why SVG: in Slack's HTML snippet preview (checked 2026-11-14) a renderer PNG
was too small to read and both SVG forms displayed.  The properties pinned
here are the ones a sandboxed preview depends on: no scripts, no external
resources, an image with a natural pixel size, and a failed figure that is
visible in the page and reported, never silently dropped.
"""
import asyncio
import base64
import re

import pytest

from app.utils import html_report as hr

SVG = ('<svg id="m1" width="100%" xmlns="http://www.w3.org/2000/svg" class="flowchart" '
       'style="max-width: 1422px;" viewBox="4 4 1422 200"><style>#m1{fill:red}</style>'
       '<rect width="10" height="10"/></svg>')

BODY = """<div class="summary"><b>Summary.</b> It broke.</div>
<h2 onclick="x()">Sequence</h2>
<figure data-render="mermaid">
<script type="text/x-diagram">
flowchart LR
    A[a <b> & c] --> B
</script>
<figcaption>What happens <i>after</i> a burn.</figcaption>
</figure>
<script>alert(1)</script>
<p>After.</p>"""


def _build(body=BODY, meta=None, render="default", **kw):
    calls = []

    async def ok(kind, spec):
        calls.append((kind, spec))
        return SVG.encode(), "image/svg+xml"

    fn = ok if render == "default" else render
    page, info = asyncio.run(hr.build_report(body, meta or {"title": "T"}, fn, **kw))
    return page, info, calls


def _img_svgs(page):
    return [base64.b64decode(b).decode() for b in
            re.findall(r'<img [^>]*src="data:image/svg\+xml;base64,([^"]+)"', page)]


def test_figure_is_rendered_from_its_source_and_embedded_as_svg_data_image():
    page, info, calls = _build()
    # The renderer received the spec verbatim: no HTML escaping needed in it.
    assert calls == [("mermaid", "flowchart LR\n    A[a <b> & c] --> B")]
    assert info["figures"] == 1 and info["failed"] == []
    svgs = _img_svgs(page)
    assert len(svgs) == 1 and "<rect" in svgs[0]
    # Caption kept (with its markup), and used as the image's alt text.
    assert "<figcaption>What happens <i>after</i> a burn.</figcaption>" in page
    assert 'alt="What happens after a burn."' in page
    # The source is gone from the page.
    assert "text/x-diagram" not in page and "flowchart LR" not in page


def test_embedded_svg_has_pixel_size_from_its_viewbox():
    """width="100%" has no intrinsic size as an image; the viewBox does."""
    page, _info, _ = _build()
    root = re.search(r"<svg\b[^>]*>", _img_svgs(page)[0]).group(0)
    assert 'width="1422"' in root and 'height="200"' in root and "100%" not in root
    assert 'viewBox="4 4 1422 200"' in root
    # No viewBox: left alone rather than guessed.
    plain = '<svg width="100%"><g/></svg>'
    assert hr.size_svg_to_viewbox(plain) == plain


def test_page_is_self_contained_no_scripts_no_handlers_external_urls_reported():
    body = BODY + '<img src="https://example.com/x.png"><img src="data:image/png;base64,AAAA"><a href="https://ok">l</a>'
    page, info, _ = _build(body)
    assert "<script" not in page.lower() and "alert(1)" not in page
    assert "onclick" not in page and "<h2>Sequence</h2>" in page
    assert info["stripped_scripts"] == 1
    # A link is fine; a resource the page would LOAD is reported.
    assert info["external"] == ["https://example.com/x.png"]
    assert page.count("<style>") == 1 and "<link" not in page
    assert info["bytes"] == len(page.encode())


def test_failed_figure_is_visible_and_reported_and_the_rest_survives():
    async def boom(kind, spec):
        if "bad" in spec:
            raise RuntimeError("Parse error on line 2 <x>")
        return SVG.encode(), "image/svg+xml"

    body = ('<figure data-render="mermaid"><script type="text/x-diagram">bad</script></figure>'
            '<figure data-render="graphviz"><script type="text/x-diagram">digraph{a->b}</script></figure>'
            '<figure data-render="mermaid"><figcaption>c</figcaption></figure><p>tail</p>')
    page, info, _ = _build(body, render=boom)
    assert info["figures"] == 1
    assert [f["type"] for f in info["failed"]] == ["mermaid", "mermaid"]
    assert "Parse error on line 2 &lt;x&gt;" in page          # shown, escaped
    assert "no diagram source" in info["failed"][1]["error"]
    assert page.count('class="figure-error"') == 2 and len(_img_svgs(page)) == 1
    assert "<p>tail</p>" in page


def test_a_diagram_that_renders_empty_is_a_failure_not_a_blank_figure():
    """Mermaid given a dangling edge returns a marker-only SVG with a 16px
    viewBox instead of raising; embedded, that is an invisible figure."""
    async def empty(kind, spec):
        return b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-8 -8 16 16"><g/></svg>', "image/svg+xml"

    page, info, _ = _build(render=empty)
    assert info["figures"] == 0 and "rendered empty" in info["failed"][0]["error"]
    assert "figure-error" in page and not _img_svgs(page)
    assert hr.svg_is_empty(SVG) is False               # a real diagram is not flagged


def test_svg_without_namespace_or_viewbox_is_made_standalone():
    """Root tags as the renderer returned them on 2026-11-14: drawio and
    flamegraph lack xmlns (an <img> draws nothing), and all three lack a
    viewBox (the drawing does not scale with the image)."""
    drawio = ('<svg style="left: 0px; top: 0px; width: 100%; height: 100%; display: block; '
              'min-width: 940px; min-height: 364px;"><g/></svg>')
    flame = '<svg width="600" class="partition" height="72" style="display: block;"><g/></svg>'
    plotly = ('<svg class="main-svg" xmlns="http://www.w3.org/2000/svg" '
              'xmlns:xlink="http://www.w3.org/1999/xlink" width="1248" height="576"><g/></svg>')
    d, f, p = (hr.make_svg_standalone(s) for s in (drawio, flame, plotly))
    assert 'xmlns="http://www.w3.org/2000/svg"' in d and 'viewBox="0 0 940 364"' in d
    assert 'xmlns="http://www.w3.org/2000/svg"' in f and 'viewBox="0 0 600 72"' in f
    assert p.count("xmlns=") == 1 and 'viewBox="0 0 1248 576"' in p
    assert hr.make_svg_standalone(SVG) == SVG          # already complete: untouched
    # HTML labels: namespaced once, and made valid XML.
    fo = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9"><foreignObject width="9" height="9">'
          '<div class="l">a<br>b&nbsp;c</div></foreignObject><foreignObject>'
          '<div xmlns="http://www.w3.org/1999/xhtml">k</div></foreignObject></svg>')
    out = hr.make_svg_standalone(fo)
    assert out.count('xmlns="http://www.w3.org/1999/xhtml"') == 2
    assert "a<br/>b&#160;c" in out
    import xml.dom.minidom
    xml.dom.minidom.parseString(out)                   # well-formed
    assert hr.make_svg_standalone(out) == out

    async def r(kind, spec):
        return drawio.encode(), "image/svg+xml"

    page, info, _ = _build(render=r)
    root = re.search(r"<svg\b[^>]*>", _img_svgs(page)[0]).group(0)
    # End to end: namespaced, and sized in pixels from the derived viewBox.
    assert "xmlns=" in root and 'width="940"' in root and 'height="364"' in root
    assert info["figures"] == 1


def test_no_renderer_marks_every_figure_failed_instead_of_dropping_it():
    page, info, _ = _build(render=None)
    assert info["figures"] == 0 and len(info["failed"]) == 1
    assert "figure-error" in page


def test_png_fallback_and_pre_source_form():
    async def png(kind, spec):
        return b"\x89PNG\r\n", "image/png"

    body = '<figure data-render="plotly"><pre><code>{"a": "x &lt; y"}</code></pre></figure>'
    seen = []

    async def spy(kind, spec):
        seen.append(spec)
        return await png(kind, spec)

    page, info, _ = _build(body, render=spy)
    assert seen == ['{"a": "x < y"}']                         # <pre> form is unescaped
    assert 'src="data:image/png;base64,' in page and info["figures"] == 1


def test_header_from_front_matter_is_escaped_and_plain_figures_untouched():
    meta = {"title": "A <b> title", "subtitle": "Why & how", "author": "Leo NOC", "date": "November 13, 2026"}
    body = '<figure><img src="data:image/png;base64,AAAA"><figcaption>mine</figcaption></figure>'
    page, info, calls = _build(body, meta, footer="made by x")
    assert "<title>A &lt;b&gt; title</title>" in page
    assert '<header class="report"><h1>A &lt;b&gt; title</h1>' in page
    assert '<div class="subtitle">Why &amp; how</div>' in page
    assert "<b>Leo NOC</b> &middot; November 13, 2026" in page
    assert '<footer class="report">made by x</footer>' in page
    assert calls == [] and info["figures"] == 0 and body in page
    # No title: no header block at all.
    assert "<header" not in _build("<p>x</p>", {"title": ""})[0]


def test_every_documented_class_has_a_rule_and_the_bar_example_uses_only_those():
    doc = hr.__doc__
    for cls in ("summary", "note", "warn", "sub", "num", "ok", "bad", "mid", "bars"):
        assert f"``{cls}``" in doc, cls
        assert re.search(r"\." + cls + r"\b", hr.REPORT_CSS), cls
    for cls in set(re.findall(r'class="([a-z]+)"', hr.BAR_TIMELINE_EXAMPLE)):
        assert re.search(r"\." + cls + r"\b", hr.REPORT_CSS), cls
    assert "@import" not in hr.REPORT_CSS and "url(" not in hr.REPORT_CSS


def test_route_and_skill_are_wired_to_the_builder():
    """The seam: a builder nobody calls and a route the skill never names
    are both dead.  Source-level, so it needs no browser."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    routes = (root / "app" / "routes" / "export_routes.py").read_text()
    assert '@router.post("/html-report")' in routes
    assert "build_report(" in routes and "load_report_source(" in routes
    from app.data.built_in_skills import get_skill_by_id
    p = get_skill_by_id("document_authoring")["prompt"]
    assert "## HTML reports" in p
    block = p[p.index("## HTML reports"):]
    assert "/api/export/html-report" in block and "$ZIYA_PORT" in block
    assert 'data-render="mermaid"' in block and "text/x-diagram" in block
    assert "figure-error" in block
    for cls in ("summary", "warn", "note", "sub", "num", "bars"):
        assert f"`{cls}`" in block, cls
    doc = (root / "Docs" / "DocumentAuthoring.md").read_text()
    assert "## HTML reports" in doc and "/api/export/html-report" in doc


def test_load_report_source_reads_front_matter_from_inline_text():
    meta, body = hr.load_report_source(
        text='---\ntitle: "Burn Outages"\nsubtitle: S\nauthor: NOC\ndate: 2026-11-13\n---\n<p>x</p>\n')
    assert meta["title"] == "Burn Outages" and meta["subtitle"] == "S" and meta["author"] == "NOC"
    assert meta["date"] == "November 13, 2026"
    assert body.strip() == "<p>x</p>"
    with pytest.raises(ValueError):
        hr.load_report_source()
