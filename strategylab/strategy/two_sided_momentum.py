"""ДВУСТОРОННИЙ ИМПУЛЬС: двусторонний пробой канала Дончиана на 15m с ATR-стопом.

Спека: strategydocs/spec_01_two_sided_momentum_20261022.md.

Правила (спека §Правила): HH = max High последних `donch_len` ЗАВЕРШЁННЫХ
15m-баров, LL = min Low тех же; LONG при Close(B) > HH, SHORT при Close(B) < LL,
где HH/LL считаются по предшествующему окну — без сигнального бара B. Фильтр
уклона — `bias_len`-сглаживание Уайлдера по Close 15m, `bias_len = 0` фильтр
выключает. Дистанция стопа — `atr_mult` × ATR(14), ATR Уайлдера заморожен на
баре-сигнале, поэтому ATR и bias считаются включая B.

Поток данных тот же, что у остальных стратегий: стратегия получает 1m-бары и
сама агрегирует 15m через `Aggregator` с якорем по эпохе; неполная группа в
расчёт не попадает. `atr_len = 14` и `risk_frac = 0.01` — константы модуля,
свободных параметров ровно три (Global Constraint 10).

Каркас: параметры, агрегация и состояние 15m-окна. Входы, выходы и ордера
надстраиваются следующими задачами плана — пока `on_bar` не выдаёт ордеров.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from decimal import Decimal

from strategylab.broker.orders import Order
from strategylab.core.bars import Bar
from strategylab.strategy.base import StrategyState
from strategylab.strategy.indicators import (
    FIFTEEN_MIN_COUNT,
    FIFTEEN_MIN_WIDTH,
    Aggregator,
    Atr,
    Rma,
    positive,
    positive_int,
)

_ZERO = Decimal("0")

# Замороженные спекой величины: не параметры, константы (Global Constraint 10).
_ATR_LEN = 14
_RISK_FRAC = Decimal("0.01")


class TwoSidedMomentumStrategy:
    """Пробой Дончиана в обе стороны на закрытии 15m-бара, выход — ATR-стоп.

    Состояние 15m-окна открыто наружу через `_hh`, `_ll`, `_atr_value` и
    `_bias_value` (Decimal | None; None — окно ещё не полно): тесты читают их
    напрямую, как `_lower`/`_adx` у FlatKeltnerStrategy.
    """

    name = "two_sided_momentum"

    PARAM_NAMES = ("donch_len", "atr_mult", "bias_len")

    def __init__(
        self,
        donch_len: Decimal = Decimal("48"),
        atr_mult: Decimal = Decimal("1.5"),
        bias_len: Decimal = Decimal("48"),
    ) -> None:
        self._donch_len = positive_int(donch_len, "donch_len")
        self._atr_mult = positive(atr_mult, "atr_mult")
        self._bias_len: int
        self._bias: Rma | None
        if bias_len == _ZERO:
            # Ноль — легальное значение сетки: фильтр уклона выключен.
            # positive_int на нуле бросил бы ValueError, поэтому проверка до делегирования.
            self._bias_len = 0
            self._bias = None
        else:
            self._bias_len = positive_int(bias_len, "bias_len")
            self._bias = Rma(self._bias_len)

        self._m15 = Aggregator(FIFTEEN_MIN_WIDTH, FIFTEEN_MIN_COUNT)
        self._atr = Atr(_ATR_LEN)
        self._window: deque[tuple[Decimal, Decimal]] = deque(maxlen=self._donch_len)

        self._closed_15m = 0
        self._hh: Decimal | None = None
        self._ll: Decimal | None = None
        self._atr_value: Decimal | None = None
        self._bias_value: Decimal | None = None

    @classmethod
    def from_params(cls, params: Mapping[str, Decimal]) -> TwoSidedMomentumStrategy:
        """Стратегия из реестра: известные параметры — свои значения, неизвестные — ошибка."""
        unknown = sorted(set(params) - set(cls.PARAM_NAMES))
        if unknown:
            raise ValueError(f"неизвестные параметры two_sided_momentum: {unknown}")
        kwargs = {name: params[name] for name in cls.PARAM_NAMES if name in params}
        return cls(**kwargs)

    @property
    def bias_len(self) -> int:
        """Параметр `bias_len` целым числом: 0 — фильтр уклона выключен."""
        return self._bias_len

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        """Один 1m-бар: обновить агрегаты 15m. Ордеров пока нет — это следующие задачи.

        `state` ещё не прочитан: ограничение «не в позиции» появится вместе с
        первым входом.
        """
        bar15 = self._m15.update(bar)
        if bar15 is not None:
            self._update_15m(bar15)
        return []

    def _update_15m(self, bar15: Bar) -> None:
        """Закрытый 15m-бар B: ATR и bias включая B, Дончиан по окну без B.

        Порядок фиксации спеки: hh/ll считаются по содержимому окна ДО
        добавления B (последние `donch_len` завершённых баров перед сигналом),
        ATR и bias — включая B: сигнальный бар и есть бар B, а стоп
        замораживается на нём.

        Состояние наружу — только после границы warmup: должно пройти
        `max(donch_len, _ATR_LEN)` закрытых 15m-баров, сигнальным становится
        следующий за ними. Со стороны Дончиана это следует из определения
        сигнала (hh/ll берутся из `donch_len` баров, завершившихся ДО сигнала),
        со стороны ATR — с запасом в один бар: `Atr` даёт первое значение уже
        на 14-м закрытом баре, но тест warmup закрепил `None` до границы
        включительно. До границы все четыре значения `None`, неполное окно не
        бросает: warmup молча не торгует.
        """
        self._closed_15m += 1
        atr = self._atr.update(bar15.high, bar15.low, bar15.close)
        bias: Decimal | None = None
        if self._bias is not None:
            bias = self._bias.update(bar15.close)
        if atr is not None and self._closed_15m > max(self._donch_len, _ATR_LEN):
            self._atr_value = atr
            self._hh = max(hi for hi, _ in self._window)
            self._ll = min(lo for _, lo in self._window)
            if bias is not None:
                self._bias_value = bias
        self._window.append((bar15.high, bar15.low))
