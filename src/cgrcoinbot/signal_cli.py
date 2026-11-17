
"""
CGRCoinBot v0.3.2
Teknik Analiz ve Sinyal Takip Sistemi

Özellikler:
- Takip listesindeki coinleri analiz eder.
- RSI, MACD ve EMA göstergelerini görüntüler.
- AL / SAT / BEKLE sinyallerini gösterir.
- Sinyalleri ve sinyal değişim olaylarını SQLite veritabanına kaydeder.
- Aynı coin ve mum için tekrarlı kayıt oluşturmaz.
- Eksik veya eski verilerle sinyal kaydetmez.
- --events seçeneği ile sinyal değişim geçmişini listeler.

GÜVENLİK:
Bu uygulama yalnızca analiz ve uyarı amacıyla çalışır.
Gerçek veya sanal alım-satım emri göndermez.
"""

import argparse

from .main import get_tracked_pairs
from .auto_signal import scan_once
from .signal_engine import analyze_pair
from .signal_service import process_analysis_result
from .signal_store import (
    initialize_signal_table,
    get_signal_history,
    get_signal_events,
)


def print_signal(result: dict) -> None:
    """Tek bir coinin analiz sonucunu terminalde gösterir."""
    print(f"\nCoin: {result['pair']}")
    print(f"Zaman Dilimi: {result['timeframe']}")
    print(f"Sinyal: {result['signal']}")
    print(f"Neden: {result['reason']}")

    if result["signal"] == "VERI_YOK":
        print("Durum: Veri eksik veya güncel değil.")
        print("-" * 50)
        return

    print(f"Fiyat: {result['price']:,.4f} USDT")
    print(f"RSI (14): {result['rsi']:.2f}")
    print(f"EMA (20): {result['ema20']:.4f}")
    print(f"EMA (50): {result['ema50']:.4f}")
    print(f"MACD Histogram: {result['macd_histogram']:.6f}")
    print(f"Mum Başlangıcı: {result['candle_time']}")
    print("-" * 50)


def print_signal_history(limit: int = 20) -> None:
    """SQLite veritabanındaki son sinyalleri listeler."""
    history = get_signal_history(limit=limit)

    print("\n=== SON SİNYALLER ===\n")

    if not history:
        print("Henüz kaydedilmiş sinyal bulunmuyor.")
        return

    for pair, timeframe, signal, price, candle_time in history:
        print(
            f"{pair:<12} | "
            f"{timeframe:<4} | "
            f"{signal:<5} | "
            f"{price:>12,.4f} USDT | "
            f"{candle_time}"
        )


def print_signal_events(limit: int = 20) -> None:
    """SQLite veritabanındaki son sinyal değişim olaylarını listeler."""
    events = get_signal_events(limit=limit)

    print("\n=== SİNYAL DEĞİŞİM OLAYLARI (EVENTS) ===\n")

    if not events:
        print("Henüz kaydedilmiş sinyal değişim olayı bulunmuyor.")
        return

    for pair, timeframe, prev_sig, curr_sig, price, candle_time, reason, _ in events:
        transition = f"{prev_sig or 'YOK'} -> {curr_sig}"
        print(
            f"{pair:<12} | "
            f"{timeframe:<4} | "
            f"{transition:<12} | "
            f"{price:>12,.4f} USDT | "
            f"{candle_time} | "
            f"{reason}"
        )


def main() -> None:
    """CGRCoinBot sinyal terminalini başlatır."""
    parser = argparse.ArgumentParser(
        description="CGRCoinBot Teknik Analiz ve Sinyal Sistemi"
    )

    parser.add_argument(
        "--timeframe",
        default="15m",
        choices=["5m", "15m", "1h", "4h"],
        help="Analiz zaman dilimi (varsayılan: 15m)",
    )

    parser.add_argument(
        "--history",
        action="store_true",
        help="Kaydedilen sinyal geçmişini göster",
    )

    parser.add_argument(
        "--events",
        action="store_true",
        help="Kaydedilen sinyal değişim olaylarını göster",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Gösterilecek geçmiş kayıt sayısı (varsayılan: 20)",
    )

    parser.add_argument(
        "--scan-once",
        action="store_true",
        help="Takip listesindeki tüm zaman dilimlerini tek seferde tara ve özetle",
    )

    args = parser.parse_args()

    print("\n" + "=" * 50)
    print("           CGRCoinBot v0.3.3")
    print("       Teknik Analiz ve Sinyal Sistemi")
    print("=" * 50)

    print("MOD: SADECE UYARI")
    print("OTOMATİK ALIM-SATIM: DEVRE DIŞI\n")

    # Tabloları oluştur/doğrula
    initialize_signal_table()

    # --history ve --events aynı anda verilirse öncelik veya açık hata
    if args.history and args.events:
        print("HATA: --history ve --events seçenekleri aynı anda kullanılamaz. Lütfen birini seçin.")
        return

    if args.history:
        print_signal_history(limit=args.limit)
        return

    if args.events:
        print_signal_events(limit=args.limit)
        return

    if args.scan_once:
        print("Tüm zaman dilimleri için tek seferlik tarama başlatılıyor (5m, 15m, 1h, 4h)...")
        summary = scan_once()
        print("\n" + "=" * 50)
        print("                 TARAMA ÖZETİ")
        print("=" * 50)
        print(f"İşlenen Seri Sayısı: {summary['processed']}")
        print(f"Yeni Kaydedilen Sinyal: {summary['new_signals']}")
        print(f"Yeni Oluşan Olay (Event): {summary['new_events']}")
        print(f"Veri Hatası / VERI_YOK: {summary['data_errors']}")
        print("=" * 50)
        return

    # Normal analiz modu
    pairs = get_tracked_pairs()

    if not pairs:
        print("Takip listesinde coin bulunamadı.")
        return

    print(f"Takip Edilen Coin Sayısı: {len(pairs)}")
    print(f"Analiz Zaman Dilimi: {args.timeframe}")
    print("-" * 50)

    signal_counts = {
        "AL": 0,
        "SAT": 0,
        "BEKLE": 0,
        "VERI_YOK": 0,
    }

    new_signals = 0
    new_events = 0

    for pair in pairs:
        try:
            result = analyze_pair(pair, timeframe=args.timeframe)
            signal = result["signal"]
            signal_counts[signal] += 1

            print_signal(result)

            # Sinyal ve olay servisi ile işle
            status_info = process_analysis_result(result)

            if status_info["is_new_signal"]:
                new_signals += 1

            if status_info["event_created"]:
                new_events += 1

            print(f"Durum: {status_info['message']}\n")

        except (OSError, ValueError, KeyError) as error:
            print(f"\n{pair}: Analiz hatası: {error}\n")

    print("\n" + "=" * 50)
    print("                 ANALİZ ÖZETİ")
    print("=" * 50)

    print(f"AL Sinyali: {signal_counts['AL']}")
    print(f"SAT Sinyali: {signal_counts['SAT']}")
    print(f"BEKLE Sinyali: {signal_counts['BEKLE']}")
    print(f"Veri Hatası: {signal_counts['VERI_YOK']}")

    print(f"\nYeni Kaydedilen Sinyal: {new_signals}")
    print(f"Yeni Oluşan Olay (Event): {new_events}")

    print("\nİşlem yapılmadı. Yalnızca analiz tamamlandı.")
    print("=" * 50)


if __name__ == "__main__":
    main()
