"""{{claim:…}} citations in exported documents (app/utils/evidence/citations.py).

design/evidence-ledger.md, Citations and Tests: export seam, policy table,
supersession, templating non-collision.  The PDF is captured through the same
fake render session the rest of tests/test_document_export.py uses, so the
assertions are on the payload the /print route receives — the formatted value
and the Evidence appendix — and on the route's 400.
"""
import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.utils.evidence import store as st
from app.utils.evidence.citations import CitationError, resolve_claim_citations
from app.utils.document_ir import parse_document

NS = "audit"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path / "proj"))
    (tmp_path / "proj").mkdir()
    st._key_cache.clear()
    st._ledgers.clear()
    return tmp_path / "proj"


@pytest.fixture
def led(env):
    led = st.ledger_for(str(env))
    f = env / "s1.csv"
    f.write_text("errors,requests\n22464,120000\n")
    oid = led.record_observation("sql", query_text="SELECT ...", result_path=f)["id"]
    led.record_claim("s1.error_rate", 0.1872, namespace=NS, unit="ratio",
                     population={"description": "retried requests", "n": 120000},
                     derivation={"obs_ids": [oid], "column": "errors"})
    led.record_claim("s1.per_unit", 512.75, namespace=NS, unit="s/day",
                     distribution={"n": 640, "median": 418.37},
                     derivation={"obs_ids": [oid]})
    return led


def doc(policy, body):
    return f"---\ntitle: T\nevidence:\n  namespace: {NS}\n  policy: {policy}\n---\n{body}"


def run(env, text, name="report.md"):
    meta, body = parse_document(text)
    return resolve_claim_citations(body, meta, project_root=str(env), document=name)


# -- substitution and appendix -----------------------------------------------

def test_values_substituted_and_appendix_appended(env, led):
    text = doc("warn", "Error rate was {{claim:s1.error_rate|fmt=.2%}}.\n"
                       "Median loses {{claim:s1.per_unit.distribution.median|fmt=,.0f|unit}}.\n"
                       "Raw {{claim:s1.per_unit}} and again {{claim:s1.error_rate|fmt=.2%}}.\n"
                       "```\nnot touched: {{claim:s1.error_rate}}\n```\n")
    body, rep = run(env, text)
    assert "Error rate was 18.72%." in body
    assert "Median loses 418 s/day." in body
    assert "Raw 512.75 and again 18.72%." in body
    assert "not touched: {{claim:s1.error_rate}}" in body            # code fence left alone
    assert "## Evidence" in body and "| `audit.s1.error_rate@v1` | 18.72% | unverified" in body
    assert "retried requests (n=120,000)" in body and "s1.csv" in body
    # One appendix row per distinct claim version + field, not per occurrence.
    assert body.count("`audit.s1.error_rate@v1`") == 1
    assert len(rep.cited) == 3 and rep.missing == [] and rep.flagged == []
    assert sorted(rep.unverified) == ["audit.s1.error_rate", "audit.s1.per_unit"]
    # Citations were recorded in the ledger.
    cits = led.read("citation")
    assert {c["document"] for c in cits} == {"report.md"}
    assert {c["claim"] for c in cits} == {"audit.s1.error_rate@v1", "audit.s1.per_unit@v1"}


def test_document_without_evidence_block_is_untouched(env, led):
    body = "Rate {{claim:s1.error_rate}} stays literal.\n"
    out, rep = run(env, "---\ntitle: T\n---\n" + body)
    assert out == body and rep is None


def test_policy_off_substitutes_without_appendix_or_report(env, led):
    body, rep = run(env, doc("off", "x {{claim:s1.error_rate}} y {{claim:nope}}\n"))
    assert body == "x 0.1872 y ⟦missing: nope⟧\n"
    assert rep.uncited_numbers == 0 and led.read("citation") == []


# -- policy table ------------------------------------------------------------

def test_warn_renders_missing_marker_and_flags_drift(env, led):
    (env / "s1.csv").write_text("changed\n")
    from app.utils.evidence.verify import verify_claim
    assert verify_claim(led, f"{NS}.s1.error_rate", "rehash")["outcome"] == "drifted"
    body, rep = run(env, doc("warn", "{{claim:s1.error_rate}} / {{claim:zz.nope}}\n"))
    assert "⚠ 0.1872 / ⟦missing: zz.nope⟧" in body
    assert "⚠ drifted" in body and "| `zz.nope` | ⟦missing⟧ | no such claim" in body
    assert rep.flagged == [f"{NS}.s1.error_rate"] and rep.missing == ["zz.nope"]


def test_strict_refuses_unknown_drifted_and_refuted_naming_all(env, led):
    led.record_verdict(f"{NS}.s1.per_unit", method="rehash", outcome="refuted")
    (env / "s1.csv").write_text("changed\n")
    from app.utils.evidence.verify import verify_claim
    verify_claim(led, f"{NS}.s1.error_rate", "rehash")
    with pytest.raises(CitationError) as e:
        run(env, doc("strict", "{{claim:s1.error_rate}} {{claim:s1.per_unit}} {{claim:nope}}\n"))
    assert e.value.problems == [(f"{NS}.s1.error_rate", "drifted"), (f"{NS}.s1.per_unit", "refuted"),
                                ("nope", "unknown claim")]
    assert isinstance(e.value, ValueError)
    assert led.read("citation") == []        # nothing recorded for a refused export


