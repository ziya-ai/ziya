"""The document_authoring skill prompt carries the print-figure guidance the
doc-aesthetics loop derived (iter_003: d003 timeline width, d004 chart
subtitle overflow, d006/d016 mermaid subgraph direction)."""
from pathlib import Path

from app.data.built_in_skills import get_skill_by_id


def _prompt() -> str:
    skill = get_skill_by_id("document_authoring")
    assert skill is not None
    return skill["prompt"]


def test_prompt_has_figure_print_guidance():
    p = _prompt()
    assert "## Figures that print well" in p
    for needle in ("subgraph", "direction", "timeline", "subtitle", "6.5"):
        assert needle in p, needle
    # The corrected rule: connect subgraphs to each other, not nodes across them.
    assert "not node-to-node" in p
    # Timelines: an explicit, testable cap on periods (columns).
    assert "4 periods" in p and "4 sections" in p
    # Flowcharts: the variant the iter_003 probe proved (columns, not rows).
    assert "direction TB" in p


def test_docs_mirror_figure_print_guidance():
    doc = (Path(__file__).resolve().parents[1] / "Docs" / "DocumentAuthoring.md").read_text()
    assert "### Figures that print well" in doc
    for needle in ("subgraph", "timeline", "subtitle", "6.5"):
        assert needle in doc, needle


def test_prompt_has_table_and_note_guidance():
    """iter_004 (d007/d008): tables short enough to balance; notes marked so
    the PDF styles them as notes."""
    p = _prompt()
    assert "## Tables and notes" in p
    assert "5 columns" in p
    assert "> **Reviewer note:**" in p and "> **TODO:**" in p
    doc = (Path(__file__).resolve().parents[1] / "Docs" / "DocumentAuthoring.md").read_text()
    assert "### Tables and notes" in doc
    assert "> **Reviewer note:**" in doc


def test_prompt_has_margin_measure_guidance():
    """iter_006 (d025): 16mm side margins at the 11pt print body run ~97
    characters per line, past the R9 95-character limit."""
    p = _prompt()
    assert "## Page margins and line length" in p
    i = p.index("## Page margins and line length")
    block = p[i:i + 900]
    assert "19-20mm" in block and "95" in block and "left: 19mm" in block
    assert "margin" in block.lower()


def test_docs_mirror_margin_measure_guidance():
    doc = (Path(__file__).resolve().parents[1] / "Docs" / "DocumentAuthoring.md").read_text()
    assert "### Page margins and line length" in doc
    i = doc.index("### Page margins and line length")
    block = doc[i:i + 1400]
    assert "19-20mm" in block and "95" in block and "left: 19mm" in block


def _docs() -> str:
    return (Path(__file__).resolve().parents[1] / "Docs" / "DocumentAuthoring.md").read_text()


def test_prompt_has_stacked_chart_and_back_to_back_rule():
    """iter_008 (d018): a tall vconcat figure ending a section of figures
    leaves a blank band the PDF cannot fill (no prose to float)."""
    p = _prompt()
    i = p.index("## Figures that print well")
    block = p[i:p.index("\n## ", i + 5)]
    assert "vconcat" in block and "back to back" in block and "half a page" in block
    d = _docs()
    j = d.index("### Figures that print well")
    dblock = d[j:d.index("\n### ", j + 5)]
    assert "vconcat" in dblock and "back to back" in dblock


def test_prompt_export_uses_env_port_not_literal_6969():
    """Attribution (2026-10-14, netscan lc-s9): a task-card stage followed
    the skill's literal `localhost:6969` curl, got a non-200 from a port
    nothing listened on, declared the export server unreachable and fell
    back to pandoc — while the real server (port 1999/1989) answered %PDF
    the whole time.  The skill must point at $ZIYA_PORT, require a %PDF
    check, and forbid concluding "unreachable" from a single probe."""
    p = _prompt()
    i = p.index("5. To render a PDF")
    block = p[i:p.index("\n6. ", i)]
    assert "localhost:$ZIYA_PORT/api/export/document" in block
    assert "6969" not in block, "a literal default port is what the stage copied"
    assert "%PDF" in block
    assert "lsof" in block
    # "Unreachable" needs every discovered port tried, not one failed probe.
    assert "every discovered port" in block
    assert "single failed probe" in block
    assert "pandoc" in block  # the fallback is named so it can be forbidden


def test_server_exports_its_port_as_ZIYA_PORT(monkeypatch):
    """Seam: the skill tells the model $ZIYA_PORT is in the shell tool's
    environment.  main.py must actually publish the bound port there (and
    not leave the registry default), or the recipe silently reverts to
    6969 on every non-default server."""
    import os
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
    assert 'os.environ["ZIYA_PORT"] = str(args.port)' in src
    # And the shell tool inherits the process environment rather than a
    # scrubbed one: a subprocess sees what the parent set.
    import subprocess
    monkeypatch.setenv("ZIYA_PORT", "4242")
    out = subprocess.run(["sh", "-c", 'echo "$ZIYA_PORT"'], capture_output=True, text=True)
    assert out.stdout.strip() == "4242"


def test_docs_mirror_env_port_recipe():
    d = _docs()
    i = d.index("## Exporting")
    block = d[i:i + 1600]
    assert "${ZIYA_PORT:-6969}/api/export/document" in block
    assert "head -c 5" in block and "%PDF" in block
    assert "lsof" in block


def test_prompt_has_list_blockquote_rule():
    """iter_008 (d013): a column-0 blockquote inside a list ends the list."""
    p = _prompt()
    i = p.index("## Tables and notes")
    assert "Never interrupt a list" in p[i:]
    d = _docs()
    j = d.index("### Tables and notes")
    assert "Never interrupt a list" in d[j:]
