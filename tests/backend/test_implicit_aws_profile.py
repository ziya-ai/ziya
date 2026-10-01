"""
Implicit AWS profile: when the user names no profile anywhere and ~/.aws
holds exactly one, Ziya runs against it instead of failing on boto3's empty
default chain and telling the user to pass --profile for the only profile
they have.  The existing "ziya" convenience profile keeps precedence.

The seam is asserted at both consumers: the session factory
(create_fresh_boto3_session) and startup (setup_environment), which is what
exports AWS_PROFILE for the provider paths that build boto3 clients directly.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.utils import aws_utils

_PROFILE_ENV = ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "ZIYA_AWS_PROFILE", "AWS_ACCESS_KEY_ID")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in _PROFILE_ENV:
        monkeypatch.delenv(k, raising=False)


def _profiles(*names):
    return patch.object(aws_utils, "_get_available_profiles", return_value=list(names))


def test_sole_profile_is_chosen():
    with _profiles("work"):
        assert aws_utils.implicit_aws_profile() == "work"


def test_ziya_convenience_profile_still_wins():
    with _profiles("ziya"):
        assert aws_utils.implicit_aws_profile() == "ziya"
    with _profiles("work", "ziya"):
        assert aws_utils.implicit_aws_profile() == "ziya"


def test_no_implicit_choice_when_ambiguous_or_already_default():
    with _profiles("a", "b"):
        assert aws_utils.implicit_aws_profile() is None
    with _profiles("default"):          # boto3 already uses it
        assert aws_utils.implicit_aws_profile() is None
    with _profiles():
        assert aws_utils.implicit_aws_profile() is None


@pytest.mark.parametrize("var", ["AWS_PROFILE", "AWS_DEFAULT_PROFILE", "ZIYA_AWS_PROFILE"])
def test_explicit_profile_is_never_overridden(monkeypatch, var):
    monkeypatch.setenv(var, "chosen")
    with _profiles("work"):
        assert aws_utils.implicit_aws_profile() is None


def test_static_env_credentials_are_not_hijacked(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA...")
    with _profiles("work"):
        assert aws_utils.implicit_aws_profile() is None


def test_session_factory_uses_sole_profile():
    with _profiles("work"), patch.object(aws_utils.boto3, "Session") as sess:
        aws_utils.create_fresh_boto3_session()
    sess.assert_called_once_with(profile_name="work")


def test_session_factory_explicit_argument_wins():
    with _profiles("work"), patch.object(aws_utils.boto3, "Session") as sess:
        aws_utils.create_fresh_boto3_session(profile_name="other")
    sess.assert_called_once_with(profile_name="other")


def test_session_factory_multiple_profiles_fall_through_to_default_chain():
    with _profiles("a", "b"), patch.object(aws_utils.boto3, "Session") as sess:
        aws_utils.create_fresh_boto3_session()
    sess.assert_called_once_with()


def _args(**over):
    base = dict(profile=None, endpoint="bedrock", region=None, model=None,
                root=None, include=None, exclude=None, model_id=None)
    base.update(over)
    return SimpleNamespace(**base)


def _run_setup(args):
    from app.config.environment import setup_environment
    # Startup does a lot besides the profile; only the profile section is
    # under test, and everything after it needs a real model registry.
    with patch("app.config.environment.validate_model_and_endpoint",
               side_effect=SystemExit(0)):
        with pytest.raises(SystemExit):
            setup_environment(args)


def test_startup_exports_sole_profile(monkeypatch):
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", "/tmp")
    with _profiles("work"):
        _run_setup(_args())
    import os
    assert os.environ.get("AWS_PROFILE") == "work"
    assert os.environ.get("ZIYA_AWS_PROFILE") == "work"


def test_startup_keeps_explicit_profile_and_skips_non_bedrock(monkeypatch):
    import os
    with _profiles("work"):
        _run_setup(_args(profile="chosen"))
    assert os.environ.get("AWS_PROFILE") == "chosen"

    for k in _PROFILE_ENV:
        monkeypatch.delenv(k, raising=False)
    with _profiles("work"):
        _run_setup(_args(endpoint="anthropic"))
    assert os.environ.get("AWS_PROFILE") is None
