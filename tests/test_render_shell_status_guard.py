"""The headless drivers' shell-status guard must never itself crash a render.

Both app/services/pdf_exporter.py and app/services/diagram_renderer.py check
the HTTP status of the page.goto() response so a 404 SPA shell is reported as
a 404 rather than as a downstream "window.__renderX is not a function"
TypeError.

The first version of that guard did `status >= 400` after a getattr, which
crashed with TypeError whenever `status` was not a number -- notably when
page.goto() is mocked, since a Mock's `.status` is another Mock rather than
None.  That broke 8 pre-existing diagram-renderer tests.  A guard whose only
job is to improve diagnostics must degrade to "no opinion" on anything it does
not understand, never raise.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _fires(status) -> bool:
    """The exact predicate both drivers use."""
    return isinstance(status, int) and not isinstance(status, bool) and status >= 400


@pytest.mark.parametrize("status", [400, 404, 500, 503])
def test_error_statuses_are_detected(status):
    assert _fires(status) is True


@pytest.mark.parametrize("status", [200, 204, 301, 399])
def test_ok_statuses_are_not_flagged(status):
    assert _fires(status) is False


@pytest.mark.parametrize(
    "status",
    [None, AsyncMock(), MagicMock(), "404", object(), True, False],
    ids=["none", "asyncmock", "magicmock", "str", "object", "true", "false"],
)
def test_non_integer_status_never_raises_and_never_fires(status):
    """Anything that is not a plain int must yield False, not an exception.

    Mock objects are the case that broke the suite; `"404"` and bools are
    included because a str/bool would otherwise compare or coerce misleadingly.
    """
    assert _fires(status) is False


@pytest.mark.parametrize(
    "module_name,method_name",
    [
        ("app.services.pdf_exporter", "_open_and_render"),
        ("app.services.diagram_renderer", "render_diagram_with_diagnostics"),
    ],
)
def test_drivers_use_the_isinstance_guard(module_name, method_name):
    """Both drivers must use the type-safe form, not a bare comparison.

    Asserting on source keeps this fast and browser-free; the bug was a bare
    `status is not None and status >= 400`.
    """
    import importlib
    import inspect

    module = importlib.import_module(module_name)
    cls_name = {
        "_open_and_render": "ConversationRenderSession",
        "render_diagram_with_diagnostics": "DiagramRenderer",
    }[method_name]
    src = inspect.getsource(getattr(getattr(module, cls_name), method_name))

    assert "isinstance(status, int)" in src, (
        f"{module_name}.{cls_name}.{method_name} does not type-check the goto "
        f"response status; a non-numeric status (e.g. a Mock) would raise "
        f"TypeError inside the guard."
    )
    assert "status is not None and status >= 400" not in src, (
        f"{module_name}.{cls_name}.{method_name} still uses the unsafe bare "
        f"comparison form of the status guard."
    )
