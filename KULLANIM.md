# WebcamFBT — Webcam ile Full Body Tracking (Meta Quest / SteamVR)

*English: [README.md](README.md)*

Webcam görüntüsünden vücut iskeleti çıkarır (MediaPipe Pose), kaskının konumuna sabitler ve
SteamVR'a **sanal Vive tracker** olarak gönderir. VRChat, Vivecraft ve diğer tüm SteamVR oyunlarında çalışır.

Kullanılabilen tracker'lar: kalça, göğüs, iki ayak, iki diz, iki dirsek. En sağlam sonucu **kalça + iki ayak** verir.

## Kurulum
1. GitHub'daki **Releases** sayfasından `WebcamFBT-Setup-<sürüm>.exe` dosyasını indir.
2. **SteamVR'ı kapat** ve kurulum dosyasını çalıştır. Python ya da .NET kurmana gerek yok, hepsi pakette var.
   - Yönetici izni istemez, programı senin kullanıcı klasörüne kurar: `%LOCALAPPDATA%\Programs\WebcamFBT`.
   - "WebcamFBT sürücüsünü SteamVR'a kaydet" seçeneği sürücüyü ve tracker rollerini ayarlar.
   - Kurulum dosyası imzalı olmadığı için Windows SmartScreen uyarı verebilir: *Ek bilgi → Yine de çalıştır*.
3. İlk açılışta kurulum sihirbazı seni adım adım yönlendirir.

**Güncelleme:** Uygulama açılışta yeni sürüm olup olmadığına bakar (*Genel* bölümünden kapatılabilir). Yeni kurulum dosyasını çalıştırman yeterli; ayarların ve kalibrasyonun korunur.

**Kaldırma:** *Ayarlar → Uygulamalar → WebcamFBT → Kaldır*. Sürücüyü SteamVR'dan ve tracker rollerini kaldırır, klasörü tamamen siler. SteamVR açıksa önce onu kapat.

> Depo **private** olduğu sürece Releases sayfasını ve güncelleme kontrolünü sadece sen görebilirsin. Bir arkadaşınla paylaşmak için kurulum dosyasını ona doğrudan gönder.

## Desteklenen kasklar
WebcamFBT sadece kaskın SteamVR'daki konumunu okur, bu yüzden SteamVR ile çalışan her kaskla çalışır:
- **Meta Quest 1, 2, 3, 3S, Pro:** Link kablosu, Air Link, Steam Link, Virtual Desktop ya da ALVR ile.
- **Oculus Rift, Rift S:** Meta Horizon Link uygulamasıyla.
- **Diğer SteamVR kaskları:** Valve Index, HTC Vive, Pico, Windows Mixed Reality.

Uygulama bağlı kaskı tanır ve SteamVR göstergesinde adını gösterir (ör. *bağlı · Meta Quest 3*). Göstergenin üstüne gelince bağlantı yolu ve o kaskta merkezlemenin nasıl yapıldığı görünür. Quest 3 ve Steam Link ile denendi; diğer kasklar aynı yoldan çalışır ama henüz denenmedi.

## Başlarken
1. **Kamerayı yerleştir:** Yaklaşık 2–3 m uzağa, bel ya da göğüs hizasına koy. En az **omuzlarından ayaklarına kadar** görünmelisin; kafan kadraj dışında kalabilir (kafa da görünürse biraz daha hassas olur). Sığmıyorsa kamerayı biraz aşağı eğ: ayakların kadrajda kalması kafadan daha önemli. Ortam iyi aydınlatılmış olmalı; karanlıkta kamera saniyede 30 yerine 20 kare verir.
2. Kaskını PC'ye bağla (Quest için Link kablosu / Air Link / Steam Link / Virtual Desktop / ALVR) ve **SteamVR**'ı aç.
3. `app\WebcamFBT.exe`'yi aç ve **Başlat**'a bas. Motor yaklaşık 3 saniyede açılır.
4. **Kalibrasyon (tuşa basmadan):** Kask takılıyken kameranın önünde **dik dur, düz ileri bak ve 2 saniye kıpırdama**, kollar yanda.
   - Uygulama hareketsiz durduğunu görünce **kendiliğinden** kalibre eder: iki kısa bip = ölçüyor, kıpırdama; tiz bip = tamam.
   - Kalın uzun bip: olmadı (ör. ölçerken hareket ettin). Tekrar hareketsiz durman yeter.
   - Bu yalnızca kalibrasyon yokken olur; çalışan bir kalibrasyonu kendiliğinden değiştirmez.
   - **Yeniden kalibre etmek için** iki elini (kumandalar ya da eller) **başının üstünde 2 saniye tut**. Bipten sonra kollarını indir, dik dur, ileri bak. **F8** de hâlâ çalışır.
