
"""
CGRCoinBot v0.2.1 - Canlı Piyasa Takibi

Binance WebSocket üzerinden herkese açık piyasa
verilerini okur. Alım-satım işlemi gerçekleştirmez.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal

from websockets.asyncio.client import connect

from .main import get_tracked_pairs


logger = logging.getLogger("CGRCoinBot")

# Sadece herkese açık piyasa verileri.
WS_BASE_URL = "wss://stream.binance.com:9443/stream?streams="

# Her coin için terminal çıktısını en fazla saniyede bir göster.
DISPLAY_INTERVAL = 1.0

# Bağlantı koparsa yeniden denemeden önce bekle.
RECONNECT_DELAY = 5


def build_stream_url(pairs: list[str]) -> str:
    """
    Takip listesinden Binance WebSocket adresi oluşturur.
    """

    streams = []

    for pair in pairs:
        symbol = pair.replace("/", "").lower()

        streams.append(f"{symbol}@miniTicker")
        streams.append(f"{symbol}@kline_15m")

    return WS_BASE_URL + "/".join(streams)


def get_event_time(timestamp_ms: int) -> str:
    """Milisaniye zamanını UTC saatine çevirir."""

    return datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=timezone.utc,
    ).strftime("%H:%M:%S")


async def listen_market() -> None:
    """Canlı piyasa mesajlarını dinler."""

    pairs = get_tracked_pairs()

    if not pairs:
        raise ValueError("Takip listesi boş.")

    url = build_stream_url(pairs)

    logger.info("Takip edilen coinler: %s", ", ".join(pairs))
    logger.info("Çalışma modu: SADECE VERİ OKUMA")

    while True:
        try:
            logger.info("Binance WebSocket bağlantısı kuruluyor...")

            async with connect(
                url,
                ping_interval=20,
                ping_timeout=20,
                open_timeout=15,
                max_size=2**20,
            ) as websocket:

                logger.info("WebSocket bağlantısı kuruldu.")

                # Bağlantı yenilendiğinde ekran sayaçlarını sıfırla.
                last_display = {}

                async for raw_message in websocket:
                    try:
                        message = json.loads(raw_message)
                        data = message.get("data", {})

                        if not isinstance(data, dict):
                            continue

                        event_type = data.get("e")

                        if event_type == "24hrMiniTicker":
                            symbol = data["s"]
                            now = time.monotonic()

                            last_time = last_display.get(symbol, 0)

                            if now - last_time < DISPLAY_INTERVAL:
                                continue

                            last_display[symbol] = now

                            price = Decimal(data["c"])
                            open_price = Decimal(data["o"])

                            change = (
                                (price - open_price)
                                / open_price * 100
                                if open_price
                                else Decimal(0)
                            )

                            event_time = get_event_time(data["E"])

                            print(
                                f"[{event_time} UTC] "
                                f"{symbol:<10} "
                                f"Fiyat: {price:>12,.4f} USDT | "
                                f"24s: {change:+.2f}%",
                                flush=True,
                            )

                        elif event_type == "kline":
                            candle = data.get("k", {})

                            # Yalnızca kapanmış mumları işle.
                            if candle.get("x") is not True:
                                continue

                            symbol = data["s"]
                            interval = candle["i"]
                            close_price = Decimal(candle["c"])

                            logger.info(
                                "KAPANMIŞ MUM | %s | %s | "
                                "Kapanış: %s USDT",
                                symbol,
                                interval,
                                close_price,
                            )

                    except (
                        KeyError,
                        ValueError,
                        TypeError,
                        json.JSONDecodeError,
                    ) as error:
                        logger.warning(
                            "Geçersiz piyasa mesajı: %s",
                            error,
                        )

        except asyncio.CancelledError:
            raise

        except (
            OSError,
            TimeoutError,
            ConnectionError,
            Exception,
        ) as error:
            logger.warning(
                "WebSocket bağlantısı kesildi: %s",
                error,
            )
            logger.info(
                "%s saniye sonra yeniden denenecek.",
                RECONNECT_DELAY,
            )

            await asyncio.sleep(RECONNECT_DELAY)


def main() -> None:
    """Canlı piyasa takip uygulamasını başlatır."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    logger.info("CGRCoinBot v0.2.1 başlatıldı.")

    try:
        asyncio.run(listen_market())

    except KeyboardInterrupt:
        logger.info("CGRCoinBot durduruldu.")

    except ValueError as error:
        logger.error("Yapılandırma hatası: %s", error)


if __name__ == "__main__":
    main()
