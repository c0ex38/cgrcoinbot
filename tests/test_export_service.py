
"""
CGRCoinBot v0.3.7 - CSV / JSON Dışa Aktarma Birim Testleri.

Test Senaryoları:
1. signals CSV kolonları, BOM (utf-8-sig), Türkçe karakter doğrulaması.
2. events JSON metadata + records yapısı.
3. performance CSV/JSON değerleri ve filtreli AL/SAT join.
4. Boş tablo CSV başlığı ve JSON boş records listesi.
5. Parite (pair) filtrelemesi.
6. Zaman dilimi (timeframe) filtrelemesi.
7. Sinyal türü (signal) filtrelemesi.
8. Horizon filtrelemesi ve signals/events için uygunsuz horizon hata fırlatması.
9. Limit parametresi ve deterministik kayıt sırası.
10. Hedef dosya overwrite varsayılan yasak (FileExistsError) / --overwrite izinli.
11. Geçersiz dataset, format veya parametre hataları.
12. CSV formula injection koruması ve negatif sayısal değerlerin bozulmadan korunması.
13. Salt-okunurluk doğrulaması: Export sonrasında DB tablolarındaki satır sayılarının değişmemesi.
14. Bağlantı ve geçici dosya temizliği (Windows üzerinde kilit/WinError 32 kalmaması).
15. Geçersiz JSON sayıları (NaN/Infinity) için null/None dönüşümü.
16. Farklı pair ve timeframe kayıtlarının birbirine karışmaması.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cgrcoinbot import candle_store, signal_store, performance_store
from cgrcoinbot.export_service import (
    sanitize_csv_cell,
    sanitize_json_value,
    fetch_dataset_records,
    export_to_csv,
    export_to_json,
    export_dataset,
    DATASET_COLUMNS,
)


class ExportServiceTests(unittest.TestCase):

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
        performance_store.initialize_performance_table()

    def tearDown(self):
        """Kaynakları temizler."""
        self.db_patch.stop()
        self.temp.cleanup()

    def _seed_data(self):
        """Test için örnek signals, events ve performance verileri ekler."""
        # 1. signals
        with candle_store.db_session() as con:
            con.execute("""
                INSERT INTO signals (id, pair, timeframe, candle_time, signal, price, rsi, ema20, ema50, macd_histogram, reason, created_at)
                VALUES (1, 'BTC/USDT', '15m', '2026-11-18T00:00:00+00:00', 'AL', 50000.0, 62.5, 50100.0, 49800.0, 10.5, 'Yükseliş trendi', '2026-11-18T00:15:00+00:00')
            """)
            con.execute("""
                INSERT INTO signals (id, pair, timeframe, candle_time, signal, price, rsi, ema20, ema50, macd_histogram, reason, created_at)
                VALUES (2, 'ETH/USDT', '5m', '2026-11-18T00:05:00+00:00', 'SAT', 2500.0, 38.0, 2480.0, 2520.0, -5.2, 'Düşüş trendi', '2026-11-18T00:10:00+00:00')
            """)

            # 2. signal_events
            con.execute("""
                INSERT INTO signal_events (id, pair, timeframe, candle_time, previous_signal, current_signal, price, reason, created_at)
                VALUES (1, 'BTC/USDT', '15m', '2026-11-18T00:00:00+00:00', 'BEKLE', 'AL', 50000.0, 'Yükseliş onayı', '2026-11-18T00:15:00+00:00')
            """)
            con.execute("""
                INSERT INTO signal_events (id, pair, timeframe, candle_time, previous_signal, current_signal, price, reason, created_at)
                VALUES (2, 'ETH/USDT', '5m', '2026-11-18T00:05:00+00:00', 'BEKLE', 'SAT', 2500.0, 'Düşüş onayı', '2026-11-18T00:10:00+00:00')
            """)

            # 3. signal_performance
            con.execute("""
                INSERT INTO signal_performance (id, event_id, pair, timeframe, event_candle_time, horizon_candles, entry_close, exit_close, raw_change_pct, directional_change_pct, result_label, evaluated_at)
                VALUES (1, 1, 'BTC/USDT', '15m', '2026-11-18T00:00:00+00:00', 3, 50000.0, 51000.0, 2.0, 2.0, 'DOGRU_YON', '2026-11-18T01:00:00+00:00')
            """)
            con.execute("""
                INSERT INTO signal_performance (id, event_id, pair, timeframe, event_candle_time, horizon_candles, entry_close, exit_close, raw_change_pct, directional_change_pct, result_label, evaluated_at)
                VALUES (2, 2, 'ETH/USDT', '5m', '2026-11-18T00:05:00+00:00', 1, 2500.0, 2450.0, -2.0, 2.0, 'DOGRU_YON', '2026-11-18T00:15:00+00:00')
            """)

    def test_signals_csv_export_and_bom(self):
        """Test 1: signals CSV çıktısı utf-8-sig BOM ve doğru kolonları içermelidir."""
        self._seed_data()
        out_file = Path(self.temp.name) / "signals.csv"

        final_path, count = export_dataset("signals", "csv", output_path=str(out_file))
        self.assertEqual(count, 2)
        self.assertTrue(final_path.exists())

        # BOM kontrolü (ef bb bf)
        with open(final_path, "rb") as f:
            header_bytes = f.read(3)
            self.assertEqual(header_bytes, b"\xef\xbb\xbf")

        # Metin içeriği kontrolü
        with open(final_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
            self.assertIn("BTC/USDT", content)
            self.assertIn("Yükseliş trendi", content)
            # Kolon başlıkları
            for col in DATASET_COLUMNS["signals"]:
                self.assertIn(col, content)

    def test_events_json_structure(self):
        """Test 2: events JSON çıktısı metadata + records şemasına tam uymalıdır."""
        self._seed_data()
        out_file = Path(self.temp.name) / "events.json"

        final_path, count = export_dataset("events", "json", output_path=str(out_file))
        self.assertEqual(count, 2)

        with open(final_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("metadata", data)
        self.assertIn("records", data)
        self.assertEqual(data["metadata"]["dataset"], "events")
        self.assertEqual(data["metadata"]["record_count"], 2)
        self.assertEqual(len(data["records"]), 2)
        self.assertEqual(data["records"][0]["pair"], "BTC/USDT")
        self.assertEqual(data["records"][0]["current_signal"], "AL")

    def test_performance_csv_and_join(self):
        """Test 3: performance CSV çıktısı JOIN ile sinyal yönünü (signal) doğru içermelidir."""
        self._seed_data()
        out_file = Path(self.temp.name) / "perf.csv"

        final_path, count = export_dataset("performance", "csv", output_path=str(out_file))
        self.assertEqual(count, 2)

        with open(final_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
            self.assertIn("BTC/USDT", content)
            self.assertIn("horizon_candles", content)
            self.assertIn("DOGRU_YON", content)
            self.assertIn("AL", content)

    def test_empty_database_export(self):
        """Test 4: Boş veritabanında CSV başlık satırı yazmalı, JSON boş records listesi üretmelidir."""
        # signals CSV boş
        out_csv = Path(self.temp.name) / "empty_signals.csv"
        _, count_csv = export_dataset("signals", "csv", output_path=str(out_csv))
        self.assertEqual(count_csv, 0)
        with open(out_csv, "r", encoding="utf-8-sig") as f:
            lines = [line.strip() for line in f if line.strip()]
            self.assertEqual(len(lines), 1)  # Yalnızca başlık satırı

        # events JSON boş
        out_json = Path(self.temp.name) / "empty_events.json"
        _, count_json = export_dataset("events", "json", output_path=str(out_json))
        self.assertEqual(count_json, 0)
        with open(out_json, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(data["metadata"]["record_count"], 0)
            self.assertEqual(len(data["records"]), 0)

    def test_pair_filtering(self):
        """Test 5: Parite filtresi yalnızca eşleşen pariteyi dışa aktarmalıdır."""
        self._seed_data()
        records_btc = fetch_dataset_records("signals", pair="BTC/USDT")
        self.assertEqual(len(records_btc), 1)
        self.assertEqual(records_btc[0]["pair"], "BTC/USDT")

        records_eth = fetch_dataset_records("signals", pair="ETH/USDT")
        self.assertEqual(len(records_eth), 1)
        self.assertEqual(records_eth[0]["pair"], "ETH/USDT")

    def test_timeframe_filtering(self):
        """Test 6: Zaman dilimi filtresi doğru çalışmalıdır."""
        self._seed_data()
        records_15m = fetch_dataset_records("signals", timeframe="15m")
        self.assertEqual(len(records_15m), 1)
        self.assertEqual(records_15m[0]["timeframe"], "15m")

        records_5m = fetch_dataset_records("signals", timeframe="5m")
        self.assertEqual(len(records_5m), 1)
        self.assertEqual(records_5m[0]["timeframe"], "5m")

    def test_signal_filtering(self):
        """Test 7: Sinyal türü filtresi doğru çalışmalıdır."""
        self._seed_data()
        records_al = fetch_dataset_records("signals", signal="AL")
        self.assertEqual(len(records_al), 1)
        self.assertEqual(records_al[0]["signal"], "AL")

        # events için
        records_sat = fetch_dataset_records("events", signal="SAT")
        self.assertEqual(len(records_sat), 1)
        self.assertEqual(records_sat[0]["current_signal"], "SAT")

    def test_horizon_filtering_and_invalid_dataset_rejection(self):
        """Test 8: Horizon filtresi performance için çalışmalı, signals/events için hata vermelidir."""
        self._seed_data()
        # performance için horizon=3
        records = fetch_dataset_records("performance", horizon=3)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["horizon_candles"], 3)

        # signals için horizon verilirse ValueError
        with self.assertRaises(ValueError):
            fetch_dataset_records("signals", horizon=3)

        with self.assertRaises(ValueError):
            fetch_dataset_records("events", horizon=1)

    def test_limit_and_deterministic_order(self):
        """Test 9: Limit parametresi doğru sınırlandırmalı ve sıralama deterministik olmalıdır."""
        self._seed_data()
        records = fetch_dataset_records("signals", limit=1)
        self.assertEqual(len(records), 1)
        # Deterministik sıralama candle_time ASC: 00:00:00 BTC, 00:05:00 ETH
        self.assertEqual(records[0]["pair"], "BTC/USDT")

    def test_overwrite_protection(self):
        """Test 10: Dosya varken overwrite=False ise FileExistsError fırlatılmalı, overwrite=True ise izin verilmelidir."""
        self._seed_data()
        out_file = Path(self.temp.name) / "test_ow.csv"

        # İlk yazım başarılı
        export_dataset("signals", "csv", output_path=str(out_file))
        self.assertTrue(out_file.exists())

        # overwrite=False ile tekrar yazım => FileExistsError
        with self.assertRaises(FileExistsError):
            export_dataset("signals", "csv", output_path=str(out_file), overwrite=False)

        # overwrite=True ile tekrar yazım => Başarılı
        export_dataset("signals", "csv", output_path=str(out_file), overwrite=True)
        self.assertTrue(out_file.exists())

    def test_invalid_arguments_rejection(self):
        """Test 11: Geçersiz dataset veya format parametreleri ValueError fırlatmalıdır."""
        with self.assertRaises(ValueError):
            export_dataset("invalid_dataset", "csv")

        with self.assertRaises(ValueError):
            export_dataset("signals", "xml")

        with self.assertRaises(ValueError):
            fetch_dataset_records("signals", limit=-5)

    def test_csv_formula_injection_sanitization(self):
        """Test 12: CSV formula injection koruması (=, +, -, @ ile başlayan metinler escape edilmeli, negatif sayılar korunmalı)."""
        # Formül metinleri
        self.assertEqual(sanitize_csv_cell("=SUM(A1:B2)"), "'=SUM(A1:B2)")
        self.assertEqual(sanitize_csv_cell("+cmd|' /C calc'!A0"), "'+cmd|' /C calc'!A0")
        self.assertEqual(sanitize_csv_cell("@SUM(1+1)"), "'@SUM(1+1)")
        self.assertEqual(sanitize_csv_cell("-DDE('cmd')"), "'-DDE('cmd')")

        # Negatif ve pozitif sayılar bozulmamalı
        self.assertEqual(sanitize_csv_cell(-5.25), -5.25)
        self.assertEqual(sanitize_csv_cell("-3.14"), "-3.14")
        self.assertEqual(sanitize_csv_cell(100), 100)
        self.assertEqual(sanitize_csv_cell("+10"), "+10")
        self.assertEqual(sanitize_csv_cell("Normal Metin"), "Normal Metin")

    def test_read_only_immutability(self):
        """Test 13: Export işlemleri veritabanındaki kayıtları asla değiştirmez."""
        self._seed_data()

        def get_counts():
            with candle_store.db_session() as con:
                c_sig = con.execute("SELECT count(*) FROM signals").fetchone()[0]
                c_ev = con.execute("SELECT count(*) FROM signal_events").fetchone()[0]
                c_perf = con.execute("SELECT count(*) FROM signal_performance").fetchone()[0]
            return c_sig, c_ev, c_perf

        before = get_counts()

        # Dışa aktarma işlemlerini yürüt
        export_dataset("signals", "csv", output_path=str(Path(self.temp.name) / "s.csv"))
        export_dataset("events", "json", output_path=str(Path(self.temp.name) / "e.json"))
        export_dataset("performance", "csv", output_path=str(Path(self.temp.name) / "p.csv"))

        after = get_counts()
        self.assertEqual(before, after)

    def test_cleanup_and_no_file_lock(self):
        """Test 14: İşlem sonrasında geçici dosyalar ve SQLite bağlantıları serbest bırakılmalıdır."""
        out_file = Path(self.temp.name) / "temp_check.csv"
        export_dataset("signals", "csv", output_path=str(out_file))
        self.assertTrue(out_file.exists())
        # Temp dizindeki geçici named temporary dosyaları temizlenmiş olmalıdır
        temp_files = list(Path(self.temp.name).glob("tmp*"))
        self.assertEqual(len(temp_files), 0)

    def test_nan_infinity_json_sanitization(self):
        """Test 15: Geçersiz float değerler (NaN, Inf) JSON için null/None değerine çevrilmelidir."""
        self.assertIsNone(sanitize_json_value(float("nan")))
        self.assertIsNone(sanitize_json_value(float("inf")))
        self.assertIsNone(sanitize_json_value(float("-inf")))
        self.assertEqual(sanitize_json_value(42.5), 42.5)
        self.assertEqual(sanitize_json_value("test"), "test")

    def test_pair_and_timeframe_isolation(self):
        """Test 16: Farklı coin ve timeframe kayıtları filtrelendiğinde kesinlikle birbirine karışmamalıdır."""
        self._seed_data()
        res = fetch_dataset_records("signals", pair="BTC/USDT", timeframe="15m")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["pair"], "BTC/USDT")
        self.assertEqual(res[0]["timeframe"], "15m")

        res_none = fetch_dataset_records("signals", pair="BTC/USDT", timeframe="5m")
        self.assertEqual(len(res_none), 0)


if __name__ == "__main__":
    unittest.main()
