"""Tests for scripts/complandscape_docdefects.py (WS0 worklist extraction).

Builds a tiny synthetic corpus in a tmp directory and drives the real
``extract`` command over it, asserting the classification rules the design
doc pins down: every class is produced, a null discrepancy yields no row, a
capability in several classes yields several rows, cited lines parse out of
``File.md:NN`` text, and the emitted JSON round-trips.
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import complandscape_docdefects as dd  # noqa: E402


# --------------------------------------------------------------------------
# synthetic corpus
# --------------------------------------------------------------------------

def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def _cap(cid, disc, maturity=3, subsystem="sub-a"):
    return {
        "id": cid,
        "name": f"Name of {cid}",
        "subsystem": subsystem,
        "aliases": [f"{cid}-alias"],
        "evidence": [{"path": f"app/{cid}.py", "lines": "1-2", "tier": "A"}],
        "maturity": maturity,
        "doc_discrepancy": disc,
    }


@pytest.fixture()
def corpus(tmp_path):
    root = tmp_path / "corpus"
    synth_run = "s-test"
    rei_run = "r-test"

    # capability ids exercising each class
    caps = [
        _cap("under-x", "The X mechanism is undocumented in the feature docs."),
        _cap("over-x", "Docs over-claim: the Y job is not implemented in code."),
        _cap("ok-x", "null"),
        _cap("ok2-x", "Docs/Capabilities.md:10 documents this accurately."),
        _cap("other-x", "A curious note that trips no classification keyword."),
        _cap("term-x", "shipped under a non-marketing name"),
        _cap("nong-x", "no discrepancy"),
        _cap("infl-x", "null"),
        _cap("part-x", "null"),
        # multi-class: keyword under-claim AND a Critic-A inflated score
        _cap("multi-x",
             "This is undocumented; see Docs/FeatureInventory.md:42 for context."),
        # maturity-1 capability: must land in the glossary's vestigial section
        # ('null' discrepancy => no extract row, so extract tests are untouched)
        _cap("vest-x", "null", maturity=1),
    ]
    # many-alias capability for the alias-cap test: a mix of reader-facing
    # phrases and code identifiers, more than the MAX_ALIASES cap.
    caps.append({
        "id": "aliasy-x",
        "name": "Name of aliasy-x",
        "subsystem": "sub-a",
        "aliases": [
            "plain phrase one", "plain phrase two", "plain phrase three",
            "plain phrase four", "plain phrase five", "plain phrase six",
            "plain phrase seven", "plain phrase eight", "plain phrase nine",
            "SomeModule.some_method", "another_code_ident", "Dup Phrase",
            "dup phrase",  # case-insensitive duplicate of the above
        ],
        "evidence": [{"path": "app/aliasy.py", "lines": "1-2", "tier": "A"}],
        "maturity": 3,
        "doc_discrepancy": "null",
    })
    ledger = {"generated": "t", "capabilities": caps}

    matrix = {
        "schema_version": "2.0",
        "domains": ["dom-a"],
        "tools": ["ziya", "other-tool"],
        "cells": [],
        "capabilities": [
            {"id": c["id"], "name": c["name"], "domain": "dom-a",
             "vendor_aliases": []} for c in caps
        ],
    }

    summary = {
        "terminology_defects_capability_ids": ["term-x"],
        "deliberate_non_goals_capability_ids": ["nong-x"],
        "documentation_defects": {},
    }

    critique = {
        "inflated_scores": [
            {"capability_id": "infl-x", "claimed": 4, "defensible": 3,
             "reasoning": "composite above the max of its parts"},
            {"capability_id": "multi-x", "claimed": 4, "defensible": 2,
             "reasoning": "stretched"},
        ],
        "reintegration_overreach": [
            {"capability_id": "part-x",
             "why_the_match_is_wrong": "mechanism differs in kind"},
            {"capability_id": "SYSTEMIC: not a real id",
             "why_the_match_is_wrong": "systemic note, not a capability"},
        ],
    }

    _write(str(root / "19-ziya-ledger.json"), ledger)
    _write(str(root / "30-matrix.json"), matrix)
    _write(str(root / "41-reintegration-summary.json"), summary)

    # synthesis run dir + critique
    (root / "60-synthesis").mkdir(parents=True)
    (root / "60-synthesis" / "CURRENT_RUN").write_text(synth_run)
    synth_dir = root / "60-synthesis" / synth_run
    synth_dir.mkdir()
    _write(str(synth_dir / "60-critique-ziya.json"), critique)

    # reintegration run dir with a Stage A record carrying an internal name
    (root / "50-reintegration").mkdir(parents=True)
    (root / "50-reintegration" / "CURRENT_RUN").write_text(rei_run)
    rei_dir = root / "50-reintegration" / rei_run
    rei_dir.mkdir()
    _write(str(rei_dir / "term-x-stageA.json"),
           {"capability_id": "term-x", "verdict": "FOUND",
            "ziya_internal_name": "internal-term-name",
            "fraction_present_if_partial": None,
            "documentation_defect": "term-x appears only as a shortcut"})
    _write(str(rei_dir / "term-x-disposition.json"),
           {"capability_id": "term-x", "disposition": "LEDGER_CORRECTION",
            "documentation_defect": "documented only as a keyboard shortcut",
            "recommendation": "dispose as ledger correction"})

    return {"root": str(root), "synth_dir": str(synth_dir)}


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------

def test_each_class_is_produced(corpus):
    res = dd.extract(corpus["root"])
    rows = res["doc"]["rows"]
    classes = {r["class"] for r in rows}
    for expected in ("under", "over", "terminology", "non-goal",
                     "inflated", "partial", "other"):
        assert expected in classes, f"missing class {expected}"


def test_null_discrepancy_yields_no_row(corpus):
    res = dd.extract(corpus["root"])
    rows = res["doc"]["rows"]
    # ok-x ('null') and ok2-x ('accurately') must not appear as free-text rows.
    assert not any(r["capability_id"] == "ok-x" for r in rows)
    assert not any(r["capability_id"] == "ok2-x" for r in rows)


def test_multi_class_capability_yields_multiple_rows(corpus):
    res = dd.extract(corpus["root"])
    rows = res["doc"]["rows"]
    multi = [r for r in rows if r["capability_id"] == "multi-x"]
    classes = {r["class"] for r in multi}
    assert len(multi) >= 2
    assert {"inflated", "under"} <= classes


def test_cited_line_parses(corpus):
    res = dd.extract(corpus["root"])
    rows = res["doc"]["rows"]
    under_multi = [r for r in rows
                   if r["capability_id"] == "multi-x" and r["class"] == "under"]
    assert under_multi and under_multi[0]["cited_line"] == 42
    assert "Docs/FeatureInventory.md" in under_multi[0]["target_docs"]


def test_corrected_maturity_from_critic(corpus):
    res = dd.extract(corpus["root"])
    rows = res["doc"]["rows"]
    infl = [r for r in rows if r["capability_id"] == "infl-x"]
    assert infl and infl[0]["corrected_maturity"] == 3


def test_systemic_overreach_entry_is_dropped(corpus):
    res = dd.extract(corpus["root"])
    rows = res["doc"]["rows"]
    partial_ids = {r["capability_id"] for r in rows if r["class"] == "partial"}
    assert partial_ids == {"part-x"}


def test_ziya_internal_name_carried(corpus):
    res = dd.extract(corpus["root"])
    rows = res["doc"]["rows"]
    term = [r for r in rows if r["capability_id"] == "term-x"]
    assert term and term[0]["ziya_internal_name"] == "internal-term-name"


def test_json_round_trips(corpus):
    res = dd.extract(corpus["root"])
    with open(res["json_path"], encoding="utf-8") as fh:
        reloaded = json.load(fh)
    assert reloaded == res["doc"]
    assert set(reloaded) == {"generated", "runs", "rows", "reconciliation"}
    assert reloaded["reconciliation"]["total_rows"] == len(reloaded["rows"])


def test_markdown_written(corpus):
    res = dd.extract(corpus["root"])
    with open(res["md_path"], encoding="utf-8") as fh:
        text = fh.read()
    assert "Documentation-defect worklist" in text
    assert "## Reconciliation" in text


def test_freetext_classifier_precedence():
    # under/over keyword beats an incidental 'accurate'/'matches' word
    assert dd._classify_freetext("is accurate but under-claims depth") == "under"
    assert dd._classify_freetext("null") is None
    assert dd._classify_freetext("documented accurately, matches code") is None
    assert dd._classify_freetext("some unmatched prose") == "other"


# --------------------------------------------------------------------------
# glossary command (WS2)
# --------------------------------------------------------------------------

def _glossary_entry(region, cid):
    """Return the generated bullet line for a capability id, or None."""
    for line in region.splitlines():
        if f"`{cid}`" in line:
            return line
    return None


def test_glossary_is_deterministic(corpus):
    a = dd.build_glossary(dd.DocDefectCorpus(corpus["root"]))["region"]
    b = dd.build_glossary(dd.DocDefectCorpus(corpus["root"]))["region"]
    assert a == b, "glossary region must be byte-identical across runs"


def test_glossary_alias_cap_respected(corpus):
    res = dd.build_glossary(dd.DocDefectCorpus(corpus["root"]))
    line = _glossary_entry(res["region"], "aliasy-x")
    assert line is not None, "aliasy-x should appear as a mature entry"
    assert "also: " in line
    aliases = line.split("also: ", 1)[1].split(", ")
    # capped at MAX_ALIASES
    assert len(aliases) <= dd.MAX_ALIASES
    assert len(aliases) == dd.MAX_ALIASES  # had far more than the cap
    # case-insensitive dedup: only one of Dup Phrase / dup phrase survives
    lowered = [a.lower() for a in aliases]
    assert len(lowered) == len(set(lowered))
    # reader-facing phrases sort ahead of code identifiers
    assert not any(dd._is_code_identifier(a) for a in aliases), (
        "reader-facing aliases should fill the cap before code identifiers")


def test_glossary_maturity_one_is_vestigial(corpus):
    res = dd.build_glossary(dd.DocDefectCorpus(corpus["root"]))
    region = res["region"]
    # vest-x (maturity 1) must appear only under the vestigial subsection,
    # after the 'Experimental or vestigial' heading, with no alias expansion.
    assert "## Experimental or vestigial" in region
    _, vest_part = region.split("## Experimental or vestigial", 1)
    vest_line = _glossary_entry(vest_part, "vest-x")
    assert vest_line is not None, "maturity-1 vest-x must be in the vestigial list"
    assert "also:" not in vest_line, "vestigial entries carry no alias expansion"
    # and it must NOT appear in the mature 'by domain' region
    domain_part = region.split("## Experimental or vestigial", 1)[0]
    assert _glossary_entry(domain_part, "vest-x") is None


def test_glossary_write_replaces_region_and_preserves_preamble(tmp_path, corpus):
    path = str(tmp_path / "CapabilityGlossary.md")
    # first write creates the file from the hand-authored scaffold
    res1 = dd.glossary(corpus["root"], write=True, path=path)
    assert res1["written"] == path
    text1 = open(path, encoding="utf-8").read()
    # preamble + placeholder section preserved
    assert "# Ziya Capability Glossary" in text1
    assert "## Terms competitors use" in text1
    assert dd.GEN_BEGIN in text1 and dd.GEN_END in text1

    # hand-edit OUTSIDE the markers, then regenerate: the edit must survive
    marker = "HAND EDITED SENTINEL OUTSIDE MARKERS"
    edited = text1.replace("_To be written._", "_To be written._\n\n" + marker)
    assert marker in edited
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(edited)

    res2 = dd.glossary(corpus["root"], write=True, path=path)
    text2 = open(path, encoding="utf-8").read()
    assert marker in text2, "hand edits outside the markers must be preserved"
    # region content is identical to a fresh build
    b = text2.find(dd.GEN_BEGIN)
    e = text2.find(dd.GEN_END) + len(dd.GEN_END)
    assert text2[b:e] == res2["region"]
    # only the region changed between the two writes: heads match
    assert text2[:text2.find(dd.GEN_BEGIN)] == edited[:edited.find(dd.GEN_BEGIN)]


# --------------------------------------------------------------------------
# glossary subcommand (WS2)
# --------------------------------------------------------------------------

def _gcap(cid, name, maturity, aliases, subsystem="sub-a"):
    return {
        "id": cid,
        "name": name,
        "subsystem": subsystem,
        "aliases": aliases,
        "evidence": [{"path": f"app/{cid}.py", "lines": "1-2", "tier": "A"}],
        "maturity": maturity,
        "doc_discrepancy": "null",
    }


@pytest.fixture()
def gcorpus(tmp_path):
    """A synthetic corpus plus a synthetic Capabilities.md for glossary tests."""
    root = tmp_path / "corpus"
    synth_run = "s-test"
    rei_run = "r-test"

    caps = [
        # >8 aliases mixing reader phrases and code identifiers -> tests the cap
        # and the reader-before-code ordering.
        _gcap("alias-heavy", "Alias heavy capability", 3, [
            "code graph build", "repo indexing", "workspace symbol index",
            "background code index", "project indexing", "codebase indexing",
            "Repo Indexing",  # dup (case-insensitive) of 'repo indexing'
            "initialize_ast_capabilities",
            "ProjectContextMiddleware._ensure_ast_indexed",
            "build_query_engine", "AstIndexer.run",
        ]),
        # matches a heading in the synthetic Capabilities.md -> gets an anchor.
        _gcap("anchored-x", "Thinking Mode", 3, ["reasoning stream"]),
        # no heading names it -> lands in the no-anchor list.
        _gcap("orphan-x", "Some undocumented mechanism", 3, ["a phrase"]),
        # maturity 1 -> vestigial subsection, no alias expansion.
        _gcap("vestige-x", "Dead experimental thing", 1,
              ["should not appear", "nor this"]),
        # maturity 0 -> also vestigial.
        _gcap("zero-x", "Placeholder only", 0, ["ignore me"]),
    ]
    ledger = {"generated": "t", "capabilities": caps}
    matrix = {
        "schema_version": "2.0",
        "domains": ["dom-a", "dom-b"],
        "tools": ["ziya"],
        "cells": [],
        "capabilities": [
            {"id": "alias-heavy", "name": "Alias heavy capability",
             "domain": "dom-b", "vendor_aliases": []},
            {"id": "anchored-x", "name": "Thinking Mode",
             "domain": "dom-a", "vendor_aliases": []},
            {"id": "orphan-x", "name": "Some undocumented mechanism",
             "domain": "dom-a", "vendor_aliases": []},
            {"id": "vestige-x", "name": "Dead experimental thing",
             "domain": "dom-a", "vendor_aliases": []},
            {"id": "zero-x", "name": "Placeholder only",
             "domain": "dom-b", "vendor_aliases": []},
        ],
    }
    summary = {
        "terminology_defects_capability_ids": [],
        "deliberate_non_goals_capability_ids": [],
        "documentation_defects": {},
    }
    critique = {"inflated_scores": [], "reintegration_overreach": []}

    _write(str(root / "19-ziya-ledger.json"), ledger)
    _write(str(root / "30-matrix.json"), matrix)
    _write(str(root / "41-reintegration-summary.json"), summary)
    (root / "60-synthesis").mkdir(parents=True)
    (root / "60-synthesis" / "CURRENT_RUN").write_text(synth_run)
    synth_dir = root / "60-synthesis" / synth_run
    synth_dir.mkdir()
    _write(str(synth_dir / "60-critique-ziya.json"), critique)
    (root / "50-reintegration").mkdir(parents=True)
    (root / "50-reintegration" / "CURRENT_RUN").write_text(rei_run)
    (root / "50-reintegration" / rei_run).mkdir()

    caps_md = tmp_path / "Capabilities.md"
    caps_md.write_text(
        "# Synthetic Capabilities\n\n"
        "## Thinking Mode\n\nsome text\n\n"
        "```\n## Not A Heading (inside a fence)\n```\n\n"
        "## Other Section\n\nmore text\n")

    glossary_path = tmp_path / "CapabilityGlossary.md"
    return {"root": str(root), "caps_md": str(caps_md),
            "glossary_path": str(glossary_path)}


def test_glossary_deterministic(gcorpus):
    a = dd.glossary(gcorpus["root"], write=False,
                    capabilities_path=gcorpus["caps_md"])
    b = dd.glossary(gcorpus["root"], write=False,
                    capabilities_path=gcorpus["caps_md"])
    assert a["region"] == b["region"]


def test_glossary_alias_cap_and_order(gcorpus):
    res = dd.glossary(gcorpus["root"], write=False,
                      capabilities_path=gcorpus["caps_md"])
    line = next(ln for ln in res["region"].splitlines()
                if "`alias-heavy`" in ln)
    assert "also: " in line
    shown = line.split("also: ", 1)[1].split(", ")
    # at most 8 aliases survive
    assert len(shown) <= dd.MAX_ALIASES
    # case-insensitive dedupe: 'repo indexing' appears once
    assert sum(s.lower() == "repo indexing" for s in shown) <= 1
    # reader-facing phrases sort ahead of code identifiers
    reader = [s for s in shown if not dd._is_code_identifier(s)]
    code = [s for s in shown if dd._is_code_identifier(s)]
    assert shown == reader + code
    # and nothing code-shaped displaced a reader phrase that was dropped
    assert reader, "expected at least one reader-facing alias"


def test_glossary_anchor_and_no_anchor(gcorpus):
    res = dd.glossary(gcorpus["root"], write=False,
                      capabilities_path=gcorpus["caps_md"])
    # anchored-x links to the Thinking Mode heading slug
    assert "(Capabilities.md#thinking-mode)" in res["region"]
    # orphan-x has no heading -> in the no-anchor list, links to file only
    assert "orphan-x" in res["no_anchor"]
    orphan_line = next(ln for ln in res["region"].splitlines()
                       if "`orphan-x`" in ln)
    assert "(Capabilities.md)" in orphan_line
    assert "#" not in orphan_line.split("(Capabilities.md")[1][:2]


def test_glossary_vestigial_placement(gcorpus):
    res = dd.glossary(gcorpus["root"], write=False,
                      capabilities_path=gcorpus["caps_md"])
    region = res["region"]
    vest_idx = region.index("## Experimental or vestigial")
    # maturity <= 1 capabilities appear only after the vestigial heading
    for ln in region.splitlines():
        if "`vestige-x`" in ln or "`zero-x`" in ln:
            assert region.index(ln) > vest_idx
    # and carry no alias expansion
    vest_line = next(ln for ln in region.splitlines() if "`vestige-x`" in ln)
    assert "also:" not in vest_line
    assert "should not appear" not in vest_line
    assert res["vestigial_count"] == 2
    assert res["mature_count"] == 3


def test_glossary_write_replaces_region_preserves_preamble(gcorpus):
    path = gcorpus["glossary_path"]
    # first write creates the scaffold + fills the region
    res1 = dd.glossary(gcorpus["root"], write=True, path=path,
                       capabilities_path=gcorpus["caps_md"])
    text1 = open(path, encoding="utf-8").read()
    assert "# Ziya Capability Glossary" in text1
    assert "## Terms competitors use" in text1
    assert dd.GEN_BEGIN in text1 and dd.GEN_END in text1
    assert "`alias-heavy`" in text1

    # hand-edit OUTSIDE the markers, then regenerate
    marker = "_To be written._"
    edited = text1.replace(marker, "_HAND EDIT PRESERVED._")
    open(path, "w", encoding="utf-8").write(edited)
    res2 = dd.glossary(gcorpus["root"], write=True, path=path,
                       capabilities_path=gcorpus["caps_md"])
    text2 = open(path, encoding="utf-8").read()
    # the hand edit outside the region survives
    assert "_HAND EDIT PRESERVED._" in text2
    # the region is byte-identical to the fresh build (regeneration is stable)
    assert res1["region"] == res2["region"]
    assert res2["region"] in text2


def test_gh_slug():
    assert dd.gh_slug("Thinking Mode") == "thinking-mode"
    assert dd.gh_slug("Context & Projects") == "context--projects"
    assert dd.gh_slug("/goal — Autonomous Goals") == "goal--autonomous-goals"


def test_parse_headings_skips_fences(gcorpus):
    heads = dd.parse_headings(gcorpus["caps_md"])
    slugs = {s for _, s in heads}
    assert "thinking-mode" in slugs
    assert "not-a-heading-inside-a-fence" not in slugs
