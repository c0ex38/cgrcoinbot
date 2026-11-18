
"""
CGRCoinBot v0.3.7
CSV ve JSON Dışa Aktarma Komut Satırı Arayüzü (CLI).

Kullanım Örnekleri:
    python -m cgrcoinbot.export_cli --dataset signals --format csv --output .\\exports\\signals.csv
    python -m cgrcoinbot.export_cli --dataset events --format json --output .\\exports\\events.json
    python -m cgrcoinbot.export_cli --dataset performance --format csv --output .\\exports\\performance.csv
    python -m cgrcoinbot.export_cli --dataset performance --format json --pair BTC/USDT --timeframe 15m --horizon 3 --signal AL --output .\\exports\\btc_15m_3_AL.json
    python -m cgrcoinbot.export_cli --dataset signals --format csv --limit 100 --output .\\exports\\latest_signals.csv

UYARI:
Bu araç yalnızca yerel SQLite veritabanındaki analitik verileri dışa aktarır.
Alım-satım veya emir gönderme yetkisi bulunmamaktadır.
"""

import argparse
import sys
from pathlib import Path

from .export_service import export_dataset, ALLOWED_DATASETS


def main():
    parser = argparse.ArgumentParser(
        description="CGRCoinBot v0.3.7 - Veri Seti Dışa Aktarma Sistemi (CSV / JSON)"
    )

    parser.add_argument(
        "--dataset",
        type=str,
        required=True,
        choices=list(ALLOWED_DATASETS),
        help="Dışa aktarılacak veri seti: signals, events veya performance",
    )
    parser.add_argument(
        "--format",
        type=str,
        required=True,
        choices=["csv", "json"],
        help="Çıktı dosya formatı: csv veya json",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Çıktı dosya yolu (örn: .\\exports\\signals.csv). Belirtilmezse varsayılan exports/ klasörüne yazılır.",
    )
    parser.add_argument(
        "--pair",
        type=str,
        default=None,
        help="Filtrelenecek parite (örn: BTC/USDT)",
    )
    parser.add_argument(
        "--timeframe",
        type=str,
        default=None,
        choices=["5m", "15m", "1h", "4h"],
        help="Filtrelenecek zaman dilimi (5m, 15m, 1h, 4h)",
    )
    parser.add_argument(
        "--signal",
        type=str,
        default=None,
        choices=["AL", "SAT", "BEKLE"],
        help="Filtrelenecek sinyal türü (AL, SAT veya BEKLE)",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=None,
        choices=[1, 3, 6, 12],
        help="Filtrelenecek mum ufkusu (yalnızca 'performance' dataseti için: 1, 3, 6, 12)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Dışa aktarılacak maksimum kayıt sayısı (varsayılan: 1000)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Tüm kayıtları sınırsız dışa aktar (limit sınırını kaldırır)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Hedef dosya mevcutsa üzerine yazmaya izin ver",
    )

    args = parser.parse_args()

    # Mantık doğrulama: signals ve events için horizon kullanılamaz
    if args.dataset in ("signals", "events") and args.horizon is not None:
        print(f"HATA: --horizon parametresi yalnızca 'performance' veri seti için kullanılabilir.", file=sys.stderr)
        sys.exit(2)

    limit = None if args.all else args.limit

    print("\n" + "=" * 65)
    print("        CGRCoinBot v0.3.7 - VERİ SETİ DIŞA AKTARMA (EXPORT)")
    print("=" * 65)
    print(f"Veri Seti : {args.dataset}")
    print(f"Format    : {args.format.upper()}")
    if args.pair:
        print(f"Parite    : {args.pair}")
    if args.timeframe:
        print(f"TF        : {args.timeframe}")
    if args.signal:
        print(f"Sinyal    : {args.signal}")
    if args.horizon:
        print(f"Ufuk      : {args.horizon} mum")
    print(f"Limit     : {'SINIRSIZ' if limit is None else limit}")
    print("-" * 65)

    try:
        dest_path, record_count = export_dataset(
            dataset=args.dataset,
            output_format=args.format,
            output_path=args.output,
            pair=args.pair,
            timeframe=args.timeframe,
            signal=args.signal,
            horizon=args.horizon,
            limit=limit,
            overwrite=args.overwrite,
        )

        print(f"\n[+] İşlem Başarılı!")
        print(f"    Yazılan Kayıt : {record_count} satır")
        print(f"    Hedef Dosya   : {dest_path}")
        if record_count == 0:
            print("    Bilgi: Kriterlere uyan kayıt bulunamadı (boş veri seti dışa aktarıldı).")
        print("\nGüvenlik Hatırlatması: Dışa aktarılan dosya analitik verileri içerir.")
        print("                       Dosyaları genel ağlarda paylaşırken dikkatli olunuz.")
        print("=" * 65 + "\n")

    except FileExistsError as fee:
        print(f"\nHATA: {fee}", file=sys.stderr)
        print("Dosyanın üzerine yazmak için '--overwrite' parametresini ekleyebilirsiniz.\n", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"\nHATA: Dışa aktarma sırasında bir hata oluştu: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
