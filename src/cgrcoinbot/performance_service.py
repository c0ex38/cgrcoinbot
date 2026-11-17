
"""
CGRCoinBot v0.3.4
Sinyal Performans Hesaplama ve Değerlendirme Servisi.

Özellikler ve Kurallar:
- signal_events tablosundaki AL ve SAT olaylarını inceler.
- Olay mumunun close fiyatını candles tablosundan doğrular (entry_close).
- [1, 3, 6, 12] mumluk ufuklar (horizons) için hedef mumu arar.
- Hedef mum kapanış zamanını now_ms ve CLOSE_GRACE_MS toleransı ile denetler.
- Hedef mum ile olay mumu arasındaki seride boşluk olup olmadığını doğrular.
- AL için directional_change_pct = raw_change_pct.
- SAT için directional_change_pct = -raw_change_pct (düşüşe isabet).
- Sonuç etiketi: DOGRU_YON (> 0), TERS_YON (< 0), NOTR (== 0).
- İstatistiksel raporlama: örnek sayısı, yön uyumu oranı, ortalama ve medyan değişim.
"""

import math
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

from .candle_store import INTERVAL_MS, db_session
from .signal_engine import CLOSE_GRACE_MS
from .performance_store import (
    initialize_performance_table,
    get_actionable_events,
    get_evaluated_horizons,
    save_performance_record,
    get_performance_records,
)

DEFAULT_HORIZONS = [1, 3, 6, 12]
PRICE_TOLERANCE_REL = 1e-4  # %0.01 göreli tolerans (float yuvarlama farkı için)


def parse_candle_time_ms(candle_time_str: str) -> int:
    """ISO 8601 UTC formatındaki candle_time değerini epoch milisaniyeye çevirir."""
    # Örnek: '2026-11-17T22:15:00+00:00' veya '2026-11-17 22:15:00 UTC'
    clean_str = candle_time_str.replace(" UTC", "+00:00")
    dt = datetime.fromisoformat(clean_str)
    return int(dt.timestamp() * 1000)


def get_candle_close(symbol: str, timeframe: str, open_time_ms: int) -> Optional[float]:
    """candles tablosundan belirtilen mumun close fiyatını çeker."""
    with db_session() as con:
        row = con.execute("""
            SELECT close
            FROM candles
            WHERE symbol = ? AND timeframe = ? AND open_time = ?
        """, (symbol, timeframe, open_time_ms)).fetchone()
    if not row:
        return None
    try:
        val = float(row[0])
        return val if math.isfinite(val) and val > 0 else None
    except (ValueError, TypeError):
        return None


def verify_intermediate_candles(
    symbol: str,
    timeframe: str,
    event_open_ms: int,
    horizon_candles: int,
) -> bool:
    """
    Olay mumu ile hedef mum arasında eksik mum olup olmadığını denetler.
    event_open_ms + i * interval (i = 1 .. horizon_candles) arasındaki tüm mumların
    veritabanında mevcut olması gerekir.
    """
    interval = INTERVAL_MS[timeframe]
    target_times = [event_open_ms + i * interval for i in range(1, horizon_candles + 1)]
    placeholders = ",".join("?" for _ in target_times)

    with db_session() as con:
        rows = con.execute(f"""
            SELECT open_time
            FROM candles
            WHERE symbol = ? AND timeframe = ? AND open_time IN ({placeholders})
        """, (symbol, timeframe, *target_times)).fetchall()

    found_times = {row[0] for row in rows}
    return len(found_times) == horizon_candles


