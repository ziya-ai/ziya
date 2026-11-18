"""Self-contained HTML reports: one small file that displays inline where
HTML snippets are previewed (chat tools, mail, a browser).

The author writes an HTML *body fragment* using a fixed, small class
vocabulary; :func:`build_report` wraps it in the fixed stylesheet below and
replaces every diagram placeholder with the rendered diagram, embedded as an
SVG ``data:`` image.  The result has no scripts and no external resources.

Source format (a file under ``<project>/.ziya/documents/``, or inline)::

    ---
    title: "Post-burn CBFD Outages"
    subtitle: "Why links stay down for hours after an orbit raise"
    author: "Leo NOC"
    date: 2026-11-13
    ---
    <div class="summary"><b>Summary.</b> ...</div>
    <h2>Sequence</h2>
    <figure data-render="mermaid">
    <script type="text/x-diagram">
    flowchart LR
        A --> B
    </script>
    <figcaption>What happens after a burn.</figcaption>
    </figure>

Why an SVG ``data:`` image rather than inline ``<svg>`` or PNG: tested in
Slack's HTML snippet preview on 2026-11-14, a PNG from the diagram renderer
came out too small to read, while both SVG forms displayed.  As an image the
SVG's own ``<style>`` and ids cannot collide with the page or with another
diagram.

Class vocabulary (everything else is plain h2/h3/p/ul/ol/table/code/pre):

``summary``            tinted box for the one-paragraph answer
``note`` / ``warn``    call-out boxes (neutral / caution)
``sub``                small grey line: provenance, caveats under a table
``num``                right-aligned numeric table cell (``td``/``th``)
``ok`` ``bad`` ``mid`` status text colours for a table cell or span
``bars``               a labelled bar timeline (see BAR_TIMELINE_EXAMPLE)
"""
from __future__ import annotations

import base64
import html as _html
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

# (diagram type, definition) -> (image bytes, media type)
RenderFn = Callable[[str, str], Awaitable[Tuple[bytes, str]]]

REPORT_CSS = """
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
max-width:960px;margin:24px auto;padding:0 18px;color:#1f2328;line-height:1.5;font-size:14px;background:#fff}
header.report{margin:0 0 22px;padding-bottom:12px;border-bottom:1px solid #d0d7de}
header.report::before{content:"";display:block;width:44px;height:4px;margin-bottom:12px;background:#0f8f83}
header.report h1{font-family:"Iowan Old Style",Charter,"Palatino Linotype",Palatino,Georgia,serif;
font-size:26px;line-height:1.15;font-weight:600;letter-spacing:-.01em;margin:0 0 4px}
header.report .subtitle{font-size:15px;color:#57606a;margin:0 0 10px}
header.report .byline{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:#57606a}
header.report .byline b{color:#1f2328;font-weight:650}
h2{font-size:17px;line-height:1.3;margin:28px 0 8px;padding-bottom:4px;border-bottom:1px solid #e1e4e8}
h3{font-size:14.5px;margin:20px 0 6px}
p{margin:0 0 10px}ul,ol{margin:0 0 10px;padding-left:1.5em}li{margin:2px 0}
a{color:#0969da}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.9em;background:#f3f4f6;
padding:0 4px;border-radius:3px}
pre{background:#f6f8fa;border:1px solid #e1e4e8;border-radius:4px;padding:10px 12px;font-size:12.5px;
line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere}
pre code{background:none;padding:0;font-size:inherit}
table{border-collapse:collapse;width:100%;font-size:13px;margin:10px 0 14px}
th{text-align:left;font-weight:650;border-bottom:1.5px solid #1f2328;padding:5px 9px}
td{border-bottom:1px solid #e1e4e8;padding:5px 9px;vertical-align:top}
tr:nth-child(even) td{background:#f8f9fb}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.summary,.note,.warn{padding:10px 14px;margin:14px 0;border-left:4px solid #0f8f83;background:#eef7f6;border-radius:0 4px 4px 0}
.note{border-left-color:#8c959f;background:#f6f8fa}
.warn{border-left-color:#d4a017;background:#fff8e1}
.summary>:last-child,.note>:last-child,.warn>:last-child{margin-bottom:0}
.sub{color:#57606a;font-size:12.5px}
.ok{color:#1a7f37}.bad{color:#cf222e}.mid{color:#9a6700}
figure{margin:14px 0 18px;text-align:center}
figure img{max-width:100%;height:auto}
figcaption{font-size:12.5px;color:#57606a;margin-top:6px;text-align:center}
.figure-error{border:1px dashed #cf222e;color:#cf222e;padding:10px;font-size:12.5px;text-align:left}
.bars{margin:10px 0 14px}
.bars .axis{position:relative;margin-left:130px;height:16px;font-size:11px;color:#57606a}
.bars .axis span{position:absolute;transform:translateX(-50%)}
.bars .row{display:flex;align-items:center;height:18px;margin:2px 0}
.bars .lab{width:130px;font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}
.bars .track{position:relative;flex:1;height:14px;background:#f6f8fa;border:1px solid #e1e4e8}
.bars .track i{position:absolute;top:0;height:14px;background:#cf222e;opacity:.5}
.bars .track b{position:absolute;top:3px;height:8px;background:#0f8f83}
.bars .legend{font-size:12px;color:#57606a;margin-bottom:4px}
.bars .legend i,.bars .legend b{display:inline-block;width:14px;height:10px;margin:0 4px 0 12px;vertical-align:middle}
.bars .legend i{background:#cf222e;opacity:.5}.bars .legend b{background:#0f8f83}
footer.report{margin-top:30px;padding-top:8px;border-top:1px solid #e1e4e8;font-size:11.5px;color:#8c959f}
""".strip()

