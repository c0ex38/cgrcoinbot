
from pathlib import Path

import pandas as pd


# Projenin ana dizini
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Binance piyasa verilerinin bulunduğu klasör
DATA_DIR = PROJECT_ROOT / "user_data" / "data" / "binance"


def get_market_summary(pair: str, timeframe: str = "15m") -> dict:
    """
    Belirtilen coin için geçmiş fiyat verilerini okur.

    Son kapanış fiyatını, son 24 saatlik fiyat değişimini
    ve 24 saatlik işlem hacmini hesaplar.

    Bu fonksiyon herhangi bir alım-satım emri göndermez.
    """

    pair_name = pair.replace("/", "_")
    file_path = DATA_DIR / f"{pair_name}-{timeframe}.feather"

    if not file_path.exists():
        raise FileNotFoundError(
            f"Veri dosyası bulunamadı: {file_path}"
        )

    # Feather dosyasındaki mum verilerini oku.
    df = pd.read_feather(file_path)
    df = df.sort_values("date").reset_index(drop=True)

    if df.empty:
        raise ValueError(f"Veri dosyası boş: {pair}")

    # Son kapanış fiyatı
    current_price = float(df.iloc[-1]["close"])

    # Verinin son 24 saatini seç.
    last_date = pd.to_datetime(df.iloc[-1]["date"], utc=True)
    start_date = last_date - pd.Timedelta(hours=24)

    dates = pd.to_datetime(df["date"], utc=True)
    last_24h = df.loc[
        (dates > start_date) & (dates <= last_date)
    ]

    # 24 saatlik değişim için başlangıç fiyatı
    previous_rows = df.loc[dates <= start_date]

    price_change = None

    if not previous_rows.empty:
        previous_price = float(previous_rows.iloc[-1]["close"])

        if previous_price != 0:
            price_change = (
                (current_price - previous_price)
                / previous_price
            ) * 100

    # İşlem hacmi, coin miktarı cinsindendir.
    volume_24h = float(last_24h["volume"].sum())

    return {
        "pair": pair,
        "timeframe": timeframe,
        "price": current_price,
        "change_24h": price_change,
        "volume_24h": volume_24h,
        "last_updated": last_date.isoformat(),
    }
