
"""Binance kapanmış mum toplama ve kurtarma servisi."""

import asyncio
import json
import logging
import time
from urllib.parse import urlencode
from urllib.request import urlopen
from contextlib import suppress
from .data_integrity import audit_series

from websockets.asyncio.client import connect

from .main import get_tracked_pairs
from .auto_signal import on_candle_closed_async
from .auto_performance import on_candle_performance_async, startup_performance_catchup
from .signal_store import initialize_signal_table
from .performance_store import initialize_performance_table
from .candle_store import (
    INTERVAL_MS,
    initialize_db,
    latest_candle_time,
    save_candle,
    count_candles,
)

logger = logging.getLogger("CGRCoinBot")

REST_URL = "https://data-api.binance.vision/api/v3/klines"
WS_URL = "wss://data-stream.binance.vision:443/stream?streams="

TIMEFRAMES = ["5m", "15m", "1h", "4h"]
RECONNECT_SECONDS = 5
PAGE_LIMIT = 1000

# İlk kurulumda zaman dilimi başına kaç mum alınacağı.
INITIAL_CANDLES = 200

# Her 5 dakikada bir veri bütünlüğünü kontrol et.
INTEGRITY_INTERVAL = 300

# Tek kurtarma döngüsünde en fazla 10 sayfa.
MAX_PAGES = 10


def fetch_klines(symbol, timeframe, start_time=None):
    """Herkese açık REST API'den mum verisi alır."""
    params = {
        "symbol": symbol,
        "interval": timeframe,
        "limit": (
            INITIAL_CANDLES
            if start_time is None else PAGE_LIMIT
        ),
    }

    if start_time is not None:
        params["startTime"] = start_time

    url = REST_URL + "?" + urlencode(params)

    with urlopen(url, timeout=20) as response:
        return json.load(response)


def recover_candles(symbol, timeframe):
    """
    İlk açılışta geçmiş mumları indirir.

    Daha sonraki açılışlarda son kaydın devamından
    veri çekerek eksik mumları tamamlar.
    """
    latest = latest_candle_time(symbol, timeframe)

    start_time = (
        None if latest is None
        else latest + INTERVAL_MS[timeframe]
    )

    total = 0
    complete = False

    for _ in range(MAX_PAGES):
        candles = fetch_klines(
            symbol, timeframe, start_time
        )

        if not candles:
            complete = True
            break

        # Açık olan mumları kaydetme.
        now_ms = int(time.time() * 1000)
        closed_count = 0

        for candle in candles:
            open_time = int(candle[0])
            close_time = int(candle[6])

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

            total += 1
            closed_count += 1

        # Dönen son mumdan sonraki başlangıcı hesapla.
        last_open = int(candles[-1][0])
        start_time = last_open + INTERVAL_MS[timeframe]

        # Son sayfada açık mum varsa veya sayfa
        # tam dolmadıysa güncel noktaya ulaştık.
        if len(candles) < PAGE_LIMIT or closed_count < len(candles):
            complete = True
            break

    logger.info(
        "%s %s | REST ile %s mum işlendi",
        symbol, timeframe, total
    )

    if not complete:
        logger.warning(
            "%s %s | Kurtarma sınırına ulaşıldı. "
            "Eksik veri kalmış olabilir.",
            symbol, timeframe
        )

    return complete


def build_ws_url(symbols):
    """Kapanmış mum akışlarının adresini oluşturur."""
    streams = [
        f"{symbol.lower()}@kline_{tf}"
        for symbol in symbols
        for tf in TIMEFRAMES
    ]
    return WS_URL + "/".join(streams)


async def recover_all(symbols):
    """Bağlantı öncesi geçmiş verileri tamamlar ve son kapanmış mumu analiz eder."""
    for symbol in symbols:
        for timeframe in TIMEFRAMES:
            # Ağ ve veritabanı işlemleri event loop'u
            # bloke etmesin diye ayrı thread'de çalışır.
            complete = await asyncio.to_thread(
                recover_candles, symbol, timeframe
            )
            # Recovery sonrası son kapanmış mum için sinyal ve performans kontrolü yap
            if complete:
                try:
                    await on_candle_closed_async(symbol, timeframe)
                except Exception as sig_err:
                    logger.debug(
                        "Recovery sonrası sinyal kontrolü atlandı: %s %s (%s)",
                        symbol,
                        timeframe,
                        sig_err,
                    )
                try:
                    await on_candle_performance_async(symbol, timeframe)
                except Exception as perf_err:
                    logger.debug(
                        "Recovery sonrası performans kontrolü atlandı: %s %s (%s)",
                        symbol,
                        timeframe,
                        perf_err,
                    )


