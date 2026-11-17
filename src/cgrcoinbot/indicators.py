
"""
CGRCoinBot v0.3.0
RSI, MACD ve EMA teknik göstergeleri.
"""

import pandas as pd


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Kapanmış mumların kapanış fiyatlarından
    teknik göstergeleri hesaplar.

    Gelecekteki verileri kullanmaz.
    """
    df = df.copy()
    close = pd.to_numeric(df["close"])

    # EMA 20 ve EMA 50
    df["ema20"] = close.ewm(
        span=20, adjust=False
    ).mean()

    df["ema50"] = close.ewm(
        span=50, adjust=False
    ).mean()

    # MACD (12, 26, 9)
    ema12 = close.ewm(
        span=12, adjust=False
    ).mean()

    ema26 = close.ewm(
        span=26, adjust=False
    ).mean()

    df["macd"] = ema12 - ema26

    df["macd_signal"] = df["macd"].ewm(
        span=9, adjust=False
    ).mean()

    df["macd_histogram"] = (
        df["macd"] - df["macd_signal"]
    )

    # RSI 14 - Wilder tipi üssel ortalama
    delta = close.diff()

    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    avg_gain = gains.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14,
    ).mean()

    avg_loss = losses.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14,
    ).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))

    # Tek yönlü hareketlerde sıfıra bölünmeyi önle.
    rsi = rsi.mask(
        (avg_loss == 0) & (avg_gain > 0), 100
    )
    rsi = rsi.mask(
        (avg_gain == 0) & (avg_loss > 0), 0
    )
    rsi = rsi.mask(
        (avg_gain == 0) & (avg_loss == 0), 50
    )

    df["rsi"] = rsi

    return df
