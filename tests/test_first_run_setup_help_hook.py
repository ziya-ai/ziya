"""First-run setup help may be supplied by an enterprise auth plugin.

``get_credential_help_message()`` assumes credentials EXIST but are expired
("run mwinit"). On a brand-new machine nothing exists to refresh, and
``build_setup_help()`` fell through to generic ``aws configure`` guidance --
which in a Midway/ada deployment cannot produce working Bedrock credentials at
all, so the user is sent in a circle.

Covers the seam: AuthProvider.get_first_run_setup_help (interfaces) ->
app.plugins.get_first_run_setup_help (resolver) ->
provider_detection.build_setup_help (renderer). A hook that is defined but
never reached, or reached but never rendered, is the failure this asserts
against.
"""
import pytest

import app.plugins as plugins
import app.utils.provider_detection as pd
from app.plugins.default_providers import DefaultAuthProvider


HELP_TEXT = "Ziya reaches Bedrock through an AWS profile (Midway + ada).\nline two"


class _FakeProvider:
    """Minimal auth provider; only the pieces the resolver touches."""

    def __init__(self, provider_id, help_text=None, priority=0,
                 detects=True, raises=False):
        self.provider_id = provider_id
        self.priority = priority
        self._help = help_text
        self._detects = detects
        self._raises = raises

    def detect_environment(self):
        return self._detects

    def get_first_run_setup_help(self):
        if self._raises:
            raise RuntimeError("provider exploded")
        return self._help


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """No credentials, no policy restriction, no AWS profiles, no plugins."""
    for key in [k for p in pd.PROVIDER_CREDENTIALS for k in p.keys]:
        monkeypatch.delenv(key, raising=False)
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                "AWS_SESSION_TOKEN", "ZIYA_ALLOW_ALL_ENDPOINTS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(pd, "available_aws_profiles", lambda: [])
    monkeypatch.setattr(pd, "_has_bedrock_credentials", lambda: False)
    monkeypatch.setattr(plugins, "get_allowed_endpoints", lambda: None)
    monkeypatch.setattr(plugins, "_auth_providers", [])
    pd.refresh_availability()
    yield


def _providers(monkeypatch, *provs):
    monkeypatch.setattr(plugins, "_auth_providers", list(provs))


# --- resolver ------------------------------------------------------------

def test_community_default_supplies_no_help(monkeypatch):
    """DefaultAuthProvider must not override the generic instructions."""
    _providers(monkeypatch, DefaultAuthProvider())
    assert plugins.get_first_run_setup_help() is None


def test_no_providers_registered(monkeypatch):
    _providers(monkeypatch)
    assert plugins.get_first_run_setup_help() is None


def test_highest_priority_provider_wins(monkeypatch):
    """_auth_providers is kept in priority order; first match must win."""
    _providers(monkeypatch,
               _FakeProvider("enterprise", "ENTERPRISE", priority=100),
               _FakeProvider("other", "OTHER", priority=10))
    assert plugins.get_first_run_setup_help() == "ENTERPRISE"


def test_provider_without_the_hook_is_skipped(monkeypatch):
    """An older plugin predating this interface must not break the walk."""
    class Legacy:
        provider_id = "legacy"
    _providers(monkeypatch, Legacy(), _FakeProvider("ent", "ENTERPRISE"))
    assert plugins.get_first_run_setup_help() == "ENTERPRISE"


def test_raising_provider_is_skipped_not_fatal(monkeypatch):
    _providers(monkeypatch,
               _FakeProvider("bad", raises=True, priority=100),
               _FakeProvider("good", "ENTERPRISE", priority=10))
    assert plugins.get_first_run_setup_help() == "ENTERPRISE"


@pytest.mark.parametrize("blank", [None, "", "   ", "\n\t "])
def test_blank_help_treated_as_absent(monkeypatch, blank):
    _providers(monkeypatch, _FakeProvider("ent", blank))
    assert plugins.get_first_run_setup_help() is None


def test_resolver_does_not_depend_on_detect_environment(monkeypatch):
    """The no-credentials path cannot rely on credential-based detection.

    AmazonAuthProvider.detect_environment() can fall through to an STS identity
    call, which fails precisely when the user has no credentials -- the case
    this help text exists for.
    """
    _providers(monkeypatch, _FakeProvider("ent", "ENTERPRISE", detects=False))
    assert plugins.get_first_run_setup_help() == "ENTERPRISE"


# --- rendering in build_setup_help ---------------------------------------

def test_hook_text_replaces_generic_aws_instructions(monkeypatch):
    _providers(monkeypatch, _FakeProvider("ent", HELP_TEXT, priority=100))
    msg = pd.build_setup_help()
    assert "Midway + ada" in msg
    assert "line two" in msg
    # The generic advice must be GONE, not merely accompanied.
    assert "aws configure" not in msg
    assert "AWS_SECRET_ACCESS_KEY" not in msg


def test_hook_text_is_indented_under_the_bedrock_bullet(monkeypatch):
    _providers(monkeypatch, _FakeProvider("ent", HELP_TEXT, priority=100))
    lines = pd.build_setup_help().splitlines()
    bullet = next(i for i, l in enumerate(lines) if "AWS Bedrock" in l)
    body = [l for l in lines[bullet + 1:] if l.strip()]
    assert body, "hook text rendered nothing under the bullet"
    assert body[0].startswith("        "), body[0]


def test_generic_text_retained_without_a_plugin(monkeypatch):
    """Community edition must be byte-for-byte unaffected."""
    _providers(monkeypatch, DefaultAuthProvider())
    msg = pd.build_setup_help()
    assert "aws configure" in msg
    assert "--profile" in msg


def test_hook_still_applies_under_bedrock_only_policy(monkeypatch):
    """The enterprise case: restricted to Bedrock AND custom AWS guidance."""
    monkeypatch.setattr(plugins, "get_allowed_endpoints", lambda: ["bedrock"])
    _providers(monkeypatch, _FakeProvider("ent", HELP_TEXT, priority=100))
    msg = pd.build_setup_help()
    assert "Midway + ada" in msg
    assert "aws configure" not in msg
    # Other providers stay hidden by policy.
    assert "OPENAI_API_KEY" not in msg


def test_hook_composes_with_other_permitted_providers(monkeypatch):
    """A mixed policy keeps non-AWS providers and only replaces the AWS body."""
    monkeypatch.setattr(plugins, "get_allowed_endpoints",
                        lambda: ["bedrock", "anthropic"])
    _providers(monkeypatch, _FakeProvider("ent", HELP_TEXT, priority=100))
    msg = pd.build_setup_help()
    assert "ANTHROPIC_API_KEY" in msg
    assert "Midway + ada" in msg
    assert "aws configure" not in msg


def test_plugin_layer_failure_falls_back(monkeypatch):
    """A broken plugin layer must not suppress all credential guidance."""
    def boom():
        raise ImportError("plugin layer unavailable")
    monkeypatch.setattr(plugins, "get_first_run_setup_help", boom)
    assert pd._auth_provider_setup_help() is None
    assert "aws configure" in pd.build_setup_help()