5. Oyundaki FBT kalibrasyonunu yap.
   - **VRChat:** T-pose ver, iki tetiğe bas.
   - **Vivecraft:** Önce *Playermodel Settings* altında model tipini *Split Arms* ya da *Split Arms/Legs* yap, sonra *Calibrate FBT…* çalıştır.

> **Her oturumda kalibrasyon gerekir**, ama artık tek yapman gereken kameranın önünde bir an hareketsiz durmak. Quest'in takip alanı oturumdan oturuma değişebildiği için eski kalibrasyon kullanılmaz ve SteamVR yeniden bağlanınca kalibrasyon sıfırlanır. Kamerayı oynatırsan ya da kaskta merkezleme yaparsan iki eli kaldır (ya da F8). Zemin yüksekliği her an doğrudan SteamVR'dan alınır.
> Yeni tracker'ları (dirsek, diz…) açtıysan oyundaki FBT kalibrasyonunu da yenile.

## İlk açılış ve bildirim alanı
- **Kurulum sihirbazı:** İlk açılışta 5 adımda dili, SteamVR sürücüsünü, kamerayı, kameranın yerleşimini ve kalibrasyonu anlatır. *Genel → Kurulum sihirbazı…* ile tekrar açılır.
- **Kamera adları:** Kameralar adıyla listelenir (ör. *Microsoft LifeCam VX-1000*). OBS ve Meta Quest Link'in eklediği **sanal** kameralar işaretlenir ve seçilirse uyarı verilir; telefon uygulamaları (DroidCam, Iriun) *telefon* olarak görünür.
- **Bildirim alanı (sistem tepsisi):** Pencere küçültülünce bildirim alanına gider ve takip arka planda sürer. Simgenin menüsünden Göster, Kalibre et, Duraklat ve Çıkış seçilebilir; simgenin üstüne gelince durum görünür. Pencere gizliyken kalibrasyon sonucu ve önemli uyarılar balon bildirimiyle gelir. *Genel* bölümünden kapatılabilir.

## Birden fazla kamera (isteğe bağlı)
Varsayılan tek kameradır. *Kamera ve model* bölümündeki **Ek kamera 1 / 2** ile en fazla iki kamera daha eklenebilir (telefonunu DroidCam ya da Iriun ile webcam olarak da kullanabilirsin).

- **Ne kazandırır:** Her kamera kendi görüş ekseninde (derinlikte) zayıftır. İki kamera birbirine 60–90° açıyla durunca birinin zayıf olduğu ekseni diğeri iyi görür, sonuçlar buna göre ağırlıklı birleştirilir. Bir kameradan saklanan eklemi diğeri görebilir.
- **Arkadan bakan kamera:** Sağı solu karıştırabileceği için, önden gören bir kamera varken birleştirmeye katılmaz.
- **Kalibrasyon:** F8 tüm kameraları aynı anda kalibre eder. Her kamera seni en az omuzlardan ayaklara görmeli. Kalibre edilemeyen kamera uyarısı çıkarsa o kamerayı düzeltip tekrar F8'e bas.
- **Performans:** Her kamera kendi modelini paralel çalıştırır. Bu bilgisayarda iki kamera full modelle kamera başına saniyede yaklaşık 25 kare işleyebiliyor.
- Ek kamera açılamazsa motor onsuz devam eder.

## Kurulum asistanı
*Kamera ve model → Kurulum asistanı…* ile açılır, motor çalışırken kullanılır. İki modu var, hangisini kullanacağını sen seçersin:

**Kask ile oda haritası**
- SteamVR açık ve kask takılıyken oyun alanını okur. İstersen **Odayı gezerek tara** ile kaskla odanın duvarları boyunca bir tur atarsın, kameralar duvarın dışına önerilmez.
- Kamera sayısına (1–3) ve webcam'in görüş açısına göre kameraların nereye, hangi yöne bakacak şekilde konacağını kuşbakışı haritada gösterir.
- Haritada sen mavi noktasın. Yeşil numaralı noktaya yürü ve kamerayı oraya, oyun alanının ortasına bakacak şekilde, yerden yaklaşık 1 m yükseğe koy.
- Renkler: yeşil = tüm kameralar seni baştan ayağa görür, sarı = bazıları, kırmızı = hiçbiri.
- Kalibrasyondan sonra kameraların tahmini gerçek yerini de turuncu olarak gösterebilir.

