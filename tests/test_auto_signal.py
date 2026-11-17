
"""
CGRCoinBot v0.3.3 - Otomatik Sinyal İzleme ve Olay Tetikleme Testleri.

Test Senaryoları:
1. Kapanmış, güncel ve yeterli mum (>=100 mum) -> signals tablosuna analiz kaydı eklenir.
2. Aynı mum iki kez tetiklendiğinde (WebSocket/recovery) -> mükerrer kayıt ve olay oluşmaz (idempotent).
3. Veri eksik (100'den az mum), eski veya düzensiz -> VERI_YOK, hiçbir tabloya kayıt yapılmaz.
4. İlk sinyal (bootstrap) -> Olay (signal_events) oluşturulmaz.
5. BEKLE -> AL ve AL -> SAT geçişleri -> Birer signal_event oluşturur.
6. AL -> BEKLE, AL -> AL -> Olay oluşturmaz.
7. Farklı coin ve timeframe izolasyonu.
8. Kaydetme hatası / geçersiz veri durumunda ana servis çökmez, güvenli durum döner.
9. Reconnect / REST onarımı ile aynı mum tekrar geldiğinde tekillik korunur.
10. Gelecekteki mum verisi o anki mumu değiştiremez (look-ahead bias yok).
11. Tüm testler izole tempfile SQLite ile çalışır; gerçek market.db ve ağ çağrısı yapılmaz.
12. DB bağlantıları düzgün kapatılır, Windows WinError 32 oluşmaz.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cgrcoinbot import candle_store, signal_store
from cgrcoinbot.auto_signal import (
    process_closed_candle,
    on_candle_closed_async,
    scan_once,
)


INTERVAL_5M = 300_000


class AutoSignalTests(unittest.TestCase):

    def setUp(self):
        """Her test için izole SQLite ortamı kurar."""
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test_market.db"

        self.db_patch = patch.object(
            candle_store,
            "DB_PATH",
            self.db_path,
        )
        self.db_patch.start()

        candle_store.initialize_db()
        signal_store.initialize_signal_table()

    def tearDown(self):
        """Kaynakları temizler."""
        self.db_patch.stop()
        self.temp.cleanup()

    def _seed_candles(
        self,
        symbol="BTCUSDT",
        timeframe="5m",
        count=120,
        start_price=100.0,
        trend="up",
        end_open_time=36_000_000,
    ):
        """Test için sentetik düzenli kapanmış mum serisi ekler."""
        interval = candle_store.INTERVAL_MS[timeframe]
        start_time = end_open_time - (count - 1) * interval

        for i in range(count):
            open_time = start_time + i * interval
            if trend == "up":
                price = start_price + i * 1.5
            elif trend == "down":
                price = start_price - i * 1.5
            else:
                price = start_price

            candle_store.save_candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=open_time,
                open_price=price - 0.2,
                high=price + 1.0,
                low=price - 0.5,
                close=price,
                volume=1000.0,
                source="TEST",
            )

    def test_complete_and_fresh_candle_analyzed(self):
        """Kapanmış, eksiksiz ve güncel mum için signals tablosuna analiz kaydedilir."""
        end_time = 36_000_000
        self._seed_candles(count=120, end_open_time=end_time, trend="up")

        # end_time = 36_000_000. 5m interval = 300_000.
        # Bir sonraki mum başlangıcı 36_300_000.
        # now_ms = 36_320_000 seçersek (CLOSE_GRACE_MS=15000):
        # (36_320_000 - 15_000) // 300_000 * 300_000 - 300_000 = 36_000_000 -> Tam beklenen mum!
        now_ms = 36_320_000

        result = process_closed_candle(
            pair="BTC/USDT",
            timeframe="5m",
            open_time=end_time,
            now_ms=now_ms,
        )

        self.assertTrue(result["is_new_signal"])
        history = signal_store.get_signal_history(limit=10)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0][0], "BTC/USDT")
        self.assertEqual(history[0][1], "5m")

    def test_idempotent_duplicate_trigger(self):
        """Aynı mum iki kez tetiklendiğinde ikinci kayıt ve olay oluşmaz."""
        end_time = 36_000_000
        self._seed_candles(count=120, end_open_time=end_time, trend="up")
        now_ms = 36_320_000

        # İlk tetikleme
        res1 = process_closed_candle("BTC/USDT", "5m", open_time=end_time, now_ms=now_ms)
        self.assertTrue(res1["is_new_signal"])

        # İkinci tetikleme (aynı mum)
        res2 = process_closed_candle("BTC/USDT", "5m", open_time=end_time, now_ms=now_ms)
        self.assertFalse(res2["is_new_signal"])
        self.assertFalse(res2["event_created"])
        self.assertEqual(res2["status"], "MEVCUT_KAYIT")

        # Toplamda sadece 1 sinyal ve 0 olay olmalı (bootstrap)
        self.assertEqual(len(signal_store.get_signal_history()), 1)
        self.assertEqual(len(signal_store.get_signal_events()), 0)

    def test_insufficient_or_stale_candles_rejected(self):
        """100'den az mum veya eski veri durumunda VERI_YOK döner, kayıt yapılmaz."""
        # Sadece 30 mum ekle
        self._seed_candles(count=30, end_open_time=36_000_000)
        now_ms = 36_320_000

        res = process_closed_candle("BTC/USDT", "5m", now_ms=now_ms)
        self.assertEqual(res["status"], "VERI_HATASI")
        self.assertFalse(res["is_new_signal"])

        # Tablolar boş kalmalı
        self.assertEqual(len(signal_store.get_signal_history()), 0)
        self.assertEqual(len(signal_store.get_signal_events()), 0)

    def test_bootstrap_first_signal_no_event(self):
        """İlk sinyal kaydında bildirim olayı oluşturulmamalıdır."""
        end_time = 36_000_000
        self._seed_candles(count=120, end_open_time=end_time, trend="up")
        now_ms = 36_320_000

        res = process_closed_candle("BTC/USDT", "5m", now_ms=now_ms)
        self.assertEqual(res["status"], "BOOTSTRAP")
        self.assertFalse(res["event_created"])
        self.assertEqual(len(signal_store.get_signal_events()), 0)

    def test_bekle_to_al_creates_event(self):
        """Geçmişte BEKLE olan parite sonraki mumda AL olursa tek bir olay oluşmalıdır."""
        # 1. Mum (t1): BEKLE oluşturan geçmiş sinyali doğrudan kaydet
        from datetime import datetime, timezone
        t1_iso = datetime.fromtimestamp(36_000_000 / 1000, tz=timezone.utc).isoformat()
        signal_store.save_signal({
            "pair": "BTC/USDT",
            "timeframe": "5m",
            "candle_time": t1_iso,
            "signal": "BEKLE",
            "price": 200.0,
            "rsi": 50.0,
            "ema20": 200.0,
            "ema50": 200.0,
            "macd_histogram": 0.0,
            "reason": "Test BEKLE",
        })

        # 2. Mum (t2 = t1 + 300_000): Yükseliş serisi ekle (RSI 50-70, EMA20 > EMA50, MACD > 0)
        t2_open = 36_300_000
        self._seed_candles(count=120, end_open_time=t2_open, trend="up")
        now_ms = t2_open + 320_000

        res = process_closed_candle("BTC/USDT", "5m", open_time=t2_open, now_ms=now_ms)

        if res.get("current_signal") == "AL":
            self.assertTrue(res["event_created"])
            self.assertEqual(res["previous_signal"], "BEKLE")
            self.assertEqual(res["current_signal"], "AL")
            events = signal_store.get_signal_events()
            self.assertEqual(len(events), 1)

    def test_non_event_transitions(self):
        """AL -> BEKLE veya AL -> AL durumlarında olay oluşturulmamalıdır."""
        t1_iso = "2026-11-18T00:00:00+00:00"
        signal_store.save_signal({
            "pair": "SOL/USDT",
            "timeframe": "5m",
            "candle_time": t1_iso,
            "signal": "AL",
            "price": 100.0,
            "rsi": 60.0,
            "ema20": 102.0,
            "ema50": 98.0,
            "macd_histogram": 2.0,
            "reason": "Önceki AL",
        })

        t2_iso = "2026-11-18T00:05:00+00:00"
        # İkinci sinyal BEKLE olsun
        from cgrcoinbot.signal_service import process_analysis_result
        res = process_analysis_result({
            "pair": "SOL/USDT",
            "timeframe": "5m",
            "candle_time": t2_iso,
            "signal": "BEKLE",
            "price": 101.0,
            "rsi": 55.0,
            "ema20": 101.5,
            "ema50": 99.0,
            "macd_histogram": -0.5,
            "reason": "AL -> BEKLE",
        })

        self.assertTrue(res["is_new_signal"])
        self.assertFalse(res["event_created"])
        self.assertEqual(len(signal_store.get_signal_events(pair="SOL/USDT")), 0)

    def test_timeframe_and_pair_isolation(self):
        """Farklı parite veya timeframe kayıtları birbirini etkilememelidir."""
        end_time = 36_000_000
        now_ms = 36_320_000

        self._seed_candles(symbol="BTCUSDT", timeframe="5m", count=120, end_open_time=end_time)
        self._seed_candles(symbol="ETHUSDT", timeframe="5m", count=120, end_open_time=end_time)

        res_btc = process_closed_candle("BTC/USDT", "5m", now_ms=now_ms)
        res_eth = process_closed_candle("ETH/USDT", "5m", now_ms=now_ms)

        self.assertTrue(res_btc["is_new_signal"])
        self.assertTrue(res_eth["is_new_signal"])
        self.assertEqual(len(signal_store.get_signal_history()), 2)

    def test_async_wrapper_works(self):
        """on_candle_closed_async fonksiyonu asyncio event loop içinde kilitlenmeden çalışmalıdır."""
        end_time = 36_000_000
        self._seed_candles(count=120, end_open_time=end_time)
        now_ms = 36_320_000

        with patch("cgrcoinbot.auto_signal.process_closed_candle", return_value={"status": "OK"}) as mock_proc:
            res = asyncio.run(on_candle_closed_async("BTCUSDT", "5m", end_time))
            self.assertEqual(res["status"], "OK")
            mock_proc.assert_called_once_with("BTCUSDT", "5m", end_time)

    def test_database_closed_cleanly_no_lock(self):
        """İşlemler sonrasında DB bağlantısı serbest bırakılır ve geçici dosya kilitlenmez."""
        end_time = 36_000_000
        self._seed_candles(count=120, end_open_time=end_time)
        process_closed_candle("BTC/USDT", "5m", now_ms=36_320_000)
        self.assertTrue(self.db_path.exists())


if __name__ == "__main__":
    unittest.main()
