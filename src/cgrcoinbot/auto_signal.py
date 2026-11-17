
"""
CGRCoinBot v0.3.3
Otomatik Sinyal İzleme ve Olay Tetikleme Servisi.

Özellikler:
- Kapanan mumları otomatik olarak analiz eder.
- Gecikme toleransı (CLOSE_GRACE_MS: 15s) gözetilerek verinin kapanmış,
  eksiksiz ve taze olduğunu doğrular.
- Analiz sonucunu signal_service üzerinden işler.
- signals tablosuna analiz kaydı, geçerli değişimlerde ise signal_events
  tablosuna olay kaydı atar.
- VERI_YOK durumunda hiçbir tabloya kayıt yapmaz.
- WebSocket ve REST recovery sonrasında thread-safe ve asenkron uyumlu çalışır.
"""

import asyncio
import logging
import time
from typing import Optional

from .main import get_tracked_pairs
from .signal_engine import analyze_pair
from .signal_service import process_analysis_result


logger = logging.getLogger("CGRCoinBot")

SUPPORTED_TIMEFRAMES = ["5m", "15m", "1h", "4h"]


def process_closed_candle(
    pair: str,
    timeframe: str,
    open_time: Optional[int] = None,
    now_ms: Optional[int] = None,
) -> dict:
    """
    Kapanmış bir mum için sinyal analizini ve olay tespitini gerçekleştirir.

    Parametreler:
        pair: Analiz edilecek parite (örn: BTC/USDT veya BTCUSDT)
        timeframe: Zaman dilimi ('5m', '15m', '1h', '4h')
        open_time: Kapanan mumun açılış milisaniyesi (opsiyonel)
        now_ms: Test ve tolerans hesaplaması için referans zamanı (opsiyonel)

    Dönen Değer:
        process_analysis_result() fonksiyonunun döndürdüğü durum sözlüğü.
    """
    # Standart parite formatına çevir (BTCUSDT -> BTC/USDT)
    if "/" not in pair and pair.endswith("USDT"):
        base = pair[:-4]
        formatted_pair = f"{base}/USDT"
    else:
        formatted_pair = pair

    if timeframe not in SUPPORTED_TIMEFRAMES:
        logger.warning(
            "Desteklenmeyen zaman dilimi: %s (%s)",
            timeframe,
            formatted_pair,
        )
        return {
            "status": "GECERSIZ_TIMEFRAME",
            "is_new_signal": False,
            "event_created": False,
            "message": f"Desteklenmeyen zaman dilimi: {timeframe}",
        }

    try:
        # signal_engine üzerinden mumları yükle, doğrula ve göstergeleri hesapla
        result = analyze_pair(formatted_pair, timeframe=timeframe, now_ms=now_ms)
        signal = result.get("signal")

        if signal == "VERI_YOK":
            reason = result.get("reason", "Bilinmeyen veri hatası")
            logger.info(
                "VERI_YOK | %s | %s | Neden: %s",
                formatted_pair,
                timeframe,
                reason,
            )
            return {
                "status": "VERI_HATASI",
                "is_new_signal": False,
                "event_created": False,
                "message": f"VERI_YOK: {reason}",
            }

        # Sinyali ve olası geçişi signal_service ile işle
        status_info = process_analysis_result(result)

        if status_info["event_created"]:
            logger.info(
                "YENI SINYAL OLAYI | %s | %s | %s -> %s | Fiyat: %.4f | Mum: %s",
                formatted_pair,
                timeframe,
                status_info.get("previous_signal"),
                status_info.get("current_signal"),
                result.get("price", 0.0),
                result.get("candle_time"),
            )
        elif status_info["is_new_signal"]:
            logger.info(
                "ANALIZ KAYDEDILDI | %s | %s | Sinyal: %s | Fiyat: %.4f | Mum: %s (%s)",
                formatted_pair,
                timeframe,
                signal,
                result.get("price", 0.0),
                result.get("candle_time"),
                status_info.get("status"),
            )
        else:
            logger.debug(
                "TEKRAR ATLANDI | %s | %s | Mum: %s",
                formatted_pair,
                timeframe,
                result.get("candle_time"),
            )

        return status_info

    except Exception as exc:
        logger.exception(
            "Otomatik sinyal işleme hatası: %s %s: %s",
            formatted_pair,
            timeframe,
            exc,
        )
        return {
            "status": "HATA",
            "is_new_signal": False,
            "event_created": False,
            "message": str(exc),
        }


async def on_candle_closed_async(
    pair: str,
    timeframe: str,
    open_time: Optional[int] = None,
) -> dict:
    """
    WebSocket veya asenkron olay döngüsünden çağrılmak üzere,
    SQLite ve analiz işlemlerini asyncio.to_thread içinde yürütür.
    Ana WebSocket döngüsünü kesinlikle kilitlemez.
    """
    return await asyncio.to_thread(
        process_closed_candle,
        pair,
        timeframe,
        open_time,
    )


def scan_once(timeframes: Optional[list[str]] = None) -> dict:
    """
    Takip edilen tüm pariteler için anlık tek seferlik tarama yapar.
    CLI veya test amacıyla senkron olarak çağrılabilir.
    """
    tfs = timeframes or SUPPORTED_TIMEFRAMES
    pairs = get_tracked_pairs()
    summary = {
        "processed": 0,
        "new_signals": 0,
        "new_events": 0,
        "data_errors": 0,
    }

    for pair in pairs:
        for tf in tfs:
            res = process_closed_candle(pair, tf)
            summary["processed"] += 1
            if res.get("is_new_signal"):
                summary["new_signals"] += 1
            if res.get("event_created"):
                summary["new_events"] += 1
            if res.get("status") == "VERI_HATASI":
                summary["data_errors"] += 1

    return summary
