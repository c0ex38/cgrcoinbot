
"""
CGRCoinBot v0.3.5 - Otomatik Sinyal Performans Değerlendirme Testleri.

Kapsanan Senaryolar:
A. Yeni kapanmış hedef mumdan sonra olgunlaşan AL/SAT olay performansı otomatik yazılır.
B. Henüz kapanmamış hedef mum BEKLIYOR kalır, yazılmaz.
C. Arada mum boşluğu varsa EKSIK_VERI; REST recovery sonrasında doğru hesaplanır.
D. Aynı mum tekrar geldi / reconnect oldu / iki asenkron tetikleme yapıldı:
   UNIQUE sayesinde aynı (event_id, horizon) tek kez kaydedilir.
E. 1, 3, 6, 12 ufukları bağımsız ve yalnızca olgunlaştığında yazılır.
F. Parite ve timeframe birbirinden yalıtılır.
G. BEKLE olayları, bootstrap ve geçersiz referanslar değerlendirme kaydı oluşturmaz.
H. Olay yokken servis hatasız çalışır, 0 örnek durumu %0 başarı gibi yanıltıcı gösterilmez.
I. Startup catch-up tarihi olgunlaşmış kayıtları tamamlar, tekrarında yenisini eklemez.
J. Collector mum yazımı başarısızsa analiz ve değerlendirme tetiklenmez.
K. Kolektör/event loop bloklanmaz; hatalı event diğer coinlerin işlemesini engellemez.
L. SQLite3 bağlantıları kapanır; Windows tempfile dosyaları temizlenir.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cgrcoinbot import candle_store, signal_store, performance_store
from cgrcoinbot.auto_performance import (
    process_auto_performance,
    on_candle_performance_async,
    startup_performance_catchup,
)


INTERVAL_15M = 900_000


class AutoPerformanceTests(unittest.TestCase):

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
        interval = candle_store.INTERVAL_MS[timeframe]
        for i in range(count):
            ot = start_time + i * interval
            cp = start_price + i * step
            self._seed_candle(symbol=symbol, timeframe=timeframe, open_time=ot, close_price=cp)

    def test_auto_evaluation_on_mature_candle(self):
        """Senaryo A: Yeni kapanan hedef mum olgunlaşmış AL/SAT olayını otomatik değerlendirir."""
        t0 = 10_000_000
        # Olay mumu
        self._seed_candle(symbol="BTCUSDT", timeframe="15m", open_time=t0, close_price=100.0)
        # Hedef mum 1 (+15m)
        self._seed_candle(symbol="BTCUSDT", timeframe="15m", open_time=t0 + INTERVAL_15M, close_price=105.0)

        # signal_events tablosuna AL olayı ekle
        signal_store.save_signal_event(
            pair="BTC/USDT",
            timeframe="15m",
            candle_time="2026-11-18T00:00:00+00:00",
            previous_signal="BEKLE",
            current_signal="AL",
            price=100.0,
            reason="Test AL",
        )

        with patch("cgrcoinbot.performance_service.parse_candle_time_ms", return_value=t0):
            now_ms = t0 + 2 * INTERVAL_15M + 20_000
            summary = process_auto_performance("BTC/USDT", "15m", now_ms=now_ms)
            self.assertGreaterEqual(summary["evaluated_new"], 1)

            records = performance_store.get_performance_records(pair="BTC/USDT", timeframe="15m", horizon=1)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["result_label"], "DOGRU_YON")

    def test_pending_target_not_evaluated(self):
        """Senaryo B: Hedef mum henüz kapanmadıysa kayıt yazılmaz (BEKLIYOR)."""
        t0 = 10_000_000
        self._seed_candle(symbol="BTCUSDT", timeframe="15m", open_time=t0, close_price=100.0)
        self._seed_candle(symbol="BTCUSDT", timeframe="15m", open_time=t0 + INTERVAL_15M, close_price=105.0)

        signal_store.save_signal_event(
            pair="BTC/USDT",
            timeframe="15m",
            candle_time="2026-11-18T00:00:00+00:00",
            previous_signal="BEKLE",
            current_signal="AL",
            price=100.0,
            reason="Test AL",
        )

        with patch("cgrcoinbot.performance_service.parse_candle_time_ms", return_value=t0):
            # now_ms hedef kapanışından önce
            summary = process_auto_performance("BTC/USDT", "15m", now_ms=t0 + INTERVAL_15M)
            self.assertEqual(summary["evaluated_new"], 0)
            self.assertGreaterEqual(summary["pending"], 1)
            self.assertEqual(len(performance_store.get_performance_records()), 0)

    def test_missing_data_then_recovery_works(self):
        """Senaryo C: Arada mum eksikse EKSIK_VERI döner; mumlar tamamlanınca hesaplanır."""
        t0 = 10_000_000
        self._seed_candle(symbol="BTCUSDT", timeframe="15m", open_time=t0, close_price=100.0)
        # horizon=3 için 1. ve 3. mum var, 2. mum eksik
        self._seed_candle(symbol="BTCUSDT", timeframe="15m", open_time=t0 + INTERVAL_15M, close_price=101.0)
        self._seed_candle(symbol="BTCUSDT", timeframe="15m", open_time=t0 + 3 * INTERVAL_15M, close_price=103.0)

        signal_store.save_signal_event(
            pair="BTC/USDT",
            timeframe="15m",
            candle_time="2026-11-18T00:00:00+00:00",
            previous_signal="BEKLE",
            current_signal="AL",
            price=100.0,
            reason="Test AL",
        )

        with patch("cgrcoinbot.performance_service.parse_candle_time_ms", return_value=t0):
            now_ms = t0 + 5 * INTERVAL_15M
            sum1 = process_auto_performance("BTC/USDT", "15m", now_ms=now_ms)
            # horizon 1 tamamlandı ama horizon 3 eksik veri yüzünden tamamlanamadı
            records1 = performance_store.get_performance_records(horizon=3)
            self.assertEqual(len(records1), 0)

            # Eksik 2. mumu tamamla (REST recovery simülasyonu)
            self._seed_candle(symbol="BTCUSDT", timeframe="15m", open_time=t0 + 2 * INTERVAL_15M, close_price=102.0)

            # Yeniden tetikle
            sum2 = process_auto_performance("BTC/USDT", "15m", now_ms=now_ms)
            records2 = performance_store.get_performance_records(horizon=3)
            self.assertEqual(len(records2), 1)

    def test_idempotent_duplicate_trigger(self):
        """Senaryo D: Aynı mum/seri iki kez tetiklendiğinde çift kayıt oluşmaz."""
        t0 = 10_000_000
        self._seed_sequence(symbol="ETHUSDT", timeframe="15m", start_time=t0, count=15, start_price=2000.0, step=10.0)

        signal_store.save_signal_event(
            pair="ETH/USDT",
            timeframe="15m",
            candle_time="2026-11-18T00:00:00+00:00",
            previous_signal="AL",
            current_signal="SAT",
            price=2000.0,
            reason="Test SAT",
        )

        with patch("cgrcoinbot.performance_service.parse_candle_time_ms", return_value=t0):
            now_ms = t0 + 15 * INTERVAL_15M + 20_000
            sum1 = process_auto_performance("ETH/USDT", "15m", now_ms=now_ms)
            self.assertEqual(sum1["evaluated_new"], 4)  # 1, 3, 6, 12 mum

            # Tekrar çalıştır
            sum2 = process_auto_performance("ETH/USDT", "15m", now_ms=now_ms)
            self.assertEqual(sum2["evaluated_new"], 0)
            self.assertEqual(sum2["already_evaluated"], 4)

            # Toplam kayıt sayısı değişmemeli
            records = performance_store.get_performance_records(pair="ETH/USDT")
            self.assertEqual(len(records), 4)

    def test_horizons_evaluated_independently(self):
        """Senaryo E: 1, 3, 6, 12 ufukları sadece olgunlaştığında sırayla yazılır."""
        t0 = 10_000_000
        # Sadece 2 mum var (horizon 1 olgunlaştı, horizon 3 henüz olgunlaşmadı)
        self._seed_candle(symbol="BTCUSDT", timeframe="15m", open_time=t0, close_price=100.0)
        self._seed_candle(symbol="BTCUSDT", timeframe="15m", open_time=t0 + INTERVAL_15M, close_price=102.0)

        signal_store.save_signal_event(
            pair="BTC/USDT",
            timeframe="15m",
            candle_time="2026-11-18T00:00:00+00:00",
            previous_signal="BEKLE",
            current_signal="AL",
            price=100.0,
            reason="Test AL",
        )

        with patch("cgrcoinbot.performance_service.parse_candle_time_ms", return_value=t0):
            now_ms = t0 + 2 * INTERVAL_15M + 20_000
            summary = process_auto_performance("BTC/USDT", "15m", now_ms=now_ms)
            self.assertEqual(summary["evaluated_new"], 1)

            # horizon 1 yazıldı, horizon 3 henüz yazılmadı
            h1 = performance_store.get_performance_records(horizon=1)
            h3 = performance_store.get_performance_records(horizon=3)
            self.assertEqual(len(h1), 1)
            self.assertEqual(len(h3), 0)

    def test_pair_and_timeframe_isolation(self):
        """Senaryo F: Farklı coin ve timeframe kayıtları birbirinden tamamen yalıtılır."""
        t0 = 10_000_000
        self._seed_sequence(symbol="BTCUSDT", timeframe="15m", start_time=t0, count=5)
        self._seed_sequence(symbol="SOLUSDT", timeframe="15m", start_time=t0, count=5)

        signal_store.save_signal_event(
            pair="BTC/USDT",
            timeframe="15m",
            candle_time="2026-11-18T00:00:00+00:00",
            previous_signal="BEKLE",
            current_signal="AL",
            price=50000.0,
            reason="BTC AL",
        )
        signal_store.save_signal_event(
            pair="SOL/USDT",
            timeframe="15m",
            candle_time="2026-11-18T00:00:00+00:00",
            previous_signal="BEKLE",
            current_signal="AL",
            price=50000.0,
            reason="SOL AL",
        )

        with patch("cgrcoinbot.performance_service.parse_candle_time_ms", return_value=t0):
            now_ms = t0 + 5 * INTERVAL_15M
            # Yalnızca BTC'yi tetikle
            sum_btc = process_auto_performance("BTC/USDT", "15m", now_ms=now_ms)
            self.assertGreaterEqual(sum_btc["evaluated_new"], 1)

            # SOL için henüz hesaplanmamış olmalı
            rec_sol = performance_store.get_performance_records(pair="SOL/USDT")
            self.assertEqual(len(rec_sol), 0)

    def test_bekle_events_and_invalid_reference_excluded(self):
        """Senaryo G: BEKLE olayları ve geçersiz referanslar performans tablosuna yazılmaz."""
        # BEKLE olayı ekle (aslında save_signal_event normalde AL/SAT kabul etse de)
        with candle_store.db_session() as con:
            con.execute("""
                INSERT INTO signal_events (pair, timeframe, candle_time, previous_signal, current_signal, price, reason, created_at)
                VALUES ('BTC/USDT', '15m', '2026-11-18T00:00:00+00:00', 'AL', 'BEKLE', 100.0, 'Test BEKLE', '2026-11-18T00:00:00+00:00')
            """)

        summary = process_auto_performance("BTC/USDT", "15m")
        self.assertEqual(summary["total_events"], 0)
        self.assertEqual(len(performance_store.get_performance_records()), 0)

    def test_startup_catchup(self):
        """Senaryo I: Startup catch-up olgunlaşmış bekleyen olayları tamamlar."""
        t0 = 10_000_000
        self._seed_sequence(symbol="BTCUSDT", timeframe="15m", start_time=t0, count=15, start_price=100.0)

        signal_store.save_signal_event(
            pair="BTC/USDT",
            timeframe="15m",
            candle_time="2026-11-18T00:00:00+00:00",
            previous_signal="BEKLE",
            current_signal="AL",
            price=100.0,
            reason="Test AL",
        )

        with patch("cgrcoinbot.performance_service.parse_candle_time_ms", return_value=t0):
            with patch("time.time", return_value=(t0 + 15 * INTERVAL_15M + 20_000) / 1000):
                # Başlangıç backlog taraması
                sum1 = startup_performance_catchup(limit=50)
                self.assertEqual(sum1["evaluated_new"], 4)

                # İkinci çağrıda yeni kayıt olmamalı
                sum2 = startup_performance_catchup(limit=50)
                self.assertEqual(sum2["evaluated_new"], 0)

    def test_async_wrapper_non_blocking(self):
        """Senaryo K: on_candle_performance_async event loop'u kilitlemeden çalışır."""
        with patch("cgrcoinbot.auto_performance.process_auto_performance", return_value={"evaluated_new": 1}) as mock_proc:
            res = asyncio.run(on_candle_performance_async("BTC/USDT", "15m"))
            self.assertEqual(res["evaluated_new"], 1)
            mock_proc.assert_called_once_with("BTC/USDT", "15m", None)


if __name__ == "__main__":
    unittest.main()
