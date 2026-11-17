
"""
CGRCoinBot v0.3.4
Sinyal Performans Veri Katmanı (SQLite).

Görevler:
- signal_performance tablosunun oluşturulması ve yönetimi.
- signal_events tablosundaki AL/SAT olaylarının listelenmesi.
- Hedef mum kapanışları ile performans hesaplama sonuçlarının idempotent kaydı.
- İstatistiksel raporlama için performans verilerinin sorgulanması.
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from .candle_store import db_session


def initialize_performance_table():
    """signal_performance tablosunu oluşturur."""
    with db_session() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS signal_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL,
                pair TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                event_candle_time TEXT NOT NULL,
                horizon_candles INTEGER NOT NULL,
                entry_close REAL NOT NULL,
                exit_close REAL NOT NULL,
                raw_change_pct REAL NOT NULL,
                directional_change_pct REAL NOT NULL,
                result_label TEXT NOT NULL,
                evaluated_at TEXT NOT NULL,
                UNIQUE(event_id, horizon_candles),
                FOREIGN KEY(event_id) REFERENCES signal_events(id) ON DELETE CASCADE
            )
        """)


def get_evaluated_horizons(event_id: int) -> set[int]:
    """Belirli bir olay için daha önce değerlendirilmiş ufukları döndürür."""
    with db_session() as con:
        rows = con.execute("""
            SELECT horizon_candles
            FROM signal_performance
            WHERE event_id = ?
        """, (event_id,)).fetchall()
    return {row[0] for row in rows}


def save_performance_record(
    event_id: int,
    pair: str,
    timeframe: str,
    event_candle_time: str,
    horizon_candles: int,
    entry_close: float,
    exit_close: float,
    raw_change_pct: float,
    directional_change_pct: float,
    result_label: str,
) -> bool:
    """
    Hesaplanan performans sonucunu idempotent olarak kaydeder.

    Aynı event_id ve horizon_candles daha önce kaydedilmişse tekrar eklemez.
    True: Yeni kayıt eklendi.
    False: Zaten kayıtlı.
    """
    evaluated_at = datetime.now(timezone.utc).isoformat()
    with db_session() as con:
        cursor = con.execute("""
            INSERT OR IGNORE INTO signal_performance (
                event_id, pair, timeframe, event_candle_time,
                horizon_candles, entry_close, exit_close,
                raw_change_pct, directional_change_pct,
                result_label, evaluated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_id,
            pair,
            timeframe,
            event_candle_time,
            horizon_candles,
            entry_close,
            exit_close,
            raw_change_pct,
            directional_change_pct,
            result_label,
            evaluated_at,
        ))
        return cursor.rowcount == 1


def get_performance_records(
    pair: Optional[str] = None,
    timeframe: Optional[str] = None,
    horizon: Optional[int] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Kaydedilmiş performans kayıtlarını filtreleyerek getirir."""
    limit = max(1, min(limit, 500))
    query = """
        SELECT id, event_id, pair, timeframe, event_candle_time,
               horizon_candles, entry_close, exit_close,
               raw_change_pct, directional_change_pct,
               result_label, evaluated_at
        FROM signal_performance
    """
    conditions = []
    params = []

    if pair:
        conditions.append("pair = ?")
        params.append(pair)
    if timeframe:
        conditions.append("timeframe = ?")
        params.append(timeframe)
    if horizon:
        conditions.append("horizon_candles = ?")
        params.append(horizon)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY event_candle_time DESC, horizon_candles ASC LIMIT ?"
    params.append(limit)

    with db_session() as con:
        rows = con.execute(query, tuple(params)).fetchall()

    records = []
    for row in rows:
        records.append({
            "id": row[0],
            "event_id": row[1],
            "pair": row[2],
            "timeframe": row[3],
            "event_candle_time": row[4],
            "horizon_candles": row[5],
            "entry_close": row[6],
            "exit_close": row[7],
            "raw_change_pct": row[8],
            "directional_change_pct": row[9],
            "result_label": row[10],
            "evaluated_at": row[11],
        })
    return records


def get_actionable_events(
    pair: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Performans ölçümüne tabi olan signal_events kayıtlarını getirir.
    Kural: Yalnızca current_signal IN ('AL', 'SAT') olayları ölçülür.
    """
    query = """
        SELECT id, pair, timeframe, candle_time,
               previous_signal, current_signal, price, reason, created_at
        FROM signal_events
        WHERE current_signal IN ('AL', 'SAT')
    """
    params = []
    if pair:
        query += " AND pair = ?"
        params.append(pair)
    if timeframe:
        query += " AND timeframe = ?"
        params.append(timeframe)

    query += " ORDER BY candle_time ASC, id ASC"

    with db_session() as con:
        rows = con.execute(query, tuple(params)).fetchall()

    events = []
    for r in rows:
        events.append({
            "id": r[0],
            "pair": r[1],
            "timeframe": r[2],
            "candle_time": r[3],
            "previous_signal": r[4],
            "current_signal": r[5],
            "price": r[6],
            "reason": r[7],
            "created_at": r[8],
        })
    return events
