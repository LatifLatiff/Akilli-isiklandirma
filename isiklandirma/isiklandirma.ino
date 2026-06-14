#include <ESP8266WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <BH1750.h>
#include <ArduinoJson.h>
#include <time.h>

// =======================
// WiFi ve MQTT Ayarlari
// =======================
const char* ssid = "Galaxy A104469";
const char* password = "asdfghjkl";

// Buraya bilgisayarinin WiFi IPv4 adresini yaz
// Ornek: 192.168.1.35
const char* mqtt_server = "192.168.164.95";

const int mqtt_port = 1883;

const char* telemetry_topic = "tarim_isik/9/telemetry";
const char* command_topic   = "tarim_isik/9/command";

// =======================
// Donanim Ayarlari
// =======================
BH1750 lightMeter;
WiFiClient espClient;
PubSubClient client(espClient);

#define RELAY_PIN D6

// Cogu role modulu LOW aktif calisir.
// Role ters calisirsa RELAY_ON ve RELAY_OFF degerlerini yer degistir.
#define RELAY_ON LOW
#define RELAY_OFF HIGH

// BH1750 baglanti:
// SDA -> D2
// SCL -> D1

// =======================
// Bitki ve Kontrol Modu
// =======================
String plantType = "marul";

float targetLux = 1000.0;
int lightStartHour = 6;
int lightDurationHour = 14;
int lightEndHour = 20;

bool ledState = false;

// auto       = sensore gore otomatik kontrol
// manual_on  = panelden manuel ac
// manual_off = panelden manuel kapat
String controlMode = "auto";

// =======================
// Bitki Profili Ayarlama
// =======================
void setPlantProfile(String plant) {
  plant.toLowerCase();

  if (plant == "marul") {
    plantType = "marul";
    targetLux = 1000.0;
    lightStartHour = 6;
    lightDurationHour = 14;
    lightEndHour = 20;
  }
  else if (plant == "nane") {
    plantType = "nane";
    targetLux = 800.0;
    lightStartHour = 7;
    lightDurationHour = 12;
    lightEndHour = 19;
  }
  else if (plant == "feslegen") {
    plantType = "feslegen";
    targetLux = 1200.0;
    lightStartHour = 6;
    lightDurationHour = 16;
    lightEndHour = 22;
  }
  else {
    plantType = "marul";
    targetLux = 1000.0;
    lightStartHour = 6;
    lightDurationHour = 14;
    lightEndHour = 20;
  }

  Serial.println();
  Serial.println("Yeni bitki profili secildi:");
  Serial.print("Bitki: ");
  Serial.println(plantType);
  Serial.print("Hedef Lux: ");
  Serial.println(targetLux);
  Serial.print("Baslangic Saati: ");
  Serial.println(lightStartHour);
  Serial.print("Sure: ");
  Serial.println(lightDurationHour);
  Serial.print("Bitis Saati: ");
  Serial.println(lightEndHour);
}

// =======================
// WiFi Baglantisi
// =======================
void setup_wifi() {
  delay(100);
  Serial.println();
  Serial.print("WiFi baglaniyor: ");
  Serial.println(ssid);

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  int retry = 0;

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    retry++;

    if (retry > 60) {
      Serial.println();
      Serial.println("WiFi baglantisi basarisiz. Cihaz yeniden baslatiliyor...");
      ESP.restart();
    }
  }

  Serial.println();
  Serial.println("WiFi baglandi.");
  Serial.print("NodeMCU IP: ");
  Serial.println(WiFi.localIP());
}

// =======================
// Saat Ayari
// =======================
void setup_time() {
  // Turkiye UTC+3
  configTime(3 * 3600, 0, "pool.ntp.org", "time.nist.gov");

  Serial.print("NTP saat aliniyor");

  time_t now = time(nullptr);
  int retry = 0;

  while (now < 100000 && retry < 20) {
    delay(500);
    Serial.print(".");
    now = time(nullptr);
    retry++;
  }

  Serial.println();

  if (now < 100000) {
    Serial.println("NTP saat alinamadi. Saat bilgisi gecici olarak 12:00 kabul edilecek.");
  } else {
    Serial.println("Saat alindi.");
  }
}

int getCurrentHour() {
  time_t now = time(nullptr);

  if (now < 100000) {
    return 12;
  }

  struct tm* timeInfo = localtime(&now);
  return timeInfo->tm_hour;
}

int getCurrentMinute() {
  time_t now = time(nullptr);

  if (now < 100000) {
    return 0;
  }

  struct tm* timeInfo = localtime(&now);
  return timeInfo->tm_min;
}

// =======================
// Isik Periyodu Kontrolu
// =======================
bool isLightPeriod(int hour) {
  int endHour = (lightStartHour + lightDurationHour) % 24;

  if (lightDurationHour >= 24) {
    return true;
  }

  if (lightStartHour < endHour) {
    return hour >= lightStartHour && hour < endHour;
  }

  return hour >= lightStartHour || hour < endHour;
}

// =======================
// LED / Role Kontrolu
// =======================
void setLed(bool state) {
  ledState = state;

  if (state) {
    digitalWrite(RELAY_PIN, RELAY_ON);
  } else {
    digitalWrite(RELAY_PIN, RELAY_OFF);
  }
}

