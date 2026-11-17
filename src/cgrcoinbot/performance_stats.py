
"""
CGRCoinBot v0.3.6
Gelişmiş Performans İstatistikleri ve Karşılaştırma Modülü (Salt Okunur).

İşlevler:
- signal_performance ve signal_events tablolarını birleştirerek (JOIN) parite, zaman dilimi,
  ufuk (1, 3, 6, 12 mum) ve sinyal yönü (AL / SAT) bazında karşılaştırmalı istatistikler üretir.
- Toplam olay sayısı, doğru/ters/nötr sayımları, yön isabet oranı (DOGRU / (DOGRU + TERS)).
- %95 Wilson score iki taraflı güven aralığı hesaplaması.
- Ortalama, medyan, min, max ve standart sapma yönlü fiyat değişimi yüzdeleri.
- Tekil event sayısını (COUNT(DISTINCT event_id)) raporlar; bağımlı ufukları bağımsız olay saymaz.
- Küçük örneklem uyarısı (n < 30).
- Kesinlikle borsa emri göndermez, veritabanına yazmaz veya manipülasyon yapmaz.
"""

import math
import statistics
from typing import Optional, List, Dict, Any

from .candle_store import db_session


def wilson_score_interval(correct: int, total_decisive: int, confidence_z: float = 1.96) -> Optional[tuple[float, float]]:
    """
    İki taraflı %95 Wilson score güven aralığını hesaplar (0 - 100 ölçeğinde).

    Yalnızca karar verilmiş (DOGRU_YON + TERS_YON, nötr hariç) n > 0 durumu için geçerlidir.
    total_decisive == 0 ise None döner.
    """
    if total_decisive <= 0 or correct < 0:
        return None

    n = total_decisive
    p = correct / n
    z = confidence_z

    denom = 1.0 + (z ** 2) / n
    center = (p + (z ** 2) / (2.0 * n)) / denom
    margin = (z * math.sqrt((p * (1.0 - p) / n) + (z ** 2) / (4.0 * (n ** 2)))) / denom

    lower = max(0.0, (center - margin) * 100.0)
    upper = min(100.0, (center + margin) * 100.0)

    return round(lower, 2), round(upper, 2)


def get_detailed_performance_records(
    pair: Optional[str] = None,
    timeframe: Optional[str] = None,
    horizon: Optional[int] = None,
    signal: Optional[str] = None,
    limit: int = 2000,
) -> List[Dict[str, Any]]:
    """
    signal_performance kayıtlarını ait olduğu signal_events tablosu ile
    birleştirerek (JOIN) salt-okunur şekilde getirir.
    """
    limit = max(1, min(limit, 10000))

    query = """
        SELECT p.id, p.event_id, p.pair, p.timeframe, p.event_candle_time,
               p.horizon_candles, p.entry_close, p.exit_close,
               p.raw_change_pct, p.directional_change_pct,
               p.result_label, p.evaluated_at,
               e.current_signal
        FROM signal_performance p
        LEFT JOIN signal_events e ON p.event_id = e.id
    """
    conditions = []
    params = []

    if pair:
        conditions.append("p.pair = ?")
        params.append(pair)
    if timeframe:
        conditions.append("p.timeframe = ?")
        params.append(timeframe)
    if horizon:
        conditions.append("p.horizon_candles = ?")
        params.append(horizon)
    if signal:
        conditions.append("e.current_signal = ?")
        params.append(signal)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY p.pair ASC, p.timeframe ASC, p.horizon_candles ASC, e.current_signal ASC, p.event_candle_time DESC LIMIT ?"
    params.append(limit)

    with db_session() as con:
        rows = con.execute(query, tuple(params)).fetchall()

    records = []
    for r in rows:
        records.append({
            "id": r[0],
            "event_id": r[1],
            "pair": r[2],
            "timeframe": r[3],
            "event_candle_time": r[4],
            "horizon_candles": r[5],
            "entry_close": r[6],
            "exit_close": r[7],
            "raw_change_pct": r[8],
            "directional_change_pct": r[9],
            "result_label": r[10],
            "evaluated_at": r[11],
            "current_signal": r[12] or "BILINMIYOR",
        })
    return records


