"""
Integration tests for the chemfig lint inside the LaTeX render pipeline.

These cover the wiring rather than the counting rules (see test_chemfig_lint.py
for those).  Three properties matter and none of them are checkable from the
lint module alone:

  1. The lint runs AFTER the security prescan, so a rejected body is never
     rewritten -- an autofixer that edited hostile input before it was refused
     would be a way to smuggle changes past the deny-list.

  2. The body that gets cached is the body that gets compiled.  If the lint ran
     after the cache key was computed, a fixed and an unfixed body would share a
     key and serve each other's bytes.

  3. A defect in the lint degrades to "render as written".  This is a
     convenience check on model output; it must never be the reason a working
     diagram fails.

Marked to skip cleanly when the ``warnings`` field is absent, so the suite still
passes before the renderer diff is applied.
"""

import pytest

from app.services.latex_renderer import Capability, LatexRenderer, RenderResult

pytestmark = pytest.mark.skipif(
    not hasattr(RenderResult, "warnings")
    and "warnings" not in getattr(RenderResult, "__dataclass_fields__", {}),
    reason="renderer lint wiring not applied yet",
)

BENZENE_SHORT = r"\chemfig{*6(-=-=-)}"
BENZENE_FIXED = r"\chemfig{*6(-=-=-=)}"
ISATIN_SHORT = r"\chemfig{*6(-=-*5(-(=O)-(=O)-)=-=)}"
INDOLE_OK = r"\chemfig{*6(-=-*5(-=--)=-=)}"


@pytest.fixture
def renderer(tmp_path):
    return LatexRenderer(cache_dir=tmp_path / "cache")


def _full_capability() -> Capability:
    return Capability(
        has_latex=True, has_dvisvgm=True, has_pdflatex=True,
        has_ghostscript=True, has_standalone=True,
    )


@pytest.fixture
def captured(monkeypatch):
    """Capture the body handed to the compiler, bypassing TeX entirely."""
    seen: dict = {}

    monkeypatch.setattr(LatexRenderer, "_kpsewhich", staticmethod(lambda _f: True))
    monkeypatch.setattr(LatexRenderer, "probe",
                        lambda self, refresh=False: _full_capability())

    def fake_compile(self, document, target, cap):
        seen["document"] = document
        return RenderResult(ok=True, content=b"stub", fmt=target)

    monkeypatch.setattr(LatexRenderer, "_compile", fake_compile)
    return seen


def test_unambiguous_short_ring_is_fixed_before_compilation(renderer, captured):
    """The compiler must receive the corrected body, not the original."""
    result = renderer.render("chemfig", BENZENE_SHORT, fmt="png", use_cache=False)
    assert result.ok
    assert r"*6(-=-=-=)" in captured["document"], "fix did not reach the compiler"
    assert result.autofixes, "a silent fix would hide the correction"
    assert not result.warnings


def test_ambiguous_short_ring_warns_and_is_left_alone(renderer, captured):
    """Isatin's five-ring: two plausible closures, so the body is untouched.

    The render still succeeds -- that is the whole problem this surfaces, since
    a caller checking only ``ok`` would describe the wrong molecule.
    """
    result = renderer.render("chemfig", ISATIN_SHORT, fmt="png", use_cache=False)
    assert result.ok
    assert r"*5(-(=O)-(=O)-)" in captured["document"], "must not rewrite"
    assert not result.autofixes
    assert result.warnings and "*5" in result.warnings[0]


def test_correct_body_is_neither_altered_nor_flagged(renderer, captured):
    result = renderer.render("chemfig", INDOLE_OK, fmt="png", use_cache=False)
    assert result.ok
    assert not result.warnings and not result.autofixes
    assert r"*5(-=--)" in captured["document"]


def test_cache_key_covers_the_fixed_body(renderer, captured):
    """A short body and its fixed form must share the cache entry.

    They compile to identical output, so they should hit the same key.  If the
    lint ran after the key was computed the two would diverge, and the unfixed
    body would cache bytes that do not correspond to it.
    """
    first = renderer.render("chemfig", BENZENE_SHORT, fmt="png")
    assert first.ok and not first.cached

    second = renderer.render("chemfig", BENZENE_FIXED, fmt="png")
    assert second.ok
    assert second.cached, "fixed body should hit the cache the short one filled"
    assert first.content == second.content


def test_lint_runs_after_the_security_prescan(renderer, captured):
    """A rejected body must never be rewritten.

    Ordering is a security property, not a preference: rewriting hostile input
    before refusing it would be a route to smuggling edits past the deny-list.
    """
    hostile = r"\chemfig{*6(-=-=-)}\input{/etc/passwd}"
    result = renderer.render("chemfig", hostile, fmt="png", use_cache=False)
    assert not result.ok
    assert result.error_kind == "rejected"
    assert not result.autofixes and not result.warnings
    assert "document" not in captured, "rejected input must not reach the compiler"


def test_lint_failure_does_not_break_the_render(renderer, captured, monkeypatch):
    """A bug in the lint must degrade to rendering the body as written."""
    def explode(_body):
        raise RuntimeError("synthetic lint defect")

    monkeypatch.setattr("app.utils.chemfig_lint.autofix", explode)

    result = renderer.render("chemfig", BENZENE_SHORT, fmt="png", use_cache=False)
    assert result.ok, "a lint defect must not fail an otherwise valid render"
    assert r"*6(-=-=-)" in captured["document"]
    assert not result.autofixes and not result.warnings


def test_non_chemfig_types_are_untouched(renderer, captured):
    """The ring rule is chemfig-specific; TikZ ``*`` means multiplication."""
    body = r"\draw (0,0) -- (2*1,0);"
    result = renderer.render("tikz", body, fmt="png", use_cache=False)
    assert result.ok
    assert not result.warnings and not result.autofixes
    assert "2*1" in captured["document"]
