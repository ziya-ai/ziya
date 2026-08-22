"""A provider without pagination must not be capped by the page-size hint.

`_fetch_from_provider` requested `min(remaining, 500)` per page and stopped when
a provider returned no `next_token`.  A provider that does not paginate -- the
CLI-backed internal registry, which shells out to `mcp-registry list` and gets
the whole catalogue in one shot -- honours `max_results` by slicing its own
result and reports no token, so the loop exited after one page.  Measured
against the unpatched code: a provider holding 5218 entries yielded 500.

These tests assert on the COUNT RETURNED rather than on the page-size constant,
because the bug is that a hint silently became a ceiling; a test pinning the
constant would pass either way.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.mcp.registry.aggregator import RegistryAggregator
from app.mcp.registry.interface import (
    RegistryServiceInfo, ServiceStatus, SupportLevel, InstallationType,
)


def _svc(i: int) -> RegistryServiceInfo:
    now = datetime.now()
    return RegistryServiceInfo(
        service_id=f"svc-{i}",
        service_name=f"service {i}",
        service_description="d",
        version=1,
        status=ServiceStatus.ACTIVE,
        support_level=SupportLevel.SUPPORTED,
        created_at=now,
        last_updated_at=now,
        installation_instructions={},
        installation_type=InstallationType.UNKNOWN,
        tags=[],
        provider_metadata={'provider_id': 'nonpaging'},
    )


class NonPagingProvider:
    """Mirrors the CLI provider: full catalogue, self-slice, no next_token."""

    identifier = "nonpaging"
    supports_search = True

    def __init__(self, total: int):
        self._all = [_svc(i) for i in range(total)]
        self.calls = 0
        self.requested = []

    async def list_services(self, max_results=50, next_token=None,
                            filter_params=None):
        self.calls += 1
        self.requested.append(max_results)
        return {
            'services': self._all[:max_results],
            'next_token': None,
        }


@pytest.mark.asyncio
async def test_non_paginating_provider_is_not_capped_at_page_size():
    """Asking for 4000 from a 5218-entry provider must not yield 500."""
    p = NonPagingProvider(5218)
    agg = RegistryAggregator()

    got = await agg._fetch_from_provider(p, 4000)

    assert len(got) == 4000, (
        f"got {len(got)} of the 4000 requested. The page-size hint has become "
        f"a hard ceiling: this provider reports no next_token, so the "
        f"pagination loop exits after one page at whatever size was asked for."
    )
    assert max(p.requested) >= 4000, (
        f"the provider was never asked for more than {max(p.requested)}, so it "
        f"could not have returned more regardless of the loop"
    )


@pytest.mark.asyncio
async def test_provider_holding_fewer_than_requested_is_not_looped_forever():
    """A short provider must terminate, not spin to the page-limit guard."""
    p = NonPagingProvider(12)
    agg = RegistryAggregator()

    got = await agg._fetch_from_provider(p, 4000)

    assert len(got) == 12
    assert p.calls == 1, f"terminated in {p.calls} calls, expected 1"


@pytest.mark.asyncio
async def test_partial_fetch_is_not_cached_for_the_full_ttl():
    """One failing provider must not pin a truncated catalogue for 5 minutes."""
    good = NonPagingProvider(10)

    class Failing:
        identifier = "failing"
        supports_search = True

        async def list_services(self, **kw):
            raise RuntimeError("expired credential")

    agg = RegistryAggregator()

    class _Reg:
        def get_available_providers(self, include_internal: bool = True):
            return [good, Failing()]

    agg.provider_registry = _Reg()

    await agg.get_all_services(max_results=100)
    assert agg._last_fetch_partial is True, (
        "a provider raised but the refresh was recorded as complete, so the "
        "incomplete catalogue is served for the full 5-minute TTL"
    )

    calls_before = good.calls
    await agg.get_all_services(max_results=100)
    assert good.calls > calls_before, (
        "the second call was served from a cache populated by a PARTIAL "
        "refresh; a transient auth failure should be retried promptly"
    )
