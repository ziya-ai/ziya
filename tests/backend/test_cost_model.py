"""
Cost model: catalog + rate plans + pricer + ledger + meter.

Runs against a temp ZIYA_HOME so no user ledger or pricing.json is touched.
Every test asserts on the OUTERMOST surface it can reach (rollup output,
ledger rows) rather than on intermediates it built itself.
"""
import json
import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("isolated_home")


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    # Same path the suite-wide autouse ``_isolate_ziya_home`` (tests/conftest.py)
    # creates, so it may already exist; this fixture adds the cost-model
    # cache resets on top of that sandbox rather than competing with it.
    home = tmp_path / "ziya_home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("ZIYA_HOME", str(home))
    from app.config import pricing
    from app.storage import usage_ledger
    from app.cost import meter
    pricing.reset_catalog_cache()
    usage_ledger.reset_ledger_for_tests()
    meter.reset_for_tests()
    yield home
    pricing.reset_catalog_cache()
    usage_ledger.reset_ledger_for_tests()
    meter.reset_for_tests()


def _rec(**kw):
    from app.storage.usage_ledger import UsageRecord
    base = dict(id="r1", ts=time.time(), provider="anthropic",
                model_id="claude-sonnet-4-5-20250929", region=None,
                tier="standard", status="actual", dims={"input": 100_000, "output": 10_000})
    base.update(kw)
    return UsageRecord(**base)


# ── catalog ────────────────────────────────────────────────────────────────

def test_builtin_catalog_entries_are_well_formed():
    from app.config.pricing import BUILTIN_CATALOG, DIMENSIONS, build_catalog
    cat = build_catalog(user_file=Path("/nonexistent/pricing.json"))
    # Every built-in row parsed (a malformed one is dropped with a warning,
    # which would silently shrink coverage).
    assert len([e for e in cat.entries if e.source == "builtin"]) == len(BUILTIN_CATALOG)
    for e in cat.entries:
        assert set(e.unit_prices) <= set(DIMENSIONS) | {"reasoning_additive"}, e
        assert "standard" in e.tier_factors
    assert cat.version.startswith("builtin:")
    assert "+user:" not in cat.version


def test_catalog_lookup_prefers_user_then_region_then_latest(isolated_home):
    from app.config.pricing import build_catalog, pricing_file
    pricing_file().write_text(json.dumps({
        "revision": "test-rev",
        "entries": [{
            "provider": "anthropic", "model_pattern": "claude-sonnet-4-5*",
            "unit_prices": {"input": 1.0, "output": 2.0},
        }],
    }))
    cat = build_catalog()
    e = cat.lookup("anthropic", "claude-sonnet-4-5-20250929", None, time.time())
    assert e is not None and e.source == "user" and e.unit_prices["input"] == 1.0
    assert cat.version == "builtin:%s+user:test-rev" % cat.builtin_revision

    # Dated entries: a superseding entry is used only for records after
    # its effective_from; older records keep the older price.
    from app.config.pricing import build_catalog as bc
    cat2 = bc(user_file=Path("/nonexistent"), builtin=[
        {"provider": "p", "model_pattern": "m", "effective_from": "2020-01-01",
         "unit_prices": {"input": 1.0}},
        {"provider": "p", "model_pattern": "m", "effective_from": "2030-01-01",
         "unit_prices": {"input": 9.0}},
    ])
    now = time.time()
    assert cat2.lookup("p", "m", None, now).unit_prices["input"] == 1.0
    far = time.mktime((2031, 1, 1, 0, 0, 0, 0, 0, 0))
    assert cat2.lookup("p", "m", None, far).unit_prices["input"] == 9.0


def test_malformed_user_file_degrades_to_builtin(isolated_home):
    from app.config.pricing import build_catalog, pricing_file
    pricing_file().write_text("{not json")
    cat = build_catalog()
    assert all(e.source == "builtin" for e in cat.entries)
    assert cat.lookup("anthropic", "claude-haiku-4-5-20251001", None, time.time()) is not None


# ── pricer ─────────────────────────────────────────────────────────────────

