"""boto3 client construction must not run on the event loop.

The stall watchdog caught the loop twice on 2026-10-10, both times inside
``botocore.loaders.list_available_services`` (an ``os.path.isdir`` /
``isfile`` scan of the data directories) reached from ``session.client``:

  * ``app/api/task_cards.py`` ``_run`` -> ``check_aws_credentials`` (launch
    preflight; also an STS round trip, ~10 s cold);
  * ``app/services/model_resolver.py`` ``_call_bedrock`` -> ``boto3.Session``
    + ``session.client`` (memory triage, extraction, every service call).

Only the ``converse`` call itself used to go through ``asyncio.to_thread``.
"""
import ast
import asyncio
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import pytest


@pytest.fixture(autouse=True)
def _fresh_service_clients():
    from app.services import model_resolver as mr
    clear = getattr(mr, "clear_service_client_cache", lambda: None)
    clear()
    yield
    clear()


def test_call_bedrock_builds_session_and_client_off_the_loop(monkeypatch):
    import boto3
    from app.services import model_resolver as mr
    from app.utils import service_tier as st

    seen = {}

    class _Client:
        def converse(self, **kw):
            seen["converse"] = threading.current_thread()
            return {"output": {"message": {"content": [{"text": "ok"}]}}}

    class _Session:
        def __init__(self, *a, **kw):
            seen["session"] = threading.current_thread()

        def client(self, *a, **kw):
            seen["client"] = threading.current_thread()
            return _Client()

    monkeypatch.setattr(boto3, "Session", _Session)
    monkeypatch.setattr(st, "converse_kwargs", lambda _mid: {})
    loop = asyncio.new_event_loop()
    try:
        out = loop.run_until_complete(mr._call_bedrock(
            {"model_id": "m", "region": "us-east-1"}, "s", "u", 10, 0.2))
    finally:
        loop.close()
    assert out == "ok"
    assert set(seen) == {"session", "client", "converse"}, seen
    for k, t in seen.items():
        assert t is not threading.main_thread(), f"{k} ran on the event loop"


# ── service-client cache: keyed per (profile, region), refreshed on auth ────

class _ExpiredTokenException(Exception):
    pass


_ExpiredTokenException.__name__ = "ExpiredTokenException"


def _install_fake_boto(monkeypatch, fail_first_n=0, error=None):
    """Fake boto3.Session; records each construction's (profile, region)."""
    import boto3
    from app.utils import service_tier as st
    made = []
    state = {"fails": fail_first_n}

    class _Client:
        def __init__(self, tag):
            self.tag = tag

        def converse(self, **kw):
            if state["fails"] > 0:
                state["fails"] -= 1
                raise error
            return {"output": {"message": {"content": [{"text": f"ok:{self.tag}"}]}}}

    class _Session:
        def __init__(self, profile_name=None, **kw):
            self.profile = profile_name

        def client(self, service, region_name=None, **kw):
            made.append((self.profile, region_name))
            return _Client(len(made))

    monkeypatch.setattr(boto3, "Session", _Session)
    monkeypatch.setattr(st, "converse_kwargs", lambda _mid: {})
    return made


def _call(region="us-east-1", model="m"):
    from app.services import model_resolver as mr
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(mr._call_bedrock(
            {"model_id": model, "region": region}, "s", "u", 10, 0.2))
    finally:
        loop.close()


def test_same_profile_and_region_reuse_one_client_across_models(monkeypatch):
    monkeypatch.setenv("ZIYA_AWS_PROFILE", "p1")
    made = _install_fake_boto(monkeypatch)
    assert _call(model="a") == "ok:1"
    assert _call(model="b") == "ok:1"
    assert made == [("p1", "us-east-1")]


def test_different_region_or_profile_gets_its_own_client(monkeypatch):
    monkeypatch.setenv("ZIYA_AWS_PROFILE", "p1")
    made = _install_fake_boto(monkeypatch)
    _call(region="us-east-1")
    _call(region="us-west-2")
    monkeypatch.setenv("ZIYA_AWS_PROFILE", "p2")
    _call(region="us-east-1")
    assert made == [("p1", "us-east-1"), ("p1", "us-west-2"), ("p2", "us-east-1")]


def test_concurrent_calls_build_one_client(monkeypatch):
    from app.services import model_resolver as mr
    monkeypatch.setenv("ZIYA_AWS_PROFILE", "p1")
    made = _install_fake_boto(monkeypatch)

    async def many():
        return await asyncio.gather(*[
            mr._call_bedrock({"model_id": f"m{i}", "region": "us-east-1"}, "s", "u", 10, 0.2)
            for i in range(8)])

    loop = asyncio.new_event_loop()
    try:
        out = loop.run_until_complete(many())
    finally:
        loop.close()
    assert out == ["ok:1"] * 8
    assert made == [("p1", "us-east-1")]


def test_auth_failure_rebuilds_the_client_once_and_succeeds(monkeypatch):
    """A refreshed ~/.aws/credentials must be picked up: the cached client
    is dropped and rebuilt from a fresh Session."""
    monkeypatch.setenv("ZIYA_AWS_PROFILE", "p1")
    err = _ExpiredTokenException("The security token included in the request is expired")
    made = _install_fake_boto(monkeypatch, fail_first_n=1, error=err)
    assert _call() == "ok:2"
    assert made == [("p1", "us-east-1"), ("p1", "us-east-1")]
    # The rebuilt client is what is cached now.
    assert _call() == "ok:2" and len(made) == 2


def test_non_auth_failure_does_not_rebuild(monkeypatch):
    monkeypatch.setenv("ZIYA_AWS_PROFILE", "p1")
    made = _install_fake_boto(monkeypatch, fail_first_n=1,
                              error=ValueError("ValidationException: bad input"))
    with pytest.raises(ValueError):
        _call()
    assert made == [("p1", "us-east-1")]


def test_launch_preflight_offloads_check_aws_credentials():
    tree = ast.parse((ROOT / "app/api/task_cards.py").read_text())
    runs = [n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_run"]
    assert runs, "async _run not found in app/api/task_cards.py -- renamed?"
    inline, offloaded = [], []

    def visit(node, off):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            o = off
            if isinstance(ch, ast.Call):
                f = ch.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                if name in ("to_thread", "run_in_executor"):
                    o = True
                    if any(getattr(a, "id", None) == "check_aws_credentials" for a in ch.args):
                        offloaded.append(ch.lineno)
                elif name == "check_aws_credentials" and not off:
                    inline.append(ch.lineno)
            visit(ch, o)

    for fn in runs:
        visit(fn, False)
    assert not inline, f"check_aws_credentials called on the event loop at L{inline}"
    assert offloaded, "preflight no longer checks credentials at all"
