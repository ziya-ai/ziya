"""Credential preflight for unattended work (app/utils/credential_preflight.py)
and the two seams that consume it: ``run_driver._preflight`` at launch and
``block_executor._lane_preflight`` before every fan-out lane.

Run 5f83000a (2026-11-05): credentials died at 03:00; lanes kept being
dispatched into the dead endpoint until 06:00.  The launch preflight that
existed was bedrock-only and ran once; nothing checked between lanes.  The
check is now per endpoint (auth provider for bedrock, key presence for
API-key endpoints, nothing for local), cached, invalidated on an observed
auth fault, and consulted before each lane -- so a lane that cannot run is
refused with the same ``authentication_error`` fault an observed failure
produces, and the run holds at that lane without spending model turns.
"""
import asyncio
import time
from pathlib import Path

import pytest

from app.utils import credential_preflight as cp
# Imported at module scope so any import-time environment writes (the .env
# load that app.agents.models used to perform; see
# tests/test_models_import_no_env_side_effect.py) happen before the fixture's
# delenv rather than after it.
import app.agents.block_executor  # noqa: F401
import app.agents.run_driver  # noqa: F401


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    cp.reset_for_tests()
    monkeypatch.delenv(cp.SKIP_ENV, raising=False)
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "GOOGLE_API_KEY",
              "GOOGLE_APPLICATION_CREDENTIALS", "ZAI_API_KEY", "ZHIPUAI_API_KEY", "ZAI_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    yield
    cp.reset_for_tests()


# ------------------------------------------------------------- per endpoint

def test_api_key_endpoints_check_presence_not_validity(monkeypatch):
    ok, msg = cp.check_endpoint("anthropic")
    assert not ok and "ANTHROPIC_API_KEY" in msg
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    cp.invalidate("anthropic")
    assert cp.check_endpoint("anthropic")[0]
    # Aliases from the registry count (zai accepts three names).
    monkeypatch.setenv("ZAI_API_TOKEN", "t")
    assert cp.check_endpoint("zai")[0]
    # OpenAI-compatible local server: a base URL is the credential.
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8000/v1")
    assert cp.check_endpoint("openai")[0]


def test_google_accepts_application_default_credentials(monkeypatch, tmp_path):
    assert not cp.check_endpoint("google")[0]
    adc = tmp_path / "adc.json"
    adc.write_text("{}")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(adc))
    cp.invalidate()
    assert cp.check_endpoint("google")[0]


def test_local_and_unknown_endpoints_never_block():
    assert cp.check_endpoint("local")[0]
    assert cp.check_endpoint("some-future-endpoint")[0]


def test_no_initialised_endpoint_is_not_verified_and_never_probes(monkeypatch):
    """A process with no model state (tests, a tool-only card) must not be
    guessed to be on bedrock and sent to STS on every lane."""
    monkeypatch.setattr("app.agents.models.ModelManager.get_state", classmethod(lambda cls: {}))
    monkeypatch.delenv("ZIYA_ENDPOINT", raising=False)
    monkeypatch.setattr(cp, "_check_uncached", lambda ep: (_ for _ in ()).throw(AssertionError("probed")))
    assert cp.current_endpoint() is None
    ok, msg = cp.check_endpoint()
    assert ok and "not verified" in msg
    assert cp._cache == {}


def test_bedrock_routes_to_the_active_auth_provider(monkeypatch):
    class _Prov:
        def check_credentials(self, profile_name=None, region=None):
            return False, "expired"

        def get_credential_help_message(self, ctx=None):
            return "Renew with your organisation's login tool."
    monkeypatch.setattr("app.plugins.get_active_auth_provider", lambda: _Prov())
    ok, msg = cp.check_endpoint("bedrock")
    assert not ok and "login tool" in msg          # the provider's words, not AWS-specific text
    # The default (community) provider's message carries no vendor-internal tool name.
    from app.plugins.default_providers import DefaultAuthProvider
    assert "mwinit" not in DefaultAuthProvider().get_credential_help_message()


def test_check_that_crashes_does_not_block(monkeypatch):
    def _boom():
        raise RuntimeError("no plugins")
    monkeypatch.setattr("app.plugins.get_active_auth_provider", _boom)
    ok, msg = cp.check_endpoint("bedrock")
    assert ok and "not verified" in msg


# ---------------------------------------------------------- cache & skip

def test_cached_until_ttl_force_or_invalidate(monkeypatch):
    calls = []

    def fake(ep):
        calls.append(ep)
        return True, "ok"
    monkeypatch.setattr(cp, "_check_uncached", fake)
    cp.check_endpoint("anthropic"); cp.check_endpoint("anthropic")
    assert calls == ["anthropic"]
    cp.check_endpoint("anthropic", force=True)
    assert len(calls) == 2
    cp.invalidate("anthropic")
    cp.check_endpoint("anthropic")
    assert len(calls) == 3
    cp.check_endpoint("anthropic", ttl_s=0)
    assert len(calls) == 4


def test_skip_env_disables_every_check(monkeypatch):
    monkeypatch.setenv(cp.SKIP_ENV, "1")
    ok, msg = cp.check_endpoint("anthropic")
    assert ok and cp.SKIP_ENV in msg