BAR_TIMELINE_EXAMPLE = """<div class="bars">
<div class="legend"><b></b>burn<i></i>link down</div>
<div class="axis"><span style="left:0%">09Z</span><span style="left:50%">15Z</span><span style="left:100%">21Z</span></div>
<div class="row"><div class="lab">sat-00505</div><div class="track"><i style="left:7%;width:53%"></i><b style="left:9%;width:11%"></b></div></div>
</div>"""

_FIGURE_RE = re.compile(
    r"<figure\b(?P<attrs>[^>]*\bdata-render\s*=\s*[\"'](?P<type>[A-Za-z0-9_-]+)[\"'][^>]*)>"
    r"(?P<inner>.*?)</figure>", re.DOTALL | re.IGNORECASE)
_SPEC_SCRIPT_RE = re.compile(
    r"<script\b[^>]*\btype\s*=\s*[\"']text/x-diagram[\"'][^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE)
_SPEC_PRE_RE = re.compile(r"<pre\b[^>]*>(.*?)</pre>", re.DOTALL | re.IGNORECASE)
_CAPTION_RE = re.compile(r"<figcaption\b[^>]*>.*?</figcaption>", re.DOTALL | re.IGNORECASE)
_SCRIPT_RE = re.compile(r"<script\b.*?</script\s*>|<script\b[^>]*/?>", re.DOTALL | re.IGNORECASE)
_HANDLER_RE = re.compile(r"\s+on[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_EXTERNAL_RE = re.compile(
    r"<(?:img|link|iframe|video|audio|source|object|embed)\b[^>]*\b(?:src|href|data)\s*=\s*[\"']?"
    r"(?!data:|#)([^\"'\s>]+)", re.IGNORECASE)
_SVG_ROOT_RE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)
_VIEWBOX_RE = re.compile(r"\bviewBox\s*=\s*[\"']\s*([-\d.eE]+)[\s,]+([-\d.eE]+)[\s,]+([-\d.eE]+)[\s,]+([-\d.eE]+)")


def size_svg_to_viewbox(svg: str) -> str:
    """Give the root ``<svg>`` pixel ``width``/``height`` from its viewBox.

    Renderers emit ``width="100%"``; loaded as an image that has no
    intrinsic size, so the browser picks one.  With explicit pixels the
    image has a natural size and ``max-width:100%`` only ever shrinks it.
    Returned unchanged when there is no usable viewBox.
    """
    m = _SVG_ROOT_RE.search(svg)
    if not m:
        return svg
    root = m.group(0)
    vb = _VIEWBOX_RE.search(root)
    if not vb:
        return svg
    try:
        w, h = float(vb.group(3)), float(vb.group(4))
    except ValueError:
        return svg
    if not (w > 0 and h > 0):
        return svg
    new = re.sub(r"\s(?:width|height)\s*=\s*(\"[^\"]*\"|'[^']*')", "", root)
    new = new[:4] + f' width="{w:g}" height="{h:g}"' + new[4:]
    return svg[:m.start()] + new + svg[m.end():]


