"""Tests for first-run credential auto-detection and setup guidance.

Covers app/utils/provider_detection.py and the KnownCredentialException
banner-dedup fix that stopped the "no AWS credentials" message repeating
on every chat turn.
"""
import io
import contextlib

import pytest

import app.utils.provider_detection as pd
from app.utils.custom_exceptions import KnownCredentialException


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch):
    """Start each test with no provider env vars set."""
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL",
                "GOOGLE_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    yield


def _force_bedrock(monkeypatch, present: bool):
    monkeypatch.setattr(pd, "_has_bedrock_credentials", lambda: present)


# --- detect_available_providers ------------------------------------------

def test_detect_reports_each_provider(monkeypatch):
    _force_bedrock(monkeypatch, False)
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    result = pd.detect_available_providers()
    assert result == {
        "bedrock": False, "anthropic": False, "openai": False, "google": True,
    }


def test_openai_base_url_counts_as_openai(monkeypatch):
    _force_bedrock(monkeypatch, False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8080/v1")
    assert pd.detect_available_providers()["openai"] is True


# --- maybe_autoselect_endpoint -------------------------------------------

def test_autoselect_single_provider(monkeypatch):
    _force_bedrock(monkeypatch, False)
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    assert pd.maybe_autoselect_endpoint("bedrock", False) == "google"


def test_no_autoselect_when_multiple(monkeypatch):
    _force_bedrock(monkeypatch, False)
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    assert pd.maybe_autoselect_endpoint("bedrock", False) is None


def test_no_autoselect_when_bedrock_available(monkeypatch):
    _force_bedrock(monkeypatch, True)
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    assert pd.maybe_autoselect_endpoint("bedrock", False) is None


def test_explicit_endpoint_is_respected(monkeypatch):
    _force_bedrock(monkeypatch, False)
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    assert pd.maybe_autoselect_endpoint("bedrock", True) is None


def test_non_default_endpoint_untouched(monkeypatch):
    _force_bedrock(monkeypatch, False)
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    assert pd.maybe_autoselect_endpoint("openai", False) is None


def test_no_autoselect_when_nothing_configured(monkeypatch):
    _force_bedrock(monkeypatch, False)
    assert pd.maybe_autoselect_endpoint("bedrock", False) is None


# --- build_setup_help -----------------------------------------------------

def test_setup_help_lists_all_providers(monkeypatch):
    monkeypatch.setattr(pd, "available_aws_profiles", lambda: [])
    msg = pd.build_setup_help()
    for token in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY",
                  "aws configure", "--profile"):
        assert token in msg


def test_setup_help_shows_existing_profiles(monkeypatch):
    monkeypatch.setattr(pd, "available_aws_profiles", lambda: ["work", "home"])
    msg = pd.build_setup_help()
    assert "work" in msg and "home" in msg


# --- KnownCredentialException banner dedup --------------------------------

def test_identical_credential_banner_prints_once():
    # Reset dedup state so ordering with other tests doesn't matter.
    KnownCredentialException._last_printed_message = None
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for _ in range(3):
            KnownCredentialException("repeated creds error")
    assert buf.getvalue().count("repeated creds error") == 1


def test_different_credential_banner_still_shows():
    KnownCredentialException._last_printed_message = None
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        KnownCredentialException("first error")
        KnownCredentialException("second error")
    out = buf.getvalue()
    assert out.count("first error") == 1
    assert out.count("second error") == 1