def test_public_plan_prices_list_and_never_guesses():
    from app.config.pricing import build_catalog
    from app.cost.pricer import price_record
    from app.cost.rate_plans import PublicPlan
    cat = build_catalog(user_file=Path("/nonexistent"))
    line = price_record(_rec(), cat, PublicPlan())
    # 100k input @ $3/M + 10k output @ $15/M = 0.30 + 0.15.  Kept under the
    # Anthropic card's 200k long-context threshold on purpose: the step-up
    # has its own test below and must not leak into the baseline arithmetic.
    assert line.effective_cost == pytest.approx(0.45)
    assert line.list_cost == pytest.approx(0.45)
    assert line.provenance == "list" and not line.is_partial

    unknown = price_record(_rec(model_id="claude-mythos-5"), cat, PublicPlan())
    assert unknown.effective_cost is None and unknown.provenance == "unknown"
    assert unknown.unpriced_dims == ["input", "output"]


def test_partial_when_a_dimension_is_unlisted():
    from app.config.pricing import build_catalog
    from app.cost.pricer import price_record
    from app.cost.rate_plans import PublicPlan
    cat = build_catalog(user_file=Path("/nonexistent"))
    # Nova has no cache_read rate in the built-in table on purpose.
    line = price_record(_rec(provider="bedrock", model_id="us.amazon.nova-pro-v1:0",
                             dims={"input": 1_000_000, "cache_read": 500_000}), cat, PublicPlan())
    assert line.effective_cost == pytest.approx(0.80)
    assert line.is_partial and line.unpriced_dims == ["cache_read"]


def test_reasoning_not_double_counted_unless_additive():
    from app.config.pricing import build_catalog
    from app.cost.pricer import price_record
    from app.cost.rate_plans import PublicPlan
    cat = build_catalog(user_file=Path("/nonexistent"), builtin=[
        {"provider": "p", "model_pattern": "m", "unit_prices": {"input": 0.0, "output": 10.0}},
        {"provider": "p", "model_pattern": "m-add",
         "unit_prices": {"input": 0.0, "output": 10.0, "reasoning_additive": 1}},
    ])
    dims = {"input": 1, "output": 1_000_000, "reasoning": 1_000_000}
    assert price_record(_rec(provider="p", model_id="m", dims=dims), cat, PublicPlan()).effective_cost == pytest.approx(10.0)
    assert price_record(_rec(provider="p", model_id="m-add", dims=dims), cat, PublicPlan()).effective_cost == pytest.approx(20.0)


def test_long_context_step_up_applies_to_whole_request():
    from app.config.pricing import build_catalog
    from app.cost.pricer import price_record
    from app.cost.rate_plans import PublicPlan
    cat = build_catalog(user_file=Path("/nonexistent"))
    small = price_record(_rec(dims={"input": 100_000, "output": 0}), cat, PublicPlan())
    big = price_record(_rec(dims={"input": 300_000, "output": 0}), cat, PublicPlan())
    assert small.rates_applied["input"] == pytest.approx(3.0) and not small.long_context_applied
    assert big.rates_applied["input"] == pytest.approx(6.0) and big.long_context_applied


def test_tier_without_factor_is_unknown_not_standard():
    from app.config.pricing import build_catalog
    from app.cost.pricer import price_record
    from app.cost.rate_plans import PublicPlan
    cat = build_catalog(user_file=Path("/nonexistent"))
    line = price_record(_rec(tier="priority"), cat, PublicPlan())   # Anthropic card has no priority factor
    assert line.effective_cost is None and line.provenance == "unknown"
    batch = price_record(_rec(tier="batch"), cat, PublicPlan())
    assert batch.effective_cost == pytest.approx(0.225) and batch.tier_factor == 0.5


# ── rate plans ─────────────────────────────────────────────────────────────

def test_discount_table_plan_provenance_and_inheritance():
    from app.config.pricing import build_catalog
    from app.cost.pricer import price_record
    from app.cost.rate_plans import DiscountTablePlan
    cat = build_catalog(user_file=Path("/nonexistent"))
    plan = DiscountTablePlan(
        discounts={"anthropic/claude-sonnet-4-5*": 0.40},
        default_rules=[{"pattern": "anthropic/claude-*", "discount": 0.50}],
        unlisted_discount=0.70,
    )
    dims = {"input": 100_000, "cache_read": 100_000, "output": 0}
    table = price_record(_rec(dims=dims), cat, plan)
    # (0.30 + 0.030) × 0.6 — cache inherits the model's discount.
    assert table.effective_cost == pytest.approx(0.330 * 0.6)
    assert table.list_cost == pytest.approx(0.330)
    assert table.provenance == "custom"

    rule = price_record(_rec(model_id="claude-haiku-4-5-20251001", dims=dims), cat, plan)
    assert rule.effective_cost == pytest.approx((0.10 + 0.010) * 0.5)
    assert rule.provenance == "default_rule"

    unlisted = price_record(_rec(provider="openai", model_id="gpt-4o", dims={"input": 1_000_000}), cat, plan)
    assert unlisted.effective_cost == pytest.approx(2.5 * 0.3)
    assert unlisted.provenance == "default_rule"

    # No list price → still unknown; a discount is not a price.
    none = price_record(_rec(model_id="claude-mythos-5"), cat, plan)
    assert none.effective_cost is None and none.provenance == "unknown"


