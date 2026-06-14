# 🌱 Akıllı Işıklandırma — IoT Tarım Aydınlatma Sistemi

Tarım serası için geliştirilmiş, **NodeMCU (ESP8266)** tabanlı, **MQTT** protokolü üzerinden iletişim kuran ve **makine öğrenmesi** destekli akıllı aydınlatma kontrol sistemi.

---

## 📌 Proje Hakkında

Bu proje, bitki gelişimini optimize etmek için ışık seviyesini otomatik olarak izleyen ve kontrol eden bir IoT sistemidir. Sistem;

- BH1750 ışık sensörü ile ortam lux değerini ölçer,
- Seçilen bitkinin ihtiyacına göre LED/röle kontrolü yapar,
- MQTT üzerinden Flask web arayüzüne veri gönderir,
- Web panelinden manuel veya otomatik mod seçimine olanak tanır,
- Makine öğrenmesi modeli ile LED açma/kapama kararını tahmin eder,
- OpenWeatherMap API üzerinden anlık hava durumu bilgisi gösterir.

---

## 🗂️ Proje Yapısı

```
Akilli-isiklandirma/
├── isiklandirma/
│   ├── isiklandirma.ino       # Arduino / NodeMCU kaynak kodu
│   ├── app.py                 # Flask web sunucusu + MQTT subscriber
│   ├── iot_data.db            # SQLite veritabanı (otomatik oluşturulur)
│   └── led_decision_model.pkl # Eğitilmiş ML modeli
├── IoT_Tarim_Sunumu.pptx      # Proje sunumu
└── README.md
```

---

## 🔧 Donanım Gereksinimleri

| Bileşen | Açıklama |
|---|---|
| NodeMCU (ESP8266) | Ana mikrodenetleyici |
| BH1750 | Işık yoğunluğu sensörü (lux) |
| Röle Modülü | LED / aydınlatma kontrolü |
| LED / Grow Light | Bitki büyüme lambası |

### Bağlantı Şeması

```
BH1750:
  SDA → D2
  SCL → D1
  VCC → 3.3V
  GND → GND

Röle:
  IN  → D6
  VCC → 5V (veya 3.3V, modüle göre)
  GND → GND
```

> **Not:** Çoğu röle modülü LOW aktif çalışır. Eğer röle ters davranıyorsa `isiklandirma.ino` dosyasında `RELAY_ON` ve `RELAY_OFF` değerlerini yer değiştirin.

---

## 🌿 Desteklenen Bitki Profilleri

| Bitki | Hedef Lux | Işık Başlangıcı | Işık Süresi | Işık Bitişi |
|---|---|---|---|---|
| Marul | 1000 lux | 06:00 | 14 saat | 20:00 |
| Nane | 800 lux | 07:00 | 12 saat | 19:00 |
| Fesleğen | 1200 lux | 06:00 | 16 saat | 22:00 |

---

## 🚀 Kurulum

### 1. Arduino / NodeMCU Kurulumu

Arduino IDE'ye aşağıdaki kütüphaneleri yükleyin:

- `ESP8266WiFi`
- `PubSubClient`
- `Wire`
- `BH1750`
- `ArduinoJson`

`isiklandirma.ino` dosyasında kendi ağ bilgilerinizi girin:

```cpp
const char* ssid        = "WiFi_Aginizin_Adi";
const char* password    = "WiFi_Sifreniz";
const char* mqtt_server = "192.168.x.x";  // Bilgisayarınızın yerel IP adresi
```

Kodu NodeMCU'ya yükleyin.

---

### 2. Python / Flask Sunucu Kurulumu

Gerekli Python paketlerini yükleyin:

```bash
pip install flask paho-mqtt requests pandas scikit-learn joblib
```

`app.py` dosyasında OpenWeatherMap API anahtarınızı girin:

```python
OPENWEATHER_API_KEY = "buraya_api_keyinizi_yazin"
CITY = "Bursa"       # Şehrinizi değiştirin
COUNTRY_CODE = "TR"
```

Sunucuyu başlatın:

```bash
cd isiklandirma
python app.py
```

Web arayüzüne tarayıcıdan erişin:

```
http://localhost:5000
```

---

### 3. MQTT Broker Kurulumu

Bilgisayarınıza **Mosquitto** MQTT Broker kurulmalıdır.

**Windows:**
```bash
# https://mosquitto.org/download/ adresinden indirin, ardından:
net start mosquitto
```

**Linux / macOS:**
```bash
sudo apt install mosquitto mosquitto-clients   # Ubuntu/Debian
brew install mosquitto                         # macOS
mosquitto -v                                   # Başlat
```

---

## 📡 MQTT Topic Yapısı

| Topic | Yön | Açıklama |
|---|---|---|
| `tarim_isik/9/telemetry` | NodeMCU → Flask | Sensör verisi (lux, durum, saat...) |
| `tarim_isik/9/command` | Flask → NodeMCU | Bitki seçimi ve ışık modu komutları |

### Telemetry Mesaj Örneği

```json
{
  "problem_id": "tarim_isik",
  "takim_no": 9,
  "mesaj_tipi": "telemetry",
  "device_id": "node_01",
  "plant_type": "marul",
  "sensor": "BH1750",
  "lux": 743.50,
  "target_lux": 1000,
  "light_start_hour": 6,
  "light_duration_hour": 14,
  "light_end_hour": 20,
  "durum": "YETERSIZ",
  "mod": "ISIK_PERIYODU",
  "saat": "09:15",
  "led": true,
  "control_mode": "auto"
}
```

### Command Mesaj Örnekleri

```json
// Bitki değiştirme
{ "command": "set_plant", "plant_type": "nane" }

// Işık modunu değiştirme
{ "command": "set_light_mode", "mode": "manual_on" }
{ "command": "set_light_mode", "mode": "manual_off" }
{ "command": "set_light_mode", "mode": "auto" }
```

---

## 🤖 Makine Öğrenmesi

Sistem, `led_decision_model.pkl` dosyasında saklanan bir sınıflandırma modeli kullanır.

Model giriş özellikleri:

| Özellik | Açıklama |
|---|---|
| `lux` | Anlık ışık değeri |
| `target_lux` | Bitkinin hedef lux değeri |
| `light_start_hour` | Işık periyodu başlangıcı |
| `light_duration_hour` | Işık süresi |
| `hour` | Mevcut saat |
| `plant_type` | Bitki türü (one-hot encoded) |

> Model mevcut değilse sistem kural tabanlı otomatik mantıkla çalışmaya devam eder.

---

## 🌐 Web Arayüzü Özellikleri

- 📊 Canlı sensör verisi tablosu (5 sn. otomatik yenileme)
- 🌤️ Anlık hava durumu (OpenWeatherMap entegrasyonu)
- 🌿 Bitki profili seçimi (Marul / Nane / Fesleğen)
- 💡 Manuel ışık kontrolü (Aç / Kapat / Otomatik)
- 🤖 ML tabanlı LED karar tahmini ve güven skoru
- 📱 Mobil uyumlu (responsive) tasarım

---

## ⚙️ Kontrol Modları

| Mod | Açıklama |
|---|---|
| `auto` | Sensör verisine ve bitki profiline göre otomatik karar |
| `manual_on` | LED'i her zaman açık tutar |
| `manual_off` | LED'i her zaman kapalı tutar |

Otomatik modda karar mantığı:

1. Mevcut saat, ışık periyodunda mı? (`lightStartHour` – `lightEndHour`)
2. **Evet ise:** `lux < targetLux` → LED AÇ, aksi hâlde LED KAPAT
3. **Hayır ise:** LED KAPAT (gece modu)

---

## 📦 Kullanılan Teknolojiler

| Katman | Teknoloji |
|---|---|
| Donanım | NodeMCU ESP8266, BH1750, Röle |
| Firmware | Arduino C++ |
| İletişim | MQTT (Mosquitto Broker) |
| Backend | Python, Flask |
| Veritabanı | SQLite |
| ML | scikit-learn, pandas, joblib |
| Harici API | OpenWeatherMap |

---

## 📄 Lisans

Bu proje eğitim amaçlı geliştirilmiştir.