def evaluate_event_horizon(
    event: Dict[str, Any],
    horizon: int,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Tek bir olay ve tek bir ufuk için performans durumunu değerlendirir.

    Dönen durumlar (status):
    - TAMAMLANDI: Performans hesaplandı.
    - BEKLIYOR: Hedef mum henüz kapanmadı.
    - EKSIK_VERI: Hedef mum veya ara mumlar veritabanında eksik.
    - GECERSIZ_REFERANS: Olay referans mumu yok veya event.price ile mum kapanışı uyumsuz.
    """
    pair = event["pair"]
    timeframe = event["timeframe"]
    current_signal = event["current_signal"]
    event_candle_time = event["candle_time"]
    event_price = float(event["price"])

    if timeframe not in INTERVAL_MS:
        return {"status": "GECERSIZ_REFERANS", "message": f"Desteklenmeyen timeframe: {timeframe}"}

    interval = INTERVAL_MS[timeframe]
    event_open_ms = parse_candle_time_ms(event_candle_time)
    symbol = pair.replace("/", "").upper()

    # 1. Referans giriş mumu doğrulaması
    entry_close = get_candle_close(symbol, timeframe, event_open_ms)
    if entry_close is None:
        return {"status": "GECERSIZ_REFERANS", "message": "Olay mumuna ait veri candles tablosunda bulunamadı."}

    # Fiyat farkı kontrolü (göreli tolerans)
    if abs(entry_close - event_price) / entry_close > PRICE_TOLERANCE_REL:
        return {
            "status": "GECERSIZ_REFERANS",
            "message": f"Event fiyatı ({event_price}) ile mum close ({entry_close}) uyuşmuyor.",
        }

    # 2. Hedef mum zamanlaması ve kapanma kontrolü
    target_open_ms = event_open_ms + horizon * interval
    target_close_ms = target_open_ms + interval

    if now_ms is None:
        now_ms = int(time.time() * 1000)

    # Hedef mumun tam kapanmış olması için: now_ms >= target_close_ms + CLOSE_GRACE_MS
    if now_ms < target_close_ms + CLOSE_GRACE_MS:
        return {
            "status": "BEKLIYOR",
            "message": f"Hedef mum henüz tamamlanmadı veya kapanış toleransı içinde ({horizon} mum).",
        }

    # 3. Ara mumların ve hedef mumun eksiksizliği kontrolü
    if not verify_intermediate_candles(symbol, timeframe, event_open_ms, horizon):
        return {
            "status": "EKSIK_VERI",
            "message": f"Hedef mum veya aradaki mum serisinde eksik kayıt var ({horizon} mum).",
        }

    exit_close = get_candle_close(symbol, timeframe, target_open_ms)
    if exit_close is None or exit_close <= 0:
        return {"status": "EKSIK_VERI", "message": "Hedef mum kapanış fiyatı geçersiz."}

    # 4. Yönlü fiyat değişimi hesaplama
    raw_change_pct = ((exit_close / entry_close) - 1.0) * 100.0

    if current_signal == "AL":
        directional_change_pct = raw_change_pct
    elif current_signal == "SAT":
        directional_change_pct = -raw_change_pct
    else:
        return {"status": "GECERSIZ_REFERANS", "message": f"Ölçülemeyen sinyal türü: {current_signal}"}

    if directional_change_pct > 0:
        result_label = "DOGRU_YON"
    elif directional_change_pct < 0:
        result_label = "TERS_YON"
    else:
        result_label = "NOTR"

    return {
        "status": "TAMAMLANDI",
        "event_id": event["id"],
        "pair": pair,
        "timeframe": timeframe,
        "event_candle_time": event_candle_time,
        "horizon_candles": horizon,
        "entry_close": entry_close,
        "exit_close": exit_close,
        "raw_change_pct": round(raw_change_pct, 4),
        "directional_change_pct": round(directional_change_pct, 4),
        "result_label": result_label,
    }


def evaluate_all_performances(
    horizons: Optional[List[int]] = None,
    now_ms: Optional[int] = None,
) -> Dict[str, int]:
    """
    Tüm uygun sinyal olaylarını inceler, olgunlaşmış ufuklar için
    hesaplama yapar ve signal_performance tablosuna idempotent kaydeder.
    """
    initialize_performance_table()
    hzs = horizons or DEFAULT_HORIZONS
    events = get_actionable_events()

    summary = {
        "total_events": len(events),
        "evaluated_new": 0,
        "already_evaluated": 0,
        "pending": 0,
        "missing_data": 0,
        "invalid_reference": 0,
    }

    for ev in events:
        event_id = ev["id"]
        evaluated_hzs = get_evaluated_horizons(event_id)

        for h in hzs:
            if h in evaluated_hzs:
                summary["already_evaluated"] += 1
                continue

            res = evaluate_event_horizon(ev, h, now_ms=now_ms)
            status = res["status"]

            if status == "TAMAMLANDI":
                is_saved = save_performance_record(
                    event_id=res["event_id"],
                    pair=res["pair"],
                    timeframe=res["timeframe"],
                    event_candle_time=res["event_candle_time"],
                    horizon_candles=res["horizon_candles"],
                    entry_close=res["entry_close"],
                    exit_close=res["exit_close"],
                    raw_change_pct=res["raw_change_pct"],
                    directional_change_pct=res["directional_change_pct"],
                    result_label=res["result_label"],
                )
                if is_saved:
                    summary["evaluated_new"] += 1
                else:
                    summary["already_evaluated"] += 1
            elif status == "BEKLIYOR":
                summary["pending"] += 1
            elif status == "EKSIK_VERI":
                summary["missing_data"] += 1
            elif status == "GECERSIZ_REFERANS":
                summary["invalid_reference"] += 1

    return summary


def calculate_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Kayıt listesinden yön uyumu ve istatistiksel metrikleri çıkarır."""
    total = len(records)
    if total == 0:
        return {
            "total": 0,
            "dogru_yon": 0,
            "ters_yon": 0,
            "notr": 0,
            "win_rate": None,
            "avg_change": None,
            "median_change": None,
        }

    dogru = sum(1 for r in records if r["result_label"] == "DOGRU_YON")
    ters = sum(1 for r in records if r["result_label"] == "TERS_YON")
    notr = sum(1 for r in records if r["result_label"] == "NOTR")

    # Fiyat yönü isabet oranı = DOGRU_YON / (DOGRU_YON + TERS_YON) (NÖTR hariç)
    decisive = dogru + ters
    win_rate = (dogru / decisive * 100.0) if decisive > 0 else None

    changes = [r["directional_change_pct"] for r in records]
    avg_change = sum(changes) / len(changes)

    sorted_changes = sorted(changes)
    n = len(sorted_changes)
    if n % 2 == 1:
        median_change = sorted_changes[n // 2]
    else:
        median_change = (sorted_changes[n // 2 - 1] + sorted_changes[n // 2]) / 2.0

    return {
        "total": total,
        "dogru_yon": dogru,
        "ters_yon": ters,
        "notr": notr,
        "win_rate": round(win_rate, 2) if win_rate is not None else None,
        "avg_change": round(avg_change, 4),
        "median_change": round(median_change, 4),
    }


def generate_performance_report(
    pair: Optional[str] = None,
    timeframe: Optional[str] = None,
    horizon: Optional[int] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """Performans kayıtlarını toplayıp gruplandırılmış metrik raporu üretir."""
    records = get_performance_records(
        pair=pair,
        timeframe=timeframe,
        horizon=horizon,
        limit=limit,
    )
    overall_metrics = calculate_metrics(records)

    # Ufuk (horizon) bazında gruplandırma
    by_horizon = {}
    for h in DEFAULT_HORIZONS:
        hz_records = [r for r in records if r["horizon_candles"] == h]
        if hz_records:
            by_horizon[h] = calculate_metrics(hz_records)

    return {
        "records": records,
        "overall": overall_metrics,
        "by_horizon": by_horizon,
    }
