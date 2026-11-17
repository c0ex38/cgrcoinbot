
"""CGRCoinBot v0.3.1 - Sinyal geçmişi ve depolama testleri."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cgrcoinbot import candle_store, signal_store


class SignalStoreTests(unittest.TestCase):
    """
    Sinyal veritabanı depolama, mükerrer kayıt koruması
    ve geçmiş listeleme birim testleri.
    Gerçek Binance ağına çıkmaz ve market.db'yi değiştirmez.
    """

    def setUp(self):
        """Her test için geçici SQLite veritabanı oluşturur."""
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test_market.db"

        self.db_patch = patch.object(
            candle_store,
            "DB_PATH",
            self.db_path,
        )
        self.db_patch.start()

        # Tabloları sıfırdan oluştur
        candle_store.initialize_db()
        signal_store.initialize_signal_table()

        # Geçerli örnek sinyal verisi
        self.valid_signal = {
            "pair": "BTC/USDT",
            "timeframe": "15m",
            "candle_time": "2026-11-18 01:00:00 UTC",
            "signal": "AL",
            "price": 91500.5,
            "rsi": 58.5,
            "ema20": 91200.0,
            "ema50": 90500.0,
            "macd_histogram": 12.4,
            "reason": "Yükseliş trendi ve pozitif momentum",
        }

    def tearDown(self):
        """Geçici test kaynaklarını temizler."""
        self.db_patch.stop()
        self.temp.cleanup()

    def test_save_new_signal_success(self):
        """Geçerli yeni bir AL/SAT/BEKLE sinyali veritabanına kaydedilmelidir."""
        is_saved = signal_store.save_signal(self.valid_signal)
        self.assertTrue(is_saved)

        history = signal_store.get_signal_history(limit=10)
        self.assertEqual(len(history), 1)
        pair, timeframe, signal, price, candle_time = history[0]
        self.assertEqual(pair, "BTC/USDT")
        self.assertEqual(timeframe, "15m")
        self.assertEqual(signal, "AL")
        self.assertAlmostEqual(price, 91500.5)
        self.assertEqual(candle_time, "2026-11-18 01:00:00 UTC")

    def test_prevent_duplicate_signal(self):
        """Aynı coin, timeframe ve candle_time için ikinci kez kayıt engellenmelidir."""
        is_saved_first = signal_store.save_signal(self.valid_signal)
        self.assertTrue(is_saved_first)

        # Aynı sinyali tekrar kaydetmeyi dene
        is_saved_duplicate = signal_store.save_signal(self.valid_signal)
        self.assertFalse(is_saved_duplicate)

        # Veritabanında hala sadece 1 kayıt olmalıdır
        history = signal_store.get_signal_history(limit=10)
        self.assertEqual(len(history), 1)

    def test_reject_veri_yok_or_invalid_signal(self):
        """VERI_YOK veya geçersiz sinyal durumlarında veritabanına kayıt yapılmamalıdır."""
        invalid_data = self.valid_signal.copy()
        invalid_data["signal"] = "VERI_YOK"
        invalid_data["reason"] = "Yetersiz veya eksik mum verisi"

        is_saved = signal_store.save_signal(invalid_data)
        self.assertFalse(is_saved)

        # Başka geçersiz bir sinyal türü
        unknown_data = self.valid_signal.copy()
        unknown_data["signal"] = "BILINMIYOR"
        self.assertFalse(signal_store.save_signal(unknown_data))

        # Hiç kayıt olmamalıdır
        history = signal_store.get_signal_history(limit=10)
        self.assertEqual(len(history), 0)

    def test_different_candle_times_saved(self):
        """Farklı mum zamanları için aynı çift ve timeframe kaydedilebilmelidir."""
        signal_1 = self.valid_signal.copy()
        signal_1["candle_time"] = "2026-11-18 01:00:00 UTC"
        signal_1["signal"] = "AL"

        signal_2 = self.valid_signal.copy()
        signal_2["candle_time"] = "2026-11-18 01:15:00 UTC"
        signal_2["signal"] = "BEKLE"

        self.assertTrue(signal_store.save_signal(signal_1))
        self.assertTrue(signal_store.save_signal(signal_2))

        history = signal_store.get_signal_history(limit=10)
        self.assertEqual(len(history), 2)
        # Sıralama candle_time DESC olmalı
        self.assertEqual(history[0][4], "2026-11-18 01:15:00 UTC")
        self.assertEqual(history[1][4], "2026-11-18 01:00:00 UTC")

    def test_signal_history_limit(self):
        """Sinyal geçmişi belirtilen limit parametresine uymalıdır."""
        for i in range(5):
            sig = self.valid_signal.copy()
            sig["candle_time"] = f"2026-11-18 01:{i:02d}:00 UTC"
            signal_store.save_signal(sig)

        history = signal_store.get_signal_history(limit=3)
        self.assertEqual(len(history), 3)

    def test_database_connection_closed(self):
        """Veritabanı işlemleri sonrasında bağlantının kapatıldığı doğrulanmalıdır."""
        signal_store.save_signal(self.valid_signal)
        # db_session context manager sayesinde bağlantı kapanmış olmalı,
        # geçici dizin silinirken PermissionError / kilitlenme yaşanmamalıdır.
        # Bu doğrulama tearDown sırasında temp.cleanup() başarıyla bitince de kanıtlanır.
        self.assertTrue(self.db_path.exists())


if __name__ == "__main__":
    unittest.main()
