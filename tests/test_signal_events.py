
"""
CGRCoinBot v0.3.2 - Sinyal Değişimleri ve Olay (Event) Birim Testleri.

Test Senaryoları:
- İzole geçici veritabanı (tempfile) ve patch kullanımı (gerçek market.db ve ağ yok).
- İlk sinyal (bootstrap) -> Olay oluşturulmaz.
- BEKLE -> AL, BEKLE -> SAT, AL -> SAT, SAT -> AL -> Olay oluşur.
- AL -> AL, SAT -> SAT, AL/SAT -> BEKLE, BEKLE -> BEKLE -> Olay oluşmaz.
- Aynı mum tekrar işlendiğinde mükerrer sinyal veya olay oluşmaz (idempotent).
- Farklı parite ve timeframe bağımsızlığı.
- VERI_YOK -> Hiçbir tabloya kayıt yapılmaz.
- Geriye dönük eski kayıtlar otomatik olaya dönüştürülmez (yalnızca yeni analizde karşılaştırma).
- DB session kaynaklarının düzgün kapanması (Windows kilitlenme/WinError 32 yok).
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cgrcoinbot import candle_store, signal_store
from cgrcoinbot.signal_service import process_analysis_result


class SignalEventsTests(unittest.TestCase):

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

    def _create_result(
        self,
        pair="BTC/USDT",
        timeframe="15m",
        candle_time="2026-11-18 01:00:00 UTC",
        signal="AL",
        price=90000.0,
        reason="Test gerekçesi",
    ):
        return {
            "pair": pair,
            "timeframe": timeframe,
            "candle_time": candle_time,
            "signal": signal,
            "price": price,
            "rsi": 60.0,
            "ema20": 90100.0,
            "ema50": 89900.0,
            "macd_histogram": 5.0,
            "reason": reason,
        }

    def test_bootstrap_no_event(self):
        """İlk sinyal kaydında (daha önce geçmiş kayıt yokken) olay oluşmamalıdır."""
        res = self._create_result(signal="AL")
        status = process_analysis_result(res)

        self.assertTrue(status["is_new_signal"])
        self.assertFalse(status["event_created"])
        self.assertEqual(status["status"], "BOOTSTRAP")

        events = signal_store.get_signal_events()
        self.assertEqual(len(events), 0)

    def test_valid_transitions_create_events(self):
        """
        BEKLE -> AL, BEKLE -> SAT, AL -> SAT, SAT -> AL
        geçişleri birer olay (event) oluşturmalıdır.
        """
        valid_transitions = [
            ("BEKLE", "AL"),
            ("BEKLE", "SAT"),
            ("AL", "SAT"),
            ("SAT", "AL"),
        ]

        for idx, (first_sig, second_sig) in enumerate(valid_transitions):
            pair = f"COIN{idx}/USDT"

            # İlk mum (t1)
            t1 = self._create_result(pair=pair, candle_time="2026-11-18 01:00:00 UTC", signal=first_sig)
            process_analysis_result(t1)

            # İkinci mum (t2)
            t2 = self._create_result(pair=pair, candle_time="2026-11-18 01:15:00 UTC", signal=second_sig)
            status2 = process_analysis_result(t2)

            self.assertTrue(status2["is_new_signal"])
            self.assertTrue(status2["event_created"], f"Geçiş {first_sig} -> {second_sig} olay üretmeliydi")
            self.assertEqual(status2["status"], "OLAY_OLUSTU")

            # Kaydedilen olayı kontrol et
            events = signal_store.get_signal_events(pair=pair)
            self.assertEqual(len(events), 1)
            ev = events[0]
            self.assertEqual(ev[0], pair) # pair
            self.assertEqual(ev[2], first_sig) # previous_signal
            self.assertEqual(ev[3], second_sig) # current_signal

    def test_non_event_transitions(self):
        """
        AL -> AL, SAT -> SAT, AL -> BEKLE, SAT -> BEKLE, BEKLE -> BEKLE
        geçişlerinde olay (event) oluşmamalıdır.
        """
        non_event_pairs = [
            ("AL", "AL"),
            ("SAT", "SAT"),
            ("AL", "BEKLE"),
            ("SAT", "BEKLE"),
            ("BEKLE", "BEKLE"),
        ]

        for idx, (first_sig, second_sig) in enumerate(non_event_pairs):
            pair = f"TOKEN{idx}/USDT"

            t1 = self._create_result(pair=pair, candle_time="2026-11-18 01:00:00 UTC", signal=first_sig)
            process_analysis_result(t1)

            t2 = self._create_result(pair=pair, candle_time="2026-11-18 01:15:00 UTC", signal=second_sig)
            status2 = process_analysis_result(t2)

            self.assertTrue(status2["is_new_signal"])
            self.assertFalse(status2["event_created"], f"Geçiş {first_sig} -> {second_sig} olay üretmemeliydi")
            self.assertEqual(status2["status"], "DEGISIM_YOK")

            events = signal_store.get_signal_events(pair=pair)
            self.assertEqual(len(events), 0)

    def test_idempotent_duplicate_processing(self):
        """Aynı mum için tekrar çalıştırma ne yeni sinyal ne de yeni olay üretmelidir."""
        pair = "ETH/USDT"
        t1 = self._create_result(pair=pair, candle_time="2026-11-18 01:00:00 UTC", signal="BEKLE")
        process_analysis_result(t1)

        t2 = self._create_result(pair=pair, candle_time="2026-11-18 01:15:00 UTC", signal="AL")
        status2_first = process_analysis_result(t2)
        self.assertTrue(status2_first["event_created"])

        # Tekrar aynı mum t2 çağrılıyor
        status2_second = process_analysis_result(t2)
        self.assertFalse(status2_second["is_new_signal"])
        self.assertFalse(status2_second["event_created"])
        self.assertEqual(status2_second["status"], "MEVCUT_KAYIT")

        # Toplamda 1 olay olmalı
        events = signal_store.get_signal_events(pair=pair)
        self.assertEqual(len(events), 1)

    def test_pair_and_timeframe_independence(self):
        """Farklı pariteler ve farklı timeframe'ler birbirini etkilememelidir."""
        # BTC/USDT 15m (BEKLE -> AL)
        process_analysis_result(self._create_result(pair="BTC/USDT", timeframe="15m", candle_time="2026-11-18 01:00:00 UTC", signal="BEKLE"))
        status_btc_15 = process_analysis_result(self._create_result(pair="BTC/USDT", timeframe="15m", candle_time="2026-11-18 01:15:00 UTC", signal="AL"))
        self.assertTrue(status_btc_15["event_created"])

        # BTC/USDT 1h (İlk kayıt bootstrap)
        status_btc_1h = process_analysis_result(self._create_result(pair="BTC/USDT", timeframe="1h", candle_time="2026-11-18 01:00:00 UTC", signal="AL"))
        self.assertFalse(status_btc_1h["event_created"])
        self.assertEqual(status_btc_1h["status"], "BOOTSTRAP")

        # SOL/USDT 15m (İlk kayıt bootstrap)
        status_sol = process_analysis_result(self._create_result(pair="SOL/USDT", timeframe="15m", candle_time="2026-11-18 01:15:00 UTC", signal="AL"))
        self.assertFalse(status_sol["event_created"])
        self.assertEqual(status_sol["status"], "BOOTSTRAP")

        # Yalnızca 1 adet BTC/USDT 15m olayı olmalı
        events = signal_store.get_signal_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "BTC/USDT")
        self.assertEqual(events[0][1], "15m")

    def test_veri_yok_no_record(self):
        """VERI_YOK durumunda hiçbir tabloya kayıt yapılmamalıdır."""
        res = self._create_result(signal="VERI_YOK")
        status = process_analysis_result(res)

        self.assertFalse(status["is_new_signal"])
        self.assertFalse(status["event_created"])
        self.assertEqual(status["status"], "VERI_HATASI")

        self.assertEqual(len(signal_store.get_signal_history()), 0)
        self.assertEqual(len(signal_store.get_signal_events()), 0)

    def test_database_cleanup_no_lock(self):
        """İşlemler sonrasında DB bağlantısı kapatılmış olmalı (WinError 32 olmadan silinebilmeli)."""
        res = self._create_result(signal="BEKLE")
        process_analysis_result(res)
        self.assertTrue(self.db_path.exists())


if __name__ == "__main__":
    unittest.main()