def compute_group_statistics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Belirli bir grup kayıt için gelişmiş istatistikleri hesaplar.
    Sonlu sayı (finite) kontrolleri ve standart sapma gereksinimlerini içerir.
    """
    total = len(records)
    if total == 0:
        return {
            "total": 0,
            "distinct_events": 0,
            "correct": 0,
            "wrong": 0,
            "neutral": 0,
            "non_neutral": 0,
            "direction_hit_rate_pct": None,
            "wilson_ci_95": None,
            "mean_directional_change_pct": None,
            "median_directional_change_pct": None,
            "std_directional_change_pct": None,
            "min_directional_change_pct": None,
            "max_directional_change_pct": None,
            "mean_raw_change_pct": None,
            "is_sample_small": True,
        }

    distinct_events = len({r["event_id"] for r in records})
    correct = sum(1 for r in records if r["result_label"] == "DOGRU_YON")
    wrong = sum(1 for r in records if r["result_label"] == "TERS_YON")
    neutral = sum(1 for r in records if r["result_label"] == "NOTR")
    non_neutral = correct + wrong

    hit_rate = (correct / non_neutral * 100.0) if non_neutral > 0 else None
    wilson_ci = wilson_score_interval(correct, non_neutral)

    # Sonlu sayı doğrulama
    valid_directional = [
        float(r["directional_change_pct"])
        for r in records
        if math.isfinite(float(r["directional_change_pct"]))
    ]
    valid_raw = [
        float(r["raw_change_pct"])
        for r in records
        if math.isfinite(float(r["raw_change_pct"]))
    ]

    if valid_directional:
        mean_dir = sum(valid_directional) / len(valid_directional)
        sorted_dir = sorted(valid_directional)
        n_dir = len(sorted_dir)
        if n_dir % 2 == 1:
            median_dir = sorted_dir[n_dir // 2]
        else:
            median_dir = (sorted_dir[n_dir // 2 - 1] + sorted_dir[n_dir // 2]) / 2.0
        min_dir = min(valid_directional)
        max_dir = max(valid_directional)
        std_dir = statistics.stdev(valid_directional) if len(valid_directional) >= 2 else None
    else:
        mean_dir = median_dir = min_dir = max_dir = std_dir = None

    mean_raw = (sum(valid_raw) / len(valid_raw)) if valid_raw else None

    return {
        "total": total,
        "distinct_events": distinct_events,
        "correct": correct,
        "wrong": wrong,
        "neutral": neutral,
        "non_neutral": non_neutral,
        "direction_hit_rate_pct": round(hit_rate, 2) if hit_rate is not None else None,
        "wilson_ci_95": wilson_ci,
        "mean_directional_change_pct": round(mean_dir, 4) if mean_dir is not None else None,
        "median_directional_change_pct": round(median_dir, 4) if median_dir is not None else None,
        "std_directional_change_pct": round(std_dir, 4) if std_dir is not None else None,
        "min_directional_change_pct": round(min_dir, 4) if min_dir is not None else None,
        "max_directional_change_pct": round(max_dir, 4) if max_dir is not None else None,
        "mean_raw_change_pct": round(mean_raw, 4) if mean_raw is not None else None,
        "is_sample_small": non_neutral < 30,
    }


def generate_advanced_stats_report(
    pair: Optional[str] = None,
    timeframe: Optional[str] = None,
    horizon: Optional[int] = None,
    signal: Optional[str] = None,
    limit: int = 2000,
) -> Dict[str, Any]:
    """
    Belirtilen filtrelere göre gelişmiş performans istatistiklerini ve
    (pair, timeframe, horizon, current_signal) alt gruplarını hesaplar.
    """
    records = get_detailed_performance_records(
        pair=pair,
        timeframe=timeframe,
        horizon=horizon,
        signal=signal,
        limit=limit,
    )

    overall = compute_group_statistics(records)

    # Gruplandırma: (pair, timeframe, horizon_candles, current_signal)
    grouped_data: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in records:
        key = (r["pair"], r["timeframe"], r["horizon_candles"], r["current_signal"])
        if key not in grouped_data:
            grouped_data[key] = []
        grouped_data[key].append(r)

    groups = []
    # Deterministik sıralama
    for key in sorted(grouped_data.keys()):
        grp_records = grouped_data[key]
        stats = compute_group_statistics(grp_records)
        groups.append({
            "pair": key[0],
            "timeframe": key[1],
            "horizon_candles": key[2],
            "signal": key[3],
            "stats": stats,
        })

    return {
        "filters": {
            "pair": pair,
            "timeframe": timeframe,
            "horizon": horizon,
            "signal": signal,
        },
        "overall": overall,
        "groups": groups,
    }
