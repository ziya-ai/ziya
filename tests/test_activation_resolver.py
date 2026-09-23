"""
Activation resolver (design/capabilities-hub.md, slice 2).

Both sides of the parity pair assert ``tests/fixtures/activation_cases.json``:
this suite drives ``app.services.activation`` and
``frontend/src/utils/__tests__/activationParity.test.ts`` drives the TS mirror
the lens UI uses to render ghosts/pins client-side.  A rule changed on one
side without the other fails one suite instead of shipping a hub whose
server and client disagree about what is active.

The fixture-driven assertions are on the OUTERMOST surface — the always-set
the prompt builder will read and the catalog the model will be offered — not
only on the per-item origin, because those two lists are what the live bugs
(design §Problem 1) got wrong.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.models.activation import (
    ItemSpec, PlacementRejected, ResolveResult, make_key, parse_key,
)
from app.services.activation import (
    default_placement, resolve_activation, validate_placement,
)

FIXTURES = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "activation_cases.json").read_text())


def _items(specs):
    return [ItemSpec(**s) for s in specs]


@pytest.mark.parametrize("case", FIXTURES["resolve"], ids=lambda c: c["name"])
def test_resolve_matches_shared_fixture(case):
    result = resolve_activation(case["layers"], _items(case["items"]))
    exp = case["expect"]

    by_key = {i.key: i for i in result.items}
    assert set(by_key) == set(exp["items"]), "every installed item resolves, nothing else"
    for key, want in exp["items"].items():
        got = by_key[key].model_dump()
        for field, value in want.items():
            assert got[field] == value, f"{case['name']}: {key}.{field}"

    assert result.always_set() == exp["always"]
    assert result.catalog_set() == exp["catalog"]
    assert [d.model_dump() for d in result.dropped] == exp["dropped"]


@pytest.mark.parametrize("case", FIXTURES["validate"], ids=lambda c: c["name"])
def test_validate_matches_shared_fixture(case):
    items = _items(case["items"])
    if case["reason"] is None:
        validate_placement(items, case["key"], case["layer"], case["state"])
        return
    with pytest.raises(PlacementRejected) as exc:
        validate_placement(items, case["key"], case["layer"], case["state"])
    assert exc.value.reason == case["reason"]
    assert exc.value.key == case["key"]


def test_write_refusal_and_read_ignore_agree():
    """A value refused on PUT is also ignored if it is already on disk.

    Otherwise a hand-edited or pre-rule file would activate what the API
    refuses to let the user activate.  Exercised for every rejectable
    fixture that names a real item and a writable layer.
    """
    for case in FIXTURES["validate"]:
        if case["reason"] in (None, "invalid_layer", "unknown_key"):
            continue
        items = _items(case["items"])
        result = resolve_activation({case["layer"]: {case["key"]: case["state"]}}, items)
        assert [d.reason for d in result.dropped] == [case["reason"]], case["name"]
        item = next(i for i in result.items if i.key == case["key"])
        assert item.placements[case["layer"]] is None, "refused entry must not become a pin"


def test_item_order_is_preserved():
    items = _items([{"key": "skill:z", "kind": "skill"},
                    {"key": "mcp:a", "kind": "mcp"},
                    {"key": "skill:m", "kind": "skill", "discoverable": True}])
    result = resolve_activation({}, items)
    assert [i.key for i in result.items] == ["skill:z", "mcp:a", "skill:m"]


def test_non_writable_layer_in_input_is_ignored():
    # ``default`` is computed; a stored "default" layer must not act as a pin.
    items = _items([{"key": "skill:a", "kind": "skill", "discoverable": True}])
    result = resolve_activation({"default": {"skill:a": "always"}}, items)
    assert result.items[0].effective == "ondemand"
    assert result.items[0].origin == "default"


def test_default_placement_is_the_fixture_default():
    assert default_placement(ItemSpec(key="skill:a", kind="skill", discoverable=True)) == "ondemand"
    assert default_placement(ItemSpec(key="skill:a", kind="skill")) == "off"
    assert default_placement(ItemSpec(key="mcp:a", kind="mcp")) == "always"
    assert default_placement(ItemSpec(key="mcp:shell", kind="mcp", tier="environment")) == "always"


def test_keys_round_trip_and_reject_garbage():
    assert parse_key(make_key("skill", "kuiper-conventions")) == ("skill", "kuiper-conventions")
    assert parse_key("mcp:shell") == ("mcp", "shell")
    for bad in ("shell", "tool:x", "skill:", "", None, 42):
        assert parse_key(bad) is None, bad


def test_result_is_json_serialisable_for_the_api():
    result = resolve_activation({}, _items([{"key": "mcp:a", "kind": "mcp"}]))
    assert isinstance(result, ResolveResult)
    json.dumps(result.model_dump())
