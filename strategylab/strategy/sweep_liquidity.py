"""ЗАЧИСТКА ЛИКВИДНОСТИ: лонг-only покупка смётки минимума диапазона фитилём.

Спека: strategydocs/Qwen_python_20261022_p95htwp5e.py («Зачистка ликвидности»).

Поток данных: стратегия получает 1m-бары и сама агрегирует 1H-бары общим
`Aggregator` из `strategylab.strategy.indicators`. Агрегация ведётся якорем
по эпохе (`ts - ts % width`), поэтому окна совпадают с биржевыми. Группа
участвует в расчёте только целиком: неполная группа (пропуск минут, обрыв
окна) не собирается никогда и в индикаторы не попадает.

Сигнал (на закрытии 1H-бара B): минимум Low последних `lookback`
ЗАВЕРШЁННЫХ 1H-баров перед B (`rolling_low`) пробит фитилём — Low(B) <<
rolling_low, но бар закрылся обратно в диапазон — Close(B) > rolling_low.
Это сбор ликвидности (стоп-хант) и возврат без закрепления под диапазоном.

Выход: SL = Low(B) - atr_mult * ATR1H (STOP_MARKET, reduce_only), заморожен
на сигнальном баре. Брекет после исполнения входа: стоп на весь объём плюс
LIMIT reduce_only на ПОЛОВИНУ объёма с целью TP1 = Entry + tp1_r * R,
R = Entry - SL. После исполнения TP1 движок снимает цель, стратегия
перезакладывает брекет по остатку: стоп на Entry (безубыток), цель
TP2 = Entry + tp2_r * R. Размер: qty = equity * risk_frac / R.

Решения, которые здесь зафиксированы
-----------------------------------

1. ATR считается сглаживанием Уайлдера (RMA) общего модуля, как во ФЛЭТе,
   а не SMA, как в скетче: формула одного индикатора живёт в одном месте
   (`strategylab.strategy.indicators`), и стратегии не расходятся в её
   определении; SMA-версия скетча дала бы другую ширину стопа на той же
   истории. Наш статус приоритетен — здесь RMA. Готовность та же по смыслу,
   что у скетча («ATR не NaN»), но чуть строже: значение обязано быть и
   готово, и > 0 — окно из нулевых true range (полностью плоские бары)
   нулевым ATR стоп не задаёт.

2. Границы диапазона — простое окно, как в скетче (`rolling min по Low`),
   без усреднений: условие смётки определено именно экстремумом, а не
   сглаженной величиной. Окно считается по ЗАВЕРШЁННЫМ 1H-барам перед B:
   сигнальный бар в него не входит.

3. Вход — MARKET BUY, идущий на исполнение со следующего 1m-бара, как и во
   всех стратегиях пакета: сигнал на закрытии 1H-бара B есть, а исполняется
   ордер открытым баром после него. Сайзинг считается по оценке входа =
   close сигнального 1H-бара; брекет ставится на баре исполнения входа и за
   цену входа берёт close ЭТОГО бара (движок не отдаёт стратегию цену
   исполнения, ошибка оценки — одно проскальзывание). SL при этом
   заморожен с сигнального бара: стоп и размер обязаны быть теми, по
   которым принято решение входить.

4. Частичный TP и перенос стопа — семантика движка, проверенная в
   `strategylab.broker.sim` и `strategylab.core`:
   - LIMIT reduce_only на половину объёма зеркалит в позицию и уровень, и
     размер цели (`Position.take_profit_qty`); _apply_exits исполняет цель
     только этим объёмом — половина позиции закрывается на TP1, остаток
     живёт дальше;
   - после частичного исполнения зеркало уровня снято в той же точке, так
     что отдыхающий лимит того же уровня не может исполнить остаток в
     том же баре (движок дополнительно снимает его из очереди);
   - перезакладка брекета по остатку (стоп на безубыток, цель на TP2)
     — та же повторная reduce_only-постановка, что и первичная: зеркала
     стопа и цели перезаписываются, старые resting-заявки дорабатывают
     свой век как мёртвый груз и снимаются при закрытии позиции;
   - журнал (`TradeLog`) и метрики пишут частичный выход как сделку —
     отдельный филл с фактическим объёмом и pnl-частью; время входа у
     обеих частей одно, суммы pnl журнала и отчёта совпадают.

5. «Оба уровня превышены на одном баре»: потолок движка — один зеркальный
   уровень цели, и на баре исполнения TP1 цель — это ещё TP1, поэтому
   беру половину на TP1, а не всё на TP2, как скетч (у него до TP1 просто
   не доходит очередь проверки). Это согласуется с осторожным внутрибазовым
   порядком PESSIMISTIC: бар не даёт «двойной» прибыли от одного
   экстремума. TP2 становится целью только следующей постановкой — с бара,
   когда частичный выход уже виден; вторая часть закрывается на TP2 не
   раньше следующего бара. Сметка скетча осознанно заменена на эту
   последовательность, потому что она не требует знания, чьё исполнение
   «первым по времени» внутри бара, — существует лишь в допущении скетча.

6. R для цели после частичного выхода — оценочный: TP2 считается от
   оценки входа брекета (close бара исполнения входа), знаменатель R там
   же. Реальная цена входа отличается на одно проскальзывание, но стоп и
   цели обязаны быть одним набором значений, замороженным решением, а не
   пересчитываться.

7. Безубыток после TP1 — стоп на ОЦЕНКУ входа из брекета, а не на
   фактическую цену исполнения: фактическая цена известна только движку.
   Остаток до стопа тогда не строго ноль, а ±пара проскальзываний —
   честная цена того, что движок не раскрывает цену исполнения стратегиям.

8. Лонг-only, как в скетче: смётка нижней границы диапазона. Пока позиция
   открыта (включая перезаложенный брекет), новых входов нет; вход
   возможен снова только после полного закрытия позиции.

9. Капитал берётся из `StrategyState.equity` — накопленный реализованный
   P&L: у прогона без стартового капитала equity = 0, размер вышел бы
   нулевым, и стратегия не открывает позицию вовсе.

10. Диапазон отдаётся наружу по запросу: `enable_channel_log` включает
   запись точек канала по 1H-барам, `channel_log` отдаёт её. Запись
   выключена по умолчанию и на решения стратегии не влияет. Экспорт сцены
   берёт диапазон отсюда, а не пересчитывает заново: пересчёт показал бы
   другие границы, если бы окно считалось иначе, и экспорт врал бы о
   том, по чему торговали. Точка снимается на закрытии каждого 1H-бара и
   несёт границы окна последних `lookback` ЗАВЕРШЁННЫХ 1H-баров — того
   самого окна, которое сигнал этого бара проверяет: lower = минимум
   Low (та самая граница, что проверяется в сигнале), upper = максимум
   High тех же баров (зеркало для контекста, стратегия по нему не торгует), mid =
   (upper + lower) / 2.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from decimal import Decimal

from strategylab.broker.orders import Order, OrderKind, Side
from strategylab.core.bars import Bar
from strategylab.strategy.base import StrategyState
from strategylab.strategy.indicators import (
    ONE_HOUR_COUNT,
    ONE_HOUR_WIDTH,
    Aggregator,
    Atr,
    ChannelPoint,
    positive,
    positive_int,
)

_ONE = Decimal("1")
_HALF = Decimal("0.5")
_TWO = Decimal("2")


class SweepLiquidityStrategy:
    """ЗАЧИСТКА: лонг на смётке минимума диапазона и возврате внутрь.

    Сигнал читается на закрытии 1H-бара, вход — MARKET-ордером, выходы —
    reduce_only-брекетом (SL под пробитым минимумом, половина на TP1, остаток
    после TP1 — на бисек-стопе в безубыток и цели TP2). Пока позиция открыта,
    новых входов нет.
    """

    name = "sweep_liquidity"

    PARAM_NAMES = (
        "lookback",
        "atr_len",
        "atr_mult",
        "tp1_r",
        "tp2_r",
        "risk_frac",
    )

    def __init__(
        self,
        lookback: Decimal = Decimal("20"),
        atr_len: Decimal = Decimal("14"),
        atr_mult: Decimal = Decimal("1.5"),
        tp1_r: Decimal = Decimal("1.5"),
        tp2_r: Decimal = Decimal("3.0"),
        risk_frac: Decimal = Decimal("0.01"),
    ) -> None:
        self._lookback = positive_int(lookback, "lookback")
        self._atr_len = positive_int(atr_len, "atr_len")
        self._atr_mult = positive(atr_mult, "atr_mult")
        self._tp1_r = positive(tp1_r, "tp1_r")
        self._tp2_r = positive(tp2_r, "tp2_r")
        if self._tp2_r <= self._tp1_r:
            raise ValueError(
                f"tp2_r должен быть больше tp1_r: tp2_r={self._tp2_r}, tp1_r={self._tp1_r}"
            )
        self._risk_frac = positive(risk_frac, "risk_frac")
        if self._risk_frac > _ONE:
            raise ValueError(f"risk_frac должен быть <= 1, получено {risk_frac}")

        self._h1 = Aggregator(ONE_HOUR_WIDTH, ONE_HOUR_COUNT)
        self._atr_h1 = Atr(self._atr_len)
        self._lows: deque[Decimal] = deque(maxlen=self._lookback)
        self._highs: deque[Decimal] = deque(maxlen=self._lookback)

        self._atr_h1_value: Decimal | None = None
        self._rolling_low: Decimal | None = None
        self._rolling_high: Decimal | None = None
        self._entry_pending = False
        self._entry_stop: Decimal | None = None
        self._entry_est: Decimal | None = None
        self._bracket_qty: Decimal | None = None
        self._tp2: Decimal | None = None
        self._tp1_done = False
        self._channel_log: list[ChannelPoint] | None = None

    @classmethod
    def from_params(cls, params: Mapping[str, Decimal]) -> SweepLiquidityStrategy:
        """Стратегия из реестра: известные параметры — свои значения, неизвестные — ошибка."""
        unknown = sorted(set(params) - set(cls.PARAM_NAMES))
        if unknown:
            raise ValueError(f"неизвестные параметры sweep_liquidity: {unknown}")
        kwargs = {name: params[name] for name in cls.PARAM_NAMES if name in params}
        return cls(**kwargs)

    @property
    def last_atr(self) -> Decimal | None:
        """ATR последнего закрытого 1H-бара: из него стоп и размер."""
        return self._atr_h1_value

    @property
    def last_rolling_low(self) -> Decimal | None:
        """Минимум Low последних `lookback` закрытых 1H-баров перед текущим.

        Действует в течение бара B, для которого посчитан: следующий закрытый
        1H-бар сдвигает окно. Граница диапазона для условия смётки.
        """
        return self._rolling_low

    @property
    def last_rolling_high(self) -> Decimal | None:
        """Максимум High последних `lookback` закрытых 1H-баров перед текущим.

        Зеркало `last_rolling_low` на том же окне: нижнюю границу диапазона
        проверяет сигнал смётки, верхняя нужна только контексту экспорта.
        """
        return self._rolling_high

    def enable_channel_log(self) -> None:
        """Включает запись точек диапазона по закрытиям 1H-баров.

        Запись выключена по умолчанию: без неё стратегия считает ровно то же
        самое, только ничего не запоминает. Включать надо до прогона — журнал
        наполняется по ходу баров.
        """
        if self._channel_log is None:
            self._channel_log = []

    @property
    def channel_log(self) -> tuple[ChannelPoint, ...]:
        """Точки диапазона по 1H-барам: пусто, если запись не включалась.

        Точка появляется на каждом закрытом 1H-баре, на котором окно границ
        уже полное: до первых `lookback` закрытых 1H-баров диапазона нет, и
        точки нет тоже.
        """
        if self._channel_log is None:
            return ()
        return tuple(self._channel_log)

    def on_bar(self, bar: Bar, state: StrategyState) -> list[Order]:
        """Один 1m-бар: обновить агрегаты, повесить брекет, поискать вход."""
        orders: list[Order] = []
        if (
            state.position == 0
            and not self._entry_pending
            and self._bracket_qty is not None
        ):
            # Позиция закрылась: брекет отработал весь, торговля сброшена.
            self._reset_trade()
        bar_h1 = self._h1.update(bar)
        if bar_h1 is not None:
            self._update_h1(bar_h1)
            self._log_channel(bar_h1)
            if self._is_signal(bar_h1, state):
                entry = self._entry_order(bar_h1, state)
                if entry is not None:
                    orders.append(entry)
        if self._entry_pending and state.position > 0:
            # Вход исполнен на этом баре: цена входа оценивается его закрытием.
            self._entry_pending = False
            orders.extend(self._bracket_orders(bar, state))
        elif self._tp1_fills(state):
            self._tp1_done = True
            orders.extend(self._after_tp1_orders(state))
        return orders

    def _update_h1(self, bar_h1: Bar) -> None:
        """Границы диапазона — до сдвига окна, ATR — уже по самому бару B."""
        if len(self._lows) == self._lookback:
            self._rolling_low = min(self._lows)
            self._rolling_high = max(self._highs)
        atr = self._atr_h1.update(bar_h1.high, bar_h1.low, bar_h1.close)
        if atr is not None:
            self._atr_h1_value = atr
        self._lows.append(bar_h1.low)
        self._highs.append(bar_h1.high)

    def _log_channel(self, bar_h1: Bar) -> None:
        """Точка диапазона на закрытом 1H-баре: границы окна перед этим баром.

        Точка снимается после сдвига окна: сигнал этого 1H-бара решался по
        только что обновлённому окну, и экспорт должен показывать тот же.
        """
        log = self._channel_log
        if log is None:
            return
        lower = self._rolling_low
        upper = self._rolling_high
        if lower is None or upper is None:
            return
        log.append(
            ChannelPoint(
                ts=bar_h1.ts,
                mid=(upper + lower) / _TWO,
                upper=upper,
                lower=lower,
            )
        )

    def _is_signal(self, bar_h1: Bar, state: StrategyState) -> bool:
        """Сметка фитилём ниже минимума диапазона и возврат закрытия внутрь.

        Окно границ — предыдущие `lookback` закрытых 1H-баров, сигнальный
        бар в него не входит: смётка по определению пробивает стоявший
        минимум, а не свой собственный.
        """
        if self._rolling_low is None:
            return False
        if state.position != 0 or self._entry_pending or self._bracket_qty is not None:
            return False
        atr = self._atr_h1_value
        if atr is None or atr <= 0 or state.equity <= 0:
            return False
        return bar_h1.low < self._rolling_low and bar_h1.close > self._rolling_low

    def _entry_order(self, bar_h1: Bar, state: StrategyState) -> Order | None:
        """MARKET-вход: размер по риску risk_frac капитала и расстоянию до стопа.

        Стоп замораживается на сигнальном баре: Low(B) - atr_mult * ATR(B).
        Сайзинг ведётся по оценке входа = close сигнального бара; знаменатель
        — Entry - SL. None — входить нечем: ATR не готов, риск неположителен
        (Low неподалёку от входа при горячем ATR) или капитал не положителен.
        """
        atr = self._atr_h1_value
        if atr is None or atr <= 0 or state.equity <= 0:
            return None
        stop = bar_h1.low - self._atr_mult * atr
        risk = bar_h1.close - stop
        if risk <= 0:
            return None
        qty = state.equity * self._risk_frac / risk
        self._entry_pending = True
        self._entry_stop = stop
        return Order(Side.BUY, OrderKind.MARKET, qty, None, None, False)

    def _bracket_orders(self, bar: Bar, state: StrategyState) -> list[Order]:
        """Брекет по открытой позиции: стоп на весь объём, половина на TP1.

        Цена входа оценивается как close бара, на котором вход исполнен
        (MARKET исполняется по закрытию бара плюс проскальзывание), стоп —
        замороженный с сигнального бара. Цель зеркалится половинным
        объёмом: движок исполнит TP1 только этой половиной.
        """
        stop = self._entry_stop
        if stop is None:
            return []
        entry = bar.close
        qty = state.position
        risk = entry - stop
        if risk <= 0:
            return []
        self._bracket_qty = qty
        self._entry_est = entry
        self._tp2 = entry + self._tp2_r * risk
        return [
            Order(Side.SELL, OrderKind.STOP_MARKET, qty, None, stop, True),
            Order(Side.SELL, OrderKind.LIMIT, qty * _HALF, entry + self._tp1_r * risk, None, True),
        ]

    def _tp1_fills(self, state: StrategyState) -> bool:
        """Половина ушла: позиция открыта и меньше, чем была при брекете.

        Единственный способ уменьшиться младше полного объёма — исполнение
        TP1: стопы, сведение и ливидация уходят всем остатком.
        """
        if self._tp1_done or self._bracket_qty is None:
            return False
        return Decimal("0") < state.position < self._bracket_qty

    def _after_tp1_orders(self, state: StrategyState) -> list[Order]:
        """Брекет по остатку: стоп в безубыток, цель TP2 на весь остаток.

        Уровни — те же замороженные значения брекета: безубыток = оценка
        входа, TP2 = оценка входа + tp2_r * R. Остаток берётся из
        фактического размера позиции, а не из расчёта половины.
        """
        entry = self._entry_est
        target = self._tp2
        if entry is None or target is None:
            return []
        remaining = state.position
        return [
            Order(Side.SELL, OrderKind.STOP_MARKET, remaining, None, entry, True),
            Order(Side.SELL, OrderKind.LIMIT, remaining, target, None, True),
        ]

    def _reset_trade(self) -> None:
        """Позиция закрылась целиком: состояние сделки обратно к чистому."""
        self._entry_stop = None
        self._entry_est = None
        self._bracket_qty = None
        self._tp2 = None
        self._tp1_done = False
