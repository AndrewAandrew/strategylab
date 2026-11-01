"""Тесты стратегии JAGUAR (jaguar_ema): синтетические 5m-бары, без реальных данных.

Серии строятся ступенькой (закрытия ±1 за бар, тела плоские): в монотонном
ряду EMA13/EMA34 гарантированно в порядке тренда и растут, поэтому единственный
нужный бар-крафт — сигнальный: медвежья свеча (long) или бычья (short) с
касанием EMA13. Ожидаемые EMA-значения пересчитываются в тестах независимым
Ema-ораклом и сравниваются побайтово (Decimal, тот же контекст).

Часть тестов гоняет стратегию через Engine+BrokerSimulator — это закрепляет
ФАКТИЧЕСКУЮ reduce_only-семантику ядра (два покоящихся стопа, частичный тейк,
сведение окна), а не желаемую.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator, Mapping
from decimal import Decimal

import pytest

from strategylab.broker.costs import CostModel, IntrabarOrder
from strategylab.broker.orders import Fill, Order, OrderKind, Side
from strategylab.broker.sim import BrokerSimulator
from strategylab.core.bars import Bar, Interval
from strategylab.core.engine import Engine
from strategylab.core.metrics import Metrics
from strategylab.strategy.base import Strategy, StrategyState
from strategylab.strategy.examples import STRATEGY_CLASSES, UnknownStrategy, build
from strategylab.strategy.indicators import Ema
from strategylab.strategy.jaguar_ema import JaguarEmaStrategy

START = 1_717_200_000  # граница часа: кратно и 5m
FIVE_MIN = 300
EQUITY = Decimal("10000")
RISK_FRAC = Decimal("0.01")
TP_MULT = Decimal("1.5")
COSTS = CostModel()
SLIPPAGE = COSTS.slippage
ZERO = Decimal("0")


def _mk(index: int, open_: str, high: str, low: str, close: str) -> Bar:
    """5m-бар по строковым ценам: Decimal без float-артефактов."""
    return Bar(
        ts=START + FIVE_MIN * index,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1"),
    )


def _staircase(step: str, start_level: str, count: int = 60) -> list[Bar]:
    """Ступенька закрытий ±step: тела плоские (O==C), триггер не строит."""
    bars: list[Bar] = []
    level = Decimal(start_level)
    delta = Decimal(step)
    for index in range(count):
        close = level + delta * index
        bars.append(
            Bar(
                ts=START + FIVE_MIN * index,
                open=close,
                high=close + Decimal("0.5"),
                low=close - Decimal("0.5"),
                close=close,
                volume=Decimal("1"),
            )
        )
    return bars


# Сигнальный бар long: медвежья свеча, лона касается EMA13, close над EMA13.
SIGNAL_LONG = _mk(60, "160", "160", "150", "156")
# Бар исполнения входа: close 157 — оценка входа по решению 4 (close исполняющего).
FILL_LONG = _mk(61, "157", "157.5", "156.5", "157")
# Сигнальный бар short: бычья свеча, хай касается EMA13, close под EMA13.
SIGNAL_SHORT = _mk(60, "139", "150", "138", "144")
FILL_SHORT = _mk(61, "141", "141.5", "140.5", "141")


def _long_series() -> list[Bar]:
    return _staircase("1", "100") + [SIGNAL_LONG, FILL_LONG]


def _short_series() -> list[Bar]:
    return _staircase("-1", "200") + [SIGNAL_SHORT, FILL_SHORT]


def _oracle_emas(bars: list[Bar]) -> tuple[list[Decimal], list[Decimal]]:
    """Независимый прогон Ema(13)/Ema(34): ожидаемые значения на каждом баре."""
    fast, slow = Ema(13), Ema(34)
    f_values: list[Decimal] = []
    s_values: list[Decimal] = []
    for bar in bars:
        f_value = fast.update(bar.close)
        s_value = slow.update(bar.close)
        assert f_value is not None and s_value is not None
        f_values.append(f_value)
        s_values.append(s_value)
    return f_values, s_values


def _drive(
    strategy: JaguarEmaStrategy,
    bars: list[Bar],
    positions: Mapping[int, Decimal],
    *,
    equity: Decimal = EQUITY,
) -> dict[int, list[Order]]:
    """Эмуляция движка: позиция на баре i берётся из positions (вне — флэт)."""
    out: dict[int, list[Order]] = {}
    for index, bar in enumerate(bars):
        state = StrategyState(
            position=positions.get(index, ZERO), equity=equity, bar_index=index
        )
        orders = strategy.on_bar(bar, state)
        if orders:
            out[index] = orders
    return out


class _ListFeed:
    """Лента из списка 5m-баров."""

    interval = Interval.FIVE_MIN

    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars

    def bars(self) -> Iterator[Bar]:
        yield from self._bars


class _RecordingBroker(BrokerSimulator):
    """Брокер, запоминающий филлы, применённые через apply_bar."""

    def __init__(self, intrabar: IntrabarOrder = IntrabarOrder.PESSIMISTIC) -> None:
        super().__init__(COSTS, intrabar)
        self.fills: list[Fill] = []

    def apply_bar(self, bar: Bar) -> list[Fill]:
        fills = super().apply_bar(bar)
        self.fills.extend(fills)
        return fills


def _engine_run(bars: list[Bar]) -> tuple[_RecordingBroker, list[Fill]]:
    broker = _RecordingBroker()
    Engine().run(
        _ListFeed(bars), JaguarEmaStrategy(), broker, Metrics(equity=EQUITY)
    )
    return broker, list(broker.fills)


def _is_entry(order: Order) -> bool:
    """Заявка входа: не reduce-only MARKET."""
    return order.kind is OrderKind.MARKET and not order.reduce_only


# Реестр, параметры, дефолты пререгистрации


def test_registry_builds_jaguar_and_satisfies_protocol() -> None:
    assert STRATEGY_CLASSES["jaguar_ema"] is JaguarEmaStrategy
    strategy = build("jaguar_ema", {})
    assert strategy.name == "jaguar_ema"
    assert isinstance(strategy, Strategy)
    with pytest.raises(UnknownStrategy):
        build("jaguar_ema_typo", {})


def test_defaults_match_preregistration_k1() -> None:
    """к1 заморожен: риск 1% капитала, TP1 на 1.5R — дефолты конструктора."""
    defaults = {
        name: parameter.default
        for name, parameter in inspect.signature(JaguarEmaStrategy.__init__).parameters.items()
        if parameter.default is not inspect.Parameter.empty
    }
    assert defaults == {"risk_frac": RISK_FRAC, "tp_mult": TP_MULT}
    assert JaguarEmaStrategy.PARAM_NAMES == ("risk_frac", "tp_mult")


def test_from_params_rejects_unknown_and_passes_known() -> None:
    with pytest.raises(ValueError, match="неизвестные параметры jaguar_ema"):
        build("jaguar_ema", {"ema_len": Decimal("13")})
    strategy = JaguarEmaStrategy.from_params({"risk_frac": Decimal("0.02")})
    assert isinstance(strategy, JaguarEmaStrategy)


def test_invalid_params_are_rejected() -> None:
    for name, value in [
        ("risk_frac", ZERO),
        ("risk_frac", Decimal("-1")),
        ("risk_frac", Decimal("1.5")),
        ("tp_mult", ZERO),
    ]:
        with pytest.raises(ValueError, match=name):
            build("jaguar_ema", {name: value})


# Тренд-фильтр читается по t−1, не по t


def test_trend_filter_reads_previous_bar_not_current() -> None:
    """На баре ралли фильтр по t истинен, по t−1 ложен — входа нет; на следующем
    баре (тот же истинный фильтр по t−1) — вход. Отсрочка на один бар и есть
    замороженный §4 фильтр по t−1.
    """
    flat = [_mk(index, "100", "100", "100", "100") for index in range(59)]
    rally = _mk(59, "115", "115", "100", "110")  # медвежья, L<=e13(59)<C: триггер истинен
    pull = _mk(60, "106", "106", "101", "103")  # триггер и фильтр(t−1=59) истинны
    bars = flat + [rally, pull]

    result = _drive(JaguarEmaStrategy(), bars, {})

    f_values, s_values = _oracle_emas(bars)
    # на 59 фильтр по t истинен (иначе тест ничего не различает), по t−1 — ложен
    assert f_values[59] > s_values[59] and f_values[59] > f_values[58]
    assert f_values[58] == s_values[58]  # плоский ряд: строгое > не выполнено
    assert sorted(result) == [60]  # вход ровно на 60, на 59 — несмотря на t-фильтр — пусто
    assert _is_entry(result[60][0]) and result[60][0].side is Side.BUY


# Триггер long: медвежья свеча, лона до EMA13, close над EMA13


def test_long_signal_requires_bearish_candle_and_ema13_touch() -> None:
    """Базовый крафт даёт вход; три разрушенных условия триггера — не дают."""
    base = _staircase("1", "100")
    variants = {
        "bearish touch": SIGNAL_LONG,  # эталон: вход есть
        "bullish candle": _mk(60, "154", "160", "150", "156"),  # C>O — не медвежья
        "no touch": _mk(60, "160", "160", "154", "156"),  # L > EMA13 — касания нет
        "close under ema": _mk(60, "160", "160", "150", "152"),  # C < EMA13 — возврат нет
    }
    entered: list[str] = []
    for name, signal in variants.items():
        result = _drive(JaguarEmaStrategy(), base + [signal], {})
        if result:
            entered.append(name)
    assert entered == ["bearish touch"]


# Сайзинг, капитал, R<=0


def test_entry_size_is_equity_risk_frac_over_r() -> None:
    """qty = equity·risk_frac/R, R = close(t) − EMA34(t) сигнального бара (§3)."""
    bars = _long_series()
    _f_values, s_values = _oracle_emas(bars)
    risk = SIGNAL_LONG.close - s_values[60]
    assert risk > 0

    result = _drive(JaguarEmaStrategy(), bars, {})

    entry = result[60][0]
    assert entry.qty == EQUITY * RISK_FRAC / risk
    assert entry.qty > 0


def test_no_entry_when_risk_is_not_positive() -> None:
    """Гард R<=0: стоп не ниже оценки входа — сделки нет (решение 5 trend_pullback).

    При живом сигнале геометрически недостижим, поэтому проверка прямая:
    history фильтра выставлена вручную, slow подан выше close.
    """
    strategy = JaguarEmaStrategy()
    strategy._p1_fast, strategy._p2_fast = Decimal("100"), Decimal("99")
    strategy._p1_slow, strategy._p2_slow = Decimal("99"), Decimal("98")
    bar = _mk(0, "110", "110", "99.5", "100")
    state = StrategyState(position=ZERO, equity=EQUITY, bar_index=0)

    assert strategy._entry_orders(bar, state, fast=Decimal("99.8"), slow=Decimal("105")) == []
    assert strategy._entry_pending is False
    # та же свеча с позитивным риском и без капитала — тоже входа нет
    assert strategy._entry_orders(bar, state, fast=Decimal("99.8"), slow=Decimal("99")) != []
    strategy._entry_pending = False
    broke = StrategyState(position=ZERO, equity=ZERO, bar_index=0)
    assert strategy._entry_orders(bar, broke, fast=Decimal("99.8"), slow=Decimal("99")) == []


def test_no_entry_without_capital_on_real_series() -> None:
    """equity<=0 — сайзинг нечем, сделки нет (решение 7: капитал из state.equity)."""
    result = _drive(JaguarEmaStrategy(), _long_series(), {}, equity=ZERO)
    assert result == {}


# Позиционный блокировщик новых входов


def test_no_second_entry_while_position_is_open() -> None:
    """Два подряд триггерных бара: во флэте входы на обоих, с занятной
    позицией — только на первом (+ брекет на баре исполнения).
    """
    second = _mk(61, "159", "159", "151", "155")  # тоже медвежья с касанием
    bars = _staircase("1", "100") + [SIGNAL_LONG, second]

    flat_run = _drive(JaguarEmaStrategy(), bars, {})
    entry_qty = flat_run[60][0].qty
    held_run = _drive(JaguarEmaStrategy(), bars, {61: entry_qty})

    assert sorted(flat_run) == [60, 61]  # во флэте второй вход состоялся бы
    entries = [order for orders in held_run.values() for order in orders if _is_entry(order)]
    assert len(entries) == 1  # занятая позиция глушит второй вход
    assert sorted(held_run) == [60, 61]
    assert held_run[60] == entries  # на 60 — тот единственный вход
    assert all(order.reduce_only for order in held_run[61])  # на 61 — только брекет


# Брекет: ровно на баре исполнения, стоп заморожен, TP1 = половина на 1.5R


def test_bracket_is_placed_exactly_on_the_fill_bar() -> None:
    """Сигнальный бар не вешает брекет (reduce_only при флэте запрещён ядром);
    брекет появляется на баре, где позиция знаковая nonzero, и несёт замороженный
    стоп сигнального бара плюс половину объёма на 1.5R от ОЦЕНКИ исполнения.
    """
    bars = _long_series()
    _f_values, s_values = _oracle_emas(bars)
    frozen_stop = s_values[60]
    entry_qty = EQUITY * RISK_FRAC / (SIGNAL_LONG.close - frozen_stop)
    risk = FILL_LONG.close - frozen_stop  # оценка входа = close исполняющего бара

    result = _drive(JaguarEmaStrategy(), bars, {61: entry_qty})

    assert sorted(result) == [60, 61]
    stop, target = result[61]
    assert stop.side is Side.SELL and stop.kind is OrderKind.STOP_MARKET
    assert stop.reduce_only is True
    assert stop.qty == entry_qty  # полный остаток позиции
    assert stop.stop_price == frozen_stop  # заморожен на сигнальном баре
    assert target.side is Side.SELL and target.kind is OrderKind.LIMIT
    assert target.reduce_only is True
    assert target.qty == entry_qty / Decimal("2")  # TP1 — половина
    assert target.limit_price == FILL_LONG.close + TP_MULT * risk


def test_bracket_is_skipped_when_fill_gaps_below_the_stop() -> None:
    """Исполнение гэпом ниже замороженного SL: R<=0 — брекета нет, позиция
    живёт до полного выхода по close<EMA34 (решение 5 trend_pullback).
    """
    gap_fill = _mk(61, "140", "141", "139", "140")  # close ниже SL=143.75...
    exit_bar = _mk(62, "140", "141", "138", "140")  # close всё ещё под EMA34
    bars = _staircase("1", "100") + [SIGNAL_LONG, gap_fill, exit_bar]
    _f_values, s_values = _oracle_emas(_long_series())
    entry_qty = EQUITY * RISK_FRAC / (SIGNAL_LONG.close - s_values[60])

    result = _drive(JaguarEmaStrategy(), bars, {61: entry_qty, 62: entry_qty})

    assert sorted(result) == [60, 62]  # на 61 — ни брекета, ничего: гэп исполнения под SL
    exit_order = result[62][0]
    assert exit_order.kind is OrderKind.MARKET and exit_order.reduce_only is True
    assert exit_order.side is Side.SELL and exit_order.qty == entry_qty


# Полный выход close < EMA34


def test_full_exit_when_close_breaks_ema34() -> None:
    """Выход MARKET reduce_only на остаток подаётся на баре закрытия под EMA34 и
    исполняется на следующем баре — не позже и не через сведение окна.
    """
    bars = _long_series()
    dip = _mk(62, "157", "157", "143.8", "143.9")  # close < EMA34(62), лона выше SL
    after = _mk(63, "145", "146", "144", "145")
    tail = _mk(64, "145", "145.5", "144.5", "145")
    bars = bars + [dip, after, tail]

    broker, fills = _engine_run(bars)

    assert [fill.reason for fill in fills] == ["market", "market"]
    entry, exit_fill = fills
    assert entry.side is Side.BUY
    assert exit_fill.side is Side.SELL
    assert exit_fill.qty == entry.qty  # остаток = вся позиция (TP1 не было)
    assert exit_fill.ts == after.ts  # исполнен на баре after, не на сведении окна
    assert exit_fill.price == after.close * (Decimal(1) - SLIPPAGE)
    assert not broker.position.is_open
    assert broker.pending_orders == []


# Шорт-зеркало


def test_short_mirror_enters_and_brackets() -> None:
    """Бычья свеча в нисходящем контексте: SELL MARKET, зеркальный брекет —
    BUY-стоп на замороженном EMA34 сигнального бара, BUY-лимит половины на −1.5R.
    """
    bars = _short_series()
    _f_values, s_values = _oracle_emas(bars)
    frozen_stop = s_values[60]
    entry_qty = EQUITY * RISK_FRAC / (frozen_stop - SIGNAL_SHORT.close)
    risk = frozen_stop - FILL_SHORT.close

    result = _drive(JaguarEmaStrategy(), bars, {61: -entry_qty})

    assert sorted(result) == [60, 61]
    entry = result[60][0]
    assert entry.side is Side.SELL and entry.kind is OrderKind.MARKET
    assert entry.qty == entry_qty
    stop, target = result[61]
    assert stop.side is Side.BUY and stop.kind is OrderKind.STOP_MARKET and stop.reduce_only
    assert stop.stop_price == frozen_stop
    assert stop.qty == entry_qty
    assert target.side is Side.BUY and target.kind is OrderKind.LIMIT and target.reduce_only
    assert target.qty == entry_qty / Decimal("2")
    assert target.limit_price == FILL_SHORT.close - TP_MULT * risk


# Трейлинг после TP1: max(старый стоп, EMA13 закрытия), назад не уезжает


def test_trailing_stop_follows_ema13_and_never_relaxes() -> None:
    """После TP1 (|позиция| меньше записанного) стоп = EMA13 текущего бара, если
    выше старого; при падении EMA13 — не переэкзистируется (§4, без BE-доумка).
    """
    bars = _long_series()
    tp_bar = _mk(62, "170", "180", "170", "175")  # high >= TP1, close над EMA34
    up_bar = _mk(63, "160", "160.5", "159.5", "160")  # EMA13 ещё выше
    down_bar = _mk(64, "152", "153", "150", "152")  # EMA13 ниже прежнего трейла
    bars = bars + [tp_bar, up_bar, down_bar]

    full = _long_series()
    _f_values, s_values = _oracle_emas(full)
    entry_qty = EQUITY * RISK_FRAC / (SIGNAL_LONG.close - s_values[60])
    half = entry_qty / Decimal("2")

    result = _drive(
        JaguarEmaStrategy(),
        bars,
        {61: entry_qty, 62: half, 63: half, 64: half},
    )

    expected_fast, _ = _oracle_emas(bars)
    stop_61 = result[61][0]
    trail_62 = result[62][0]
    trail_63 = result[63][0]
    assert trail_62.kind is OrderKind.STOP_MARKET and trail_62.reduce_only is True
    assert trail_62.qty == half  # ре-брекет на остаток
    assert trail_62.stop_price == expected_fast[62] > stop_61.stop_price
    assert trail_63.stop_price == expected_fast[63]  # EMA13 вырос — трейлинг жив
    assert 64 not in result  # EMA13 упал ниже стопа — перестановки нет


# ФАКТИЧЕСКАЯ reduce_only-семантика двух покоящихся стопов (аудит broker/sim.py)


def test_second_reduce_only_stop_overrides_the_mirror() -> None:
    """Два покоящихся reduce_only STOP_MARKET в очереди — норма ядра, а не ошибка.

    Аудит sim.py: `submit` зеркалит уровень стопa в `position.stop_price`
    (перезаписывает), отдыхающие reduce_only-стопы `_apply_stops` не исполняет
    вовсе (стоп живёт зеркалом позиции), а `_cancel_reduced` при флэте снимает
    весь reduce-only хвост. Итог: старый стоп остаётся в очереди неактивным,
    активен только последний уровень, выход ровно один, двойного выхода нет
    по построению.
    """
    broker = _RecordingBroker()
    broker.submit(Order(Side.BUY, OrderKind.MARKET, Decimal("10"), None, None, False))
    broker.apply_bar(_mk(0, "100", "100", "100", "100"))
    # брекет: стоп на весь остаток, цель на половину
    broker.submit(
        Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("10"), None, Decimal("90"), True)
    )
    broker.submit(Order(Side.SELL, OrderKind.LIMIT, Decimal("5"), Decimal("120"), None, True))
    broker.apply_bar(_mk(1, "110", "121", "110", "115"))  # TP1: ровно 5 по 120
    assert broker.position.qty == Decimal("5")

    broker.submit(
        Order(Side.SELL, OrderKind.STOP_MARKET, Decimal("5"), None, Decimal("100"), True)
    )
    stops = [o for o in broker.pending_orders if o.kind is OrderKind.STOP_MARKET]
    assert len(stops) == 2  # старый 90 и новый 100 покоятся вместе
    assert broker.position.stop_price == Decimal("100")  # зеркало — последний уровень

    broker.apply_bar(_mk(2, "98", "98", "88", "88"))
    stop_fills = [fill for fill in broker.fills if fill.reason == "stop"]
    assert len(stop_fills) == 1  # двойного выхода нет
    assert stop_fills[0].qty == Decimal("5")  # полный остаток, не 10 из старого стопа
    assert stop_fills[0].price == Decimal("98") * (Decimal(1) - SLIPPAGE)
    assert not broker.position.is_open
    assert broker.pending_orders == []  # ядро сняло и старый неактивный стоп


def test_engine_lifecycle_tp1_then_trailing_stop_single_exit() -> None:
    """Сквозь движок: вход → брекет → TP1 на 1.5R → трейл по EMA13 → один stop-филл.

    Три заявки-стопа прошли через брокер (старый брекетный + трейл), позиция
    умерла ровно одним stop-филлом по трейл-уровню (в примере — по гэпу открытия),
    old stop не оставил второго филла.
    """
    bars = _long_series()
    tp_bar = _mk(62, "170", "180", "170", "175")  # TP1 176.86... в хаях, стоп не задет
    break_bar = _mk(63, "157", "158", "150", "151")  # гэп вниз от трейл-уровня
    bars = bars + [tp_bar, break_bar]

    broker, fills = _engine_run(bars)

    full = _long_series()
    f_values, s_values = _oracle_emas(full)
    entry_qty = EQUITY * RISK_FRAC / (SIGNAL_LONG.close - s_values[60])
    target = FILL_LONG.close + TP_MULT * (FILL_LONG.close - s_values[60])

    assert [fill.reason for fill in fills] == ["market", "take_profit", "stop"]
    entry, take, stop = fills
    assert entry.qty == entry_qty
    assert take.qty == entry_qty / Decimal("2")
    assert take.price == target  # maker-цена уровня, без проскальзывания
    assert stop.qty == entry_qty / Decimal("2")
    # трейл-уровень = EMA13 бара TP1 (156.94...); бар открылся ВЫШЕ уровня (гэпа нет) —
    # база филла сам уровень со скольжением вниз: филл на 13+ пунктов выше исходного
    # SL 143.75 — значит активен именно второй (трейл) стоп, старый не ударал
    trail_level = _oracle_emas(bars)[0][62]
    assert s_values[60] < stop.price < trail_level
    assert stop.price == trail_level * (Decimal(1) - SLIPPAGE)
    assert not broker.position.is_open
    assert broker.pending_orders == []


def test_short_engine_lifecycle_tp1_then_trailing_stop() -> None:
    """Шорт-зеркало сквозь движок: SELL вход → BUY-брекет → TP1 → BUY stop по
    трейл-уровню EMA13 ровно один раз.
    """
    bars = _short_series()
    tp_bar = _mk(62, "130", "131", "112", "114")  # low <= цели, high не бьёт стоп
    rally_bar = _mk(63, "138", "145", "137", "142")  # хай пробивает трейл-стоп
    bars = bars + [tp_bar, rally_bar]

    broker, fills = _engine_run(bars)

    _f_values, s_values = _oracle_emas(_short_series())
    entry_qty = EQUITY * RISK_FRAC / (s_values[60] - SIGNAL_SHORT.close)

    assert [fill.reason for fill in fills] == ["market", "take_profit", "stop"]
    entry, take, stop = fills
    assert entry.side is Side.SELL and entry.qty == entry_qty
    assert take.side is Side.BUY and take.qty == entry_qty / Decimal("2")
    assert stop.side is Side.BUY and stop.qty == entry_qty / Decimal("2")
    assert stop.price == _oracle_emas(bars)[0][62] * (Decimal(1) + SLIPPAGE)
    assert not broker.position.is_open
    assert broker.pending_orders == []
