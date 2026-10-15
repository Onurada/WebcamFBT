"""Engine texts in every supported language. To add a language, add its code to LANGUAGES
and a translation to every entry below (missing entries fall back to English)."""

import unicodedata

LANGUAGES = ("tr", "en")
_lang = "en"
_hotkeys = {"cal": "F8", "pause": "F9"}  # filled into every text that mentions a hotkey

MESSAGES = {
    "extra_camera_failed": {
        "tr": 'Ek kamera {i} açılamadı ({err}); onsuz devam ediliyor.',
        "en": 'Extra camera {i} could not be opened ({err}); continuing without it.',
    },
    "vr_connected": {"tr": "SteamVR'a bağlanıldı: {name} ({conn})", "en": "Connected to SteamVR: {name} ({conn})"},
    "vr_headset": {"tr": "Kask: {name} ({conn})", "en": "Headset: {name} ({conn})"},
    "conn_meta_link": {"tr": "Meta Horizon Link: kablo ya da Air Link", "en": "Meta Horizon Link: cable or Air Link"},
    "conn_steam_link": {"tr": "Steam Link", "en": "Steam Link"},
    "conn_virtual_desktop": {"tr": "Virtual Desktop", "en": "Virtual Desktop"},
    "conn_alvr": {"tr": "ALVR", "en": "ALVR"},
    "conn_lighthouse": {"tr": "SteamVR Lighthouse", "en": "SteamVR Lighthouse"},
    "conn_wmr": {"tr": "Windows Mixed Reality", "en": "Windows Mixed Reality"},
    "conn_pico_connect": {"tr": "Pico Connect", "en": "Pico Connect"},
    "conn_other": {"tr": "SteamVR", "en": "SteamVR"},
    "vr_closed": {"tr": "SteamVR kapandı", "en": "SteamVR closed"},
    "vr_error": {"tr": "SteamVR hatası: {err}", "en": "SteamVR error: {err}"},
    "calib_failed": {
        "tr": 'Kamera {i}: kalibrasyon başarısız, omuzlardan ayaklara vücut veya kask yeterince görülmedi',
        "en": 'Camera {i}: calibration failed, the body (shoulders to feet) or the headset was not seen well enough',
    },
    "calib_moved": {
        "tr": 'Kamera {i}: kalibrasyon iptal, ölçüm sırasında yürüdün ya da döndün. Önceki kalibrasyon (varsa) korunuyor.',
        "en": 'Camera {i}: calibration discarded, you walked or turned while it measured. The previous calibration (if any) is kept.',
    },
    "calib_no_head": {
        "tr": "(kafa görüntüde değildi: vücut omuzlardan kaska bağlandı)",
        "en": "(head not in the picture: the body hangs from the headset by the shoulders)",
    },
    "calib_auto": {
        "tr": "Dik ve hareketsiz durduğun görüldü: otomatik kalibrasyon, 2 saniye kıpırdama...",
        "en": "Standing still detected: calibrating automatically, hold still for 2 seconds...",
    },
    "calib_gesture": {
        "tr": "İki el yukarıda: kalibrasyon başlıyor. Kollarını indir, dik dur ve ileri bak.",
        "en": "Both hands up: calibration starts. Lower your arms, stand straight and look ahead.",
    },
    "calib_reset_vr": {
        "tr": "SteamVR yeniden bağlandı, takip alanı değişmiş olabilir: kalibrasyon sıfırlandı.",
        "en": "SteamVR reconnected and its tracking space may have changed: calibration cleared.",
    },
    "calib_ok": {
        "tr": 'Kamera {i}: kalibrasyon tamam (yön {yaw:.1f}°, ölçek {scale:.2f}, zemin {floor:.2f} m, {n} örnek)',
        "en": 'Camera {i}: calibration done (yaw {yaw:.1f}°, scale {scale:.2f}, floor {floor:.2f} m, {n} samples)',
    },
    "calib_loaded": {
        "tr": "Kayıtlı kalibrasyon yüklendi (kamera {cams}). Kamerayı oynattıysan ya da kaskta merkezleme yaptıysan {cal} ile yenile.",
        "en": 'Saved calibration loaded (camera {cams}). Press {cal} to redo it if a camera moved or you recentered the headset.',
    },
    "camera_ok": {"tr": "Kamera {i}: çalışıyor {w}x{h}", "en": "Camera {i}: working {w}x{h}"},
    "camera_no_image": {"tr": "Kamera {i}: açıldı ama görüntü yok", "en": "Camera {i}: opened but no image"},
    "camera_open_failed": {
        "tr": "Kamera {i} açılamadı. Kamera takılı olmayabilir, numarası yanlış olabilir (Tara ile kontrol et) ya da "
              "Windows kamera izni kapalı olabilir (Ayarlar > Gizlilik ve güvenlik > Kamera > Masaüstü uygulamaları).",
        "en": "Could not open camera {i}. It may be unplugged, the index may be wrong (check with Scan), or Windows "
              "camera access may be off (Settings > Privacy & security > Camera > Desktop apps).",
    },
    "camera_no_frames": {
        "tr": "Kamera {i} açıldı ama görüntü gelmiyor. Başka bir program (Discord, OBS, Zoom, tarayıcı) kamerayı "
              "kullanıyor olabilir; onu kapatınca motor kendiliğinden bağlanır.",
        "en": "Camera {i} opened but sends no image. Another program (Discord, OBS, Zoom, a browser) may be using it; "
              "close it and the engine reconnects by itself.",
    },
    "camera_lost": {"tr": "Kamera bağlantısı koptu, yeniden bağlanılıyor…", "en": "Camera connection lost, reconnecting…"},
    "camera_back": {"tr": "Kamera yeniden bağlandı", "en": "Camera reconnected"},
    "error": {"tr": "HATA: {err}", "en": "ERROR: {err}"},
    "crashed": {
        "tr": "Motor bir hatayla durdu: {err}. Ayrıntılar logs\\engine.log dosyasında.",
        "en": "The engine stopped with an error: {err}. Details are in logs\\engine.log.",
    },
    "config_invalid": {
        "tr": "Ayar '{key}' geçersiz ({value}), {default} kullanılıyor.",
        "en": "Setting '{key}' is invalid ({value}), using {default}.",
    },
    "config_corrupt": {
        "tr": "Ayar dosyası okunamadı ({err}). Bozuk dosya {backup} olarak saklandı, varsayılan ayarlar kullanılıyor.",
        "en": "The settings file could not be read ({err}). The broken file was kept as {backup}; using defaults.",
    },
    "driver_not_loaded": {
        "tr": "SteamVR'da WebcamFBT tracker'ı yok: sürücü yüklü değil ya da kapalı. SteamVR'ı kapat, arayüzde "
              "'Kur / onar'a bas ve SteamVR'ı yeniden aç (SteamVR > Ayarlar > Başlangıç/Kapanış > Eklentiler'de açık olmalı).",
        "en": "SteamVR has no WebcamFBT trackers: the driver is not installed or is disabled. Close SteamVR, press "
              "'Install / repair' in the app and reopen SteamVR (it must be on under SteamVR > Settings > Startup/Shutdown > Add-ons).",
    },
    "port_busy": {
        "tr": "SteamVR sürücüsü veri alamıyor: UDP port {port} başka bir program tarafından kullanılıyor. O programı "
              "kapatıp SteamVR'ı yeniden başlat.",
        "en": "The SteamVR driver cannot receive data: UDP port {port} is used by another program. Close that "
              "program and restart SteamVR.",
    },
    "hotkey_failed": {
        "tr": "Kısayol {key} kaydedilemedi: başka bir program kullanıyor olabilir. Arayüzden başka bir tuş seç.",
        "en": "Hotkey {key} could not be registered: another program may be using it. Pick another key in the app.",
    },
    "driver_outdated": {
        "tr": "SteamVR eski bir WebcamFBT sürücüsü çalıştırıyor (sürüm {have}, gereken {want}). SteamVR'ı tamamen "
              "kapatıp yeniden aç; düzelmezse SteamVR kapalıyken 'Kur / onar'a bas.",
        "en": "SteamVR is running an older WebcamFBT driver (version {have}, needed {want}). Fully close and reopen "
              "SteamVR; if that does not help, press 'Install / repair' while SteamVR is closed.",
    },
    "driver_ok": {"tr": "SteamVR sürücüsü yeniden veri alıyor.", "en": "The SteamVR driver is receiving data again."},
    "rec_waiting": {
        "tr": "Kayıt en erken {s} saniye sonra, kalibrasyon yapılınca başlayacak ({n} saniye sürecek). Kaskı tak, kameranın önünde dik dur; iki bipte kayıt başlar.",
        "en": "Recording starts once calibrated, {s} seconds from now at the earliest, and lasts {n} seconds. Put the headset on and stand still in front of the camera; two beeps mark the start.",
    },
    "rec_started": {
        "tr": "Kayıt başladı ({n} sn): yürü, dön, otur-kalk, adım at; her zamanki gibi oyna. Bitince tiz bip.",
        "en": "Recording ({n} s): walk, turn, sit and stand, take steps; play as usual. A high beep marks the end.",
    },
    "rec_saved": {"tr": "Kayıt kaydedildi: {path}", "en": "Recording saved: {path}"},
    "rec_empty": {"tr": "Kayıt boş: kamera kimseyi görmedi.", "en": "Recording is empty: the camera saw nobody."},
    "rec_cancelled": {"tr": "Kayıt iptal edildi.", "en": "Recording cancelled."},
    "calib_needed_auto": {
        "tr": "Kalibrasyon gerekli: kask takılıyken kameranın önünde dik dur, ileri bak ve 2 saniye kıpırdama; "
              "kendiliğinden kalibre olur (ya da {cal}). Kalibre edilene kadar tracker gönderilmez.",
        "en": "Calibration needed: wearing the headset, stand straight in front of the camera, look ahead and hold "
              "still for 2 seconds; it calibrates by itself (or press {cal}). No trackers are sent until then.",
    },
    "calib_needed": {
        "tr": "Kalibrasyon gerekli: kask takılıyken kameranın önünde dik dur ve {cal} tuşuna bas. Kalibre edilene kadar tracker gönderilmez.",
        "en": "Calibration needed: wearing the headset, stand straight in front of the camera and press {cal}. No trackers are sent until then.",
    },
    "recenter_maybe": {
        "tr": "SteamVR takip alanının değiştiğini bildirdi. Tracker'lar kayık görünürse iki elini başının üstünde "
              "2 saniye tut (ya da {cal}) ve yeniden kalibre et.",
        "en": "SteamVR reported a tracking space change. If the trackers look off, hold both hands above your head "
              "for 2 seconds (or press {cal}) to recalibrate.",
    },
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
    "ov_nocal": {"tr": "Kalibrasyon yok: kaskı tak, {cal} tuşuna bas", "en": "Not calibrated: put the headset on, press {cal}"},
    "ov_cal": {"tr": "Kalibre (ölçek {scale:.2f})  gönderilen tracker: {n}", "en": "Calibrated (scale {scale:.2f})  trackers sent: {n}"},
    "ov_paused": {"tr": "DURAKLATILDI ({pause})", "en": "PAUSED ({pause})"},
    "help": {
        "tr": "Tuşlar (önizleme penceresi): C = kalibrasyon, P = duraklat, [ ] = yön -/+ 2°, - = = ölçek, Q/Esc = çıkış\n"
              "Genel kısayollar (VR'dayken de çalışır): {cal} = kalibrasyon, {pause} = duraklat/devam",
        "en": "Keys (preview window): C = calibrate, P = pause, [ ] = yaw -/+ 2°, - = = scale, Q/Esc = quit\n"
              "Global hotkeys (work while in VR): {cal} = calibrate, {pause} = pause/resume",
    },
}


def set_language(code):
    global _lang
    _lang = code if code in LANGUAGES else "en"


def set_hotkeys(calibrate, pause):
    _hotkeys.update(cal=calibrate, pause=pause)


def T(key, **kw):
    entry = MESSAGES.get(key, {})
    text = entry.get(_lang) or entry.get("en") or key
    return text.format(**{**_hotkeys, **kw})


def ascii_fold(text):
    """OpenCV's Hershey fonts only draw ASCII (ı/ş/ğ… become '?')."""
    text = text.replace("ı", "i").replace("İ", "I").replace("°", " deg").replace("…", "...")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
