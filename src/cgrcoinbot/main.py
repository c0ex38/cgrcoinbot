
import json
from pathlib import Path

from .market_data import get_market_summary


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "user_data" / "config.json"


def get_tracked_pairs() -> list[str]:
    """Freqtrade yapılandırmasındaki coin listesini okur."""

    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = json.load(file)

    return config["exchange"]["pair_whitelist"]


def main():
    """Takip edilen coinlerin piyasa özetini gösterir."""

    pairs = get_tracked_pairs()

    print("\n=== CGRCoinBot v0.2.0 ===")
    print("Piyasa Takip Sistemi\n")

    for pair in pairs:
        try:
            data = get_market_summary(pair)

            change = data["change_24h"]
            change_text = (
                f"{change:+.2f}%"
                if change is not None
                else "Hesaplanamadı"
            )

            print(f"Coin: {data['pair']}")
            print(f"Fiyat: {data['price']:,.4f} USDT")
            print(f"24s Değişim: {change_text}")
            print(f"24s Hacim: {data['volume_24h']:,.2f}")
            print(f"Son Veri: {data['last_updated']}")
            print("-" * 40)

        except (FileNotFoundError, ValueError) as error:
            print(f"{pair}: {error}")


if __name__ == "__main__":
    main()
