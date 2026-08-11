"""
Tests for cross-endpoint /api/set-model resolution
(_set_model_sync in app/routes/model_routes.py).

The endpoint pulldown in the model config modal makes a cross-endpoint model
change the NORMAL case, which exposed three latent bugs in the alias search:

  1. ``found_endpoint`` was assigned only on the alias-match and dict-match
     paths.  The three model_id-match paths (Cases 2-4) left it None, so
     ``os.environ["ZIYA_ENDPOINT"] = found_endpoint`` wrote None.
  2. The inner ``break`` only exited the model loop.  With no outer break,
     the scan continued into later endpoints and could overwrite a match.
  3. Post-match lookups used the caller's stale ``endpoint`` rather than the
     model's own ``found_endpoint``, so a bedrock -> google change raised
     KeyError on ``MODEL_CONFIGS[endpoint][found_alias]``.

These tests exercise the resolution/verification logic without performing a
real model change: the search runs before any initialization, and
``initialize_model`` is stubbed so the assertions are about which endpoint
and alias were resolved.
"""
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.routes.model_routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# Two endpoints, distinct aliases and distinct model_ids -- mirrors the real
# config, where aliases are globally unique across endpoints.
FAKE_CONFIGS = {
    "bedrock": {
        "b-model": {"model_id": "vendor.b-model-v1"},
    },
    "google": {
        "g-model": {"model_id": "models/g-model-001"},
    },
}


def _mock_manager():
    """A ModelManager stub whose model change always 'succeeds'.

    get_model_id echoes the configured id so the route's own
    expected-vs-actual verification passes, letting the test assert on which
    endpoint/alias were resolved.
    """
    mm = MagicMock()
    mm.MODEL_CONFIGS = FAKE_CONFIGS
    mm.DEFAULT_MODELS = {"bedrock": "b-model", "google": "g-model"}
    mm._state = {"aws_region": "us-west-2"}

    def get_model_config(endpoint, alias=None):
        # KeyError here is exactly bug #3: looking a model up on the wrong
        # endpoint.  Surface it as a hard failure rather than a fallback.
        return dict(FAKE_CONFIGS[endpoint][alias])

    mm.get_model_config.side_effect = get_model_config

    def get_model_id(_model=None):
        alias = os.environ.get("ZIYA_MODEL")
        ep = os.environ.get("ZIYA_ENDPOINT")
        return FAKE_CONFIGS[ep][alias]["model_id"]

    mm.get_model_id.side_effect = get_model_id
    mm.initialize_model.return_value = MagicMock()
    return mm


def _change_model(client, model_id, start_endpoint="bedrock",
                  start_model="b-model"):
    """POST /api/set-model with model init and agent wiring stubbed out."""
    mm = _mock_manager()
    env = {"ZIYA_ENDPOINT": start_endpoint, "ZIYA_MODEL": start_model,
           "ZIYA_ALLOW_ALL_ENDPOINTS": "1"}
    with patch('app.routes.model_routes.ModelManager', mm), \
         patch.dict(os.environ, env, clear=False), \
         patch('app.agents.agent.model'), \
         patch('app.agents.agent.create_agent_chain'), \
         patch('app.agents.agent.create_agent_executor'):
        resp = client.post('/api/set-model', json={'model_id': model_id})
    return resp, mm


# ---- Cross-endpoint change by alias ----

def test_cross_endpoint_alias_sets_target_endpoint(client):
    """Changing to a google alias while bedrock runs must switch ZIYA_ENDPOINT.

    Guards bugs #1 and #3: a None or stale endpoint yields a 500 rather than
    a successful change.
    """
    resp, _ = _change_model(client, 'g-model')
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data['status'] == 'success'
    assert data['model'] == 'g-model'
    assert data['changed'] is True