**Webcam ile yer puanı**
- Kask gerekmez. Kamerayı (ya da telefonu) aday bir yere koy, karşısına geç ve baştan ayağa dur.
- Her kamera için 0–100 puan ve ne yapman gerektiği yazar: ayakların görünmüyor, çok uzaksın, kamera çok yüksek, ortam karanlık gibi.
- Webcam kendi konumunu bilmediği için bu mod harita çıkarmaz; denediğin her yeri ayrı puanlar.

## Arayüz
| Bölüm | Ne yapar |
|---|---|
| Durum çipleri | Motor, kamera FPS'i, modelin bir karede harcadığı süre (ms), SteamVR, kişi, kalibrasyon, gönderilen tracker sayısı |
| Kontrol | Başlat/Durdur, Kalibre et (F8), Duraklat (F9), SteamVR olmadan test |
| Genel | **Dil (Türkçe/English)**, açılışta motoru başlatma, kalibrasyonu saklama (isteğe bağlı, kapalı), önizlemeyi gösterme |
| İnce ayar | Kamera yönünü ±2° döndürür, ölçeği değiştirir (kalibrasyon sonrası küçük düzeltmeler) |
| Tracker'lar | Hangi vücut noktalarının gönderileceği, anında uygulanır |
| Kamera ve model | Kamera seçimi ve tarama, model seçimi: Hızlı (lite, 22 ms) / Dengeli (full, 37 ms) / Hassas (heavy, 133 ms) |
| Yumuşatma | Titreme azaltma, hızlı harekette gecikme azaltma, derinlik yumuşatma, görünürlük eşiği, zemin kilidi, ayak sabitleme |
| SteamVR sürücüsü | Kur/onar ve kaldır (SteamVR kapalıyken) |

## Kısayollar
| Tuş | İşlev |
|---|---|
| **F8** (her yerde) | Kalibrasyon |
| **F9** (her yerde) | Tracker gönderimini duraklat / devam ettir |

Tuşlar *Genel* bölümünden değiştirilebilir (F1–F12, Ctrl ile de). Windows'un `RegisterHotKey` mekanizması kullanıldığı için seçilen tuş WebcamFBT açıkken oyuna gitmez.

## Performans ve kararlılık
Bu bilgisayarda (Ryzen 5 2600) ölçülen değerler:
- Model süresi: lite 22 ms, full 37 ms, heavy 133 ms. Önizleme ve diğer adımlar toplam 2 ms'nin altında.
- Asıl sınır kamera: karanlık odada saniyede 20 kare. İyi ışıkla 30'a çıkabilir.

Kararlılık için yapılanlar:
- **Sürücüde ara geçiş:** Saniyede ~20 gelen poz, kaskın 72–120 Hz'ine akıcı biçimde yayılır. Karşılığında yaklaşık 50 ms ek gecikme var.
- **Derinlik yumuşatma:** Kameraya yaklaşma/uzaklaşma ekseni, tek kameranın en gürültülü ekseni olduğu için daha güçlü yumuşatılır.
- **Görünürlüğe göre yumuşatma:** Az görünen eklemler daha güçlü yumuşatılır.
- **Ayak sabitleme:** Yerdeki ayak kaldırılana kadar sabit kalır. Ayakta dururken ayak titremesi ortalama 1,1 cm'den 0,3 cm'ye indi.
- **İşlemci önceliği:** Motor "normalin üstü" öncelikte çalışır, oyun işlemciyi yorsa bile takılmaz.
- **Önizleme:** Pencere küçültülünce önizleme durur, işlemci oyuna kalır.
- **Kamera bağlantısı:** Koparsa motor kendiliğinden yeniden bağlanır.

