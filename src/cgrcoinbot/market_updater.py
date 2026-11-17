
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .main import get_tracked_pairs
from .market_data import get_market_summary


# Proje dizinleri
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FREQTRADE_DIR = PROJECT_ROOT / "freqtrade"
CONFIG_PATH = PROJECT_ROOT / "user_data" / "config.json"

# Piyasa verilerini her 60 saniyede bir güncelle
UPDATE_INTERVAL = 60

# İndirilecek mum zaman dilimleri
TIMEFRAMES = ["5m", "15m", "1h", "4h"]

# Kayıt sistemi
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("CGRCoinBot")


def update_market_data() -> bool:
    """
    Freqtrade kullanarak piyasa verilerini günceller.

    Yalnızca download-data komutu çalıştırılır.
    Alım-satım emirleri oluşturulmaz.
    """

    command = [
        sys.executable,
        "-m",
        "freqtrade",
        "download-data",
        "--config",
        str(CONFIG_PATH),
        "--timeframes",
        *TIMEFRAMES,
        "--new-pairs-days",
        "30",
    ]

    logger.info("Piyasa verileri güncelleniyor...")

    try:
        result = subprocess.run(
            command,
            cwd=FREQTRADE_DIR,
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )

        logger.info("Veriler başarıyla güncellendi.")
        logger.debug(result.stdout)
        return True

    except subprocess.TimeoutExpired:
        logger.error("Veri indirme zaman aşımına uğradı.")
    except subprocess.CalledProcessError as error:
        logger.error(
            "Freqtrade güncelleme hatası: %s",
            error.stderr or error.stdout,
        )
    except OSError as error:
        logger.error("Sistem hatası: %s", error)

    return False


def print_market_summary():
    """Takip edilen coinlerin son verilerini gösterir."""

    for pair in get_tracked_pairs():
        try:
            data = get_market_summary(pair)

            change = data["change_24h"]
            change_text = (
                f"{change:+.2f}%"
                if change is not None
                else "Veri yetersiz"
            )

            print(
                f"{pair:<12} "
                f"Fiyat: {data['price']:>12,.4f} USDT | "
                f"24s: {change_text}"
            )

        except (FileNotFoundError, ValueError) as error:
            logger.error("%s: %s", pair, error)


def main():
    """Piyasa verilerini belirlenen aralıklarla yeniler."""

    logger.info("CGRCoinBot v0.2.0 başlatıldı.")
    logger.info("Çalışma modu: SADECE PİYASA TAKİBİ")
    logger.info("Güncelleme aralığı: %s saniye", UPDATE_INTERVAL)

    try:
        while True:
            start = time.monotonic()

            logger.info(
                "Güncelleme döngüsü: %s",
                datetime.now(timezone.utc).isoformat(),
            )

            if update_market_data():
                print_market_summary()
            else:
                logger.warning(
                    "Veri yenilenemedi. Eski fiyatlar "
                    "güncel olarak gösterilmeyecek."
                )

            elapsed = time.monotonic() - start
            time.sleep(max(0, UPDATE_INTERVAL - elapsed))

    except KeyboardInterrupt:
        logger.info("CGRCoinBot kullanıcı tarafından durduruldu.")


if __name__ == "__main__":
    main()
