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

Реализовано (задача 7): лонг-вход и стоп-брекет. На закрытии 15m-бара B, когда
окно готово, позиция плоская и исполнение не ожидается, `Close(B) > hh` (и,
если фильтр уклона включён, `Close(B) > bias`) порождает MARKET-заявку с
размером `equity · risk_frac / (atr_mult · ATR)`; ATR морозится на баре
сигнала. На баре исполнения входа вешается reduce_only STOP_MARKET по
`close − atr_mult · ATR_замороженный`. Фиксированной цели в спеке нет — LIMIT
не выдаётся никогда. Шорт (задача 8), реверс (9) и трейлинг (10) надстраиваются
следующими задачами плана.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from decimal import Decimal

from strategylab.broker.orders import Order, OrderKind, Side
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
    напрямую, как `_lower`/`_adx` у FlatKeltnerStrategy. Исключение —
    `_bias_value`: при `bias_len == 0` фильтр уклона выключен, и значение
    остаётся `None` НАВСЕГДА ПО ЗАМЫСЛУ, а не как «не готов». Поэтому
    выключенность фильтра проверяется по `self.bias_len == 0` /
    `self._bias is None` (выключен — условия на bias нет), и только при
    включённом фильтре `None` в `_bias_value` означает «ещё не готово»
    (решение R9).
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
        # Сигнал дан, ждём исполнения: сторона входа и ATR, замороженный на баре
        # сигнала. Именованы так намеренно — задачи 8–10 их расширяют.
        self._pending_entry: Side | None = None
        self._pending_atr: Decimal | None = None

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
        """Один 1m-бар: обновить агрегаты 15m, искать лонг-вход, вешать брекет.

        Порядок как в FlatKeltnerStrategy.on_bar: сначала закрытие 15m-бара и
        поиск сигнала входа (ограничен `state.position == 0` и отсутствием
        ожидающего исполнения), затем — проверка, что выданный MARKET уже
        исполнен: если позиция открыта и вход ожидается, на этом баре вешается
        стоп-брекет и ожидающее состояние снимается.
        """
        orders: list[Order] = []
        bar15 = self._m15.update(bar)
        if bar15 is not None:
            self._update_15m(bar15)
            entry = self._entry_order(bar15, state)
            if entry is not None:
                orders.append(entry)
        if self._pending_entry is Side.BUY and state.position > 0:
            # Вход исполнен на этом баре: цена входа оценивается его закрытием.
            self._pending_entry = None
            atr_pending = self._pending_atr
            self._pending_atr = None
            if atr_pending is not None:
                orders.extend(self._bracket_orders(bar, state, atr_pending))
        return orders

    def _entry_order(self, bar15: Bar, state: StrategyState) -> Order | None:
        """LONG-вход: Close(B) выше хаёва Дончиана — MARKET-ордер с размером по риску.

        Готовность окна — по `_hh`/`_atr_value` (после границы warmup);
        `_bias_value` в этой проверке НЕ участвует: при выключенном фильтре
        (`bias_len == 0`) он `None` по замыслу, а не как «не готово»
        (решение R9). Фильтр уклона добавляет условие Close(B) > bias только
        когда включён (`self._bias is not None`), и тогда уже его `None`
        честно означает «bias ещё не посчитан».

        Размер `qty = equity · _RISK_FRAC / (atr_mult · atr)`: риск на сделку
        — _RISK_FRAC капитала, дистанция до стопа — atr_mult · ATR, ATR
        замораживается на сигнальном баре в `_pending_atr`. Вход запрещён,
        пока позиция не плоская или прошлое исполнение ещё ожидается:
        не более одного сигнала на позицию.
        """
        hh = self._hh
        atr = self._atr_value
        if hh is None or atr is None or atr <= 0:
            return None
        if state.position != 0 or self._pending_entry is not None:
            return None
        if bar15.close <= hh:
            return None
        if self._bias is not None:
            bias = self._bias_value
            if bias is None or bar15.close <= bias:
                return None
        if state.equity <= 0:
            return None
        qty = state.equity * _RISK_FRAC / (self._atr_mult * atr)
        self._pending_entry = Side.BUY
        self._pending_atr = atr
        return Order(Side.BUY, OrderKind.MARKET, qty, None, None, False)

    def _bracket_orders(self, bar: Bar, state: StrategyState, entry_atr: Decimal) -> list[Order]:
        """Стоп-брекет на баре исполнения входа: единственный выход, без цели.

        Цена входа оценивается как close бара, на котором MARKET исполнен:
        движок не отдаёт стратегии цену исполнения (решение 4 Flat-Кельтнера —
        ошибка оценки равна одному проскальзыванию). Дистанция стопа — та же,
        что закладывалась в размер: `atr_mult · entry_atr`, с ATR, замороженным
        на сигнальном баре. LIMIT-ордера нет: фиксированной цели в спеке нет.
        """
        entry = bar.close
        stop = entry - self._atr_mult * entry_atr
        return [Order(Side.SELL, OrderKind.STOP_MARKET, state.position, None, stop, True)]

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
