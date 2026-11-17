
"""CGRCoinBot v0.3.0 sinyal motoru testleri."""

import unittest

import pandas as pd

from cgrcoinbot.signal_engine import (
    evaluate_signal,
    validate_candles,
)


class SignalEngineTests(unittest.TestCase):

    def test_buy_signal(self):
        """Yükseliş koşullarında AL beklenir."""
        signal, _ = evaluate_signal(
            rsi=58,
            ema20=105,
            ema50=100,
            macd_histogram=0.5,
        )
        self.assertEqual(signal, "AL")

    def test_sell_signal(self):
        """Düşüş koşullarında SAT beklenir."""
        signal, _ = evaluate_signal(
            rsi=42,
            ema20=95,
            ema50=100,
            macd_histogram=-0.5,
        )
        self.assertEqual(signal, "SAT")

    def test_wait_signal(self):
        """Çelişkili koşullarda BEKLE beklenir."""
        signal, _ = evaluate_signal(
            rsi=55,
            ema20=105,
            ema50=100,
            macd_histogram=-0.5,
        )
        self.assertEqual(signal, "BEKLE")

    def test_missing_candle_rejected(self):
        """Aradaki eksik mum analizi engellemeli."""
        times = [
            i * 300_000 for i in range(101)
            if i != 50
        ]

        df = pd.DataFrame({"open_time": times})

        valid, reason = validate_candles(
            df, "5m", now_ms=31_000_000
        )

        self.assertFalse(valid)
        self.assertIn("düzensiz", reason)

    def test_stale_data_rejected(self):
        """Eski mumlarla sinyal üretilmemeli."""
        df = pd.DataFrame({
            "open_time": [
                i * 300_000 for i in range(100)
            ]
        })

        valid, reason = validate_candles(
            df, "5m", now_ms=40_000_000
        )

        self.assertFalse(valid)
        self.assertIn("eski", reason)

    def test_valid_candles(self):
        """Eksiksiz ve güncel mumlar kabul edilmeli."""
        times = [
            i * 300_000 for i in range(100)
        ]

        df = pd.DataFrame({"open_time": times})

        # Son mum başlangıcı 29.700.000 ms.
        # CLOSE_GRACE_MS = 15.000 ms olduğunda:
        # now_ms = 30.015.000 ms veya üstü: (30.015.000 - 15.000) // 300.000 * 300.000 - 300.000 = 29.700.000 ms
        valid, _ = validate_candles(
            df, "5m", now_ms=30_020_000
        )

        self.assertTrue(valid)


if __name__ == "__main__":
    unittest.main()
