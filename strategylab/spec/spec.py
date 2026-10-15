from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from strategylab.core.bars import Interval


@dataclass(frozen=True, slots=True)
class StrategySpec:
    """Замороженное описание запуска: стратегия, символ, интервал, окно, параметры.

    version — производное поле: вычисляется в __post_init__ через object.__setattr__,
    чтобы замороженный слот-датакласс не принимал его в конструкторе.
    """

    name: str
    symbol: str
    interval: Interval
    start_ts: int
    end_ts: int
    params: dict[str, Decimal]
    rules_ref: str
    version: str = field(init=False)

    def __post_init__(self) -> None:
        _validate(self)
        object.__setattr__(self, "version", compute_version(self))

    def dump(self) -> str:
        """Канонический JSON объекта целиком, включая поле version."""
        payload = _to_dict(self)
        payload["version"] = self.version
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def load(cls, text: str) -> StrategySpec:
        """Собирает объект из текста dump(); version вычисляется заново."""
        data = json.loads(text)
        return cls(
            name=data["name"],
            symbol=data["symbol"],
            interval=Interval(data["interval"]),
            start_ts=data["start_ts"],
            end_ts=data["end_ts"],
            params={key: Decimal(value) for key, value in data["params"].items()},
            rules_ref=data["rules_ref"],
        )


def compute_version(spec: StrategySpec) -> str:
    """Первые 12 символов sha256 канонического JSON объекта без поля version."""
    canonical = json.dumps(_to_dict(spec), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _to_dict(spec: StrategySpec) -> dict[str, Any]:
    """Представление содержимого для канонического JSON: Decimal — строкой, Interval — как есть."""
    return {
        "name": spec.name,
        "symbol": spec.symbol,
        "interval": spec.interval,
        "start_ts": spec.start_ts,
        "end_ts": spec.end_ts,
        "params": {key: str(value) for key, value in spec.params.items()},
        "rules_ref": spec.rules_ref,
    }


def _validate(spec: StrategySpec) -> None:
    if not spec.name:
        raise ValueError("name must be non-empty")
    if not spec.symbol:
        raise ValueError("symbol must be non-empty")
    if not spec.rules_ref:
        raise ValueError("rules_ref must be non-empty")
    if spec.start_ts >= spec.end_ts:
        raise ValueError("start_ts must be strictly before end_ts")
    if not isinstance(spec.interval, Interval):
        raise ValueError("interval must be an Interval member")
