from collections.abc import Sequence
from decimal import Decimal
from itertools import groupby

from strategylab.core.bars import Bar, Interval

_ZERO = Decimal(0)


class IncompleteBarGroup(Exception):
    """Из минутных баров не набирается целый бар интервала.

    `expected` — сколько минутных баров требовалось, `actual` — сколько пришло
    (ноль, если не пришёл ни один), `ts` — начало окна интервала, в котором
    группа должна была закрыться, то есть время недостроенного бара.
    """

    def __init__(self, expected: int, actual: int, ts: int) -> None:
        super().__init__(f"окно {ts}: минутных баров {actual} из {expected}")
        self.expected = expected
        self.actual = actual
        self.ts = ts


def aggregate(bars: Sequence[Bar], interval: Interval) -> list[Bar]:
    """Бары 1m, собранные в бары `interval`, по возрастанию времени.

    Группировка идёт по `ts // interval.value` — это и есть `factor * 60` из
    плана, — с якорем на эпоху Unix, поэтому границы совпадают с биржевыми, а
    время бара равно началу его окна. Минутные бары на входе обязаны идти по
    возрастанию времени — таким их отдаёт `CandleStore.read_1m`, а внутри
    хранилища дублей по времени не бывает.

    Разрыв не соединяется, и это два разных случая:

    1. Группа неполна — пришло меньше минут, чем `factor`. Бар меньшего размера
       не собирается никогда: он выглядел бы настоящим и исказил прогон.
    2. Группа целиком отсутствует, хотя соседние полны. Длины такие группы не
       видят, пропущенный бар дошёл бы до стратегии как настоящий, поэтому
       пропуск поднимается тем же исключением, с `actual = 0`.

    При `factor == 1` (интервал `MINUTE`) бары возвращаются как есть: агрегировать
    нечего, проверять нечего, и `IncompleteBarGroup` не поднимается никогда.
    """
    factor = interval.factor
    if factor == 1:
        return list(bars)
    width = interval.value
    out: list[Bar] = []
    previous_start: int | None = None
    for start, group in groupby(bars, key=lambda bar: bar.ts - bar.ts % width):
        if previous_start is not None and start != previous_start + width:
            raise IncompleteBarGroup(factor, 0, previous_start + width)
        members = list(group)
        if len(members) != factor:
            raise IncompleteBarGroup(factor, len(members), start)
        out.append(_collapse(start, members))
        previous_start = start
    return out


def _collapse(start: int, members: Sequence[Bar]) -> Bar:
    """Один бар интервала из полной группы минутных баров.

    Значения `Decimal` переносятся как есть и не оборачиваются заново: `read_1m`
    уже отдаёт их фабрикой `connect()`, а повторный `Decimal()` бросил бы
    `TypeError`. Сумма стартуется с `Decimal(0)`, чтобы результат оставался
    `Decimal` и не зависел от того, есть ли элементы.
    """
    return Bar(
        ts=start,
        open=members[0].open,
        high=max(bar.high for bar in members),
        low=min(bar.low for bar in members),
        close=members[-1].close,
        volume=sum((bar.volume for bar in members), _ZERO),
    )
