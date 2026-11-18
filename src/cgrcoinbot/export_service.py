
"""
CGRCoinBot v0.3.7
CSV ve JSON Dışa Aktarma Servisi (Export Service).

Görevler:
- signals, signal_events ve signal_performance tablolarından salt-okunur veri sorgulama.
- Filtreleme (pair, timeframe, signal, horizon, limit).
- CSV formatında (utf-8-sig BOM, Excel uyumlu, formula injection korumalı) dosya yazımı.
- JSON formatında (utf-8, sonlu sayılar, metadata + records şeması) dosya yazımı.
- Güvenli atomik dosya kaydı (temp dosya + os.replace) ve --overwrite koruması.
- Kesinlikle veritabanı kaydı değiştirmez, al-sat emri içermez.
"""

import csv
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from .candle_store import db_session


# İzin verilen veri setleri ve açık kolon allowlist'i
ALLOWED_DATASETS = ("signals", "events", "performance")

DATASET_COLUMNS = {
    "signals": [
        "id", "pair", "timeframe", "candle_time", "signal",
        "price", "rsi", "ema20", "ema50", "macd_histogram",
        "reason", "created_at"
    ],
    "events": [
        "id", "pair", "timeframe", "candle_time", "previous_signal",
        "current_signal", "price", "reason", "created_at"
    ],
    "performance": [
        "id", "event_id", "pair", "timeframe", "event_candle_time",
        "horizon_candles", "signal", "entry_close", "exit_close",
        "raw_change_pct", "directional_change_pct", "result_label",
        "evaluated_at"
    ],
}

FORMULA_PREFIXES = ("=", "+", "-", "@")


def sanitize_csv_cell(value: Any) -> Any:
    """
    Excel formula injection (CSV Injection) koruması.
    Eğer değer bir string ise ve '=', '+', '-', '@' ile başlıyorsa,
    başına tek tırnak (') ekler.
    Negatif sayısal değerler (float/int) korunur, tırnak eklenmez.
    """
    if isinstance(value, str):
        # Sayı olarak parse edilebiliyorsa (örn: "-5.2", "+3") formül değil sayıdır
        try:
            float(value)
            return value
        except ValueError:
            pass

        if value.startswith(FORMULA_PREFIXES):
            return f"'{value}"
    return value


def sanitize_json_value(value: Any) -> Any:
    """
    JSON serileştirmesinde geçersiz NaN / Infinity değerlerini None (null) yapar.
    """
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
    return value


def fetch_dataset_records(
    dataset: str,
    pair: Optional[str] = None,
    timeframe: Optional[str] = None,
    signal: Optional[str] = None,
    horizon: Optional[int] = None,
    limit: Optional[int] = 1000,
) -> List[Dict[str, Any]]:
    """
    İlgili veri setini parametreli SQL ve allowlist ile çeker.
    Deterministik sıralama: candle_time ASC / event_candle_time ASC, id ASC.
    """
    if dataset not in ALLOWED_DATASETS:
        raise ValueError(f"Geçersiz dataset: {dataset}. İzin verilenler: {ALLOWED_DATASETS}")

    conditions = []
    params: List[Any] = []

    if dataset == "signals":
        cols = ", ".join(DATASET_COLUMNS["signals"])
        query = f"SELECT {cols} FROM signals"
        if pair:
            conditions.append("pair = ?")
            params.append(pair)
        if timeframe:
            conditions.append("timeframe = ?")
            params.append(timeframe)
        if signal:
            conditions.append("signal = ?")
            params.append(signal)
        if horizon is not None:
            raise ValueError("--horizon filtresi yalnızca 'performance' dataseti için geçerlidir.")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY candle_time ASC, id ASC"

    elif dataset == "events":
        cols = ", ".join(DATASET_COLUMNS["events"])
        query = f"SELECT {cols} FROM signal_events"
        if pair:
            conditions.append("pair = ?")
            params.append(pair)
        if timeframe:
            conditions.append("timeframe = ?")
            params.append(timeframe)
        if signal:
            conditions.append("current_signal = ?")
            params.append(signal)
        if horizon is not None:
            raise ValueError("--horizon filtresi yalnızca 'performance' dataseti için geçerlidir.")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY candle_time ASC, id ASC"

    elif dataset == "performance":
        query = """
            SELECT p.id, p.event_id, p.pair, p.timeframe, p.event_candle_time,
                   p.horizon_candles, COALESCE(e.current_signal, 'BILINMIYOR') AS signal,
                   p.entry_close, p.exit_close, p.raw_change_pct,
                   p.directional_change_pct, p.result_label, p.evaluated_at
            FROM signal_performance p
            LEFT JOIN signal_events e ON p.event_id = e.id
        """
        if pair:
            conditions.append("p.pair = ?")
            params.append(pair)
        if timeframe:
            conditions.append("p.timeframe = ?")
            params.append(timeframe)
        if horizon is not None:
            conditions.append("p.horizon_candles = ?")
            params.append(horizon)
        if signal:
            conditions.append("e.current_signal = ?")
            params.append(signal)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY p.event_candle_time ASC, p.horizon_candles ASC, p.id ASC"

    if limit is not None:
        if limit <= 0:
            raise ValueError("--limit pozitif bir tam sayı olmalıdır.")
        query += " LIMIT ?"
        params.append(limit)

    with db_session() as con:
        rows = con.execute(query, tuple(params)).fetchall()

    target_columns = DATASET_COLUMNS[dataset]
    records = []
    for row in rows:
        record_dict = {}
        for col_name, val in zip(target_columns, row):
            record_dict[col_name] = val
        records.append(record_dict)

    return records


