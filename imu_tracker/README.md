# IMU tracker (ESP32 + MPU6050) — deneysel / experimental

*English summary at the bottom.*

Kemere (belin arkasına) takılan küçük bir hareket sensörü. Webcam konumu verir; sensör kalçanın
yönünü saniyede 200 kez, ~5 ms gecikmeyle ölçer (kameradan gelen kalça dönüşü ~150–190 ms geriden
geliyor ve 20 Hz). Tek tracker için kalça seçildi: oyunlarda gövdenin yönü kalçadan gelir. Sensörün
yön kayması kamerayla düzeltilir. Şu an ilk aşama: sensör veriyi Wi-Fi ile bilgisayara gönderiyor ve
`imu_test.py` ile kontrol ediliyor. Motorla birleştirme bir sonraki adım.

**Yer:** kemer hizasında belin arkasının ortası (kuyruk sokumunun hemen üstü); arkalıklı sandalyede çok
oturuluyorsa kalçanın yanı, kalça kemiğinin üstü. Kutu kemere sıkı otursun, içinde MPU6050 oynamasın.

## Parçalar
- ESP32 geliştirme kartı (ör. ESP32-WROOM-32U'lu DevKit). **32U'nun kart üstü anteni yok:** u.FL (IPEX)
  soketine 2,4 GHz bir anten tak, yoksa Wi-Fi bağlanmaz ya da çok kısa mesafede çalışır.
- MPU6050 modülü (GY-521).
- Güç: ilk denemede USB kabloyla bir powerbank. Kalıcı sürüm için 18650 + korumalı TP4056 (DW01'li)
  + 5 V yükseltici; DevKit'lerdeki AMS1117 regülatörü pilden doğrudan beslenince düzgün çalışmaz.
  Pili asla doğrudan 3V3 pinine bağlama (4,2 V ESP'yi yakar).

## Bağlantı
| MPU6050 | ESP32 |
|---|---|
| VCC | 3V3 |
| GND | GND |
| SDA | GPIO21 |
| SCL | GPIO22 |
| AD0, INT | boş |

## Yükleme (Arduino IDE)
1. Arduino IDE 2'yi kur. *Dosya → Tercihler → Ek kart yöneticisi URL'leri*:
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
2. *Kart Yöneticisi*'nde **esp32** (Espressif Systems) paketini kur.
3. Bu klasördeki `secrets.example.h` dosyasını aynı klasöre `secrets.h` adıyla kopyala ve içine Wi-Fi
   adını ve şifresini yaz. Ağ **2,4 GHz** olmalı. `secrets.h` git'e yüklenmez.
4. `imu_tracker.ino`'yu aç, kart olarak **ESP32 Dev Module**'ü ve kartın COM portunu seç, *Yükle*.
   Yükleme başlamazsa yüklerken karttaki **BOOT** düğmesini basılı tut.
5. *Seri Port Ekranı*'nı 115200 baud'da aç: `MPU6050 ready`, `connected` ve saniyede bir durum satırı görmelisin.

## Sonraki güncellemeler Wi-Fi'dan
USB veri kablosu sadece **ilk** yükleme için gerekir. Sonrasında tracker yeni yazılımı Wi-Fi'dan alır
(ağda `webcamfbt-imu-XXXXXXXX` adıyla görünür). Bunun için `secrets.h`'deki `OTA_PASSWORD`'e bir şifre
yaz; güncellerken bu şifre sorulur. Kart güç almak için şarj kablosuyla da çalışır.

## Kontrol
WebcamFBT motorunu durdur (aynı UDP portunu kullanırlar), sonra:
```
.venv\Scripts\python.exe tracker\imu_test.py
```
- Windows güvenlik duvarı Python için izin sorarsa **Özel ağlar**'a izin ver.
- Sensör masada dururken: ~200/s, `gravity` ~1.00 g, `still`, kayıp paket çok az olmalı.
- Sensörü çevirince `pitch`/`roll` değişmeli; `yaw(gyro only)` yavaşça kayar, bu normal (motorda kamera düzeltecek).

---
**English:** ESP32 + MPU6050 streams raw accel/gyro at 200 Hz over UDP 39580 (the PC answers the
tracker's broadcast "hello", then samples go unicast to it). Wiring: VCC→3V3, GND→GND, SDA→21, SCL→22.
Copy `secrets.example.h` to `secrets.h` with your 2.4 GHz Wi-Fi, flash with the esp32 Arduino core
(board "ESP32 Dev Module"), check with `tracker/imu_test.py`. ESP32-WROOM-32U needs an external antenna. Only the first upload needs USB; later ones go over Wi-Fi (ArduinoOTA, password OTA_PASSWORD).
Worn on the belt at the lower back (hip tracker). Fusion with the webcam in the engine is the next step.