def test_cross_endpoint_change_reports_display_name(client):
    """The response reads MODEL_CONFIGS[found_endpoint][alias].

    With the stale-endpoint bug this raised KeyError -> 500.
    """
    resp, _ = _change_model(client, 'g-model')
    assert resp.status_code == 200, resp.text
    assert data_key(resp, 'model_display_name') == 'g-model'


def data_key(resp, key):
    return resp.json()[key]


def test_same_endpoint_change_still_works(client):
    """Backward compatibility: a same-endpoint change is unaffected."""
    resp, _ = _change_model(client, 'b-model', start_endpoint='bedrock',
                            start_model='g-model')
    assert resp.status_code == 200, resp.text
    assert resp.json()['model'] == 'b-model'


def test_no_op_change_returns_changed_false(client):
    """Selecting the already-active model must short-circuit."""
    resp, mm = _change_model(client, 'b-model', start_model='b-model')
    assert resp.status_code == 200, resp.text
    assert resp.json()['changed'] is False
    mm.initialize_model.assert_not_called()


# ---- Resolution by model_id (Cases 2-4) ----

def test_cross_endpoint_resolution_by_model_id_string(client):
    """A raw model_id string must resolve to its OWN endpoint.

    Case 2 (direct string comparison) previously matched the alias but never
    set found_endpoint -- the specific omission behind bug #1.
    """
    resp, _ = _change_model(client, 'models/g-model-001')
    assert resp.status_code == 200, resp.text
    assert resp.json()['model'] == 'g-model'


def test_same_endpoint_resolution_by_model_id_string(client):
    resp, _ = _change_model(client, 'vendor.b-model-v1', start_model='g-model')
    assert resp.status_code == 200, resp.text
    assert resp.json()['model'] == 'b-model'


# ---- Search correctness ----

def test_match_is_not_overwritten_by_later_endpoints(client):
    """A first-endpoint match must survive the rest of the scan.

    Guards bug #2: with only an inner break, iteration continued into later
    endpoints and could clobber found_alias/found_endpoint.
    """
    resp, _ = _change_model(client, 'b-model', start_model='g-model')
    assert resp.status_code == 200, resp.text
    assert resp.json()['model'] == 'b-model', (
        "resolution drifted to another endpoint's model -- the outer loop "
        "did not stop at the match"
    )


def test_unknown_model_is_rejected(client):
    resp, _ = _change_model(client, 'not-a-real-model')
    assert resp.status_code == 400
    assert 'Invalid model identifier' in resp.text


def test_empty_model_id_is_rejected(client):
    resp, _ = _change_model(client, '')
    assert resp.status_code == 400


# ---- Policy / allowlist gates still apply ----

def test_forbidden_endpoint_is_rejected(client):
    """The enterprise clamp must still gate a model change."""
    mm = _mock_manager()
    env = {"ZIYA_ENDPOINT": "bedrock", "ZIYA_MODEL": "b-model"}
    with patch('app.routes.model_routes.ModelManager', mm), \
         patch.dict(os.environ, env, clear=False):
        os.environ.pop("ZIYA_ALLOW_ALL_ENDPOINTS", None)
        with patch('app.plugins.get_allowed_endpoints', return_value=['google']):
            resp = client.post('/api/set-model', json={'model_id': 'g-model'})
    assert resp.status_code == 403
    assert 'not permitted' in resp.text


def test_model_not_in_user_allowlist_is_rejected(client):
    """A cross-endpoint change must not bypass ~/.ziya/models.json."""
    mm = _mock_manager()
    env = {"ZIYA_ENDPOINT": "bedrock", "ZIYA_MODEL": "b-model",
           "ZIYA_ALLOW_ALL_ENDPOINTS": "1"}
    with patch('app.routes.model_routes.ModelManager', mm), \
         patch.dict(os.environ, env, clear=False), \
         patch('app.config.models_config.get_user_allowed_models',
               return_value=['b-model']):
        resp = client.post('/api/set-model', json={'model_id': 'g-model'})
    assert resp.status_code == 403
    assert 'allowed model list' in resp.text