def test_strict_allows_verified_and_unverified(env, led):
    from app.utils.evidence.verify import verify_claim
    verify_claim(led, f"{NS}.s1.error_rate", "rehash")
    body, rep = run(env, doc("strict", "{{claim:s1.error_rate|fmt=.1%}} {{claim:s1.per_unit}}\n"))
    assert body.startswith("18.7% 512.75\n")
    assert "| verified |" in body and "| unverified |" in body


def test_superseded_pin_strict_keeps_pin_warn_uses_latest(env, led):
    oid = led.observation(led.claim(f"{NS}.s1.error_rate")["derivation"]["obs_ids"][0])["id"]
    led.record_claim("s1.error_rate", 0.25, namespace=NS, derivation={"obs_ids": [oid]})
    body, rep = run(env, doc("strict", "{{claim:s1.error_rate@v1}} {{claim:s1.error_rate}}\n"))
    assert body.startswith("0.1872 0.25\n") and "pinned v1, latest v2" in body
    body, rep = run(env, doc("warn", "{{claim:s1.error_rate@v1}}\n"))
    assert body.startswith("0.25\n") and "pin v1 superseded; shows v2" in body
    assert rep.superseded_pins == [f"{NS}.s1.error_rate"]
    # Strict fails on a pinned version only when THAT version is refuted.
    led.record_verdict(f"{NS}.s1.error_rate@v1", method="rehash", outcome="refuted")
    with pytest.raises(CitationError):
        run(env, doc("strict", "{{claim:s1.error_rate@v1}}\n"))
    run(env, doc("strict", "{{claim:s1.error_rate}}\n"))   # latest version still fine


# -- uncited-number lint -----------------------------------------------------

def test_lint_counts_hand_typed_numbers_not_citations_or_code(env, led):
    text = doc("warn", "## 2. Results\n\n1. first item\n"
                       "Error rate {{claim:s1.error_rate|fmt=.2%}} vs 19.5% typed, 250,000 host-s.\n"
                       "See ds_0123 and v3 and 2026-10-02.\n```\n99 100\n```\n")
    _, rep = run(env, text)
    assert rep.uncited_numbers == 2
    assert [s["text"] for s in rep.uncited_samples] == ["19.5%", "250,000"]


# -- templating non-collision ------------------------------------------------

def test_task_templating_leaves_claim_citations_alone():
    from app.agents.task_templating import IterationBindings, list_placeholders, render
    t = "Report {{claim:a.b|fmt=.2%}} and {{claim:ns.x@v2.distribution.median}} at {{index}}."
    out = render(t, IterationBindings(index=3))
    assert out == t.replace("{{index}}", "3")
    assert list_placeholders(t) == ["index"]
    # A dotted form WOULD be claimed by templating as an unknown head; that
    # is why the syntax uses a colon.
    assert list_placeholders("{{claim.x}}") == ["claim.x"]


# -- export seam -------------------------------------------------------------

class _FakeSession:
    def __init__(self):
        self.calls = []

    async def capture_pdf(self, payload, **kwargs):
        self.calls.append(payload)
        return b"%PDF-fake"


def _patch_session(monkeypatch):
    import app.services.pdf_exporter as pe
    fake = _FakeSession()

    async def get(port=6969):
        return fake
    monkeypatch.setattr(pe, "get_render_session", get)
    return fake


def test_export_document_pdf_carries_values_appendix_and_report(env, led, monkeypatch):
    import app.services.pdf_exporter as pe
    fake = _patch_session(monkeypatch)
    pdf, meta = asyncio.run(pe.export_document_pdf(
        markdown=doc("warn", "Error rate {{claim:s1.error_rate|fmt=.2%}} on 1,200 hosts.\n"),
        model="m", provider="p"))
    assert pdf == b"%PDF-fake"
    sections = fake.calls[0]["sections"]
    text = "\n".join(sections)
    assert "Error rate 18.72% on 1,200 hosts." in text and "## Evidence" in text
    assert meta["evidence"]["citations"] == 1 and meta["evidence"]["uncited_numbers"] == 1
    # Positive control for the next test: a plain document exports with no report.
    _, meta2 = asyncio.run(pe.export_document_pdf(markdown="---\ntitle: T\n---\nhello\n",
                                                 model="m", provider="p"))
    assert meta2["evidence"] is None


def test_route_returns_400_naming_ids_under_strict(env, led, monkeypatch):
    _patch_session(monkeypatch)
    from app.routes.export_routes import router
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    led.record_verdict(f"{NS}.s1.per_unit", method="rehash", outcome="refuted")
    r = c.post("/api/export/document", json={
        "markdown": doc("strict", "{{claim:s1.per_unit}} {{claim:nope}}\n")})
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["evidence_problems"] == [{"claim": f"{NS}.s1.per_unit", "reason": "refuted"},
                                         {"claim": "nope", "reason": "unknown claim"}]
    assert f"{NS}.s1.per_unit" in body["error"]
    # Positive control: the verified claim exports, with the report in a header.
    r = c.post("/api/export/document", json={
        "markdown": doc("strict", "{{claim:s1.error_rate|fmt=.2%}}\n")})
    assert r.status_code == 200, r.text
    rep = json.loads(r.headers["X-Ziya-Evidence"])
    assert rep["citations"] == 1 and rep["policy"] == "strict"