async def periodic_integrity_check(symbols):
    """
    Veri bütünlüğünü periyodik olarak denetler.

    - Her coin ve zaman dilimini kontrol eder.
    - Eksik mumları REST API ile tamamlamayı dener.
    - Ağ veya veri hatalarını kaydeder.
    - WebSocket akışını bloke etmez.
    """

    logger.info("Otomatik veri bütünlüğü servisi başlatıldı.")

    while True:
        healthy = 0
        total = len(symbols) * len(TIMEFRAMES)

        logger.info(
            "Veri bütünlüğü kontrolü başlıyor..."
        )

        for symbol in symbols:
            for timeframe in TIMEFRAMES:
                try:
                    # Senkron REST/SQLite işlemlerini
                    # ayrı bir thread'de çalıştır.
                    result = await asyncio.to_thread(
                        audit_series,
                        symbol,
                        timeframe,
                        True,
                    )

                    if result:
                        healthy += 1

                except asyncio.CancelledError:
                    raise

                except Exception:
                    logger.exception(
                        "Veri kontrol hatası: %s %s",
                        symbol,
                        timeframe,
                    )

        logger.info(
            "OTOMATİK DENETİM SONUCU: %s/%s sağlıklı",
            healthy,
            total,
        )

        if healthy != total:
            logger.warning(
                "Bazı veri setlerinde eksiklik var. "
                "Bir sonraki döngüde tekrar denenecek."
            )

        await asyncio.sleep(INTEGRITY_INTERVAL)



async def collect():
    """Canlı mumları toplayan ana servis."""

    pairs = get_tracked_pairs()

    symbols = [
        pair.replace("/", "").upper()
        for pair in pairs
    ]

    if not symbols:
        raise ValueError("Takip listesi boş.")

    initialize_db()
    initialize_signal_table()
    initialize_performance_table()
    logger.info("Mum, sinyal ve performans veritabanı tabloları hazır.")

    # Açılışta olgunlaşmış bekleyen performanslar için backlog catch-up
    await asyncio.to_thread(startup_performance_catchup)

    url = build_ws_url(symbols)

    while True:
        try:
            # Bağlantı öncesinde son kayıtları tamamla.
            logger.info("Eksik mumlar kontrol ediliyor...")
            await recover_all(symbols)

            logger.info("WebSocket bağlanıyor...")

            async with connect(
                url,
                ping_interval=20,
                ping_timeout=20,
                open_timeout=15,
            ) as websocket:

                logger.info("Mum akışı bağlandı.")

                # Arka planda çalışan denetim görevi.
                integrity_task = asyncio.create_task(
                    periodic_integrity_check(symbols)
                )

                try:
                    async for raw in websocket:
                        try:
                            message = json.loads(raw)
                            data = message.get("data", {})

                            if not isinstance(data, dict):
                                continue

                            if data.get("e") != "kline":
                                continue

                            candle = data.get("k", {})

                            # Yalnızca kapanmış mumlar.
                            if candle.get("x") is not True:
                                continue

                            symbol = data["s"]
                            timeframe = candle["i"]

                            if (
                                symbol not in symbols
                                or timeframe not in TIMEFRAMES
                            ):
                                continue

                            # SQLite yazma işlemi ana
                            # WebSocket döngüsünü engellemesin.
                            await asyncio.to_thread(
                                save_candle,
                                symbol=symbol,
                                timeframe=timeframe,
                                open_time=candle["t"],
                                open_price=candle["o"],
                                high=candle["h"],
                                low=candle["l"],
                                close=candle["c"],
                                volume=candle["v"],
                                source="WebSocket",
                            )

                            logger.info(
                                "MUM KAYDEDİLDİ | %s | %s | %s",
                                symbol,
                                timeframe,
                                candle["c"],
                            )

                            # Mum başarıyla kaydedildikten sonra otomatik sinyal analizi tetikle.
                            try:
                                await on_candle_closed_async(
                                    pair=symbol,
                                    timeframe=timeframe,
                                    open_time=candle["t"],
                                )
                            except Exception as sig_err:
                                logger.warning(
                                    "Otomatik sinyal tetikleme hatası: %s %s: %s",
                                    symbol,
                                    timeframe,
                                    sig_err,
                                )

                            # Mum kaydından sonra olgunlaşan performansları otomatik değerlendir.
                            try:
                                await on_candle_performance_async(
                                    pair=symbol,
                                    timeframe=timeframe,
                                )
                            except Exception as perf_err:
                                logger.warning(
                                    "Otomatik performans tetikleme hatası: %s %s: %s",
                                    symbol,
                                    timeframe,
                                    perf_err,
                                )

                        except (
                            KeyError,
                            ValueError,
                            TypeError,
                            json.JSONDecodeError,
                        ) as error:
                            logger.warning(
                                "Geçersiz mum mesajı: %s",
                                error,
                            )

                finally:
                    # Bağlantı kesilirse eski denetim
                    # görevi de durdurulur.
                    integrity_task.cancel()

                    with suppress(asyncio.CancelledError):
                        await integrity_task

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Mum toplama veya bağlantı hatası"
            )
            logger.info(
                "%s saniye sonra tekrar denenecek.",
                RECONNECT_SECONDS,
            )
            await asyncio.sleep(RECONNECT_SECONDS)



def main():
    """Mum toplama servisini başlatır."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )

    logger.info("CGRCoinBot v0.2.2 başlatıldı.")
    logger.info("Mod: Yalnızca veri toplama")

    try:
        asyncio.run(collect())
    except KeyboardInterrupt:
        logger.info("Servis durduruldu.")

        for symbol, timeframe, count in count_candles():
            print(f"{symbol} {timeframe}: {count} mum")


if __name__ == "__main__":
    main()
