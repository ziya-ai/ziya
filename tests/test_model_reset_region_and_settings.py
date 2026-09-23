"""
Regression tests for two follow-ups to the NoRegionError fix:

1. ModelManager._reset_state() used to unconditionally delete AWS_REGION, so a
   user who started with `--region eu-west-1` silently lost that choice on the
   first model switch / settings change. The explicit choice is now recorded in
   ZIYA_AWS_REGION by setup_environment() and restored by _reset_state().
   Model-default regions (no --region) are still cleared so a model switch can
   pick its own preferred region, as before.

2. ModelSettingsMiddleware used to call ModelManager._reset_state() BEFORE the
   /api/model-settings route ran, outside _model_mutation_lock. If the route's
   reinit then failed, the process was left with blank state (and no
   AWS_REGION). The reset now happens inside the locked route handler, only
   when the request actually carried thinking_mode.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestResetStatePreservesExplicitRegion(unittest.TestCase):

    def _reset(self, env):
        from app.agents.models import ModelManager
        with patch.dict(os.environ, env, clear=False):
            for k in ("AWS_REGION", "ZIYA_AWS_REGION"):
                if k not in env:
                    os.environ.pop(k, None)
            ModelManager._reset_state()
            return os.environ.get("AWS_REGION"), ModelManager._state.get("aws_region")

    def test_explicit_region_survives_reset(self):
        aws_region, state_region = self._reset(
            {"AWS_REGION": "eu-west-1", "ZIYA_AWS_REGION": "eu-west-1"})
        self.assertEqual(aws_region, "eu-west-1")
        self.assertEqual(state_region, "eu-west-1")

    def test_explicit_region_restored_even_if_env_was_clobbered(self):
        # A model-preference write may have replaced AWS_REGION in between;
        # the user's explicit choice still wins after a reset.
        aws_region, _ = self._reset(
            {"AWS_REGION": "us-west-2", "ZIYA_AWS_REGION": "eu-west-1"})
        self.assertEqual(aws_region, "eu-west-1")

    def test_default_region_is_still_cleared(self):
        # Positive control for the pre-existing behaviour: without an explicit
        # --region the env var is cleared so a model switch can re-select.
        aws_region, state_region = self._reset({"AWS_REGION": "us-west-2"})
        self.assertIsNone(aws_region)
        self.assertIsNone(state_region)


class TestSetupEnvironmentRecordsExplicitRegion(unittest.TestCase):

    def _run(self, region):
        from app.config.environment import setup_environment
        class _Args:
            # Any flag setup_environment probes but we don't care about
            # reads as None, matching an un-passed argparse option.
            def __getattr__(self, _name):
                return None
        args = _Args()
        args.endpoint = "bedrock"
        args.region = region
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZIYA_AWS_REGION", None)
            os.environ.pop("AWS_REGION", None)
            with patch("app.config.environment.validate_model_and_endpoint",
                       return_value=(True, None, "bedrock")):
                try:
                    setup_environment(args)
                except SystemExit:
                    pass
            return os.environ.get("AWS_REGION"), os.environ.get("ZIYA_AWS_REGION")

    def test_explicit_region_recorded(self):
        aws_region, ziya_region = self._run("eu-west-1")
        self.assertEqual(aws_region, "eu-west-1")
        self.assertEqual(ziya_region, "eu-west-1")

    def test_no_explicit_region_not_recorded(self):
        aws_region, ziya_region = self._run(None)
        self.assertIsNotNone(aws_region)
        self.assertIsNone(ziya_region)


class TestModelSettingsResetOwnership(unittest.TestCase):
    """The reset must live in the locked route, not the middleware."""

    def test_middleware_does_not_reset_state(self):
        import asyncio
        from app.middleware.request_size import ModelSettingsMiddleware
        mw = ModelSettingsMiddleware(MagicMock())
        request = MagicMock()
        request.url.path = "/api/model-settings"
        request.method = "POST"

        async def _json():
            # The middleware's thinking_mode branch is nested under the
            # max_input_tokens check; the frontend always sends both.
            return {"thinking_mode": True, "max_input_tokens": 1000}
        request.json = _json

        async def _next(_req):
            return "resp"

        with patch("app.agents.models.ModelManager._reset_state") as reset, \
             patch("app.agents.models.ModelManager.get_model_config",
                   return_value={"supports_thinking": True}), \
             patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock", "ZIYA_MODEL": "x"}):
            asyncio.run(mw.dispatch(request, _next))
        reset.assert_not_called()

    def test_middleware_normalizes_thinking_mode_without_max_input_tokens(self):
        # The thinking_mode branch used to be nested under the
        # max_input_tokens check, so a body carrying only thinking_mode was
        # silently skipped. It must be normalized on its own.
        import asyncio
        from app.middleware.request_size import ModelSettingsMiddleware
        mw = ModelSettingsMiddleware(MagicMock())
        request = MagicMock()
        request.url.path = "/api/model-settings"
        request.method = "POST"

        async def _json():
            return {"thinking_mode": True}
        request.json = _json

        async def _next(_req):
            return "resp"

        with patch("app.agents.models.ModelManager.get_model_config",
                   return_value={"supports_thinking": False}), \
             patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock", "ZIYA_MODEL": "x",
                                     "ZIYA_THINKING_MODE": "1"}):
            asyncio.run(mw.dispatch(request, _next))
            self.assertEqual(os.environ.get("ZIYA_THINKING_MODE"), "0")

    def _run_route(self, body):
        import asyncio
        from app.routes import model_routes
        from app.agents.models import ModelManager
        settings = model_routes.ModelSettingsRequest(**body)
        with patch.object(ModelManager, "_reset_state") as reset, \
             patch.object(ModelManager, "get_model_config",
                          return_value={"token_limit": 100000}), \
             patch.object(ModelManager, "filter_model_kwargs", return_value={}), \
             patch.object(ModelManager, "initialize_model",
                          return_value=MagicMock(model_kwargs={})), \
             patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock", "ZIYA_MODEL": "x",
                                     "ZIYA_MAX_OUTPUT_TOKENS": "1024"}):
            asyncio.run(model_routes._update_model_settings_locked(settings))
        return reset

    def test_route_resets_when_thinking_mode_supplied(self):
        reset = self._run_route({"thinking_mode": True})
        reset.assert_called_once()

    def test_route_does_not_reset_without_thinking_mode(self):
        reset = self._run_route({"temperature": 0.5})
        reset.assert_not_called()


if __name__ == '__main__':
    unittest.main()
