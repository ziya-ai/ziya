"""_AsyncSigV4Transport must re-resolve credentials per signature.

The transport previously called get_frozen_credentials() ONCE in __init__
and cached the result for its whole lifetime. A Ziya process outlives
short-lived STS/SSO credentials, so once those expired every subsequent
request failed permanently — with no recovery short of restarting the
server, while the credential chain could have vended fresh ones.

botocore's own docstring states a frozen credential "should be used
immediately and then discarded", and RefreshableCredentials.
get_frozen_credentials() calls self._refresh() internally. So deferring
the call to signing time fixes expiry for free while still taking each
signature from one atomic snapshot (the race the frozen form exists for).

Also pins the transport budget: an unbounded write can outlive the SigV4
signature it carries, which is what turned a stalled 1.27 MB write into a
misleading "Signature expired" authentication error.
"""
import httpx
import pytest

from app.providers.bedrock_mantle import (
    _AsyncSigV4Transport,
    _MANTLE_LIMITS,
    _MANTLE_TIMEOUT,
)


class _FakeFrozen:
    def __init__(self, n):
        self.access_key = f"AKIA{n}"
        self.secret_key = f"secret{n}"
        self.token = f"token{n}"


class _CountingResolver:
    """Stands in for botocore Credentials; counts refresh calls."""

    def __init__(self):
        self.calls = 0

    def get_frozen_credentials(self):
        self.calls += 1
        return _FakeFrozen(self.calls)


def _transport_with(resolver) -> _AsyncSigV4Transport:
    """Build a transport without touching boto3 or the real cred chain."""
    t = _AsyncSigV4Transport.__new__(_AsyncSigV4Transport)
    t._region = "us-east-1"
    t._cred_resolver = resolver
    t._inner = None  # never used: we only exercise credential resolution
    return t


class TestPerRequestResolution:
    def test_construction_does_not_freeze(self):
        """No credential snapshot is taken until a request is signed."""
        r = _CountingResolver()
        _transport_with(r)
        assert r.calls == 0

    def test_each_signature_resolves_again(self):
        r = _CountingResolver()
        t = _transport_with(r)
        t._frozen_credentials()
        t._frozen_credentials()
        t._frozen_credentials()
        assert r.calls == 3, (
            "a cached snapshot would resolve once and then serve stale "
            "credentials forever"
        )

    def test_rotated_credentials_are_observed(self):
        """The whole point: a refresh between requests must be picked up."""
        r = _CountingResolver()
        t = _transport_with(r)
        first = t._frozen_credentials().access_key
        second = t._frozen_credentials().access_key
        assert first != second

    def test_no_frozen_attribute_retained(self):
        """Guard against a well-meaning re-introduction of the cache."""
        t = _transport_with(_CountingResolver())
        assert not hasattr(t, "_creds"), (
            "_creds was the cached frozen snapshot; holding it again would "
            "restore the permanent-expiry bug"
        )


class TestTransportBudget:
    def test_write_budget_is_shorter_than_sigv4_validity(self):
        """A stalled write must fail BEFORE its signature can expire.

        AWS rejects a signature older than 5 minutes. If the write budget
        exceeds that, a stall surfaces as "Signature expired" — an auth
        error for a non-auth fault — instead of a plain timeout.
        """
        assert _MANTLE_TIMEOUT.write is not None
        assert _MANTLE_TIMEOUT.write < 300, (
            "write timeout must be under SigV4's 5-minute signature window"
        )

    def test_read_budget_stays_long_for_extended_thinking(self):
        """Extended thinking produces legitimate multi-minute silences."""
        assert _MANTLE_TIMEOUT.read is not None
        assert _MANTLE_TIMEOUT.read >= 600

    def test_connect_budget_is_bounded(self):
        assert _MANTLE_TIMEOUT.connect is not None
        assert _MANTLE_TIMEOUT.connect <= 30

    def test_differs_from_httpx_default_sentinel(self):
        """The SDKs adopt a caller client's timeout only if it differs.

        anthropic._base_client compares against httpx's 5s default and
        silently substitutes its OWN 600s default on a structural match —
        so a budget equal to the sentinel would be discarded.
        """
        assert _MANTLE_TIMEOUT != httpx.Timeout(5.0)

    def test_pool_is_bounded(self):
        assert _MANTLE_LIMITS.max_connections is not None
        assert _MANTLE_LIMITS.max_keepalive_connections is not None
