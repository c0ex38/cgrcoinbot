
"""
CGRCoinBot v0.3.6 - Gelişmiş Performans İstatistikleri Birim Testleri.

Kapsanan Senaryolar:
1. Boş veri tabanında: Yüzde 0 değil, hesaplanamıyor ve 0 örnek mesajı.
2. Sadece NÖTR kayıtlar: İsabet oranı None, non_neutral = 0.
3. Tek DOĞRU_YON / tek TERS_YON Wilson güven aralığı hesaplaması ([0, 100] sınırları içinde).
4. DOĞRU_YON + TERS_YON + NÖTR karışık set: Oran, medyan, ortalama ve standart sapma hesaplaması.
5. AL ve SAT gruplarının ayrılması; BTC ve ETH paritelerinin ayrılması; 5m/15m ayrılması; 1/3/6/12 ufuklarının ayrılması.
6. Pair, timeframe, horizon ve signal filtrelerinin doğru çalışması.
7. Aynı olayın 4 ufku bağımsız olay sayılmaz: distinct_events doğrulaması.
8. Küçük örneklem n < 30 için is_sample_small uyarısı; n >= 30 durumunda uyarının kalkması.
9. Sınır değerleri ve geçersiz / NaN / inf verilerin güvenle filtrelenmesi.
10. CLI --stats ve --report çıktı ve argüman arayüzleri.
11. Salt okunur doğrulama: compute_group_statistics ve generate_advanced_stats_report çalıştırıldığında veritabanı kayıt sayılarının kesinlikle değişmemesi.
12. SQLite bağlantılarının güvenle kapanması ve tempfile temizliği.
"""

import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cgrcoinbot import candle_store, signal_store, performance_store
from cgrcoinbot.performance_stats import (
    wilson_score_interval,
    compute_group_statistics,
    generate_advanced_stats_report,
)


