import json
import sqlite3
# Gerekli paket yoksa: pip install requests
import requests
from datetime import datetime
from threading import Thread

from flask import Flask, render_template_string, request, redirect, url_for
import paho.mqtt.client as mqtt

# ML kutuphaneleri
# Eger yuklu degilse uygulama yine acilir, sadece ML tahmini pasif olur.
try:
    import joblib
    import pandas as pd
except Exception:
    joblib = None
    pd = None


MQTT_BROKER = "localhost"
MQTT_PORT = 1883

MQTT_TOPIC = "tarim_isik/9/telemetry"
MQTT_COMMAND_TOPIC = "tarim_isik/9/command"

DB_NAME = "iot_data.db"

OPENWEATHER_API_KEY = "BURAYA_API_KEYINI_YAZ"  # OpenWeather API key'inizi buraya yazın 
CITY = "Bursa"
COUNTRY_CODE = "TR"
WEATHER_CACHE_SECONDS = 600

MODEL_FILE = "led_decision_model.pkl"

PLANT_PROFILES = {
    "marul": {
        "target_lux": 1000,
        "light_start_hour": 6,
        "light_duration_hour": 14,
        "light_end_hour": 20
    },
    "nane": {
        "target_lux": 800,
        "light_start_hour": 7,
        "light_duration_hour": 12,
        "light_end_hour": 19
    },
    "feslegen": {
        "target_lux": 1200,
        "light_start_hour": 6,
        "light_duration_hour": 16,
        "light_end_hour": 22
    }
}

app = Flask(__name__)

ml_package = None
weather_cache = None
weather_cache_time = None