@pytest.mark.asyncio
async def test_async_variant_serves_the_cache_without_a_thread(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert (await cp.check_endpoint_async("anthropic"))[0]
    called = []
    monkeypatch.setattr(asyncio, "to_thread", lambda *a, **k: called.append(a) or (_ for _ in ()).throw(AssertionError("thread")))
    assert (await cp.check_endpoint_async("anthropic"))[0]
    assert not called


# ------------------------------------------------- seam: fan-out lanes

def _ctx(tmp_path):
    from app.agents.block_executor import ExecutionContext
    return ExecutionContext(run_id="r-pre", project_id="p1", project_root=str(tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize("parallel", [True, False], ids=["parallel", "serial"])
async def test_fanout_holds_before_any_lane_runs_when_credentials_are_bad(tmp_path, monkeypatch, parallel):
    from app.agents.block_executor import execute_block
    from app.agents.task_executor import TaskInfraError
    from app.models.task_card import Block
    monkeypatch.setattr("app.agents.models.ModelManager.get_state",
                        classmethod(lambda cls: {"endpoint": "anthropic"}))
    ran = []

    async def fake(block, **kw):
        ran.append(block.id)
    monkeypatch.setattr("app.agents.block_executor.execute_task_block", fake)
    blk = Block(id="rep", block_type="repeat", name="r", repeat_mode="count", repeat_count=4,
                repeat_parallel=parallel, repeat_propagate="none",
                body=[Block(id="rep-t", block_type="task", name="w", instructions="go")])
    ctx = _ctx(tmp_path)
    t0 = time.time()
    with pytest.raises(TaskInfraError) as ei:
        await execute_block(blk, ctx)
    assert ei.value.infra_kind == "authentication_error"
    assert "ANTHROPIC_API_KEY" in str(ei.value)
    assert ran == [], "a lane ran against a credential the preflight already knew was missing"
    assert ctx.infra_faults and ctx.infra_faults[0].kind == "authentication_error"
    # Evaluated here: four lanes refused in the same tick may all finish
    # before the watcher's first poll, so the latch is not guaranteed yet.
    assert ctx.infra_gate_closed()
    assert time.time() - t0 < 5


@pytest.mark.asyncio
async def test_parallel_block_children_are_gated_too(tmp_path, monkeypatch):
    from app.agents.block_executor import execute_block
    from app.agents.task_executor import TaskInfraError
    from app.models.task_card import Block
    monkeypatch.setattr("app.agents.models.ModelManager.get_state",
                        classmethod(lambda cls: {"endpoint": "openai"}))
    ran = []

    async def fake(block, **kw):
        ran.append(block.id)
    monkeypatch.setattr("app.agents.block_executor.execute_task_block", fake)
    blk = Block(id="par", block_type="parallel", name="p",
                body=[Block(id=f"c-{i}", block_type="task", name=f"c{i}", instructions="go") for i in range(3)])
    with pytest.raises(TaskInfraError):
        await execute_block(blk, _ctx(tmp_path))
    assert ran == []


@pytest.mark.asyncio
async def test_lanes_run_when_credentials_are_present_and_an_auth_fault_invalidates_the_cache(tmp_path, monkeypatch):
    """Positive control, plus the seam from record_infra_fault back to the cache."""
    from app.agents.block_executor import execute_block
    from app.agents.task_executor import TaskInfraError
    from app.models.task_card import Artifact, Block
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr("app.agents.models.ModelManager.get_state",
                        classmethod(lambda cls: {"endpoint": "anthropic"}))
    seen = []

    async def fake(block, **kw):
        seen.append(block.id)
        return Artifact(summary="ok")
    monkeypatch.setattr("app.agents.block_executor.execute_task_block", fake)
    blk = Block(id="rep", block_type="repeat", name="r", repeat_mode="count", repeat_count=2,
                repeat_parallel=False, repeat_propagate="none",
                body=[Block(id="rep-t", block_type="task", name="w", instructions="go")])
    ctx = _ctx(tmp_path)
    await execute_block(blk, ctx)
    assert len(seen) == 2
    assert "anthropic" in cp._cache
    ctx.record_infra_fault(TaskInfraError("x", infra_kind="authentication_error", block_id="b"))
    assert "anthropic" not in cp._cache


# ------------------------------------------------- seam: launch preflight

@pytest.mark.asyncio
async def test_launch_preflight_holds_non_bedrock_runs_with_missing_keys(monkeypatch):
    from app.agents import run_driver
    monkeypatch.setattr("app.agents.models.ModelManager.get_state",
                        classmethod(lambda cls: {"endpoint": "anthropic"}))
    held = {}

    class _Storage:
        def mark_held(self, run_id, reason, block_id, error):
            held.update(run_id=run_id, reason=reason, error=error)
    pushed = []

    async def _push(run_id, ev):
        pushed.append(ev)
    monkeypatch.setattr(run_driver._relay, "safe_push", _push)
    launch = type("L", (), {})()
    launch.run_id, launch.run_storage, launch.label = "run-x", _Storage(), "card"
    launch.root = type("B", (), {"id": "root"})()
    assert await run_driver._preflight(launch) is False
    assert held["reason"] == "authentication_error" and "ANTHROPIC_API_KEY" in held["error"]
    assert pushed and pushed[0]["status"] == "held"
    # And a local-model run is never held by a credential check.
    monkeypatch.setattr("app.agents.models.ModelManager.get_state",
                        classmethod(lambda cls: {"endpoint": "local"}))
    assert await run_driver._preflight(launch) is True


def test_no_vendor_internal_tool_names_in_core_preflight_text():
    """The core must not tell a community user to run an enterprise login
    tool; that text belongs to the auth plugin that knows it."""
    for path in ("app/utils/credential_preflight.py", "app/agents/run_driver.py"):
        src = Path(path).read_text()
        assert "mwinit" not in src and "Midway" not in src, path