// =======================
// Isik Durumu
// =======================
String getLightStatus(float lux) {
  if (lux < targetLux) {
    return "YETERSIZ";
  }

  return "YETERLI";
}

// =======================
// MQTT Command Callback
// =======================
void callback(char* topic, byte* payload, unsigned int length) {
  String topicStr = String(topic);

  String message = "";
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }

  Serial.println();
  Serial.print("MQTT mesaj geldi. Topic: ");
  Serial.println(topicStr);
  Serial.print("Payload: ");
  Serial.println(message);

  if (topicStr != String(command_topic)) {
    return;
  }

  StaticJsonDocument<512> doc;

  DeserializationError error = deserializeJson(doc, message);

  if (error) {
    Serial.print("JSON parse hatasi: ");
    Serial.println(error.c_str());
    return;
  }

  String command = doc["command"] | "";

  // Flask panelinden bitki secimi
  if (command == "set_plant") {
    String selectedPlant = doc["plant_type"] | "";

    if (selectedPlant != "") {
      setPlantProfile(selectedPlant);
    }
  }

  // Flask panelinden manuel isik kontrolu
  if (command == "set_light_mode") {
    String selectedMode = doc["mode"] | "auto";

    if (
      selectedMode == "auto" ||
      selectedMode == "manual_on" ||
      selectedMode == "manual_off"
    ) {
      controlMode = selectedMode;

      Serial.print("Yeni kontrol modu: ");
      Serial.println(controlMode);
    }
  }
}

// =======================
// MQTT Baglantisi
// =======================
void reconnect_mqtt() {
  while (!client.connected()) {
    Serial.print("MQTT baglaniyor... ");

    String clientId = "NodeMCU_Tarim_Isik_";
    clientId += String(ESP.getChipId());

    if (client.connect(clientId.c_str())) {
      Serial.println("baglandi.");

      client.subscribe(command_topic);

      Serial.print("Command topic dinleniyor: ");
      Serial.println(command_topic);
    }
    else {
      Serial.print("basarisiz. rc=");
      Serial.print(client.state());
      Serial.println(" | 5 saniye sonra tekrar denenecek.");
      delay(5000);
    }
  }
}

// =======================
// Telemetry Gonderme
// =======================
void publishTelemetry(float lux, String durum, String mod, String saatStr) {
  String payload = "{";

  payload += "\"problem_id\":\"tarim_isik\",";
  payload += "\"takim_no\":9,";
  payload += "\"mesaj_tipi\":\"telemetry\",";
  payload += "\"device_id\":\"node_01\",";
  payload += "\"plant_type\":\"" + plantType + "\",";
  payload += "\"sensor\":\"BH1750\",";
  payload += "\"lux\":" + String(lux, 2) + ",";
  payload += "\"target_lux\":" + String(targetLux, 0) + ",";
  payload += "\"light_start_hour\":" + String(lightStartHour) + ",";
  payload += "\"light_duration_hour\":" + String(lightDurationHour) + ",";
  payload += "\"light_end_hour\":" + String(lightEndHour) + ",";
  payload += "\"durum\":\"" + durum + "\",";
  payload += "\"mod\":\"" + mod + "\",";
  payload += "\"saat\":\"" + saatStr + "\",";
  payload += "\"led\":";
  payload += ledState ? "true" : "false";
  payload += ",";
  payload += "\"control_mode\":\"" + controlMode + "\"";

  payload += "}";

  client.publish(telemetry_topic, payload.c_str());

  Serial.print("Telemetry gonderildi: ");
  Serial.println(payload);
}

// =======================
// Setup
// =======================
void setup() {
  Serial.begin(115200);
  delay(1000);

  pinMode(RELAY_PIN, OUTPUT);
  setLed(false);

  setPlantProfile("marul");

  Wire.begin(D2, D1);

  bool bh1750_status = lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE);

  if (bh1750_status) {
    Serial.println("BH1750 baslatildi.");
  } else {
    Serial.println("BH1750 baslatilamadi. Baglantilari kontrol et.");
  }

  setup_wifi();
  setup_time();

  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(callback);
  client.setBufferSize(512);
}

// =======================
// Loop
// =======================
void loop() {
  if (!client.connected()) {
    reconnect_mqtt();
  }

  client.loop();

  int hour = getCurrentHour();
  int minute = getCurrentMinute();

  float lux = lightMeter.readLightLevel();

  if (lux < 0) {
    lux = 0;
  }

  bool lightPeriod = isLightPeriod(hour);

  // =======================
  // Kontrol Karari
  // =======================
  if (controlMode == "manual_on") {
    setLed(true);
  }
  else if (controlMode == "manual_off") {
    setLed(false);
  }
  else {
    // Otomatik mod
    if (lightPeriod) {
      if (lux < targetLux) {
        setLed(true);
      } else {
        setLed(false);
      }
    } else {
      setLed(false);
    }
  }

  String durum = getLightStatus(lux);
  String mod = lightPeriod ? "ISIK_PERIYODU" : "GECE_MODU";

  String saatStr = "";

  if (hour < 10) {
    saatStr += "0";
  }

  saatStr += String(hour);
  saatStr += ":";

  if (minute < 10) {
    saatStr += "0";
  }

  saatStr += String(minute);

  publishTelemetry(lux, durum, mod, saatStr);

  delay(5000);
}