
"""
CGRCoinBot v0.3.4
Sinyal Performans Takip Komut Satırı Arayüzü (CLI).

Kullanım:
    python -m cgrcoinbot.performance_cli --evaluate
    python -m cgrcoinbot.performance_cli --report
    python -m cgrcoinbot.performance_cli --report --pair BTC/USDT --timeframe 15m
    python -m cgrcoinbot.performance_cli --report --horizon 3 --limit 50

UYARI:
Bu araç yalnızca geçmişte kaydedilmiş sinyal olaylarının ardından oluşan
piyasa fiyatı hareketlerini ölçer. Yatırım tavsiyesi, getiri garantisi veya
gerçekleşmiş işlem kar/zararı hesaplamaz.
"""

import argparse
import sys

from .performance_service import (
    initialize_performance_table,
    evaluate_all_performances,
    generate_performance_report,
)
from .auto_performance import startup_performance_catchup


def print_evaluation_summary(summary: dict) -> None:
    """Değerlendirme çıktısını terminale yazar."""
    print("\n" + "=" * 55)
    print("      SİNYAL PERFORMANS DEĞERLENDİRME SONUCU")
    print("=" * 55)
    print(f"İncelenen Toplam AL/SAT Olayı : {summary['total_events']}")
    print(f"Yeni Hesaplanıp Kaydedilen    : {summary['evaluated_new']}")
    print(f"Zaten Değerlendirilmiş Olan   : {summary['already_evaluated']}")
    print(f"Hedef Mum Henüz Kapanmamış    : {summary['pending']}")
    print(f"Eksik Mum / Kopuk Seri        : {summary['missing_data']}")
    print(f"Geçersiz / Uyumsuz Referans   : {summary['invalid_reference']}")
    print("=" * 55)
    if summary["evaluated_new"] > 0:
        print("Bilgi: Yeni performans kayıtları SQLite veritabanına yazıldı.")
    else:
        print("Bilgi: Veritabanında yeni olgunlaşmış performans kaydı oluşmadı.")


def print_performance_report(report_data: dict, pair_filter=None, tf_filter=None, hz_filter=None) -> None:
    """Performans istatistiklerini terminalde formatlı gösterir."""
    records = report_data["records"]
    overall = report_data["overall"]
    by_horizon = report_data["by_horizon"]

    print("\n" + "=" * 65)
    print("             CGRCoinBot v0.3.5 - SİNYAL PERFORMANS RAPORU")
    print("=" * 65)
    print("KAPSAM: Salt Fiyat Yönü Uyumu Analizi (Komisyon/Kayma Hariçtir)")
    filters = []
    if pair_filter:
        filters.append(f"Parite: {pair_filter}")
    if tf_filter:
        filters.append(f"Zaman Dilimi: {tf_filter}")
    if hz_filter:
        filters.append(f"Ufuk: {hz_filter} mum")
    if filters:
        print("Filtreler -> " + " | ".join(filters))
    print("-" * 65)

    if overall["total"] == 0:
        print("\nHenüz olgunlaşmış performans kaydı bulunmuyor (0 örnek).")
        print("Not: Yeni sinyal olayları oluştukça ve hedef mumlar kapandıkça")
        print("     kolektör tarafından otomatik olarak veya --scan-once / --evaluate")
        print("     CLI komutlarıyla performans ölçümü gerçekleştirilir.\n")
        print("=" * 65)
        return

    # İstatistiksel yeterlilik uyarısı
    if overall["total"] < 30:
        print("UYARI: Toplam örnek sayısı (n < 30) istatistiksel olarak yetersizdir.")
        print("       Elde edilen oranlar genel başarı göstergesi olarak yorumlanamaz.")
        print("-" * 65)

    win_rate_str = f"{overall['win_rate']:.2f}%" if overall["win_rate"] is not None else "Hesaplanamadı"
    print(f"Toplam Değerlendirilen Olay/Ufuk : {overall['total']}")
    print(f"Fiyat Yönü Uyumu (Doğru Yön)     : {overall['dogru_yon']}")
    print(f"Ters Yön                         : {overall['ters_yon']}")
    print(f"Nötr Değişim                     : {overall['notr']}")
    print(f"Yön İsabet Oranı                 : {win_rate_str}")
    print(f"Ortalama Yönlü Değişim           : {overall['avg_change']:+.2f}%")
    print(f"Medyan Yönlü Değişim             : {overall['median_change']:+.2f}%")
    print("-" * 65)

    if by_horizon:
        print("\n--- Ufuk (Horizon) Bazında Dağılım ---")
        print(f"{'Ufuk':<8} | {'Örnek':<6} | {'Doğru':<6} | {'Ters':<6} | {'İsabet %':<10} | {'Ortalama %':<10}")
        print("-" * 65)
        for h, m in by_horizon.items():
            h_rate = f"{m['win_rate']:.1f}%" if m["win_rate"] is not None else "-"
            print(f"{h:<2} mum   | {m['total']:<6} | {m['dogru_yon']:<6} | {m['ters_yon']:<6} | {h_rate:<10} | {m['avg_change']:+6.2f}%")

    print("\n--- Son Kayıtlar ---")
    print(f"{'Parite':<10} | {'TF':<4} | {'Mum':<2} | {'Giriş':>10} | {'Çıkış':>10} | {'Yön %':>8} | {'Sonuç':<10}")
    print("-" * 65)
    for r in records[:15]:
        print(
            f"{r['pair']:<10} | "
            f"{r['timeframe']:<4} | "
            f"{r['horizon_candles']:<2} | "
            f"{r['entry_close']:>10.4f} | "
            f"{r['exit_close']:>10.4f} | "
            f"{r['directional_change_pct']:>+7.2f}% | "
            f"{r['result_label']:<10}"
        )
    print("=" * 65 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="CGRCoinBot Sinyal Performans Takip Sistemi"
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Olgunlaşmış AL/SAT olayları için performans hesaplayıp veritabanına kaydeder",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Kaydedilmiş performans istatistiklerini raporlar",
    )
    parser.add_argument(
        "--scan-once",
        action="store_true",
        help="Tüm pariteler için bekleyen performansları tek seferde tarayıp değerlendirir",
    )
    parser.add_argument(
        "--pair",
        type=str,
        default=None,
        help="Belirli bir pariteye göre filtrele (örn: BTC/USDT)",
    )
    parser.add_argument(
        "--timeframe",
        type=str,
        default=None,
        choices=["5m", "15m", "1h", "4h"],
        help="Belirli bir zaman dilimine göre filtrele",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=None,
        choices=[1, 3, 6, 12],
        help="Belirli bir mum ufkuna göre filtrele (1, 3, 6, 12)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Gösterilecek maksimum kayıt sayısı (varsayılan: 100)",
    )

    args = parser.parse_args()

    initialize_performance_table()

    if not args.evaluate and not args.report and not args.scan_once:
        parser.print_help()
        sys.exit(0)

    if args.scan_once:
        summary = startup_performance_catchup(limit=args.limit)
        print_evaluation_summary(summary)
        return

    if args.evaluate:
        summary = evaluate_all_performances()
        print_evaluation_summary(summary)

    if args.report:
        report_data = generate_performance_report(
            pair=args.pair,
            timeframe=args.timeframe,
            horizon=args.horizon,
            limit=args.limit,
        )
        print_performance_report(
            report_data,
            pair_filter=args.pair,
            tf_filter=args.timeframe,
            hz_filter=args.horizon,
        )


if __name__ == "__main__":
    main()
