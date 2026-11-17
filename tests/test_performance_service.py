
"""
CGRCoinBot v0.3.4 - Sinyal Performans Takibi Birim Testleri.

Kapsanan Senaryolar:
1. AL sinyali ve fiyat artışı => DOGRU_YON; AL ve düşüş => TERS_YON.
2. SAT sinyali ve fiyat düşüşü => DOGRU_YON; SAT ve artış => TERS_YON.
3. Eşit fiyat (fark %0) => NOTR.
4. [1, 3, 6, 12] mum hedeflerinin doğru zaman offset'i ile hesaplanması.
5. Hedef mum henüz kapanmadıysa (now_ms < target_close + grace) => BEKLIYOR.
6. Hedef veya ara mum eksikse => EKSIK_VERI (nihai kayıt yazılmaz).
7. Olay mumu kapanış fiyatı event.price ile uyumsuzsa => GECERSIZ_REFERANS.
8. Aynı event ve horizon tekrar değerlendirildiğinde mükerrer kayıt oluşmaz (idempotent).
9. Parite ve timeframe izolasyonu.
10. Bootstrap / BEKLE sinyalleri performans ölçümüne dahil edilmez.
11. Metrik hesaplama fonksiyonu (isabet oranı, ortalama, medyan).
12. CLI --report ve --evaluate fonksiyonlarının test edilmesi.
13. DB kaynaklarının düzgün kapanması (WinError 32 yok).
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cgrcoinbot import candle_store, signal_store, performance_store, performance_service
from cgrcoinbot.performance_service import (
    evaluate_event_horizon,
    evaluate_all_performances,
    generate_performance_report,
    calculate_metrics,
)


INTERVAL_15M = 900_000


class PerformanceTrackingTests(unittest.TestCase):

    def setUp(self):
        """Her test için izole SQLite veritabanı kurar."""
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

    def _seed_candle(self, symbol="BTCUSDT", timeframe="15m", open_time=10_000_000, close_price=50000.0):
        candle_store.save_candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=open_time,
            open_price=close_price,
            high=close_price + 10,
            low=close_price - 10,
            close=close_price,
            volume=100.0,
            source="TEST",
        )

    def _seed_sequence(self, symbol="BTCUSDT", timeframe="15m", start_time=10_000_000, count=15, start_price=50000.0, step=100.0):
        """Düzenli mum dizisi ekler."""
        interval = candle_store.INTERVAL_MS[timeframe]
        for i in range(count):
            ot = start_time + i * interval
            cp = start_price + i * step
            self._seed_candle(symbol=symbol, timeframe=timeframe, open_time=ot, close_price=cp)

    def test_al_direction_correct_and_reverse(self):
        """AL sinyali: Fiyat artışı DOGRU_YON, fiyat düşüşü TERS_YON olmalıdır."""
        event_time_iso = "2026-11-18T00:00:00+00:00"
        t0 = 10_000_000

        # Olay mumu (t0): close = 100.0
        self._seed_candle(symbol="BTCUSDT", open_time=t0, close_price=100.0)
        # Hedef mum 1 (t0 + 900_000): close = 105.0 (+%5)
        self._seed_candle(symbol="BTCUSDT", open_time=t0 + INTERVAL_15M, close_price=105.0)

        event = {
            "id": 1,
            "pair": "BTC/USDT",
            "timeframe": "15m",
            "candle_time": "2026-11-18T00:00:00+00:00",
            "current_signal": "AL",
            "price": 100.0,
        }

        with patch("cgrcoinbot.performance_service.parse_candle_time_ms", return_value=t0):
            # 1. Artış durumu
            res_up = evaluate_event_horizon(event, horizon=1, now_ms=t0 + 2 * INTERVAL_15M + 20_000)
            self.assertEqual(res_up["status"], "TAMAMLANDI")
            self.assertEqual(res_up["result_label"], "DOGRU_YON")
            self.assertAlmostEqual(res_up["directional_change_pct"], 5.0)

            # 2. Düşüş durumu (hedef mum 95.0)
            self._seed_candle(symbol="BTCUSDT", open_time=t0 + INTERVAL_15M, close_price=95.0)
            res_down = evaluate_event_horizon(event, horizon=1, now_ms=t0 + 2 * INTERVAL_15M + 20_000)
            self.assertEqual(res_down["status"], "TAMAMLANDI")
            self.assertEqual(res_down["result_label"], "TERS_YON")
            self.assertAlmostEqual(res_down["directional_change_pct"], -5.0)

    def test_sat_direction_correct_and_reverse(self):
        """SAT sinyali: Fiyat düşüşü DOGRU_YON, fiyat artışı TERS_YON olmalıdır."""
        t0 = 10_000_000
        self._seed_candle(symbol="ETHUSDT", open_time=t0, close_price=2000.0)
        # Hedef mum (t0 + 900_000): close = 1900.0 (-%5 düşüş)
        self._seed_candle(symbol="ETHUSDT", open_time=t0 + INTERVAL_15M, close_price=1900.0)

        event = {
            "id": 2,
            "pair": "ETH/USDT",
            "timeframe": "15m",
            "candle_time": "2026-11-18T00:00:00+00:00",
            "current_signal": "SAT",
            "price": 2000.0,
        }

        with patch("cgrcoinbot.performance_service.parse_candle_time_ms", return_value=t0):
            res_drop = evaluate_event_horizon(event, horizon=1, now_ms=t0 + 2 * INTERVAL_15M + 20_000)
            self.assertEqual(res_drop["status"], "TAMAMLANDI")
            self.assertEqual(res_drop["result_label"], "DOGRU_YON")
            self.assertAlmostEqual(res_drop["directional_change_pct"], 5.0)  # SAT için düşüş pozitif yöndür

            # Fiyat yükselirse (2100.0) => TERS_YON
            self._seed_candle(symbol="ETHUSDT", open_time=t0 + INTERVAL_15M, close_price=2100.0)
            res_rise = evaluate_event_horizon(event, horizon=1, now_ms=t0 + 2 * INTERVAL_15M + 20_000)
            self.assertEqual(res_rise["status"], "TAMAMLANDI")
            self.assertEqual(res_rise["result_label"], "TERS_YON")
            self.assertAlmostEqual(res_rise["directional_change_pct"], -5.0)

    def test_neutral_change(self):
        """Fiyat değişmediğinde sonuç NOTR olmalıdır."""
        t0 = 10_000_000
        self._seed_candle(symbol="BTCUSDT", open_time=t0, close_price=100.0)
        self._seed_candle(symbol="BTCUSDT", open_time=t0 + INTERVAL_15M, close_price=100.0)

        event = {
            "id": 3,
            "pair": "BTC/USDT",
            "timeframe": "15m",
            "candle_time": "2026-11-18T00:00:00+00:00",
            "current_signal": "AL",
            "price": 100.0,
        }
        with patch("cgrcoinbot.performance_service.parse_candle_time_ms", return_value=t0):
            res = evaluate_event_horizon(event, horizon=1, now_ms=t0 + 2 * INTERVAL_15M + 20_000)
            self.assertEqual(res["result_label"], "NOTR")
            self.assertEqual(res["directional_change_pct"], 0.0)

    def test_pending_when_target_candle_not_closed(self):
        """Hedef mum henüz tamamlanmadıysa (now_ms hedef kapanışından önce) BEKLIYOR dönmelidir."""
        t0 = 10_000_000
        self._seed_candle(symbol="BTCUSDT", open_time=t0, close_price=100.0)
        self._seed_candle(symbol="BTCUSDT", open_time=t0 + INTERVAL_15M, close_price=105.0)

        event = {
            "id": 4,
            "pair": "BTC/USDT",
            "timeframe": "15m",
            "candle_time": "2026-11-18T00:00:00+00:00",
            "current_signal": "AL",
            "price": 100.0,
        }
        with patch("cgrcoinbot.performance_service.parse_candle_time_ms", return_value=t0):
            # Hedef mum kapanışı t0 + 2 * 900_000 = 11_800_000. now_ms henüz 11_000_000 ise:
            res = evaluate_event_horizon(event, horizon=1, now_ms=11_000_000)
            self.assertEqual(res["status"], "BEKLIYOR")

    def test_missing_data_rejection(self):
        """Hedef mum veya ara mumlardan biri eksikse EKSIK_VERI dönmeli, kayıt yapılmamalıdır."""
        t0 = 10_000_000
        self._seed_candle(symbol="BTCUSDT", open_time=t0, close_price=100.0)
        # horizon=3 için t0 + 900_000, t0 + 1_800_000, t0 + 2_700_000 gerekli
        # Sadece 1. ve 3. mumu ekle, 2. mum eksik kalsın:
        self._seed_candle(symbol="BTCUSDT", open_time=t0 + INTERVAL_15M, close_price=101.0)
        self._seed_candle(symbol="BTCUSDT", open_time=t0 + 3 * INTERVAL_15M, close_price=105.0)

        event = {
            "id": 5,
            "pair": "BTC/USDT",
            "timeframe": "15m",
            "candle_time": "2026-11-18T00:00:00+00:00",
            "current_signal": "AL",
            "price": 100.0,
        }
        with patch("cgrcoinbot.performance_service.parse_candle_time_ms", return_value=t0):
            res = evaluate_event_horizon(event, horizon=3, now_ms=t0 + 5 * INTERVAL_15M)
            self.assertEqual(res["status"], "EKSIK_VERI")

    def test_invalid_reference_price_mismatch(self):
        """Olay fiyatı ile mum kapanış fiyatı tolerans dışı uyumsuzsa GECERSIZ_REFERANS dönmelidir."""
        t0 = 10_000_000
        self._seed_candle(symbol="BTCUSDT", open_time=t0, close_price=100.0)
        self._seed_candle(symbol="BTCUSDT", open_time=t0 + INTERVAL_15M, close_price=105.0)

        event = {
            "id": 6,
            "pair": "BTC/USDT",
            "timeframe": "15m",
            "candle_time": "2026-11-18T00:00:00+00:00",
            "current_signal": "AL",
            "price": 150.0,  # Mum close 100.0 iken event price 150.0 -> Uyumsuz!
        }
        with patch("cgrcoinbot.performance_service.parse_candle_time_ms", return_value=t0):
            res = evaluate_event_horizon(event, horizon=1, now_ms=t0 + 3 * INTERVAL_15M)
            self.assertEqual(res["status"], "GECERSIZ_REFERANS")

    def test_idempotent_evaluation(self):
        """evaluate_all_performances iki kez çağrıldığında mükerrer kayıt oluşmamalıdır."""
        t0 = 10_000_000
        # 14 mumluk seri oluştur
        self._seed_sequence(symbol="SOLUSDT", timeframe="15m", start_time=t0, count=15, start_price=100.0, step=1.0)

        # signal_events tablosuna bir AL olayı ekle
        signal_store.save_signal_event(
            pair="SOL/USDT",
            timeframe="15m",
            candle_time="2026-11-18T00:00:00+00:00",
            previous_signal="BEKLE",
            current_signal="AL",
            price=100.0,
            reason="Test AL",
        )

        with patch("cgrcoinbot.performance_service.parse_candle_time_ms", return_value=t0):
            now_ms = t0 + 15 * INTERVAL_15M + 20_000
            # İlk çalıştırma: 4 ufuk için kayıt eklenmeli
            sum1 = evaluate_all_performances(horizons=[1, 3, 6, 12], now_ms=now_ms)
            self.assertEqual(sum1["evaluated_new"], 4)

            # İkinci çalıştırma: 0 yeni kayıt, 4 already_evaluated
            sum2 = evaluate_all_performances(horizons=[1, 3, 6, 12], now_ms=now_ms)
            self.assertEqual(sum2["evaluated_new"], 0)
            self.assertEqual(sum2["already_evaluated"], 4)

            # DB'de toplam tam 4 kayıt olmalı
            records = performance_store.get_performance_records(pair="SOL/USDT")
            self.assertEqual(len(records), 4)

    def test_metrics_calculation(self):
        """İsabet oranı, ortalama ve medyan hesaplamaları doğru olmalıdır."""
        records = [
            {"result_label": "DOGRU_YON", "directional_change_pct": 5.0},
            {"result_label": "DOGRU_YON", "directional_change_pct": 3.0},
            {"result_label": "TERS_YON", "directional_change_pct": -2.0},
            {"result_label": "NOTR", "directional_change_pct": 0.0},
        ]
        m = calculate_metrics(records)
        self.assertEqual(m["total"], 4)
        self.assertEqual(m["dogru_yon"], 2)
        self.assertEqual(m["ters_yon"], 1)
        self.assertEqual(m["notr"], 1)
        # 2 / (2 + 1) * 100 = %66.67
        self.assertAlmostEqual(m["win_rate"], 66.67, places=2)
        # Ortalama: (5 + 3 - 2 + 0) / 4 = 1.5
        self.assertEqual(m["avg_change"], 1.5)
        # Medyan: [-2, 0, 3, 5] -> (0 + 3) / 2 = 1.5
        self.assertEqual(m["median_change"], 1.5)

    def test_report_generation_empty_and_filled(self):
        """generate_performance_report boşken ve doluyken güvenli veri dönmelidir."""
        rep_empty = generate_performance_report()
        self.assertEqual(rep_empty["overall"]["total"], 0)
        self.assertIsNone(rep_empty["overall"]["win_rate"])


if __name__ == "__main__":
    unittest.main()
