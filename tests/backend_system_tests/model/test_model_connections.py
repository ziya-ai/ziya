"""
Every configured model and alias resolves through ModelManager, offline.

Replaces a module whose tests all patched ``ModelManager.__init__``, set
``_model`` / ``_endpoint`` by hand and asserted them back -- tautologies
that could not fail -- and depended on fixtures (``mock_aws_credentials``,
``setup_test_environment``, ``mock_bedrock_client``) no conftest has
defined since refactor 01c97e40, so every one of them errored.  It also
indexed ``MODEL_CONFIGS["bedrock"]["sonnet"]`` at import, which raised
KeyError once that entry became an alias and broke collection.

These go through the real ``get_model_config`` (pure config resolution, no
client is built, no credentials needed).  Calls that hit a provider live
in test_model_transactions.py.
"""
import pytest

from app.agents.models import ModelManager
from app.config.models_config import MODEL_ALIASES, MODEL_CONFIGS

MODELS = [(ep, m) for ep, ms in MODEL_CONFIGS.items() for m in ms]
ALIASES = [(ep, a, t) for ep, al in MODEL_ALIASES.items() for a, t in al.items()]


def test_the_catalog_is_not_empty():
    # Positive control: the parametrized tests below are not vacuous.
    assert len(MODELS) > 50 and len(ALIASES) > 10
    assert ("bedrock", "sonnet") in {(ep, a) for ep, a, _ in ALIASES}


@pytest.mark.parametrize("endpoint,model", MODELS)
def test_every_model_resolves_to_a_model_id(endpoint, model):
    cfg = ModelManager.get_model_config(endpoint, model)
    assert cfg.get("model_id"), f"{endpoint}/{model} resolved without a model_id"


@pytest.mark.parametrize("endpoint,alias,target", ALIASES)
def test_every_alias_resolves_to_its_target(endpoint, alias, target):
    assert target in MODEL_CONFIGS[endpoint], f"alias {alias!r} names missing {target!r}"
    got = ModelManager.get_model_config(endpoint, alias)
    want = ModelManager.get_model_config(endpoint, target)
    assert got["model_id"] == want["model_id"]


@pytest.mark.parametrize("endpoint", sorted(MODEL_CONFIGS))
def test_each_endpoint_has_a_resolvable_default(endpoint):
    assert ModelManager.get_model_config(endpoint, None).get("model_id")


def test_an_unknown_endpoint_is_refused_by_name():
    with pytest.raises(ValueError, match="Invalid endpoint: 'nope'"):
        ModelManager.get_model_config("nope", "x")
