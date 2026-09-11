"""
``render_diagram`` / ``recall_image`` must not be offered to the model when
Playwright is absent.

On a fresh ``pip install ziya`` Playwright is an optional extra (``ziya[render]``)
and is NOT present.  The tool was registered anyway, so the first thing a new
user saw when asking for a sample graphic was the model calling
``render_diagram`` and getting back "Playwright is not installed" -- a wasted
cycle, on every diagram, for the model to re-learn each session.

Same pattern as the PCAP tools, which are already gated on scapy: check the
dependency at registration time, register nothing when it is missing, and log
the install command once so the operator (not the model) is the one told.
"""
from __future__ import annotations

import logging

import pytest

import app.services.diagram_renderer as dr
from app.mcp import builtin_tools


@pytest.fixture(autouse=True)
def _reset_playwright_probe():
    before = dr._playwright_available
    yield
    dr._playwright_available = before


def test_render_tools_absent_when_playwright_missing(monkeypatch):
    dr._playwright_available = False
    # The ZIYA logger sets propagate=False, so caplog never sees its records;
    # intercept the module logger's info() directly instead.
    seen: list[str] = []
    monkeypatch.setattr(builtin_tools.logger, "info",
                        lambda msg, *a, **k: seen.append(msg % a if a else msg))
    tools = builtin_tools.get_diagram_render_tools()
    assert tools == [], "render_diagram offered although Playwright is not importable"
    # The install command goes to the operator's log, not to the model.
    # The log names the one installer command, not a raw pip/playwright recipe.
    assert any("ziya-install-extras --browser" in m for m in seen), seen
    # It must also name WHAT is missing, not only how to install.  The gate
    # forces _playwright_available=False while a fresh probe may find nothing
    # missing (this very test on a fully-installed box), which once produced
    # "missing None." -- a message that undermines the fix it delivers.
    gate = [m for m in seen if "not registered" in m]
    assert gate, seen
    assert not any("None" in m for m in gate), gate
    assert any("Chromium" in m or "playwright package" in m for m in gate), gate


def test_render_tools_present_when_playwright_available():
    dr._playwright_available = True
    names = {t.__name__ for t in builtin_tools.get_diagram_render_tools()}
    assert names == {"RenderDiagramTool", "RecallImageTool"}, (
        "positive control failed: gating must not remove the tools when the "
        "dependency IS present"
    )


def test_gate_is_wired_to_the_real_probe(monkeypatch):
    # The seam: builtin_tools must consult diagram_renderer's probe, not a
    # private copy that could drift.
    calls = []

    def fake():
        calls.append(1)
        return False
    monkeypatch.setattr(dr, "_check_playwright", fake)
    assert builtin_tools.get_diagram_render_tools() == []
    assert calls, "get_diagram_render_tools did not call diagram_renderer._check_playwright"