Hatalara karşı önlemler:
- **Otomatik yeniden başlatma:** Motor çökerse ya da 30 saniye yanıt vermezse arayüz onu yeniden başlatır. 3 dakika içinde 3 denemeden sonra durur ve sebebini kayda yazar.
- **Kayıt dosyaları:** `logs\app.log` (arayüz) ve `logs\engine.log` (motor, çökme ayrıntılarıyla birlikte), 1 MB'ta dönüşümlü. *Genel → Kayıt klasörünü aç* ile açılır.
- **Ayar doğrulama:** Geçersiz değerler varsayılana döner ya da sınırlara çekilir. Bozuk `config.json` dosyası `config.json.broken-<tarih>` olarak saklanır, uygulama varsayılanlarla açılır.
- **Anlaşılır hata mesajları:** Kamera açılmıyorsa, kamera başka bir programda açıksa, sürücü yüklü değilse ya da UDP portu meşgulse ne yapman gerektiği yazar ve iki kalın bip sesi duyulur.
- **Sürücü sürüm kontrolü:** SteamVR eski bir WebcamFBT sürücüsü çalıştırıyorsa uyarır.
- **Kaskta merkezleme:** SteamVR takip alanının değiştiğini bildirirse uygulama uyarır (en fazla 5 dakikada bir); kayık görünürse iki elini başının üstünde 2 saniye tut (ya da F8). Kalibrasyon kendiliğinden değiştirilmez: ilk sürümdeki otomatik düzeltme, kask uykudan uyandığında yanlış devreye girip vücudu kaydırdığı için kaldırıldı.

## Sınırlar (tek kamera)
İkinci bir kamera eklemek (yukarıya bak) ilk iki maddeyi büyük ölçüde azaltır.
- Kameraya yaklaşma/uzaklaşma (derinlik) en zayıf eksen.
- Kameraya **sırtını dönersen** sağ/sol karışabilir.
- Oturma, yere yatma ve kameradan saklanan uzuvlar kötü takip edilir.
- Toplam gecikme yaklaşık 120–170 ms (kamera + model + ara geçiş).

## Sorun giderme
- **SteamVR'da tracker yok:** Motor çalışıyor ve kalibrasyon yapılmış olmalı. Tracker'lar ancak ilk veri gelince ortaya çıkar.
- **Tracker'lar kayık ya da ters:** F8 ile yeniden kalibre et. Hâlâ kayıksa *İnce ayar* bölümünü kullan.
- **Oyunda bazı tracker'lar yok (ör. dirsek):** Tracker oyun açıldıktan sonra eklenmiş olabilir; oyundaki FBT kalibrasyonunu yenile. Vivecraft'ta model tipi *Vanilla* ise dirsek görünmez.
- **Tracker rolleri yanlış:** SteamVR → Ayarlar → Kumandalar → *Tracker'ları Yönet* altında WCFBT_HIP = Bel, WCFBT_LFOOT = Sol ayak, … şeklinde ayarla. Ya da arayüzde *Kur / onar*'a bas.
- **Kaldırmak için:** SteamVR'ı kapat, arayüzde *Kaldır*'a bas (ya da `uninstall_driver.bat`).

## Klasör
| Dosya | Ne işe yarar |
|---|---|
| `app\WebcamFBT.exe` | Arayüz uygulaması |
| `tracker\webcam_fbt.py` | Motor (Python, MediaPipe) |
| `tracker\messages.py` | Motorun Türkçe/İngilizce metinleri |
| `tracker\config.json` | Ayarlar |
| `tracker\calibration.json` | Kayıtlı kalibrasyon |
| `webcamfbt\` | SteamVR sürücüsü (`driver_src\` kaynak, `build_driver.bat` ile derlenir) |
| `ui\` | Arayüz kaynak kodu (`ui\Strings\` dil dosyaları) |
| `start.bat` | Motoru arayüz olmadan, OpenCV penceresiyle başlatır |

**Yeniden derlemek için:**
- **Arayüz:** `dotnet publish ui -c Release -o app -p:OutputPath=..\build\ui-bin\`
- **Sürücü:** SteamVR kapalıyken `build_driver.bat`

**Kurulum dosyası üretmek:** `powershell -ExecutionPolicy Bypass -File installer\package.ps1` → `build\installer\WebcamFBT-Setup-<sürüm>.exe`. Gerekenler: Visual Studio (C++), .NET 10 SDK, Python 3.12, Inno Setup 6. Sürüm numarası `VERSION` dosyasında. `git tag v<sürüm>` ve `git push origin v<sürüm>` ile GitHub kurulum dosyasını kendisi üretip Releases'e koyar.

**Yeni dil eklemek için:**
1. `ui\Strings\Strings.en.xaml` dosyasını `Strings.xx.xaml` olarak kopyalayıp çevir ve `ui\Loc.cs` içindeki listeye ekle.
2. `tracker\messages.py` içindeki her mesaja `"xx"` çevirisini ekle.