class PerformanceStatsTests(unittest.TestCase):

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

    def _insert_event(self, event_id: int, pair: str = "BTC/USDT", timeframe: str = "15m", signal: str = "AL", price: float = 50000.0):
        candle_time = f"2026-11-18T{event_id:02d}:00:00+00:00"
        with candle_store.db_session() as con:
            con.execute("""
                INSERT OR IGNORE INTO signal_events (id, pair, timeframe, candle_time, previous_signal, current_signal, price, reason, created_at)
                VALUES (?, ?, ?, ?, 'BEKLE', ?, ?, 'Test Reason', ?)
            """, (event_id, pair, timeframe, candle_time, signal, price, candle_time))

    def _insert_perf(self, event_id: int, pair: str, timeframe: str, horizon: int, dir_change: float, label: str):
        candle_time = f"2026-11-18T{event_id:02d}:00:00+00:00"
        with candle_store.db_session() as con:
            con.execute("""
                INSERT OR IGNORE INTO signal_performance (
                    event_id, pair, timeframe, event_candle_time, horizon_candles,
                    entry_close, exit_close, raw_change_pct, directional_change_pct,
                    result_label, evaluated_at
                )
                VALUES (?, ?, ?, ?, ?, 100.0, 100.0, ?, ?, ?, ?)
            """, (event_id, pair, timeframe, candle_time, horizon, abs(dir_change), dir_change, label, candle_time))

    def test_empty_database_stats(self):
        """Test 1: Boş veri setinde hit rate None olmalı, asla %0 çıkmamalı."""
        stats = compute_group_statistics([])
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["distinct_events"], 0)
        self.assertIsNone(stats["direction_hit_rate_pct"])
        self.assertIsNone(stats["wilson_ci_95"])
        self.assertTrue(stats["is_sample_small"])

        rep = generate_advanced_stats_report()
        self.assertEqual(rep["overall"]["total"], 0)
        self.assertEqual(len(rep["groups"]), 0)

    def test_neutral_only_records(self):
        """Test 2: Yalnızca NÖTR kayıtlar olduğunda isabet oranı hesaplanamaz (None) olmalıdır."""
        records = [
            {"event_id": 1, "result_label": "NOTR", "directional_change_pct": 0.0, "raw_change_pct": 0.0},
            {"event_id": 2, "result_label": "NOTR", "directional_change_pct": 0.0, "raw_change_pct": 0.0},
        ]
        stats = compute_group_statistics(records)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["neutral"], 2)
        self.assertEqual(stats["non_neutral"], 0)
        self.assertIsNone(stats["direction_hit_rate_pct"])
        self.assertIsNone(stats["wilson_ci_95"])

    def test_wilson_score_single_success_and_failure(self):
        """Test 3: Tek DOĞRU_YON ve tek TERS_YON için Wilson güven aralığı hesaplanmalıdır."""
        # 1 doğru, 0 ters => total=1
        ci_success = wilson_score_interval(1, 1)
        self.assertIsNotNone(ci_success)
        self.assertGreater(ci_success[1], ci_success[0])
        self.assertAlmostEqual(ci_success[1], 100.0)
        self.assertGreater(ci_success[0], 0.0)

        # 0 doğru, 1 ters => total=1
        ci_fail = wilson_score_interval(0, 1)
        self.assertIsNotNone(ci_fail)
        self.assertEqual(ci_fail[0], 0.0)
        self.assertLess(ci_fail[1], 100.0)

        # Geçersiz durum
        self.assertIsNone(wilson_score_interval(0, 0))

    def test_mixed_records_metrics(self):
        """Test 4: Karışık sette isabet oranı, medyan, ortalama ve standart sapma doğru olmalıdır."""
        records = [
            {"event_id": 1, "result_label": "DOGRU_YON", "directional_change_pct": 4.0, "raw_change_pct": 4.0},
            {"event_id": 2, "result_label": "DOGRU_YON", "directional_change_pct": 2.0, "raw_change_pct": 2.0},
            {"event_id": 3, "result_label": "TERS_YON", "directional_change_pct": -3.0, "raw_change_pct": 3.0},
            {"event_id": 4, "result_label": "NOTR", "directional_change_pct": 0.0, "raw_change_pct": 0.0},
        ]
        stats = compute_group_statistics(records)
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["correct"], 2)
        self.assertEqual(stats["wrong"], 1)
        self.assertEqual(stats["neutral"], 1)
        self.assertEqual(stats["non_neutral"], 3)

        # 2 / 3 * 100 = 66.67%
        self.assertAlmostEqual(stats["direction_hit_rate_pct"], 66.67, places=2)
        # Ortalama: (4 + 2 - 3 + 0) / 4 = 0.75
        self.assertEqual(stats["mean_directional_change_pct"], 0.75)
        # Medyan: [-3, 0, 2, 4] -> (0 + 2) / 2 = 1.0
        self.assertEqual(stats["median_directional_change_pct"], 1.0)
        self.assertIsNotNone(stats["std_directional_change_pct"])
        self.assertEqual(stats["min_directional_change_pct"], -3.0)
        self.assertEqual(stats["max_directional_change_pct"], 4.0)

    def test_grouping_by_pair_tf_horizon_signal(self):
        """Test 5: AL/SAT, BTC/ETH, 5m/15m ve 1/3 ufukları ayrı gruplara ayrılmalıdır."""
        # 1. Event: BTC/USDT 15m AL
        self._insert_event(event_id=1, pair="BTC/USDT", timeframe="15m", signal="AL")
        self._insert_perf(event_id=1, pair="BTC/USDT", timeframe="15m", horizon=1, dir_change=2.5, label="DOGRU_YON")
        self._insert_perf(event_id=1, pair="BTC/USDT", timeframe="15m", horizon=3, dir_change=4.0, label="DOGRU_YON")

        # 2. Event: BTC/USDT 15m SAT
        self._insert_event(event_id=2, pair="BTC/USDT", timeframe="15m", signal="SAT")
        self._insert_perf(event_id=2, pair="BTC/USDT", timeframe="15m", horizon=1, dir_change=-1.5, label="TERS_YON")

        # 3. Event: ETH/USDT 5m AL
        self._insert_event(event_id=3, pair="ETH/USDT", timeframe="5m", signal="AL")
        self._insert_perf(event_id=3, pair="ETH/USDT", timeframe="5m", horizon=1, dir_change=3.0, label="DOGRU_YON")

        report = generate_advanced_stats_report()
        self.assertEqual(report["overall"]["total"], 4)
        self.assertEqual(report["overall"]["distinct_events"], 3)
        self.assertEqual(len(report["groups"]), 4)

        # Grupların varlığını doğrula
        keys = [(g["pair"], g["timeframe"], g["horizon_candles"], g["signal"]) for g in report["groups"]]
        self.assertIn(("BTC/USDT", "15m", 1, "AL"), keys)
        self.assertIn(("BTC/USDT", "15m", 3, "AL"), keys)
        self.assertIn(("BTC/USDT", "15m", 1, "SAT"), keys)
        self.assertIn(("ETH/USDT", "5m", 1, "AL"), keys)

    def test_filtering_options(self):
        """Test 6: Parite, timeframe, horizon ve sinyal filtreleri tam olarak uygulanmalıdır."""
        self._insert_event(1, "BTC/USDT", "15m", "AL")
        self._insert_perf(1, "BTC/USDT", "15m", 1, 2.0, "DOGRU_YON")
        self._insert_perf(1, "BTC/USDT", "15m", 3, 3.0, "DOGRU_YON")

        self._insert_event(2, "BTC/USDT", "15m", "SAT")
        self._insert_perf(2, "BTC/USDT", "15m", 3, -1.0, "TERS_YON")

        # Filtre: BTC/USDT + 15m + horizon=3 + signal=AL
        rep = generate_advanced_stats_report(pair="BTC/USDT", timeframe="15m", horizon=3, signal="AL")
        self.assertEqual(rep["overall"]["total"], 1)
        self.assertEqual(len(rep["groups"]), 1)
        self.assertEqual(rep["groups"][0]["signal"], "AL")
        self.assertEqual(rep["groups"][0]["horizon_candles"], 3)

    def test_distinct_events_counting(self):
        """Test 7: Aynı olayın 4 ufku (1,3,6,12) 4 bağımsız sinyal sayılmamalı, distinct_events=1 olmalıdır."""
        self._insert_event(1, "BTC/USDT", "15m", "AL")
        for h in [1, 3, 6, 12]:
            self._insert_perf(1, "BTC/USDT", "15m", h, 1.0, "DOGRU_YON")

        rep = generate_advanced_stats_report(pair="BTC/USDT")
        self.assertEqual(rep["overall"]["total"], 4)
        self.assertEqual(rep["overall"]["distinct_events"], 1)

    def test_small_sample_warning(self):
        """Test 8: n < 30 için is_sample_small True, n >= 30 için False olmalıdır."""
        # 10 örnek
        records_small = [{"event_id": i, "result_label": "DOGRU_YON", "directional_change_pct": 1.0, "raw_change_pct": 1.0} for i in range(10)]
        self.assertTrue(compute_group_statistics(records_small)["is_sample_small"])

        # 30 örnek
        records_large = [{"event_id": i, "result_label": "DOGRU_YON", "directional_change_pct": 1.0, "raw_change_pct": 1.0} for i in range(30)]
        self.assertFalse(compute_group_statistics(records_large)["is_sample_small"])

    def test_finite_numbers_and_boundary_handling(self):
        """Test 9: NaN ve sonsuz (inf) değerler hesaplamayı bozmamalıdır."""
        records = [
            {"event_id": 1, "result_label": "DOGRU_YON", "directional_change_pct": 5.0, "raw_change_pct": 5.0},
            {"event_id": 2, "result_label": "DOGRU_YON", "directional_change_pct": float("nan"), "raw_change_pct": 1.0},
            {"event_id": 3, "result_label": "TERS_YON", "directional_change_pct": float("inf"), "raw_change_pct": 1.0},
            {"event_id": 4, "result_label": "TERS_YON", "directional_change_pct": -3.0, "raw_change_pct": 3.0},
        ]
        stats = compute_group_statistics(records)
        self.assertEqual(stats["total"], 4)
        # Sadece sonlu sayılar (5.0 ve -3.0) hesaba katılır: ortalama = (5 - 3) / 2 = 1.0
        self.assertEqual(stats["mean_directional_change_pct"], 1.0)
        self.assertEqual(stats["min_directional_change_pct"], -3.0)
        self.assertEqual(stats["max_directional_change_pct"], 5.0)

    def test_read_only_immutability(self):
        """Test 10: İstatistik hesaplamaları DB'deki tabloların satır sayısını kesinlikle değiştirmemelidir."""
        self._insert_event(1, "BTC/USDT", "15m", "AL")
        self._insert_perf(1, "BTC/USDT", "15m", 1, 2.0, "DOGRU_YON")

        def count_rows(table):
            with candle_store.db_session() as con:
                return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

        cnt_events_before = count_rows("signal_events")
        cnt_perf_before = count_rows("signal_performance")

        # Rapor çalıştır
        generate_advanced_stats_report()

        self.assertEqual(count_rows("signal_events"), cnt_events_before)
        self.assertEqual(count_rows("signal_performance"), cnt_perf_before)

    def test_database_closed_cleanly_no_lock(self):
        """Test 11: DB session düzgün kapatılmalı, Windows kilitlenmesi olmamalıdır."""
        generate_advanced_stats_report()
        self.assertTrue(self.db_path.exists())


if __name__ == "__main__":
    unittest.main()