_PX_ATTR_RE = r"\b{name}\s*=\s*[\"']\s*([\d.]+)(?:px)?\s*[\"']"
_PX_STYLE_RE = r"(?:^|[;\s\"'])(?:min-)?{name}\s*:\s*([\d.]+)px"


def make_svg_standalone(svg: str) -> str:
    """Make a renderer's root ``<svg>`` usable as a standalone image.

    Observed 2026-11-14: drawio and flamegraph output has no ``xmlns`` (an
    image without it does not draw at all), and plotly, drawio and
    flamegraph have no ``viewBox`` (the drawing cannot scale with the
    image).  The namespace is added when missing; a missing viewBox is
    taken from pixel ``width``/``height`` attributes, else from pixel
    ``width``/``min-width`` and ``height``/``min-height`` in the style.
    """
    m = _SVG_ROOT_RE.search(svg)
    if not m:
        return svg
    root = m.group(0)
    add = ""
    if not re.search(r"\bxmlns\s*=", root):
        add += ' xmlns="http://www.w3.org/2000/svg"'
    if "xlink:" in svg and not re.search(r"\bxmlns:xlink\s*=", root):
        add += ' xmlns:xlink="http://www.w3.org/1999/xlink"'
    if not _VIEWBOX_RE.search(root):
        dims = []
        for name in ("width", "height"):
            hit = (re.search(_PX_ATTR_RE.format(name=name), root)
                   or re.search(_PX_STYLE_RE.format(name=name), root))
            dims.append(float(hit.group(1)) if hit else 0.0)
        if dims[0] > 0 and dims[1] > 0:
            add += f' viewBox="0 0 {dims[0]:g} {dims[1]:g}"'
    if add:
        svg = svg[:m.start()] + root[:4] + add + root[4:] + svg[m.end():]
    if "<foreignObject" in svg:
        # HTML labels inside the drawing need the XHTML namespace to draw
        # when the SVG is parsed as XML (drawio, flamegraph omit it), and
        # the HTML serialiser's void <br> and &nbsp; are not valid XML.
        svg = _FO_CHILD_RE.sub(r'\1 xmlns="http://www.w3.org/1999/xhtml"', svg)
        svg = re.sub(r"<br\s*>", "<br/>", svg).replace("&nbsp;", "&#160;")
    return svg


_FO_CHILD_RE = re.compile(r"(<foreignObject\b[^>]*>\s*<[A-Za-z][A-Za-z0-9]*)(?![^>]*\bxmlns\s*=)")


#: A rendered SVG whose viewBox is smaller than this in either direction has
#: nothing in it.  Observed 2026-11-14: mermaid given ``flowchart LR\n  A -->``
#: does not raise; it returns a 12 KB SVG of markers with viewBox "-8 -8 16 16".
EMPTY_SVG_MAX_PX = 24.0


def svg_is_empty(svg: str) -> bool:
    """True for an SVG that drew nothing (a viewBox of a few pixels)."""
    m = _SVG_ROOT_RE.search(svg)
    vb = _VIEWBOX_RE.search(m.group(0)) if m else None
    if not vb:
        return False
    try:
        return min(float(vb.group(3)), float(vb.group(4))) < EMPTY_SVG_MAX_PX
    except ValueError:
        return False


def _data_uri(data: bytes, media_type: str) -> str:
    if "svg" in media_type:
        text = make_svg_standalone(data.decode("utf-8", errors="replace"))
        if svg_is_empty(text):
            raise ValueError("the diagram rendered empty; check its source for a syntax error")
        text = size_svg_to_viewbox(text)
        data, media_type = text.encode("utf-8"), "image/svg+xml"
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


def _figure_spec(inner: str) -> Optional[str]:
    m = _SPEC_SCRIPT_RE.search(inner)
    if m:
        return m.group(1).strip("\n")
    m = _SPEC_PRE_RE.search(inner)
    if m:
        return _html.unescape(re.sub(r"</?code\b[^>]*>", "", m.group(1))).strip("\n")
    return None


