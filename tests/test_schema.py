from decimal import Decimal
from pathlib import Path

from strategylab.data.schema import SCHEMA, connect


def test_decimal_survives_round_trip(tmp_path: Path) -> None:
    with connect(tmp_path / "t.db") as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO bars_1m VALUES (?,?,?,?,?,?,?,?)",
            ("BTCUSDT", 1717200000, "67659.5", "67735.6", "67650.0", "67663.2",
             "12.3456789012345", "fit"),
        )
        row = conn.execute(
            "SELECT open, high, low, close, volume FROM bars_1m"
        ).fetchone()
    assert row[0] == Decimal("67659.5")
    assert row[4] == Decimal("12.3456789012345")