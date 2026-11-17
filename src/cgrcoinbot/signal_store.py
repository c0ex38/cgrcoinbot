
"""
CGRCoinBot v0.3.2
Sinyal geçmişi, tekrarlı kayıt koruması ve sinyal değişim olayları (events).
"""

from datetime import datetime, timezone
from typing import Optional

from .candle_store import db_session


def initialize_signal_table():
    """Sinyal geçmişi ve sinyal olayları tablolarını oluşturur."""

    with db_session() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                candle_time TEXT NOT NULL,
                signal TEXT NOT NULL,
                price REAL NOT NULL,
                rsi REAL NOT NULL,
                ema20 REAL NOT NULL,
                ema50 REAL NOT NULL,
                macd_histogram REAL NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(pair, timeframe, candle_time)
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS signal_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                candle_time TEXT NOT NULL,
                previous_signal TEXT,
                current_signal TEXT NOT NULL,
                price REAL NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(pair, timeframe, candle_time)
            )
        """)


def get_previous_signal(
    pair: str,
    timeframe: str,
    candle_time: str,
) -> Optional[dict]:
    """
    Belirtilen mum zamanından daha eski en son kaydedilmiş sinyali döndürür.
    Yalnızca geçmiş mumları dikkate alır (candle_time < verilen candle_time).
    Kayıt yoksa None döner.
    """
    with db_session() as con:
        row = con.execute("""
            SELECT signal, price, candle_time, reason
            FROM signals
            WHERE pair = ? AND timeframe = ? AND candle_time < ?
            ORDER BY candle_time DESC, id DESC
            LIMIT 1
        """, (pair, timeframe, candle_time)).fetchone()

    if not row:
        return None

    return {
        "signal": row[0],
        "price": row[1],
        "candle_time": row[2],
        "reason": row[3],
    }


def save_signal(result: dict) -> bool:
    """
    Analiz sonucunu signals tablosuna kaydeder.

    Aynı coin, zaman dilimi ve mum için
    daha önce kayıt varsa yeni kayıt oluşturmaz.

    True: Yeni sinyal kaydedildi.
    False: Sinyal zaten kayıtlı veya veri geçersiz.
    """

    if result.get("signal") not in ("AL", "SAT", "BEKLE"):
        return False

    with db_session() as con:
        cursor = con.execute("""
            INSERT OR IGNORE INTO signals (
                pair, timeframe, candle_time,
                signal, price, rsi, ema20, ema50,
                macd_histogram, reason, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            result["pair"],
            result["timeframe"],
            result["candle_time"],
            result["signal"],
            result["price"],
            result["rsi"],
            result["ema20"],
            result["ema50"],
            result["macd_histogram"],
            result["reason"],
            datetime.now(timezone.utc).isoformat(),
        ))

        return cursor.rowcount == 1


def save_signal_event(
    pair: str,
    timeframe: str,
    candle_time: str,
    previous_signal: Optional[str],
    current_signal: str,
    price: float,
    reason: str,
) -> bool:
    """
    Sinyal değişim olayını signal_events tablosuna kaydeder.

    Aynı parite, zaman dilimi ve mum zamanı için olay zaten varsa
    ikinci kez eklenmez (idempotent).

    True: Yeni olay kaydedildi.
    False: Olay zaten kayıtlıydı veya eklenmedi.
    """
    with db_session() as con:
        cursor = con.execute("""
            INSERT OR IGNORE INTO signal_events (
                pair, timeframe, candle_time,
                previous_signal, current_signal,
                price, reason, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pair,
            timeframe,
            candle_time,
            previous_signal,
            current_signal,
            price,
            reason,
            datetime.now(timezone.utc).isoformat(),
        ))
        return cursor.rowcount == 1


def get_signal_history(limit: int = 20):
    """Son kaydedilen sinyalleri listeler."""
    limit = max(1, min(limit, 500))

    with db_session() as con:
        return con.execute("""
            SELECT pair, timeframe, signal,
                   price, candle_time
            FROM signals
            ORDER BY candle_time DESC, id DESC
            LIMIT ?
        """, (limit,)).fetchall()


def get_signal_events(
    limit: int = 20,
    pair: Optional[str] = None,
    timeframe: Optional[str] = None,
):
    """
    Son kaydedilen sinyal değişim olaylarını listeler.
    Opsiyonel olarak pair ve timeframe filtresi alır.
    """
    limit = max(1, min(limit, 500))

    query = """
        SELECT pair, timeframe, previous_signal,
               current_signal, price, candle_time, reason, created_at
        FROM signal_events
    """
    conditions = []
    params = []

    if pair:
        conditions.append("pair = ?")
        params.append(pair)
    if timeframe:
        conditions.append("timeframe = ?")
        params.append(timeframe)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY candle_time DESC, id DESC LIMIT ?"
    params.append(limit)

    with db_session() as con:
        return con.execute(query, tuple(params)).fetchall()
