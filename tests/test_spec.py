import dataclasses
import json
from dataclasses import replace
from decimal import Decimal

import pytest

from strategylab.core.bars import Interval
from strategylab.spec.spec import StrategySpec, compute_version


def make_spec() -> StrategySpec:
    return StrategySpec(
        name="ema_cross",
        symbol="BTCUSDT",
        interval=Interval.MINUTE,
        start_ts=1_717_200_000,
        end_ts=1_717_203_600,
        params={"fast": Decimal("9"), "slow": Decimal("21")},
        rules_ref="rules/ema-cross.md",
    )


def test_version_is_stable_and_content_addressed() -> None:
    a = make_spec()
    b = make_spec()
    assert a.version == b.version
    c = replace(a, params={"slow": Decimal("20")})
    assert c.version != a.version


def test_load_of_dump_round_trips_exactly() -> None:
    a = make_spec()
    assert StrategySpec.load(a.dump()) == a


def test_load_ignores_the_version_carried_by_the_text() -> None:
    a = make_spec()
    text = json.loads(a.dump())
    text["version"] = "0" * 12
    loaded = StrategySpec.load(json.dumps(text, sort_keys=True, separators=(",", ":")))
    assert loaded == a


def test_rules_ref_is_required_and_non_empty() -> None:
    a = make_spec()
    with pytest.raises(ValueError):
        replace(a, rules_ref="")
    with pytest.raises(ValueError):
        replace(a, name="")
    with pytest.raises(ValueError):
        replace(a, symbol="")


def test_version_changes_with_each_content_field() -> None:
    a = make_spec()
    for candidate in (
        replace(a, symbol="ETHUSDT"),
        replace(a, interval=Interval.HOUR),
        replace(a, start_ts=a.start_ts + 60),
        replace(a, end_ts=a.end_ts - 60),
    ):
        assert candidate.version != a.version


def test_version_is_independent_of_params_insertion_order() -> None:
    a = make_spec()
    reordered = replace(a, params={"slow": Decimal("21"), "fast": Decimal("9")})
    assert reordered.params == a.params
    assert reordered.version == a.version


def test_version_is_derived_from_the_spec_object() -> None:
    a = make_spec()
    assert compute_version(a) == a.version


def test_window_must_be_strictly_ordered() -> None:
    with pytest.raises(ValueError):
        StrategySpec(
            name="ema_cross",
            symbol="BTCUSDT",
            interval=Interval.MINUTE,
            start_ts=1_717_203_600,
            end_ts=1_717_200_000,
            params={"slow": Decimal("21")},
            rules_ref="rules/ema-cross.md",
        )
    with pytest.raises(ValueError):
        replace(make_spec(), end_ts=make_spec().start_ts)


def test_interval_must_be_an_interval_member() -> None:
    with pytest.raises(ValueError):
        StrategySpec(
            name="ema_cross",
            symbol="BTCUSDT",
            interval=60,  # type: ignore[arg-type]
            start_ts=1_717_200_000,
            end_ts=1_717_203_600,
            params={"slow": Decimal("21")},
            rules_ref="rules/ema-cross.md",
        )


def test_dump_is_canonical_json_including_version() -> None:
    a = make_spec()
    text = a.dump()
    assert json.dumps(json.loads(text), sort_keys=True, separators=(",", ":")) == text
    assert json.loads(text)["version"] == a.version


def test_dump_renders_decimals_as_strings() -> None:
    text = make_spec().dump()
    assert json.loads(text)["params"]["slow"] == "21"


def test_spec_is_frozen() -> None:
    a = make_spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.name = "other"  # type: ignore[misc]