def export_to_csv(
    records: List[Dict[str, Any]],
    dataset: str,
    output_path: Path,
    overwrite: bool = False,
) -> Path:
    """
    Verileri utf-8-sig (Excel uyumlu BOM), DictWriter ve formula injection korumalı CSV'ye yazar.
    Atomik yazım için geçici dosya ve os.replace kullanılır.
    """
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Hedef dosya zaten mevcut: {output_path}. Üzerine yazmak için --overwrite parametresini kullanın."
        )

    fieldnames = DATASET_COLUMNS[dataset]

    # Atomik yazım: Aynı dizinde geçici dosya oluştur
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        delete=False,
        dir=output_path.parent,
        encoding="utf-8-sig",
        newline="",
    )
    temp_path = Path(temp_file.name)

    try:
        writer = csv.DictWriter(temp_file, fieldnames=fieldnames)
        writer.writeheader()

        for rec in records:
            sanitized_row = {
                k: sanitize_csv_cell(rec.get(k))
                for k in fieldnames
            }
            writer.writerow(sanitized_row)

        temp_file.flush()
        temp_file.close()

        if output_path.exists() and not overwrite:
            # Yarış koşuluna karşı tekrar kontrol
            raise FileExistsError(f"Hedef dosya zaten mevcut: {output_path}.")

        os.replace(temp_path, output_path)
        return output_path

    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def export_to_json(
    records: List[Dict[str, Any]],
    dataset: str,
    output_path: Path,
    filters: Optional[Dict[str, Any]] = None,
    overwrite: bool = False,
) -> Path:
    """
    Verileri standart JSON formatında (schema metadata + records) atomik olarak yazar.
    """
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Hedef dosya zaten mevcut: {output_path}. Üzerine yazmak için --overwrite parametresini kullanın."
        )

    clean_records = []
    for rec in records:
        clean_row = {
            k: sanitize_json_value(v)
            for k, v in rec.items()
        }
        clean_records.append(clean_row)

    payload = {
        "metadata": {
            "schema_version": "1.0",
            "dataset": dataset,
            "exported_at_utc": datetime.now(timezone.utc).isoformat(),
            "record_count": len(clean_records),
            "filters": filters or {},
        },
        "records": clean_records,
    }

    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        delete=False,
        dir=output_path.parent,
        encoding="utf-8",
    )
    temp_path = Path(temp_file.name)

    try:
        json.dump(payload, temp_file, ensure_ascii=False, indent=2)
        temp_file.flush()
        temp_file.close()

        if output_path.exists() and not overwrite:
            raise FileExistsError(f"Hedef dosya zaten mevcut: {output_path}.")

        os.replace(temp_path, output_path)
        return output_path

    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def export_dataset(
    dataset: str,
    output_format: str,
    output_path: Optional[str] = None,
    pair: Optional[str] = None,
    timeframe: Optional[str] = None,
    signal: Optional[str] = None,
    horizon: Optional[int] = None,
    limit: Optional[int] = 1000,
    overwrite: bool = False,
) -> tuple[Path, int]:
    """
    Dışa aktarma ana akış fonksiyonu.
    Dönüş: (yazılan_dosya_yolu, kayıt_sayısı)
    """
    output_format = output_format.lower()
    if output_format not in ("csv", "json"):
        raise ValueError(f"Geçersiz format: {output_format}. Sadece 'csv' veya 'json' desteklenir.")

    records = fetch_dataset_records(
        dataset=dataset,
        pair=pair,
        timeframe=timeframe,
        signal=signal,
        horizon=horizon,
        limit=limit,
    )

    if output_path:
        dest_path = Path(output_path)
    else:
        # Varsayılan dosya adı: exports/{dataset}_{timestamp}.{ext}
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dest_path = Path("exports") / f"{dataset}_{timestamp_str}.{output_format}"

    filters = {
        "pair": pair,
        "timeframe": timeframe,
        "signal": signal,
        "horizon": horizon,
        "limit": limit,
    }

    if output_format == "csv":
        final_path = export_to_csv(records, dataset, dest_path, overwrite=overwrite)
    else:
        final_path = export_to_json(records, dataset, dest_path, filters=filters, overwrite=overwrite)

    return final_path, len(records)
