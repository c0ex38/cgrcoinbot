
"""
CGRCoinBot v0.3.5
Otomatik Sinyal Performans Değerlendirme Modülü.

Özellikler:
- Yeni kapanmış mum kaydedildiğinde veya recovery tamamlandığında,
  yalnızca ilgili parite ve timeframe için olgunlaşan AL/SAT olaylarını değerlendirir.
- Startup backlog catch-up: Başlangıçta henüz hesaplanmamış olgunlaşmış olayları sınırlar dahilinde tamamlar.
- Asenkron wrapper ile ana WebSocket event loop'unu bloklamaz (asyncio.to_thread).
- UNIQUE(event_id, horizon_candles) ile mükerrer hesaplama ve kayıt oluşturmaz (idempotent).
"""

import asyncio
import logging
from typing import Optional, Dict

from .performance_service import (
    initialize_performance_table,
    evaluate_all_performances,
)

logger = logging.getLogger("CGRCoinBot")


def process_auto_performance(
    pair: str,
    timeframe: str,
    now_ms: Optional[int] = None,
    limit: int = 50,
) -> Dict[str, int]:
    """
    Belirli bir parite ve timeframe serisi için olgunlaşmış performansları hesaplar.

    Yalnızca bu parite ve timeframe'e ait AL/SAT olayları incelenir,
    tüm veritabanı gereksiz yere taranmaz (sınırlı ve odaklı sorgulama).
    """
    # Standart parite formatı (BTCUSDT -> BTC/USDT)
    if "/" not in pair and pair.endswith("USDT"):
        base = pair[:-4]
        formatted_pair = f"{base}/USDT"
    else:
        formatted_pair = pair

    try:
        summary = evaluate_all_performances(
            now_ms=now_ms,
            pair=formatted_pair,
            timeframe=timeframe,
            limit=limit,
        )

        if summary["evaluated_new"] > 0:
            logger.info(
                "OTOMATIK PERFORMANS KAYDEDILDI | %s | %s | Yeni: %d | Zaten Kayıtlı: %d",
                formatted_pair,
                timeframe,
                summary["evaluated_new"],
                summary["already_evaluated"],
            )
        elif summary["total_events"] > 0:
            logger.debug(
                "PERFORMANS BEKLIYOR | %s | %s | Olay: %d | Bekleyen: %d | Eksik: %d",
                formatted_pair,
                timeframe,
                summary["total_events"],
                summary["pending"],
                summary["missing_data"],
            )

        return summary

    except Exception as exc:
        logger.exception(
            "Otomatik performans hesaplama hatası: %s %s: %s",
            formatted_pair,
            timeframe,
            exc,
        )
        return {
            "total_events": 0,
            "evaluated_new": 0,
            "already_evaluated": 0,
            "pending": 0,
            "missing_data": 0,
            "invalid_reference": 0,
        }


async def on_candle_performance_async(
    pair: str,
    timeframe: str,
    now_ms: Optional[int] = None,
) -> Dict[str, int]:
    """
    WebSocket veya recovery akışından çağrılan asenkron sarmalayıcı.
    Hesaplamaları arka plan thread havuzuna (to_thread) devrederek
    ana WebSocket akışını asla kilitlemez.
    """
    return await asyncio.to_thread(
        process_auto_performance,
        pair,
        timeframe,
        now_ms,
    )


def startup_performance_catchup(limit: int = 100) -> Dict[str, int]:
    """
    Uygulama açılışında daha önce hesaplanmamış olgunlaşmış olaylar
    için kontrollü bir backlog taraması gerçekleştirir.
    """
    initialize_performance_table()
    logger.info("Açılış performans backlog kontrolü başlatılıyor (limit=%d)...", limit)
    summary = evaluate_all_performances(limit=limit)
    if summary["evaluated_new"] > 0:
        logger.info(
            "BACKLOG TAMAMLANDI | Yeni Hesaplanan: %d | Zaten Kayıtlı: %d",
            summary["evaluated_new"],
            summary["already_evaluated"],
        )
    else:
        logger.info(
            "BACKLOG TEMİZ | Bekleyen yeni olgunlaşmış performans kaydı yok (Olay: %d).",
            summary["total_events"],
        )
    return summary
