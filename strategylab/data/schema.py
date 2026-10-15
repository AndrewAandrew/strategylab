import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars_1m (
    symbol TEXT,
    ts INTEGER,
    open TEXT,
    high TEXT,
    low TEXT,
    close TEXT,
    volume TEXT,
    zone TEXT,
    PRIMARY KEY (symbol, ts)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_bars_1m_zone ON bars_1m(symbol, zone, ts);

CREATE TABLE IF NOT EXISTS zones (
    symbol TEXT,
    start_ts INTEGER,
    end_ts INTEGER,
    zone TEXT,
    PRIMARY KEY (symbol, start_ts)
);
"""


def _decimal_or_text(raw: bytes) -> Decimal | str:
    """Числовой TEXT отдаётся как Decimal, любой другой — как str."""
    text = raw.decode()
    try:
        return Decimal(text)
    except InvalidOperation:
        return text


def connect(path: Path) -> sqlite3.Connection:
    """Открывает базу: внешние ключи включены, журнал в режиме WAL."""
    conn = sqlite3.connect(path)
    conn.text_factory = _decimal_or_text
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn