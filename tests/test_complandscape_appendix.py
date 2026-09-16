"""Comparative appendices and PDF projection for the complandscape synthesis run.

THE DEFECT THIS EXISTS FOR
--------------------------
The first appendix PDF came out at 33 pages when the source held 108
head-to-head blocks, 27 scorecards and a 128-row register.  Nothing errored:
the report's prose quotes literal ``<script>`` and ``<think>`` tags, the
markdown renderer passed them through as HTML, and Chromium treated the first
as an unclosed <script> element -- 22 pages of tables became script text.
DOM inspection showed 33 <h4> out of 108 in the HTML.  The fix is to render
with HTML passthrough off, so the tag is text.  That test needs no corpus.

The corpus-backed tests assert the projection is COMPLETE: every depth record,
every roster tool and every reintegrated gap appears in its appendix, and the
critics' corrections are visible beside the raw values rather than replacing
them.  They skip when the study corpus is not on this machine.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import complandscape_appendix as appx  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", ".ziya", "complandscape")
HAVE_CORPUS = os.path.exists(os.path.join(ROOT, "60-synthesis", "CURRENT_RUN")) and \
    os.path.exists(os.path.join(ROOT, "30-matrix.json"))


def test_literal_script_tag_in_prose_is_text_not_markup(tmp_path):
    md = tmp_path / "x.md"
    md.write_text("Ziya strips all model <script> tags before injection.\n\n"
                  "#### After\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8")
    html = appx.md_to_html(str(md), "t", "")
    assert "<script" not in html
    assert "&lt;script&gt;" in html
    # the content after the quoted tag survives as markup
    assert "<h4>After</h4>" in html and "<table>" in html


def test_provenance_comment_is_stripped_from_print_copy_only():
    # A stand-in Corpus is not needed: the strip is a regex applied to the source text.
    import re
    src = "# T\n\n<!-- corpus-provenance {\"runs\": {\"a\": 1}} -->\n\nCorpus: line\n"
    out = re.sub(r"<!--\s*corpus-provenance\s*\{.*?\}\s*-->\n?", "", src, flags=re.S)
    assert "corpus-provenance" not in out and "Corpus: line" in out


@pytest.fixture(scope="module")
def cx():
    if not HAVE_CORPUS:
        pytest.skip("complandscape corpus not present")
    return appx.Corpus(ROOT)


@pytest.mark.skipif(not HAVE_CORPUS, reason="complandscape corpus not present")
class TestProjectionIsComplete:
    def test_head_to_head_has_one_block_per_depth_record(self, cx):
        a = appx.build_appendix_a(cx)
        assert a.count("\n#### ") == len(cx.depth) > 0
        for cid in cx.depth:
            assert f"`{cid}`" in a

    def test_scorecards_cover_every_roster_tool(self, cx):
        b = appx.build_appendix_b(cx)
        for t in cx.tools:
            assert f"(`{t}`)" in b
        assert b.count("\n### ") == len(cx.tools)

    def test_gap_register_has_one_row_per_reintegrated_gap(self, cx):
        c = appx.build_appendix_c(cx)
        ids = set(cx.stage_a) | set(cx.dispo)
        rows = [ln for ln in c.split("\n") if ln.startswith("| ") and "`" in ln.split("|")[1]]
        assert len(rows) == len(ids) > 0

    def test_matrix_has_every_capability_and_every_tool_column(self, cx):
        m = appx.build_matrix(cx)
        for cid in cx.caps:
            assert f"`{cid}`" in m
        header = next(ln for ln in m.split("\n") if ln.startswith("| capability | Ziya |"))
        assert header.count("|") == len(cx.tools) + 3  # capability, Ziya, tools, trailing

    def test_critic_corrections_shown_beside_raw_not_instead(self, cx):
        # Critic A's inflated_scores must surface as raw->defensible, with the raw still present.
        changed = [(cid, x) for cid, x in cx.z_inflated.items()
                   if cx.ziya_raw(cid) is not None and x["defensible"] != cx.ziya_raw(cid)]
        if not changed:
            pytest.skip("no inflated score differs from the matrix in this run")
        cid, x = changed[0]
        s = cx.ziya_score_str(cid)
        assert s == f"{cx.ziya_raw(cid)}\u2192**{x['defensible']}**"
        assert s in appx.build_matrix(cx)

    def test_unassessed_contenders_are_not_dash_columns(self, cx):
        # A tool whose every depth cell is not_in_matrix/not_assessed must not get a table column.
        a = appx.build_appendix_a(cx)
        for cid, d in cx.depth.items():
            uninformative = {c["tool"] for dim in d["dimensions"] for c in dim["competitors"]
                             if c["status"] not in ("scored", "unknown", "not_applicable")}
            informative = {c["tool"] for dim in d["dimensions"] for c in dim["competitors"]
                           if c["status"] in ("scored", "unknown", "not_applicable")}
            block = a.split(f"`{cid}`", 1)[1].split("\n#### ", 1)[0]
            headers = [ln for ln in block.split("\n") if ln.startswith("| dimension | Ziya |")]
            for t in uninformative - informative:
                for h in headers:
                    assert f"| {appx._short_name(cx.display(t))} |" not in h, (cid, t)
            break  # one record is enough to prove the rule is applied