def test_override_and_zero_plans():
    from app.config.pricing import build_catalog
    from app.cost.pricer import price_record
    from app.cost.rate_plans import OverridePlan, ZeroPlan
    cat = build_catalog(user_file=Path("/nonexistent"))
    ov = OverridePlan(rates=[{"provider": "anthropic", "model_pattern": "claude-sonnet-4-5*",
                              "unit_prices": {"input": 1.0, "output": 1.0}}])
    line = price_record(_rec(), cat, ov)
    assert line.effective_cost == pytest.approx(0.11) and line.provenance == "custom"
    assert line.list_cost == pytest.approx(0.45)  # list still reported for comparison
    # Unmatched falls through to public.
    assert price_record(_rec(model_id="claude-haiku-4-5-20251001"), cat, ov).provenance == "list"

    zero = ZeroPlan(providers=["openai"])
    z = price_record(_rec(provider="openai", model_id="whatever"), cat, zero)
    assert z.effective_cost == 0.0 and z.provenance == "list"
    assert price_record(_rec(), cat, zero).effective_cost == pytest.approx(0.45)


def test_plan_resolution_order(isolated_home):
    from app.config.pricing import build_catalog, pricing_file
    from app.cost.rate_plans import (DiscountTablePlan, PublicPlan, RatePlan,
                                     resolve_active_plan)
    from app.plugins import _rate_plan_providers, register_rate_plan_provider
    from app.plugins.interfaces import RatePlanProvider

    assert isinstance(resolve_active_plan(build_catalog()), PublicPlan)

    pricing_file().write_text(json.dumps({"plan": {"type": "discount_table", "id": "file-plan",
                                                   "discounts": {"a/b": 0.1}}}))
    plan = resolve_active_plan(build_catalog())
    assert isinstance(plan, DiscountTablePlan) and plan.id == "file-plan"

    class _P(RatePlanProvider):
        provider_id = "test"
        def get_rate_plan(self) -> RatePlan:
            return DiscountTablePlan({}, plan_id="plugin-plan")

    saved = list(_rate_plan_providers)
    try:
        register_rate_plan_provider(_P())
        assert resolve_active_plan(build_catalog()).id == "plugin-plan"
    finally:
        _rate_plan_providers[:] = saved


# ── ledger ─────────────────────────────────────────────────────────────────

def test_ledger_estimate_then_actual_and_rollup(isolated_home):
    from app.config.pricing import build_catalog
    from app.cost.rate_plans import PublicPlan
    from app.storage.usage_ledger import get_ledger, ledger_path

    ledger = get_ledger()
    assert ledger_path().parent == isolated_home
    cat = build_catalog(user_file=Path("/nonexistent"))

    rid = ledger.append(_rec(id="", status="estimate", dims={"input": 50_000},
                             project_id="P1", conversation_id="C1"))
    assert ledger.get(rid).status == "estimate"
    assert ledger.actualize(rid, {"input": 100_000, "output": 10_000})
    got = ledger.get(rid)
    assert got.status == "actual" and got.dims == {"input": 100_000, "output": 10_000}
    assert got.actualized_ts is not None
    assert not ledger.actualize("nope", {"input": 1})

    # A never-actualized estimate on another project, and an unpriced model.
    ledger.append(_rec(id="", status="estimate", dims={"input": 200_000},
                       project_id="P2", conversation_id="C2"))
    ledger.append(_rec(id="", model_id="claude-mythos-5", project_id="P1", conversation_id="C1",
                       dims={"input": 10}))

    r = ledger.rollup(by="project", catalog=cat, plan=PublicPlan())
    assert r["catalog_version"] == cat.version and r["record_count"] == 3
    by_key = {g["key"]: g for g in r["groups"]}
    p1, p2 = by_key["P1"], by_key["P2"]
    assert p1["effective_cost"] == pytest.approx(0.45)
    assert p1["records"] == 2 and p1["unknown_records"] == 1 and p1["is_partial"]
    assert p1["has_estimates"] is False and p1["provenance"] == {"list": 1, "unknown": 1}
    assert p2["has_estimates"] is True and p2["effective_cost"] == pytest.approx(0.6)
    assert p2["estimate_dims"] == {"input": 200_000}
    assert r["totals"]["effective_cost"] == pytest.approx(1.05)  # 0.45 + 0.6
    assert r["totals"]["dims"]["input"] == 300_010

    conv = ledger.rollup(by="conversation", conversation_id="C1", catalog=cat, plan=PublicPlan())
    assert [g["key"] for g in conv["groups"]] == ["C1"]

    with pytest.raises(ValueError):
        ledger.rollup(by="bogus")


