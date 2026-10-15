"""Engine texts in every supported language. To add a language, add its code to LANGUAGES
and a translation to every entry below (missing entries fall back to English)."""

import unicodedata

LANGUAGES = ("tr", "en")
_lang = "en"

MESSAGES = {
    "vr_connected": {"tr": "SteamVR'a bağlanıldı", "en": "Connected to SteamVR"},
    "vr_closed": {"tr": "SteamVR kapandı", "en": "SteamVR closed"},
    "vr_error": {"tr": "SteamVR hatası: {err}", "en": "SteamVR error: {err}"},
    "calib_failed": {
        "tr": "Kalibrasyon başarısız: vücut veya kask yeterince görülmedi",
        "en": "Calibration failed: the body or the headset was not seen well enough",
    },
    "calib_ok": {
        "tr": "Kalibrasyon tamam: yön {yaw:.1f}°, ölçek {scale:.2f}, zemin {floor:.2f} m, {n} örnek",
        "en": "Calibration done: yaw {yaw:.1f}°, scale {scale:.2f}, floor {floor:.2f} m, {n} samples",
    },
    "calib_loaded": {
        "tr": "Kayıtlı kalibrasyon yüklendi. Kamerayı oynattıysan ya da Quest'te merkezleme yaptıysan F8 ile yenile.",
        "en": "Saved calibration loaded. Press F8 to redo it if the camera moved or you recentered the Quest.",
    },
    "camera_ok": {"tr": "Kamera {i}: çalışıyor {w}x{h}", "en": "Camera {i}: working {w}x{h}"},
    "camera_no_image": {"tr": "Kamera {i}: açıldı ama görüntü yok", "en": "Camera {i}: opened but no image"},
    "camera_open_failed": {"tr": "Kamera {i} açılamadı", "en": "Could not open camera {i}"},
    "camera_lost": {"tr": "Kamera bağlantısı koptu, yeniden bağlanılıyor…", "en": "Camera connection lost, reconnecting…"},
    "camera_back": {"tr": "Kamera yeniden bağlandı", "en": "Camera reconnected"},
    "error": {"tr": "HATA: {err}", "en": "ERROR: {err}"},
    "engine_started": {
        "tr": "Motor başladı: kamera {cam}, model {model}",
        "en": "Engine started: camera {cam}, model {model}",
    },
    "engine_stopped": {"tr": "Motor durdu", "en": "Engine stopped"},
    # OpenCV preview window (no WPF app)
    "ov_fps": {"tr": "FPS {fps:.0f}   kişi: {person}", "en": "FPS {fps:.0f}   person: {person}"},
    "ov_person_yes": {"tr": "var", "en": "yes"},
    "ov_person_no": {"tr": "YOK", "en": "NO"},
    "ov_vr_off": {"tr": "SteamVR: kapalı (--no-vr)", "en": "SteamVR: off (--no-vr)"},
    "ov_vr_on": {"tr": "SteamVR: bağlı", "en": "SteamVR: connected"},
    "ov_vr_wait": {"tr": "SteamVR: bekleniyor...", "en": "SteamVR: waiting..."},
    "ov_countdown": {"tr": "KALİBRASYON: dik dur, ileri bak... {t:.1f}", "en": "CALIBRATION: stand straight, look ahead... {t:.1f}"},
    "ov_collect": {"tr": "KALİBRASYON: kıpırdama...", "en": "CALIBRATION: hold still..."},
    "ov_nocal": {"tr": "Kalibrasyon yok: kaskı tak, F8'e bas", "en": "Not calibrated: put the headset on, press F8"},
    "ov_cal": {"tr": "Kalibre (ölçek {scale:.2f})  gönderilen tracker: {n}", "en": "Calibrated (scale {scale:.2f})  trackers sent: {n}"},
    "ov_paused": {"tr": "DURAKLATILDI (F9)", "en": "PAUSED (F9)"},
    "help": {
        "tr": "Tuşlar (önizleme penceresi): C = kalibrasyon, P = duraklat, [ ] = yön -/+ 2°, - = = ölçek, Q/Esc = çıkış\n"
              "Genel kısayollar (VR'dayken de çalışır): F8 = kalibrasyon, F9 = duraklat/devam",
        "en": "Keys (preview window): C = calibrate, P = pause, [ ] = yaw -/+ 2°, - = = scale, Q/Esc = quit\n"
              "Global hotkeys (work while in VR): F8 = calibrate, F9 = pause/resume",
    },
}


def set_language(code):
    global _lang
    _lang = code if code in LANGUAGES else "en"


def T(key, **kw):
    entry = MESSAGES.get(key, {})
    text = entry.get(_lang) or entry.get("en") or key
    return text.format(**kw) if kw else text


def ascii_fold(text):
    """OpenCV's Hershey fonts only draw ASCII (ı/ş/ğ… become '?')."""
    text = text.replace("ı", "i").replace("İ", "I").replace("°", " deg").replace("…", "...")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
