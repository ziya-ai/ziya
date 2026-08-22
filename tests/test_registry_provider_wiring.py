"""Fast, offline guard: every registered registry provider must instantiate.

app/mcp/registry/registry.py registers provider CLASSES for lazy
initialization, and RegistryProviderRegistry.get_provider() catches any
exception from provider_class() and returns None after logging an error.  That
is deliberate resilience, but it means a provider that can NEVER be constructed
disappears from get_available_providers() with no test failure -- the feature
is simply absent forever.

This bug was live: OpenMCPProvider assigned self.identifier / self.name /
self.is_internal in __init__ instead of overriding the abstract @property
declarations, and never implemented validate_service, so it raised TypeError on
every instantiation and 'open-mcp' was silently missing from the provider set
(5 providers reachable out of 6 registered).

These tests need no network and no credentials, so they run in the DEFAULT
suite -- unlike tests/test_registry_integration.py, which gates the equivalent
coverage behind @pytest.mark.integration (deselected by pytest.ini's
addopts = -m "not integration").
"""
from __future__ import annotations

import inspect

import pytest

from app.mcp.registry.interface import RegistryProvider
from app.mcp.registry.registry import (
    initialize_registry_providers,
    get_provider_registry,
)


@pytest.fixture
def registry():
    initialize_registry_providers()
    return get_provider_registry()


def test_every_registered_provider_instantiates(registry):
    """No registered provider class may fail construction.

    get_provider() swallows construction errors, so this asserts on the gap
    between what was registered and what is actually reachable.
    """
    registered = set(registry._provider_classes.keys())
    assert registered, "no provider classes registered at all"

    reachable = {p.identifier for p in registry.get_available_providers()}
    missing = sorted(registered - reachable)

    assert not missing, (
        f"registered provider(s) {missing} could not be instantiated, so they "
        f"are silently absent from get_available_providers(). "
        f"RegistryProviderRegistry.get_provider() logs and returns None on "
        f"construction failure, so this never surfaces at runtime."
    )


@pytest.mark.parametrize(
    "member", ["name", "identifier", "is_internal", "supports_search"]
)
def test_abstract_properties_are_declared_as_properties(registry, member):
    """Abstract @property members must be OVERRIDDEN as concrete properties.

    Assigning self.<member> = ... in __init__ does not satisfy ABCMeta: the
    class stays abstract and every instantiation raises TypeError.  Checking
    the class (not an instance) catches it without constructing anything.
    """
    for identifier, cls in registry._provider_classes.items():
        attr = inspect.getattr_static(cls, member, None)
        assert isinstance(attr, property), (
            f"{cls.__name__} ({identifier}) does not override the abstract "
            f"@property '{member}' as a property (found {type(attr).__name__}). "
            f"Assigning it in __init__ leaves the class abstract."
        )
        # Merely INHERITING the base class's abstract property is not an
        # override -- getattr_static would still report a property, which would
        # make this assertion vacuous.  A real override clears
        # __isabstractmethod__.
        assert not getattr(attr, "__isabstractmethod__", False), (
            f"{cls.__name__} ({identifier}) inherits the ABSTRACT "
            f"@property '{member}' without overriding it. If __init__ assigns "
            f"self.{member} instead, ABCMeta still treats the class as "
            f"abstract and every instantiation raises TypeError."
        )


def test_no_abstract_methods_remain(registry):
    """Every provider class must implement the full RegistryProvider surface."""
    for identifier, cls in registry._provider_classes.items():
        remaining = sorted(getattr(cls, "__abstractmethods__", ()) or ())
        assert not remaining, (
            f"{cls.__name__} ({identifier}) leaves abstract members "
            f"{remaining} unimplemented, so it can never be instantiated."
        )


def test_all_providers_are_registry_provider_subclasses(registry):
    """Guards against registering an unrelated class by mistake."""
    for identifier, cls in registry._provider_classes.items():
        assert issubclass(cls, RegistryProvider), (
            f"{cls.__name__} ({identifier}) is not a RegistryProvider subclass"
        )
