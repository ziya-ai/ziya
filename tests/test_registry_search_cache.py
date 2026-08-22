"""The search fan-out must be memoised, and keyed tightly enough to be safe.

`get_all_services` has had a 5-minute TTL since it was written; `search_unified`
had none, so the debounced search box re-ran the entire provider fan-out on
every keystroke.  That is invisible for the three providers holding their own
`_cache` (pulsemcp, smithery, awesome_list) and expensive for the ones that do
not: `github.search_tools` calls `list_services(max_results=1000)` on every
call, and the CLI-backed internal registry provider spawns a ~20s subprocess.

These tests count PROVIDER INVOCATIONS rather than asserting a cache attribute
exists, because a memo populated but never read leaves the attribute present
and the cost unchanged -- which is the failure this is guarding.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.mcp.registry.aggregator import RegistryAggregator
from app.mcp.registry.interface import (
    RegistryServiceInfo, RegistryTool, ToolSearchResult,
    ServiceStatus, SupportLevel, InstallationType,
)


class CountingProvider:
    """Minimal provider that records how many times it was searched."""

    def __init__(self, identifier: str):
        self._identifier = identifier
        self.search_calls = 0

    @property
    def identifier(self) -> str:
        return self._identifier

    @property
    def supports_search(self) -> bool:
        return True

    async def search_tools(self, query: str, max_results: int = 10):
        self.search_calls += 1
        now = datetime.now()
        service = RegistryServiceInfo(
            service_id=f"{self._identifier}-svc",
            service_name=f"{self._identifier} service",
            service_description="a service",
            version=1,
            status=ServiceStatus.ACTIVE,
            support_level=SupportLevel.SUPPORTED,
            created_at=now,
            last_updated_at=now,
            installation_instructions={},
            installation_type=InstallationType.UNKNOWN,
            tags=[],
            provider_metadata={'provider_id': self._identifier},
        )
        return [ToolSearchResult(
            service=service,
            matching_tools=[RegistryTool(
                tool_name="t", service_id=service.service_id
            )],
            relevance_score=10.0,
        )]


def _aggregator_with(*providers) -> RegistryAggregator:
    agg = RegistryAggregator()

    class _Reg:
        def get_available_providers(self, include_internal: bool = True):
            return list(providers)

    agg.provider_registry = _Reg()
    return agg


@pytest.mark.asyncio
async def test_repeated_search_does_not_refan_out():
    """The same query twice must query each provider once."""
    p = CountingProvider("counting")
    agg = _aggregator_with(p)

    first = await agg.search_unified("database", max_results=20)
    assert p.search_calls == 1, "first search must actually reach the provider"

    second = await agg.search_unified("database", max_results=20)
    assert p.search_calls == 1, (
        "the second identical search re-ran the provider fan-out; every "
        "keystroke in the debounced search box pays a provider's full "
        "catalogue fetch again"
    )
    assert [r.service.service_id for r in first] == \
           [r.service.service_id for r in second]


@pytest.mark.asyncio
async def test_distinct_queries_are_not_conflated():
    """A different query must not be answered from another query's memo."""
    p = CountingProvider("counting")
    agg = _aggregator_with(p)

    await agg.search_unified("database", max_results=20)
    await agg.search_unified("filesystem", max_results=20)

    assert p.search_calls == 2, (
        "a distinct query was served from the cache, so the memo key is too "
        "coarse -- searching would return the previous query's results"
    )


@pytest.mark.asyncio
async def test_provider_filter_change_is_not_served_from_cache():
    """Narrowing the provider set must not reuse the wider set's results.

    The key is built from the RESOLVED provider list; keying on the query
    alone would let a one-registry search return every registry's hits.
    """
    a = CountingProvider("alpha")
    b = CountingProvider("beta")
    agg = _aggregator_with(a, b)

    both = await agg.search_unified("db", max_results=20)
    assert len(both) == 2

    only_alpha = await agg.search_unified(
        "db", max_results=20, provider_filter=["alpha"]
    )
    assert [r.service.provider_metadata['provider_id'] for r in only_alpha] == \
           ["alpha"], "a filtered search was served the unfiltered memo"
    assert b.search_calls == 1, "beta must not be re-queried for a filter that excludes it"


@pytest.mark.asyncio
async def test_invalidate_caches_forces_a_refetch():
    """A provider refresh must not leave the search memo answering stale."""
    p = CountingProvider("counting")
    agg = _aggregator_with(p)

    await agg.search_unified("database", max_results=20)
    agg.invalidate_caches()
    await agg.search_unified("database", max_results=20)

    assert p.search_calls == 2, (
        "invalidate_caches() did not clear the search memo, so a refreshed "
        "registry keeps answering from pre-refresh results"
    )
