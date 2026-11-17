
"""
CGRCoinBot v0.3.0
Kurallara dayalı AL / SAT / BEKLE sinyal motoru.

Bu modül emir göndermez.
"""

import math
import time
from datetime import datetime, timezone

import pandas as pd

from .candle_store import INTERVAL_MS, db_session
from .indicators import calculate_indicators


MIN_CANDLES = 100
DEFAULT_TIMEFRAME = "15m"
CLOSE_GRACE_MS = 15000


def load_candles(
    symbol: str,
    timeframe: str,
    limit: int = 200,
) -> pd.DataFrame:
    """SQLite üzerinden son kapanmış mumları yükler."""

    with db_session() as con:
        rows = con.execute(
            """
            SELECT open_time, open, high, low, close, volume
            FROM candles
            WHERE symbol = ? AND timeframe = ?
            ORDER BY open_time DESC
            LIMIT ?
            """,
            (symbol, timeframe, limit),
        ).fetchall()

    df = pd.DataFrame(
        rows,
        columns=[
            "open_time", "open", "high",
            "low", "close", "volume"
        ],
    )

    if df.empty:
        return df

    df = df.sort_values(
        "open_time"
    ).reset_index(drop=True)

    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="raise")

    return df


def validate_candles(
    df: pd.DataFrame,
    timeframe: str,
    now_ms: int | None = None,
) -> tuple[bool, str]:
    """
    Sinyal öncesi veri güvenliğini kontrol eder.

    - Yeterli mum var mı?
    - Mumlar düzenli mi?
    - Gelecek tarihli kayıt var mı?
    - Son kapanmış mum mevcut mu?
    """

    if timeframe not in INTERVAL_MS:
        return False, "Desteklenmeyen zaman dilimi"

    if len(df) < MIN_CANDLES:
        return False, "Yetersiz mum verisi"

    interval = INTERVAL_MS[timeframe]

    times = df["open_time"].astype("int64")

    if (times.diff().dropna() != interval).any():
        return False, "Eksik veya düzensiz mum var"

    if now_ms is None:
        now_ms = int(time.time() * 1000)

    latest_expected = (
        (now_ms - CLOSE_GRACE_MS) // interval
    ) * interval - interval

    latest_actual = int(times.iloc[-1])

    if latest_actual > latest_expected:
        return False, "Tamamlanmamış veya gelecek tarihli mum"

    if latest_actual < latest_expected:
        return False, "Son kapanmış mum eksik veya veri eski"

    return True, "Veri sağlıklı"


def evaluate_signal(
    rsi: float,
    ema20: float,
    ema50: float,
    macd_histogram: float,
) -> tuple[str, str]:
    """Teknik gösterge değerlerinden sinyal üretir."""

    values = (rsi, ema20, ema50, macd_histogram)

    if not all(math.isfinite(value) for value in values):
        return "BEKLE", "Gösterge hesaplaması tamamlanamadı"

    if not 0 <= rsi <= 100:
        return "BEKLE", "Geçersiz RSI değeri"

    bullish_trend = ema20 > ema50
    bearish_trend = ema20 < ema50

    positive_momentum = macd_histogram > 0
    negative_momentum = macd_histogram < 0

    if (
        bullish_trend
        and positive_momentum
        and 50 <= rsi <= 70
    ):
        return (
            "AL",
            "EMA20 > EMA50, MACD pozitif, RSI 50-70",
        )

    if (
        bearish_trend
        and negative_momentum
        and 30 <= rsi < 50
    ):
        return (
            "SAT",
            "EMA20 < EMA50, MACD negatif, RSI 30-50",
        )

    return (
        "BEKLE",
        "AL veya SAT koşullarının tamamı sağlanmadı",
    )


def analyze_pair(
    pair: str,
    timeframe: str = DEFAULT_TIMEFRAME,
    now_ms: int | None = None,
) -> dict:
    """
    Tek bir pariteyi analiz eder.

    Hatalı veya eski veri varsa sinyal üretimini
    engeller ve VERI_YOK sonucu döndürür.
    """

    symbol = pair.replace("/", "").upper()
    df = load_candles(symbol, timeframe)

    valid, reason = validate_candles(df, timeframe, now_ms=now_ms)

    if not valid:
        return {
            "pair": pair,
            "timeframe": timeframe,
            "signal": "VERI_YOK",
            "reason": reason,
        }

    df = calculate_indicators(df)
    last = df.iloc[-1]

    signal, explanation = evaluate_signal(
        rsi=float(last["rsi"]),
        ema20=float(last["ema20"]),
        ema50=float(last["ema50"]),
        macd_histogram=float(last["macd_histogram"]),
    )

    candle_time = datetime.fromtimestamp(
        int(last["open_time"]) / 1000,
        tz=timezone.utc,
    ).isoformat()

    return {
        "pair": pair,
        "timeframe": timeframe,
        "signal": signal,
        "reason": explanation,
        "price": float(last["close"]),
        "rsi": round(float(last["rsi"]), 2),
        "ema20": round(float(last["ema20"]), 4),
        "ema50": round(float(last["ema50"]), 4),
        "macd_histogram": round(
            float(last["macd_histogram"]), 6
        ),
        "candle_time": candle_time,
    }
