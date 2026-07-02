"""
Tests for ``ziya-approve --provision`` (ASR F-004/F-007 distribution fix).

The provisioning subcommand is the pure-Python replacement for the former
scripts/provision_approve_key.sh, added so the escalation-approval control is
reachable on toolbox/pip installs (where a repo-root .sh never ships). It must
run only as root; the non-root path is the security-relevant one — the Ziya
agent runs as the normal user and must NOT be able to mint the keypair/sudoers.

We exercise only the paths safe to run off-root and off a real /etc: the
non-root refusal, the SUDO_USER guard, and argparse wiring. The actual keygen +
sudoers install requires root and a writable /etc, so it is covered by the
on-machine deployment verification, not here.
"""

import os

import pytest

from app.utils import ziya_approve


def test_provision_refuses_non_root(monkeypatch, capsys):
    """As a normal user (the agent's UID), --provision must refuse and write
    nothing. This is the security property: the agent cannot provision."""
    monkeypatch.setattr(os, "geteuid", lambda: 1000)  # pretend non-root
    rc = ziya_approve.main(["--provision"])
    assert rc == 2
    assert "must run as root" in capsys.readouterr().err


def test_provision_requires_sudo_user(monkeypatch, capsys):
    """Even as root, a missing SUDO_USER (can't name the sudoers grantee) must
    refuse rather than guess."""
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.delenv("SUDO_USER", raising=False)
    rc = ziya_approve.main(["--provision"])
    assert rc == 2
    assert "SUDO_USER" in capsys.readouterr().err


def test_force_flag_parses(monkeypatch):
    """--force parses alongside --provision (regeneration path); non-root still
    refuses, but with rc 2 from the root check, not an argparse error."""
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert ziya_approve.main(["--provision", "--force"]) == 2
