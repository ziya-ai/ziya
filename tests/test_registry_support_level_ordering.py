"""Support level must order by trustworthiness, not by display string.

`get_all_services` sorted on `s.support_level.value` with `reverse=True` -- the
DISPLAY STRING, descending, i.e. reverse-alphabetical.  Measured on the real
enum values that yields:

    Under assessment, Supported, Recommended, In development,
    Experimental, Community

so "Under assessment" ranked best and "Community" worst.  Because this sort
feeds `sorted_services[:max_results]`, truncation discarded the RECOMMENDED
servers first -- latent until the page-size ceiling fix made truncation
reachable.

These tests assert on the ORDER OF REAL VALUES rather than on the existence of
a rank map, because a rank table that is defined but never consulted leaves the
map present and every ordering unchanged.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.mcp.registry.aggregator import RegistryAggregator
from app.mcp.registry.interface import (
    RegistryServiceInfo, ServiceStatus, SupportLevel, InstallationType,
)


def _svc(name: str, level: SupportLevel) -> RegistryServiceInfo:
    now = datetime.now()
    return RegistryServiceInfo(
        service_id=name,
        service_name=name,
        service_description="d",
        version=1,
        status=ServiceStatus.ACTIVE,
        support_level=level,
        created_at=now,
        last_updated_at=now,
        installation_instructions={},
        installation_type=InstallationType.UNKNOWN,
        tags=[],
        provider_metadata={'provider_id': 'p'},
    )


def test_every_support_level_has_a_rank():
    """A member added without a rank must not silently sort to one end."""
    for level in SupportLevel:
        rank = level.rank  # raises KeyError if unmapped
        assert isinstance(rank, int)


def test_ranks_are_unique():
    ranks = [level.rank for level in SupportLevel]
    assert len(set(ranks)) == len(ranks), (
        f"duplicate ranks {ranks}; two levels sharing a rank makes their "
        f"relative order depend on input order"
    )


def test_recommended_outranks_under_assessment():
    """The specific inversion the string sort produced."""
    assert SupportLevel.RECOMMENDED.rank < SupportLevel.UNDER_ASSESSMENT.rank


def test_recommended_is_best_and_experimental_is_worst():
    ranks = {level: level.rank for level in SupportLevel}
    assert ranks[SupportLevel.RECOMMENDED] == min(ranks.values())
    assert ranks[SupportLevel.EXPERIMENTAL] == max(ranks.values())


def test_rank_order_is_not_the_alphabetical_order():
    """Guards against a 'fix' that reproduces the bug via a different route.

    Sorting by rank must give a different sequence than sorting by the display
    string in either direction -- otherwise the ordering is still incidental.
    """
    by_rank = sorted(SupportLevel, key=lambda l: l.rank)
    by_string_desc = sorted(SupportLevel, key=lambda l: l.value, reverse=True)
    by_string_asc = sorted(SupportLevel, key=lambda l: l.value)

    assert by_rank != by_string_desc, (
        "rank order equals reverse-alphabetical order, which is the buggy "
        "ordering this change replaced"
    )
    assert by_rank != by_string_asc


@pytest.mark.asyncio
async def test_aggregator_returns_recommended_before_under_assessment():
    """End-to-end through the sort that feeds the max_results truncation."""
    services = [
        _svc("under", SupportLevel.UNDER_ASSESSMENT),
        _svc("recommended", SupportLevel.RECOMMENDED),
        _svc("community", SupportLevel.COMMUNITY),
        _svc("supported", SupportLevel.SUPPORTED),
    ]

    class Provider:
        identifier = "p"
        supports_search = True

        async def list_services(self, max_results=50, next_token=None,
                                filter_params=None):
            return {'services': services[:max_results], 'next_token': None}

    agg = RegistryAggregator()

    class _Reg:
        def get_available_providers(self, include_internal: bool = True):
            return [Provider()]

    agg.provider_registry = _Reg()

    got = await agg.get_all_services(max_results=100)
    names = [s.service_name for s in got]

    assert names.index("recommended") < names.index("under"), (
        f"order was {names}; a recommended server sorted below one under "
        f"assessment, so truncating to max_results drops recommended servers "
        f"in preference to unreviewed ones"
    )
    assert names.index("supported") < names.index("under")


@pytest.mark.asyncio
async def test_truncation_keeps_the_most_trustworthy():
    """The consequence that matters: what survives max_results."""
    services = [
        _svc("experimental", SupportLevel.EXPERIMENTAL),
        _svc("under", SupportLevel.UNDER_ASSESSMENT),
        _svc("recommended", SupportLevel.RECOMMENDED),
    ]

    class Provider:
        identifier = "p"
        supports_search = True

        async def list_services(self, max_results=50, next_token=None,
                                filter_params=None):
            return {'services': services[:max_results], 'next_token': None}

    agg = RegistryAggregator()

    class _Reg:
        def get_available_providers(self, include_internal: bool = True):
            return [Provider()]

    agg.provider_registry = _Reg()

    got = await agg.get_all_services(max_results=1)
    assert [s.service_name for s in got] == ["recommended"], (
        f"truncating to 1 kept {[s.service_name for s in got]} rather than the "
        f"recommended server"
    )