def test_ledger_survives_reopen(isolated_home):
    from app.storage import usage_ledger
    rid = usage_ledger.get_ledger().append(_rec(id=""))
    usage_ledger.reset_ledger_for_tests()
    assert usage_ledger.get_ledger().get(rid) is not None


# ── meter (executor-facing seam) ───────────────────────────────────────────

def test_meter_open_close_writes_estimate_then_actual(isolated_home):
    from app.cost import meter
    from app.storage.usage_ledger import get_ledger

    class Usage:  # duck-typed IterationUsage
        input_tokens = 1234
        output_tokens = 56
        cache_read_tokens = 7000
        cache_write_tokens = 0

    rid = meter.open_iteration(provider="bedrock", model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                               region="us-west-2", conversation_id="conv", project_root=str(isolated_home / "nope"),
                               iteration=3, estimated_input_tokens=999)
    assert rid
    est = get_ledger().get(rid)
    assert est.status == "estimate" and est.dims == {"input": 999}
    assert est.source == "chat" and est.tier == "standard" and est.iteration == 3
    assert est.project_id is None and est.project_root == str(isolated_home / "nope")

    meter.close_iteration(rid, Usage())
    act = get_ledger().get(rid)
    assert act.status == "actual"
    assert act.dims == {"input": 1234, "output": 56, "cache_read": 7000}

    # Empty usage leaves the estimate in place (a throttled call).
    rid2 = meter.open_iteration(provider="bedrock", model_id="m", region=None, conversation_id=None,
                                project_root=None, iteration=0, estimated_input_tokens=5)
    class Empty:
        input_tokens = output_tokens = cache_read_tokens = cache_write_tokens = 0
    meter.close_iteration(rid2, Empty())
    assert get_ledger().get(rid2).status == "estimate"
    meter.close_iteration(None, Usage())  # tolerated


def test_meter_attribution_from_contextvars(isolated_home):
    from app.context import (reset_task_service_tier, reset_usage_attribution,
                             set_task_service_tier, set_usage_attribution)
    from app.cost import meter
    from app.storage.usage_ledger import get_ledger

    t1 = set_usage_attribution({"source": "task", "run_id": "run-9", "block_id": "blk-2"})
    t2 = set_task_service_tier("flex")
    try:
        rid = meter.open_iteration(provider="bedrock", model_id="m", region=None,
                                   conversation_id="run-9", project_root=None, iteration=0,
                                   estimated_input_tokens=1)
    finally:
        reset_task_service_tier(t2)
        reset_usage_attribution(t1)
    rec = get_ledger().get(rid)
    assert (rec.source, rec.run_id, rec.block_id, rec.tier) == ("task", "run-9", "blk-2", "flex")

    rid_d = meter.open_iteration(provider="bedrock", model_id="m", region=None, conversation_id=None,
                                 project_root=None, iteration=0, estimated_input_tokens=1, is_delegate=True)
    assert get_ledger().get(rid_d).source == "delegate"


def test_meter_resolves_project_id_from_root(isolated_home):
    from app.cost import meter
    from app.models.project import ProjectCreate
    from app.storage.projects import ProjectStorage
    from app.storage.usage_ledger import get_ledger
    root = isolated_home / "proj"
    root.mkdir()
    project = ProjectStorage(isolated_home).create(ProjectCreate(path=str(root), name="p"))
    rid = meter.open_iteration(provider="bedrock", model_id="m", region=None, conversation_id=None,
                               project_root=str(root), iteration=0, estimated_input_tokens=1)
    assert get_ledger().get(rid).project_id == project.id


def test_meter_is_failure_isolated(isolated_home, monkeypatch):
    from app.cost import meter
    from app.storage import usage_ledger
    def boom():
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(usage_ledger, "get_ledger", boom)
    assert meter.open_iteration(provider="p", model_id="m", region=None, conversation_id=None,
                                project_root=None, iteration=0, estimated_input_tokens=1) is None
    meter.close_iteration("x", object())  # must not raise