def _header(meta: Dict[str, Any]) -> str:
    title = (meta.get("title") or "").strip()
    if not title:
        return ""
    parts = [f"<h1>{_html.escape(title)}</h1>"]
    if meta.get("subtitle"):
        parts.append(f'<div class="subtitle">{_html.escape(str(meta["subtitle"]).strip())}</div>')
    by = []
    if meta.get("author"):
        by.append(f"<b>{_html.escape(str(meta['author']).strip())}</b>")
    if meta.get("date"):
        by.append(_html.escape(str(meta["date"])))
    if by:
        parts.append(f'<div class="byline">{" &middot; ".join(by)}</div>')
    return f'<header class="report">{"".join(parts)}</header>'


async def build_report(
    body: str, meta: Optional[Dict[str, Any]], render: Optional[RenderFn],
    *, footer: str = "",
) -> Tuple[str, Dict[str, Any]]:
    """Assemble the self-contained report.  Returns ``(html, info)``.

    ``info``: ``figures`` (rendered), ``failed`` (list of ``{type, error}``;
    each leaves a visible error box in the page), ``external`` (URLs the
    body loads from the network, which a sandboxed preview will not show),
    ``stripped_scripts``, ``bytes``.
    """
    meta = dict(meta or {})
    info: Dict[str, Any] = {"figures": 0, "failed": [], "external": [], "stripped_scripts": 0}

    # Figures first, so their spec <script> blocks are consumed before the
    # script strip below removes anything else.
    out: List[str] = []
    pos = 0
    for m in _FIGURE_RE.finditer(body):
        out.append(body[pos:m.start()])
        pos = m.end()
        kind, inner = m.group("type").lower(), m.group("inner")
        cap = _CAPTION_RE.search(inner)
        caption = cap.group(0) if cap else ""
        spec = _figure_spec(inner)
        err: Optional[str] = None
        img = ""
        if spec is None or not spec.strip():
            err = "no diagram source (expected <script type=\"text/x-diagram\">)"
        elif render is None:
            err = "no renderer available"
        else:
            try:
                data, media = await render(kind, spec)
                alt = _html.escape(re.sub(r"<[^>]+>", "", caption).strip() or f"{kind} diagram", quote=True)
                img = f'<img alt="{alt}" src="{_data_uri(data, media)}">'
            except Exception as exc:  # noqa: BLE001 -- one bad figure must not lose the report
                err = str(exc) or type(exc).__name__
        if err is not None:
            info["failed"].append({"type": kind, "error": err})
            img = (f'<div class="figure-error">Figure ({_html.escape(kind)}) did not render: '
                   f"{_html.escape(err[:300])}</div>")
        else:
            info["figures"] += 1
        out.append(f"<figure>{img}{caption}</figure>")
    out.append(body[pos:])
    body = "".join(out)

    body, n = _SCRIPT_RE.subn("", body)
    info["stripped_scripts"] = n
    body = _HANDLER_RE.sub("", body)
    info["external"] = sorted(set(_EXTERNAL_RE.findall(body)))

    title = _html.escape((meta.get("title") or "Report").strip())
    foot = f'<footer class="report">{_html.escape(footer)}</footer>' if footer else ""
    page = ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{title}</title><style>{REPORT_CSS}</style></head><body>\n"
            f"{_header(meta)}\n{body.strip()}\n{foot}</body></html>\n")
    info["bytes"] = len(page.encode("utf-8"))
    return page, info


def load_report_source(name: Optional[str] = None, text: Optional[str] = None) -> Tuple[Dict[str, Any], str]:
    """``(meta, body)`` from a stored source (``.ziya/documents/<name>``) or
    inline text.  Same front-matter keys as the PDF document format."""
    from app.utils.document_ir import parse_document, resolve_document_path
    if text is None:
        if not name:
            raise ValueError("html report requires either name or html")
        path = resolve_document_path(name)
        if not path.is_file():
            raise FileNotFoundError(f"Document not found: {name}")
        text = path.read_text(encoding="utf-8")
    meta, body = parse_document(text)
    if meta.get("date"):
        # Same display form as the PDF title block ("November 13, 2026").
        from app.utils.document_print_decor import format_document_date
        meta = {**meta, "date": format_document_date(meta["date"])}
    return meta, body