last_control_mode = "auto"
last_selected_plant = "marul"


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sensor_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT
        )
    """)

    cursor.execute("PRAGMA table_info(sensor_data)")
    existing_columns = [row[1] for row in cursor.fetchall()]

    required_columns = {
        "problem_id": "TEXT",
        "takim_no": "INTEGER",
        "mesaj_tipi": "TEXT",
        "device_id": "TEXT",
        "plant_type": "TEXT",
        "sensor": "TEXT",
        "lux": "REAL",
        "target_lux": "REAL",
        "light_start_hour": "INTEGER",
        "light_duration_hour": "INTEGER",
        "light_end_hour": "INTEGER",
        "durum": "TEXT",
        "mod": "TEXT",
        "saat": "TEXT",
        "led": "INTEGER"
    }

    for column, col_type in required_columns.items():
        if column not in existing_columns:
            cursor.execute(f"ALTER TABLE sensor_data ADD COLUMN {column} {col_type}")

    conn.commit()
    conn.close()


def load_ml_model():
    global ml_package

    if joblib is None or pd is None:
        ml_package = None
        print("ML kutuphaneleri yuklu degil. Gerekirse: pip install pandas scikit-learn joblib")
        return

    try:
        ml_package = joblib.load(MODEL_FILE)
        print("ML modeli yuklendi:", MODEL_FILE)
    except Exception as e:
        ml_package = None
        print("ML modeli yuklenemedi:", e)


def parse_hour(data):
    saat = data.get("saat")

    if isinstance(saat, str) and ":" in saat:
        try:
            return int(saat.split(":")[0])
        except Exception:
            pass

    timestamp = data.get("timestamp")

    if isinstance(timestamp, str):
        try:
            if pd is not None:
                return pd.to_datetime(timestamp).hour
            return datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").hour
        except Exception:
            pass

    return datetime.now().hour


def predict_ml(data):
    if ml_package is None:
        return {
            "decision": "MODEL_YOK",
            "text": "Model yok",
            "confidence": 0
        }

    try:
        model = ml_package["model"]
        feature_columns = ml_package["feature_columns"]

        lux = float(data.get("lux") or 0)
        target_lux = float(data.get("target_lux") or 1000)
        light_start_hour = int(data.get("light_start_hour") or 6)
        light_duration_hour = int(data.get("light_duration_hour") or 14)
        hour = parse_hour(data)
        plant_type = data.get("plant_type") or "marul"

        sample = pd.DataFrame([{
            "lux": lux,
            "target_lux": target_lux,
            "light_start_hour": light_start_hour,
            "light_duration_hour": light_duration_hour,
            "hour": hour,
            "plant_type": plant_type
        }])

        sample = pd.get_dummies(sample, columns=["plant_type"])
        sample = sample.reindex(columns=feature_columns, fill_value=0)

        prediction = int(model.predict(sample)[0])

        confidence = 0
        if hasattr(model, "predict_proba"):
            probabilities = model.predict_proba(sample)[0]
            confidence = round(float(max(probabilities)) * 100, 2)

        if prediction == 1:
            return {
                "decision": "LED_AC",
                "text": "LED AÇ",
                "confidence": confidence
            }

        return {
            "decision": "LED_KAPAT",
            "text": "LED KAPAT",
            "confidence": confidence
        }

    except Exception as e:
        return {
            "decision": "HATA",
            "text": f"Tahmin hatasi: {e}",
            "confidence": 0
        }


def normalize_led(value):
    if isinstance(value, bool):
        return 1 if value else 0

    if isinstance(value, int):
        return 1 if value == 1 else 0

    if isinstance(value, str):
        return 1 if value.lower() in ["true", "1", "on", "acik", "açik", "açık"] else 0

    return 0


def get_weather():
    global weather_cache, weather_cache_time

    now = datetime.now()

    if weather_cache is not None and weather_cache_time is not None:
        cache_age = (now - weather_cache_time).total_seconds()
        if cache_age < WEATHER_CACHE_SECONDS:
            return weather_cache

    if (
        not OPENWEATHER_API_KEY
        or OPENWEATHER_API_KEY == "BURAYA_API_KEYINI_YAZ"
        or OPENWEATHER_API_KEY == "api_keyinizi_buraya_yazin"
    ):
        weather_cache = {
            "ok": False,
            "city": CITY,
            "description": "API key yok",
            "temp": "-",
            "humidity": "-",
            "clouds": "-",
            "wind_speed": "-",
            "sunrise": "-",
            "sunset": "-",
            "error": "OpenWeather API key girilmemis"
        }
        weather_cache_time = now
        return weather_cache

    try:
        url = "https://api.openweathermap.org/data/2.5/weather"

        params = {
            "q": f"{CITY},{COUNTRY_CODE}",
            "appid": OPENWEATHER_API_KEY,
            "units": "metric",
            "lang": "tr"
        }

        response = requests.get(url, params=params, timeout=5)
        data = response.json()

        if response.status_code != 200:
            weather_cache = {
                "ok": False,
                "city": CITY,
                "description": "Hava verisi alinamadi",
                "temp": "-",
                "humidity": "-",
                "clouds": "-",
                "wind_speed": "-",
                "sunrise": "-",
                "sunset": "-",
                "error": data.get("message", "Bilinmeyen hata")
            }
            weather_cache_time = now
            return weather_cache

        timezone_offset = int(data.get("timezone", 0))
        sunrise = datetime.utcfromtimestamp(data["sys"]["sunrise"] + timezone_offset).strftime("%H:%M")
        sunset = datetime.utcfromtimestamp(data["sys"]["sunset"] + timezone_offset).strftime("%H:%M")

        weather_cache = {
            "ok": True,
            "city": data.get("name", CITY),
            "description": data["weather"][0]["description"],
            "temp": data["main"]["temp"],
            "humidity": data["main"]["humidity"],
            "clouds": data["clouds"]["all"],
            "wind_speed": data["wind"]["speed"],
            "sunrise": sunrise,
            "sunset": sunset,
            "error": None
        }
        weather_cache_time = now
        return weather_cache

    except Exception as e:
        weather_cache = {
            "ok": False,
            "city": CITY,
            "description": "Baglanti hatasi",
            "temp": "-",
            "humidity": "-",
            "clouds": "-",
            "wind_speed": "-",
            "sunrise": "-",
            "sunset": "-",
            "error": str(e)
        }
        weather_cache_time = now
        return weather_cache


def save_to_db(data):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO sensor_data (
            timestamp,
            problem_id,
            takim_no,
            mesaj_tipi,
            device_id,
            plant_type,
            sensor,
            lux,
            target_lux,
            light_start_hour,
            light_duration_hour,
            light_end_hour,
            durum,
            mod,
            saat,
            led
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        data.get("problem_id", "tarim_isik"),
        int(data.get("takim_no", 9)),
        data.get("mesaj_tipi", "telemetry"),
        data.get("device_id", "node_01"),
        data.get("plant_type", "marul"),
        data.get("sensor", "BH1750"),
        float(data.get("lux", 0)),
        float(data.get("target_lux", 1000)),
        int(data.get("light_start_hour", 6)),
        int(data.get("light_duration_hour", 14)),
        int(data.get("light_end_hour", 20)),
        data.get("durum", "BILINMIYOR"),
        data.get("mod", "BILINMIYOR"),
        data.get("saat", "--:--"),
        normalize_led(data.get("led", False))
    ))

    conn.commit()
    conn.close()


def publish_mqtt_command(payload):
    try:
        client = mqtt.Client()
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.publish(MQTT_COMMAND_TOPIC, json.dumps(payload), qos=1)
        client.disconnect()

        print("Komut gonderildi:", payload)
        return True

    except Exception as e:
        print("Komut gonderilemedi:", e)
        return False


def send_plant_command(plant_type):
    global last_selected_plant

    if plant_type not in PLANT_PROFILES:
        return False

    profile = PLANT_PROFILES[plant_type]

    payload = {
        "problem_id": "tarim_isik",
        "takim_no": 9,
        "mesaj_tipi": "command",
        "command": "set_plant",
        "plant_type": plant_type,
        "target_lux": profile["target_lux"],
        "light_start_hour": profile["light_start_hour"],
        "light_duration_hour": profile["light_duration_hour"],
        "light_end_hour": profile["light_end_hour"]
    }

    success = publish_mqtt_command(payload)

    if success:
        last_selected_plant = plant_type

    return success


def send_light_command(mode):
    global last_control_mode

    allowed_modes = ["auto", "manual_on", "manual_off"]

    if mode not in allowed_modes:
        return False

    payload = {
        "problem_id": "tarim_isik",
        "takim_no": 9,
        "mesaj_tipi": "command",
        "command": "set_light_mode",
        "mode": mode
    }

    success = publish_mqtt_command(payload)

    if success:
        last_control_mode = mode

    return success


@app.route("/select_plant", methods=["POST"])
def select_plant():
    plant_type = request.form.get("plant_type", "marul")
    send_plant_command(plant_type)
    return redirect(url_for("index"))


@app.route("/control_light", methods=["POST"])
def control_light():
    mode = request.form.get("mode", "auto")
    send_light_command(mode)
    return redirect(url_for("index"))


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("MQTT baglandi")
        client.subscribe(MQTT_TOPIC)
        print("Dinlenen topic:", MQTT_TOPIC)
    else:
        print("MQTT baglanti hatasi:", rc)


def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode("utf-8")
        print("Gelen veri:", payload)

        data = json.loads(payload)
        save_to_db(data)

    except Exception as e:
        print("Veri isleme hatasi:", e)


def mqtt_thread():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_forever()
    except Exception as e:
        print("MQTT baslatilamadi:", e)


@app.route("/")
def index():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 
            timestamp,
            device_id,
            plant_type,
            sensor,
            lux,
            target_lux,
            light_start_hour,
            light_duration_hour,
            light_end_hour,
            durum,
            mod,
            saat,
            led
        FROM sensor_data
        ORDER BY id DESC
        LIMIT 50
    """)

    rows = cursor.fetchall()

    cursor.execute("""
        SELECT 
            timestamp,
            device_id,
            plant_type,
            sensor,
            lux,
            target_lux,
            light_start_hour,
            light_duration_hour,
            light_end_hour,
            durum,
            mod,
            saat,
            led
        FROM sensor_data
        ORDER BY id DESC
        LIMIT 1
    """)

    latest = cursor.fetchone()
    conn.close()

    if latest:
        latest_data = {
            "timestamp": latest[0],
            "device_id": latest[1],
            "plant_type": latest[2],
            "sensor": latest[3],
            "lux": latest[4],
            "target_lux": latest[5],
            "light_start_hour": latest[6],
            "light_duration_hour": latest[7],
            "light_end_hour": latest[8],
            "durum": latest[9],
            "mod": latest[10],
            "saat": latest[11],
            "led": latest[12]
        }
        ml_result = predict_ml(latest_data)
        selected_plant = latest[2] or last_selected_plant
    else:
        ml_result = {
            "decision": "VERI_YOK",
            "text": "Veri yok",
            "confidence": 0
        }
        selected_plant = last_selected_plant

    weather = get_weather()

    html = """
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <title>Tarım Işıklandırma IoT Paneli</title>
        <meta http-equiv="refresh" content="5">

        <style>
            body {
                font-family: Arial, sans-serif;
                background: #eef5ee;
                padding: 30px;
                color: #222;
            }

            h1 {
                color: #1b5e20;
                margin-bottom: 5px;
            }

            .subtitle {
                color: #555;
                margin-bottom: 25px;
            }

            .cards {
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 18px;
                margin-bottom: 25px;
            }

            .card {
                background: white;
                padding: 20px;
                border-radius: 16px;
                box-shadow: 0 2px 12px rgba(0,0,0,0.10);
            }

            .ml-card {
                border: 2px solid #2e7d32;
                background: #f4fff4;
            }

            .control-card {
                border: 2px solid #1565c0;
                background: #f3f8ff;
            }

            .weather-card {
                border: 2px solid #f9a825;
                background: #fffdf2;
            }

            .card h2 {
                font-size: 16px;
                margin-top: 0;
                color: #555;
            }

            .value {
                font-size: 30px;
                font-weight: bold;
                color: #2e7d32;
            }

            .small-value {
                font-size: 22px;
                font-weight: bold;
                color: #2e7d32;
            }

            .led-on, .ml-on {
                color: #2e7d32;
                font-weight: bold;
            }

            .led-off, .ml-off {
                color: #c62828;
                font-weight: bold;
            }

            .YETERSIZ {
                color: #c62828;
                font-weight: bold;
            }

            .YETERLI {
                color: #2e7d32;
                font-weight: bold;
            }

            .ISIK_PERIYODU {
                color: #1565c0;
                font-weight: bold;
            }

            .GECE_MODU {
                color: #6a1b9a;
                font-weight: bold;
            }

            select {
                width: 100%;
                padding: 10px;
                border-radius: 8px;
                border: 1px solid #bbb;
                font-size: 15px;
                margin-bottom: 10px;
            }

            button {
                width: 100%;
                padding: 10px;
                margin-bottom: 8px;
                border: none;
                border-radius: 8px;
                color: white;
                font-weight: bold;
                cursor: pointer;
                font-size: 14px;
            }

            .btn-green {
                background: #2e7d32;
            }

            .btn-red {
                background: #c62828;
            }

            .btn-blue {
                background: #1565c0;
            }

            table {
                width: 100%;
                border-collapse: collapse;
                background: white;
                border-radius: 14px;
                overflow: hidden;
                box-shadow: 0 2px 12px rgba(0,0,0,0.10);
            }

            th, td {
                padding: 12px;
                border-bottom: 1px solid #ddd;
                text-align: center;
                font-size: 14px;
            }

            th {
                background: #2e7d32;
                color: white;
            }

            tr:hover {
                background: #f1f8e9;
            }

            @media (max-width: 1000px) {
                .cards {
                    grid-template-columns: repeat(2, 1fr);
                }
            }

            @media (max-width: 600px) {
                .cards {
                    grid-template-columns: 1fr;
                }
            }
        </style>
    </head>

    <body>
        <h1>🌱 Tarım Işıklandırma IoT Paneli</h1>
        <div class="subtitle">
            MQTT Telemetry: tarim_isik/9/telemetry |
            MQTT Command: tarim_isik/9/command
        </div>

        <div class="cards">
            <div class="card weather-card">
                <h2>🌤️ Bursa Hava Durumu</h2>
                <div class="small-value">{{ weather.description }}</div>
                <p>Şehir: {{ weather.city }}</p>
            </div>

            <div class="card weather-card">
                <h2>Dış Sıcaklık</h2>
                <div class="value">{{ weather.temp }} °C</div>
                <p>Nem: %{{ weather.humidity }}</p>
            </div>

            <div class="card weather-card">
                <h2>Bulutluluk</h2>
                <div class="value">%{{ weather.clouds }}</div>
                <p>Rüzgâr: {{ weather.wind_speed }} m/s</p>
            </div>

            <div class="card weather-card">
                <h2>Güneş Bilgisi</h2>
                <div class="small-value">{{ weather.sunrise }} - {{ weather.sunset }}</div>
                <p>Gün doğumu / Gün batımı</p>
            </div>
        </div>

        {% if not weather.ok %}
        <div class="card">
            OpenWeather uyarısı: {{ weather.error }}
        </div>
        <br>
        {% endif %}

        <div class="cards">
            <div class="card control-card">
                <h2>Bitki Seçimi</h2>

                <form method="POST" action="/select_plant">
                    <select name="plant_type">
                        <option value="marul" {% if selected_plant == "marul" %}selected{% endif %}>Marul</option>
                        <option value="nane" {% if selected_plant == "nane" %}selected{% endif %}>Nane</option>
                        <option value="feslegen" {% if selected_plant == "feslegen" %}selected{% endif %}>Fesleğen</option>
                    </select>

                    <button type="submit" class="btn-green">Bitkiyi Güncelle</button>
                </form>

                <p>Seçim MQTT ile NodeMCU'ya gönderilir.</p>
            </div>

            <div class="card control-card">
                <h2>Manuel Işık Kontrolü</h2>

                <form method="POST" action="/control_light">
                    <button type="submit" name="mode" value="auto" class="btn-blue">
                        OTOMATİK MOD
                    </button>

                    <button type="submit" name="mode" value="manual_on" class="btn-green">
                        IŞIĞI AÇ
                    </button>

                    <button type="submit" name="mode" value="manual_off" class="btn-red">
                        IŞIĞI KAPAT
                    </button>
                </form>

                <p>Son komut modu: <b>{{ last_control_mode }}</b></p>
            </div>

            <div class="card control-card">
                <h2>Komut Topic</h2>
                <div class="small-value">tarim_isik/9/command</div>
                <p>Bitki seçimi ve manuel ışık komutları bu topic üzerinden gider.</p>
            </div>

            <div class="card control-card">
                <h2>Sistem Modu</h2>
                {% if last_control_mode == "auto" %}
                    <div class="small-value">OTOMATİK</div>
                    <p>Sensör + hedef lux kuralı aktif.</p>
                {% elif last_control_mode == "manual_on" %}
                    <div class="led-on">MANUEL AÇIK</div>
                    <p>LED açık komutu gönderildi.</p>
                {% elif last_control_mode == "manual_off" %}
                    <div class="led-off">MANUEL KAPALI</div>
                    <p>LED kapalı komutu gönderildi.</p>
                {% endif %}
            </div>
        </div>

        {% if latest %}
        <div class="cards">
            <div class="card">
                <h2>Anlık Lux</h2>
                <div class="value">{{ "%.2f"|format(latest[4]) }} lux</div>
                <p>Hedef: {{ "%.0f"|format(latest[5]) }} lux</p>
            </div>

            <div class="card">
                <h2>Seçilen Bitki</h2>
                <div class="small-value">{{ latest[2] }}</div>
                <p>Sensör: {{ latest[3] }}</p>
            </div>

            <div class="card">
                <h2>Işık Rejimi</h2>
                <div class="small-value">
                    {{ latest[6] }}:00 - {{ latest[8] }}:00
                </div>
                <p>Süre: {{ latest[7] }} saat</p>
            </div>

            <div class="card">
                <h2>LED Durumu</h2>
                {% if latest[12] == 1 %}
                    <div class="led-on">AÇIK</div>
                {% else %}
                    <div class="led-off">KAPALI</div>
                {% endif %}
                <p class="{{ latest[10] }}">{{ latest[10] }}</p>
            </div>
        </div>

        <div class="cards">
            <div class="card">
                <h2>Işık Durumu</h2>
                <div class="{{ latest[9] }}">{{ latest[9] }}</div>
            </div>

            <div class="card">
                <h2>Cihaz Saati</h2>
                <div class="small-value">{{ latest[11] }}</div>
            </div>

            <div class="card">
                <h2>Cihaz</h2>
                <div class="small-value">{{ latest[1] }}</div>
            </div>

            <div class="card">
                <h2>Son Kayıt</h2>
                <div>{{ latest[0] }}</div>
            </div>
        </div>

        <div class="cards">
            <div class="card ml-card">
                <h2>ML Tahmini</h2>

                {% if ml_result.decision == "LED_AC" %}
                    <div class="ml-on">{{ ml_result.text }}</div>
                {% elif ml_result.decision == "LED_KAPAT" %}
                    <div class="ml-off">{{ ml_result.text }}</div>
                {% else %}
                    <div>{{ ml_result.text }}</div>
                {% endif %}

                <p>Model dosyası: led_decision_model.pkl</p>
            </div>

            <div class="card ml-card">
                <h2>ML Güven Oranı</h2>
                <div class="small-value">%{{ ml_result.confidence }}</div>
                <p>Son ölçüme göre tahmin</p>
            </div>

            <div class="card ml-card">
                <h2>Kural vs ML</h2>
                <p>Kural kararı:
                    {% if latest[12] == 1 %}
                        <span class="led-on">LED AÇIK</span>
                    {% else %}
                        <span class="led-off">LED KAPALI</span>
                    {% endif %}
                </p>
                <p>ML önerisi: <b>{{ ml_result.text }}</b></p>
            </div>

            <div class="card ml-card">
                <h2>ML Girdileri</h2>
                <p>Lux: {{ "%.2f"|format(latest[4]) }}</p>
                <p>Bitki: {{ latest[2] }} | Saat: {{ latest[11] }}</p>
            </div>
        </div>
        {% else %}
            <div class="card">
                <h2>Henüz veri yok</h2>
                <p>NodeMCU MQTT üzerinden veri göndermeye başladığında burada görünecek.</p>
            </div>
            <br>
        {% endif %}

        <h2>Son Ölçümler</h2>

        <table>
            <tr>
                <th>Zaman</th>
                <th>Cihaz</th>
                <th>Bitki</th>
                <th>Lux</th>
                <th>Hedef Lux</th>
                <th>Işık Aralığı</th>
                <th>Durum</th>
                <th>Mod</th>
                <th>LED</th>
            </tr>

            {% for row in rows %}
            <tr>
                <td>{{ row[0] }}</td>
                <td>{{ row[1] }}</td>
                <td>{{ row[2] }}</td>
                <td>{{ "%.2f"|format(row[4]) }}</td>
                <td>{{ "%.0f"|format(row[5]) }}</td>
                <td>{{ row[6] }}:00 - {{ row[8] }}:00</td>
                <td class="{{ row[9] }}">{{ row[9] }}</td>
                <td class="{{ row[10] }}">{{ row[10] }}</td>
                <td>
                    {% if row[12] == 1 %}
                        <span class="led-on">AÇIK</span>
                    {% else %}
                        <span class="led-off">KAPALI</span>
                    {% endif %}
                </td>
            </tr>
            {% endfor %}
        </table>
    </body>
    </html>
    """

    return render_template_string(
        html,
        rows=rows,
        latest=latest,
        ml_result=ml_result,
        weather=weather,
        selected_plant=selected_plant,
        last_control_mode=last_control_mode
    )


if __name__ == "__main__":
    init_db()
    load_ml_model()

    t = Thread(target=mqtt_thread)
    t.daemon = True
    t.start()

    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)