
"""
CGRCoinBot v0.3.2
Sinyal Değerlendirme ve Olay (Event) Yönetim Servisi.

Görevleri:
- Analiz sonucunu değerlendirmek.
- Geçmiş mumu sorgulayarak sinyal değişimini tespit etmek.
- signals tablosuna kaydetmek.
- Sadece geçerli değişimlerde (BEKLE->AL, BEKLE->SAT, AL->SAT, SAT->AL)
  signal_events tablosuna olay kaydetmek.
- Tekrarlı çalıştırmalarda 0 yeni olay / sinyal üretmek (idempotent).
"""

from typing import Optional
from .signal_store import (
    get_previous_signal,
    save_signal,
    save_signal_event,
)


# Olay (bildirim) üretilecek geçerli geçişler:
# Yeni sinyal AL veya SAT olmalı ve önceki sinyalden farklı olmalı.
VALID_EVENT_TRANSITIONS = {
    ("BEKLE", "AL"),
    ("BEKLE", "SAT"),
    ("AL", "SAT"),
    ("SAT", "AL"),
}


def process_analysis_result(result: dict) -> dict:
    """
    Tek bir parite için analiz sonucunu işler.
    Adımlar:
    1. Sinyal VERI_YOK ise kaydetmez, durum bilgisi döner.
    2. signals tablosuna kaydetmeden ÖNCE candle_time'dan önceki mumu sorgular.
    3. signals tablosuna kaydeder.
    4. Eğer yeni bir kayıt ise (ilk kez bu mum işleniyorsa) ve
       önceki sinyal mevcutsa, geçiş geçerli mi kontrol eder.
    5. Geçerliyse signal_events tablosuna ekler.
    6. Detaylı durum sözlüğü döner.
    """
    signal = result.get("signal")
    pair = result.get("pair")
    timeframe = result.get("timeframe")
    candle_time = result.get("candle_time")
    price = result.get("price", 0.0)
    reason = result.get("reason", "")

    if signal not in ("AL", "SAT", "BEKLE"):
        return {
            "status": "VERI_HATASI",
            "is_new_signal": False,
            "event_created": False,
            "previous_signal": None,
            "current_signal": signal,
            "message": "Veri geçersiz veya yetersiz olduğu için kayıt yapılmadı.",
        }

    # 1. Mevcut mum kaydedilmeden önce geçmişteki son sinyali al
    prev_record = get_previous_signal(pair, timeframe, candle_time)
    prev_signal = prev_record["signal"] if prev_record else None

    # 2. Sinyali kaydet (mükerrer ise False döner)
    is_new_signal = save_signal(result)

    if not is_new_signal:
        return {
            "status": "MEVCUT_KAYIT",
            "is_new_signal": False,
            "event_created": False,
            "previous_signal": prev_signal,
            "current_signal": signal,
            "message": "Bu coin ve mum için sinyal kaydı zaten mevcut.",
        }

    # 3. Yeni sinyal kaydı ise olay oluşma durumunu kontrol et
    # Kural: İlk sinyal (bootstrap) ise olay oluşturma
    if prev_signal is None:
        return {
            "status": "BOOTSTRAP",
            "is_new_signal": True,
            "event_created": False,
            "previous_signal": None,
            "current_signal": signal,
            "message": "İlk sinyal kaydı (başlangıç/bootstrap). Bildirim olayı oluşturulmadı.",
        }

    transition = (prev_signal, signal)
    if transition in VALID_EVENT_TRANSITIONS:
        event_saved = save_signal_event(
            pair=pair,
            timeframe=timeframe,
            candle_time=candle_time,
            previous_signal=prev_signal,
            current_signal=signal,
            price=price,
            reason=reason,
        )
        return {
            "status": "OLAY_OLUSTU",
            "is_new_signal": True,
            "event_created": event_saved,
            "previous_signal": prev_signal,
            "current_signal": signal,
            "message": f"Sinyal değişimi: {prev_signal} -> {signal} (Olay kaydedildi)",
        }

    # AL -> AL, SAT -> SAT, AL/SAT -> BEKLE, BEKLE -> BEKLE
    return {
        "status": "DEGISIM_YOK",
        "is_new_signal": True,
        "event_created": False,
        "previous_signal": prev_signal,
        "current_signal": signal,
        "message": f"Bildirimlik değişim yok ({prev_signal} -> {signal})",
    }
