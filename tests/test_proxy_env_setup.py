"""Tests for --proxy / --ca-bundle network configuration in
app.config.environment.setup_environment."""

import os
import sys
import types

import pytest

from app.config.environment import setup_environment

NETWORK_VARS = [
    "ZIYA_PROXY", "HTTPS_PROXY", "HTTP_PROXY",
    "ZIYA_CA_BUNDLE", "AWS_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
]


def _make_args(**overrides):
    """Minimal args namespace accepted by setup_environment."""
    base = dict(
        root=None, exclude=None, include=None, include_only=None,
        endpoint="bedrock", model=None, model_id=None,
        profile=None, region=None,
        temperature=None, top_p=None, top_k=None,
        max_output_tokens=None, thinking_level=None,
        proxy=None, ca_bundle=None, memory=False,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in NETWORK_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


def test_proxy_flag_sets_proxy_env(monkeypatch):
    setup_environment(_make_args(proxy="http://proxy.corp:8080"))
    assert os.environ["ZIYA_PROXY"] == "http://proxy.corp:8080"
    assert os.environ["HTTPS_PROXY"] == "http://proxy.corp:8080"
    assert os.environ["HTTP_PROXY"] == "http://proxy.corp:8080"


def test_ziya_proxy_env_var_alone_is_honored(monkeypatch):
    monkeypatch.setenv("ZIYA_PROXY", "http://envproxy:3128")
    setup_environment(_make_args())
    assert os.environ["HTTPS_PROXY"] == "http://envproxy:3128"
    assert os.environ["HTTP_PROXY"] == "http://envproxy:3128"


def test_no_proxy_flag_leaves_env_untouched():
    setup_environment(_make_args())
    assert "HTTPS_PROXY" not in os.environ
    assert "ZIYA_PROXY" not in os.environ


def test_ca_bundle_fans_out_to_all_stacks(tmp_path):
    pem = tmp_path / "corp-ca.pem"
    pem.write_text("-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n")
    setup_environment(_make_args(ca_bundle=str(pem)))
    expected = str(pem)
    assert os.environ["ZIYA_CA_BUNDLE"] == expected
    assert os.environ["AWS_CA_BUNDLE"] == expected
    assert os.environ["SSL_CERT_FILE"] == expected
    assert os.environ["REQUESTS_CA_BUNDLE"] == expected


def test_ca_bundle_env_var_alone_is_honored(monkeypatch, tmp_path):
    pem = tmp_path / "corp-ca.pem"
    pem.write_text("cert")
    monkeypatch.setenv("ZIYA_CA_BUNDLE", str(pem))
    setup_environment(_make_args())
    assert os.environ["AWS_CA_BUNDLE"] == str(pem)


def test_missing_ca_bundle_exits(tmp_path):
    missing = tmp_path / "nope.pem"
    with pytest.raises(SystemExit):
        setup_environment(_make_args(ca_bundle=str(missing)))


def test_ca_bundle_tilde_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    pem = tmp_path / "ca.pem"
    pem.write_text("cert")
    setup_environment(_make_args(ca_bundle="~/ca.pem"))
    assert os.environ["AWS_CA_BUNDLE"] == str(pem)
