
"""
CGRCoinBot v0.2.3
Veri bütünlüğü, güncellik ve eksik mum onarımı.

Varsayılan mod sadece kontrol eder.
--repair verilirse eksik mumları Binance REST ile tamamlar.
Alım-satım emri göndermez.
"""

import argparse
import logging
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import urlopen

import json

from .main import get_tracked_pairs
from .candle_store import (
    INTERVAL_MS,
    db_session,
    initialize_db,
    save_candle,
)

logger = logging.getLogger("CGRCoinBot")

API_URL = "https://data-api.binance.vision/api/v3/klines"
TIMEFRAMES = ("5m", "15m", "1h", "4h")
PAGE_LIMIT = 1000
MAX_REQUESTS_PER_SERIES = 20

# Borsa ile yerel saat arasındaki küçük sapmaları tolere eder.
CLOSE_GRACE_MS = 5000


def utc_time(timestamp_ms):
    """Milisaniyeyi okunabilir UTC saatine çevirir."""
    return datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=timezone.utc,
    ).strftime("%Y-%m-%d %H:%M:%S UTC")


def latest_expected_open(timeframe, now_ms):
    """Kapanmış olması beklenen son mumun başlangıcı."""
    interval = INTERVAL_MS[timeframe]
    return (
        (now_ms - CLOSE_GRACE_MS) // interval
    ) * interval - interval


def get_open_times(symbol, timeframe):
    """Kaydedilmiş mumların başlangıç zamanlarını okur."""
    with db_session() as con:
        rows = con.execute(
            """
            SELECT open_time
            FROM candles
            WHERE symbol = ? AND timeframe = ?
            ORDER BY open_time
            """,
            (symbol, timeframe),
        ).fetchall()

    return [row[0] for row in rows]


def find_missing_ranges(times, timeframe, now_ms):
    """
    Kayıtlar arasındaki boşlukları ve son kayıttan
    bugüne kadar eksik kalan mumları bulur.

    İlk kayıt öncesindeki geçmiş bu kapsamda değildir.
    """
    if not times:
        return []

    interval = INTERVAL_MS[timeframe]
    last_expected = latest_expected_open(
        timeframe, now_ms
    )

    ranges = []

    # Kayıtların kendi aralarındaki boşluklar.
    for previous, current in zip(times, times[1:]):
        expected = previous + interval

        if current > expected:
            ranges.append((
                expected,
                current - interval,
            ))

    # Son kayıt güncel değilse aradaki eksik dönem.
    latest = times[-1]

    if latest < last_expected:
        ranges.append((
            latest + interval,
            last_expected,
        ))

    return ranges


def fetch_range(symbol, timeframe, start, end):
    """Binance API'den belirtilen aralığı indirir."""
    params = {
        "symbol": symbol,
        "interval": timeframe,
        "startTime": start,
        "endTime": end,
        "limit": PAGE_LIMIT,
    }

    url = API_URL + "?" + urlencode(params)

    with urlopen(url, timeout=20) as response:
        return json.load(response)


def repair_range(symbol, timeframe, start, end):
    """
    Eksik zaman aralığını sayfalar hâlinde onarır.

    Yalnızca kapanmış ve istenen aralıktaki mumlar
    kaydedilir. Eksik yanıtlar başarı sayılmaz.
    """
    interval = INTERVAL_MS[timeframe]
    cursor = start
    saved = 0

    for _ in range(MAX_REQUESTS_PER_SERIES):
        if cursor > end:
            break

        # Tek istekte en fazla 1000 mum sorgula.
        page_end = min(
            end,
            cursor + (PAGE_LIMIT - 1) * interval,
        )

        candles = fetch_range(
            symbol, timeframe, cursor, page_end
        )

        now_ms = int(time.time() * 1000)
        last_expected = latest_expected_open(
            timeframe, now_ms
        )

        for candle in candles:
            open_time = int(candle[0])
            close_time = int(candle[6])

            if not (cursor <= open_time <= page_end):
                continue

            if open_time > last_expected:
                continue

            if close_time >= now_ms:
                continue

            save_candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=open_time,
                open_price=candle[1],
                high=candle[2],
                low=candle[3],
                close=candle[4],
                volume=candle[5],
                source="REST",
            )
            saved += 1

        # Sonraki sayfaya geç.
        cursor = page_end + interval

    if cursor <= end:
        logger.warning(
            "%s %s: Onarım istek sınırına ulaşıldı.",
            symbol, timeframe
        )

    return saved


def audit_series(symbol, timeframe, repair=False):
    """Tek coin ve zaman dilimini kontrol eder."""
    now_ms = int(time.time() * 1000)

    times = get_open_times(symbol, timeframe)

    if not times:
        logger.warning(
            "%s %s: Hiç mum bulunamadı.",
            symbol, timeframe
        )
        return False

    ranges = find_missing_ranges(
        times, timeframe, now_ms
    )

    missing_count = sum(
        (end - start) // INTERVAL_MS[timeframe] + 1
        for start, end in ranges
    )

    logger.info(
        "%s %-3s | Kayıt: %s | Eksik: %s | Son: %s",
        symbol,
        timeframe,
        len(times),
        missing_count,
        utc_time(times[-1]),
    )

    if not ranges:
        return True

    for start, end in ranges:
        logger.warning(
            "EKSİK | %s %s | %s -> %s",
            symbol,
            timeframe,
            utc_time(start),
            utc_time(end),
        )

        if repair:
            repaired = repair_range(
                symbol, timeframe, start, end
            )
            logger.info(
                "Onarım: %s %s | İşlenen: %s",
                symbol, timeframe, repaired
            )

    if not repair:
        return False

    # Onarım sonrası gerçek kayıtları yeniden sorgula.
    updated = get_open_times(symbol, timeframe)

    remaining = find_missing_ranges(
        updated, timeframe, now_ms
    )

    if remaining:
        logger.error(
            "%s %s: Hâlâ eksik mum bulunuyor.",
            symbol, timeframe
        )
        return False

    logger.info(
        "%s %s: Onarım doğrulandı.",
        symbol, timeframe
    )
    return True


def main():
    """Bütün takip listesini denetler."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Eksik mumları REST üzerinden tamamla",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    initialize_db()

    pairs = get_tracked_pairs()
    results = []

    for pair in pairs:
        symbol = pair.replace("/", "").upper()

        for timeframe in TIMEFRAMES:
            try:
                result = audit_series(
                    symbol,
                    timeframe,
                    repair=args.repair,
                )
                results.append(result)
            except (
                OSError,
                ValueError,
                sqlite3.Error,
                json.JSONDecodeError,
            ):
                logger.exception(
                    "Kontrol başarısız: %s %s",
                    symbol, timeframe
                )
                results.append(False)

    success = sum(results)

    logger.info(
        "SONUÇ: %s/%s veri seti sağlıklı.",
        success, len(results)
    )

    if success != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
