
"""Kapanmış mumlar için SQLite veri katmanı."""

import sqlite3
from pathlib import Path
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "user_data" / "market.db"

INTERVAL_MS = {
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
}


def connect_db():
    """Veritabanı bağlantısını açar."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.execute("PRAGMA journal_mode=WAL")
    return con

@contextmanager
def db_session():
    """
    SQLite bağlantısını güvenli şekilde yönetir.

    İşlem başarılıysa commit yapar.
    Hata oluşursa rollback yapar.
    Her durumda bağlantıyı kapatır.
    """

    con = connect_db()

    try:
        yield con
        con.commit()

    except Exception:
        con.rollback()
        raise

    finally:
        con.close()


def initialize_db():
    """Mum tablosunu oluşturur."""
    with db_session() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS candles (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                open_time INTEGER NOT NULL,
                open TEXT NOT NULL,
                high TEXT NOT NULL,
                low TEXT NOT NULL,
                close TEXT NOT NULL,
                volume TEXT NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY (
                    symbol, timeframe, open_time
                )
            )
        """)


def save_candle(
    symbol, timeframe, open_time,
    open_price, high, low, close, volume, source
):
    """
    Kapanmış mumu kaydeder.

    Aynı coin, zaman dilimi ve başlangıç saatindeki
    kayıt daha önce varsa günceller.
    """
    if timeframe not in INTERVAL_MS:
        raise ValueError("Desteklenmeyen zaman dilimi")

    with db_session() as con:
        con.execute("""
            INSERT INTO candles (
                symbol, timeframe, open_time,
                open, high, low, close, volume, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, timeframe, open_time)
            DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                source = excluded.source
        """, (
            symbol, timeframe, int(open_time),
            str(open_price), str(high), str(low),
            str(close), str(volume), source
        ))


def latest_candle_time(symbol, timeframe):
    """Son kaydedilen mumun başlangıç zamanını döndürür."""
    with db_session() as con:
        row = con.execute("""
            SELECT MAX(open_time)
            FROM candles
            WHERE symbol = ? AND timeframe = ?
        """, (symbol, timeframe)).fetchone()
    return row[0]


def count_candles():
    """Her parite ve zaman dilimindeki mumları sayar."""
    with db_session() as con:
        return con.execute("""
            SELECT symbol, timeframe, COUNT(*)
            FROM candles
            GROUP BY symbol, timeframe
            ORDER BY symbol, timeframe
        """).fetchall()
