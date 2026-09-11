"""
Startup must not build a provider client before --profile has been applied.

Observed on a fresh Internal Edition install:

    ziya --profile kuiper-ziya
    ...
    Initializing Bedrock model
    Using default AWS credentials
    ❌ AWS Profile Error: The profile 'default' could not be found.
    ...
    Using AWS profile: kuiper-ziya        <-- applied AFTER the failure

The chain was: parse_arguments() -> add_common_arguments() initialised the
plugin system to enrich --help text -> an edition's register() imported
app.server -> app.agents.agent ran ``model.bind(...)`` at MODULE level ->
ModelManager.initialize_model() -> with no profile set it wrote
AWS_PROFILE="default" -> boto3 ProfileNotFound.  setup_environment() then set
the real profile, too late for that first client.

Three seams pinned here, each independently sufficient to stop the banner:

  1. add_common_arguments() only initialises plugins when --help was asked.
  2. Importing app.agents.agent does not call ModelManager.initialize_model.
  3. _initialize_bedrock_model() never sets AWS_PROFILE="default" itself.
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
from unittest import mock

import pytest


# --------------------------------------------------------------------------
# 1. Argument parsing does not initialise plugins unless --help is requested
# --------------------------------------------------------------------------

class TestArgparseDoesNotInitPlugins:

    def _parse(self, argv, monkeypatch):
        import app.plugins as plugins
        from app.config.common_args import add_common_arguments
        calls = []
        monkeypatch.setattr(plugins, "initialize", lambda: calls.append("init"))
        monkeypatch.setattr(plugins, "get_allowed_endpoints", lambda: None)
        monkeypatch.delenv("ZIYA_ALLOW_ALL_ENDPOINTS", raising=False)
        monkeypatch.setattr(sys, "argv", ["ziya", *argv])
        parser = argparse.ArgumentParser(add_help=False)
        add_common_arguments(parser)
        return calls

    def test_profile_start_does_not_init_plugins(self, monkeypatch):
        calls = self._parse(["--profile", "kuiper-ziya"], monkeypatch)
        assert calls == [], (
            "REGRESSION: add_common_arguments() initialised the plugin system "
            "during a normal start, before --profile could be applied"
        )

    def test_help_still_consults_plugins_for_endpoint_list(self, monkeypatch):
        # Positive control: the help-text enrichment path still runs.
        calls = self._parse(["--help"], monkeypatch)
        assert calls == ["init"]


# --------------------------------------------------------------------------
# 2. Importing app.agents.agent builds no model
# --------------------------------------------------------------------------

def test_importing_agent_module_does_not_initialize_model(monkeypatch):
    from app.agents.models import ModelManager
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("ZIYA_AWS_PROFILE", raising=False)

    with mock.patch.object(
        ModelManager, "initialize_model",
        side_effect=AssertionError(
            "REGRESSION: importing app.agents.agent called "
            "ModelManager.initialize_model (module-level model.bind)"
        ),
    ) as init:
        sys.modules.pop("app.agents.agent", None)
        mod = importlib.import_module("app.agents.agent")
        assert init.call_count == 0
    # The module still exposes the lazy handle for create_agent_chain().
    assert hasattr(mod, "model")
    assert "llm_with_stop" in ModelManager._state


# --------------------------------------------------------------------------
# 3. No profile -> AWS_PROFILE is left alone, not forced to "default"
# --------------------------------------------------------------------------

def test_bedrock_init_does_not_force_default_profile(monkeypatch):
    from app.agents import models as m
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("ZIYA_AWS_PROFILE", raising=False)

    # Stop the method right after the profile branch: the region lookup is
    # the first thing that runs afterwards, so raising there bounds the test
    # to the seam under review without a network or a real model config.
    sentinel = RuntimeError("stop after profile branch")
    monkeypatch.setattr(
        m, "ziya_env",
        lambda k, *a, **kw: None if k == "ZIYA_AWS_PROFILE" else os.environ.get(k),
    )
    monkeypatch.setattr(m.ModelManager,
                        "_get_region_specific_model_id_with_region_update",
                        classmethod(lambda cls, *a, **k: (_ for _ in ()).throw(sentinel)))

    with pytest.raises(RuntimeError, match="stop after profile branch"):
        m.ModelManager._initialize_bedrock_model({"model_id": "x"}, "x")

    assert os.environ.get("AWS_PROFILE") is None, (
        "REGRESSION: _initialize_bedrock_model wrote AWS_PROFILE='default'; "
        "boto3 then hard-fails on any machine without a [default] section"
    )


def test_bedrock_init_honours_explicit_profile(monkeypatch):
    # Positive control for (3): a real profile IS exported.
    from app.agents import models as m
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("ZIYA_AWS_PROFILE", "kuiper-ziya")
    sentinel = RuntimeError("stop after profile branch")
    monkeypatch.setattr(m.ModelManager,
                        "_get_region_specific_model_id_with_region_update",
                        classmethod(lambda cls, *a, **k: (_ for _ in ()).throw(sentinel)))
    with pytest.raises(RuntimeError):
        m.ModelManager._initialize_bedrock_model({"model_id": "x"}, "x")
    assert os.environ.get("AWS_PROFILE") == "kuiper-ziya"
