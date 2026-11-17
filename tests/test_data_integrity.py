
"""CGRCoinBot v0.2.3 - Veri bütünlüğü testleri."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cgrcoinbot import candle_store
from cgrcoinbot import data_integrity


# Sabit test zamanı: 30 dakika 10 saniye
NOW_MS = 1_810_000

# 5 dakikalık mum süresi
INTERVAL = 300_000


class DataIntegrityTests(unittest.TestCase):
    """Gerçek piyasa verilerini değiştirmeden test yapar."""

    def setUp(self):
        """Her test için geçici SQLite veritabanı oluştur."""
        self.temp = tempfile.TemporaryDirectory()

        self.db_patch = patch.object(
            candle_store,
            "DB_PATH",
            Path(self.temp.name) / "test_market.db",
        )
        self.db_patch.start()

        self.time_patch = patch(
            "cgrcoinbot.data_integrity.time.time",
            return_value=NOW_MS / 1000,
        )
        self.time_patch.start()

        candle_store.initialize_db()

    def tearDown(self):
        """Geçici test kaynaklarını temizle."""
        self.time_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def add_candle(self, open_time):
        """Örnek kapanmış mum kaydet."""
        candle_store.save_candle(
            symbol="BTCUSDT",
            timeframe="5m",
            open_time=open_time,
            open_price="100",
            high="105",
            low="95",
            close="102",
            volume="1000",
            source="TEST",
        )

    def test_complete_data(self):
        """Eksiksiz mum dizisi sağlıklı olmalı."""
        for timestamp in (600000, 900000, 1200000, 1500000):
            self.add_candle(timestamp)

        result = data_integrity.audit_series(
            "BTCUSDT", "5m"
        )
        self.assertTrue(result)

    def test_missing_middle_candle(self):
        """Ortadaki eksik mum tespit edilmeli."""
        for timestamp in (600000, 900000, 1500000):
            self.add_candle(timestamp)

        result = data_integrity.audit_series(
            "BTCUSDT", "5m"
        )
        self.assertFalse(result)

        ranges = data_integrity.find_missing_ranges(
            candle_store_times(),
            "5m",
            NOW_MS,
        )

        self.assertEqual(ranges, [(1200000, 1200000)])

    def test_missing_latest_candles(self):
        """Son kapanmış mumlar eksikse tespit edilmeli."""
        for timestamp in (600000, 900000):
            self.add_candle(timestamp)

        ranges = data_integrity.find_missing_ranges(
            candle_store_times(),
            "5m",
            NOW_MS,
        )

        self.assertEqual(ranges, [(1200000, 1500000)])

    def test_repair_and_no_duplicates(self):
        """Eksik mum onarılmalı, tekrar eklenmemeli."""
        for timestamp in (600000, 900000, 1500000):
            self.add_candle(timestamp)

        # Binance REST API yanıtını taklit ediyoruz.
        fake_response = [[
            1200000,
            "100",
            "105",
            "95",
            "102",
            "1000",
            1499999,
        ]]

        with patch(
            "cgrcoinbot.data_integrity.fetch_range",
            return_value=fake_response,
        ) as fake_api:

            self.assertTrue(
                data_integrity.audit_series(
                    "BTCUSDT", "5m", repair=True
                )
            )

            self.assertTrue(
                data_integrity.audit_series(
                    "BTCUSDT", "5m", repair=True
                )
            )

            self.assertEqual(fake_api.call_count, 1)

        times = candle_store_times()

        self.assertEqual(len(times), 4)
        self.assertEqual(len(times), len(set(times)))


def candle_store_times():
    """Test veritabanındaki mum zamanlarını oku."""

    with candle_store.db_session() as con:
        rows = con.execute(
            """
            SELECT open_time
            FROM candles
            WHERE symbol = 'BTCUSDT'
              AND timeframe = '5m'
            ORDER BY open_time
            """
        ).fetchall()

    return [row[0] for row in rows]


if __name__ == "__main__":
    unittest.main()
