from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum


@dataclass(frozen=True, slots=True)
class Bar:
    """Закрытый бар: только завершённый бар, открытого состояния у типа нет."""

    ts: int  # Unix-секунды, время ОТКРЫТИЯ бара, UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class Interval(IntEnum):
    """Интервал агрегации: значение — длина бара в секундах."""

    MINUTE = 60
    FIVE_MIN = 300
    FIFTEEN_MIN = 900
    HOUR = 3600

    @property
    def factor(self) -> int:
        """Сколько минутных баров агрегируется в один бар этого интервала."""
        return self.value // Interval.MINUTE.value