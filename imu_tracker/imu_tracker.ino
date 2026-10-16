// WebcamFBT IMU tracker: ESP32 + MPU6050 -> raw accelerometer and gyro samples over Wi-Fi (UDP).
//
// The tracker does no sensor fusion itself: it streams raw samples at 200 Hz and the engine on the
// PC fuses them with the webcam (orientation from the gyro, drift corrected by the camera). That way
// tuning never needs reflashing, and movement recordings keep the raw data for replay.
//
// Wiring (ESP32 DevKit):  MPU6050 VCC -> 3V3, GND -> GND, SDA -> GPIO21, SCL -> GPIO22 (AD0 open).
// ESP32-WROOM-32U has no antenna on the board: plug a 2.4 GHz antenna into its u.FL socket first.
//
// Finding the PC: until the engine answers, the tracker broadcasts a "hello" twice a second on
// UDP 39580. The engine replies, and from then on samples go straight to the PC that replied.
// If the PC stays silent for 5 s, the tracker goes back to broadcasting hellos.

#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>

#include "secrets.h"  // WIFI_SSID, WIFI_PASSWORD: copy secrets.example.h to secrets.h and fill it in

static const uint16_t PORT = 39580;
static const uint32_t MAGIC = 0x554D4957;  // "WIMU"
static const uint8_t VERSION = 1;
static const uint8_t TYPE_SAMPLE = 1, TYPE_HELLO = 2, TYPE_ACK = 3;
static const uint32_t PERIOD_US = 5000;  // 200 Hz
static const uint32_t PC_TIMEOUT_MS = 5000;
static const int SDA_PIN = 21, SCL_PIN = 22;
static const uint8_t MPU = 0x68;

#pragma pack(push, 1)
struct Packet {
  uint32_t magic;
  uint8_t version;
  uint8_t type;
  uint16_t seq;
  uint32_t id;     // from the chip's MAC address, tells trackers apart
  uint32_t t_us;   // micros() when the sample was read
  int16_t accel[3];  // +-16 g, 2048 LSB/g, sensor axes
  int16_t gyro[3];   // +-2000 deg/s, 16.4 LSB/(deg/s), sensor axes
  uint16_t battery_mv;  // 0: not measured
};
#pragma pack(pop)

WiFiUDP udp;
IPAddress pc;
bool havePc = false;
uint32_t lastAckMs = 0, lastHelloMs = 0, lastStatusMs = 0;
uint32_t trackerId = 0, nextSampleUs = 0, sent = 0, readErrors = 0;
uint16_t seq = 0;
bool mpuOk = false;

static bool writeReg(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(MPU);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

static int readReg(uint8_t reg) {
  Wire.beginTransmission(MPU);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return -1;
  if (Wire.requestFrom(MPU, (uint8_t)1) != 1) return -1;
  return Wire.read();
}

static bool initMpu() {
  int who = readReg(0x75);  // WHO_AM_I: 0x68 on a genuine MPU6050, clones answer 0x70, 0x72, 0x98...
  Serial.printf("MPU6050 WHO_AM_I = 0x%02X\n", who);
  if (who < 0) return false;
  bool ok = writeReg(0x6B, 0x80);  // reset
  delay(100);
  ok &= writeReg(0x6B, 0x01);  // wake up, clock from the gyro PLL
  ok &= writeReg(0x1A, 0x02);  // low-pass ~94 Hz (accel) / ~98 Hz (gyro)
  ok &= writeReg(0x19, 0x04);  // sample rate 1 kHz / (1 + 4) = 200 Hz
  ok &= writeReg(0x1B, 0x18);  // gyro +-2000 deg/s
  ok &= writeReg(0x1C, 0x18);  // accel +-16 g (a heel strike can pass 8 g)
  return ok;
}

static bool readMpu(int16_t *accel, int16_t *gyro) {
  Wire.beginTransmission(MPU);
  Wire.write(0x3B);  // ACCEL_XOUT_H .. GYRO_ZOUT_L, temperature in between
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(MPU, (uint8_t)14) != 14) return false;
  uint8_t b[14];
  for (int i = 0; i < 14; i++) b[i] = Wire.read();
  for (int i = 0; i < 3; i++) {
    accel[i] = (int16_t)((b[2 * i] << 8) | b[2 * i + 1]);
    gyro[i] = (int16_t)((b[8 + 2 * i] << 8) | b[9 + 2 * i]);
  }
  return true;
}

static void sendPacket(const Packet &p, const IPAddress &to) {
  udp.beginPacket(to, PORT);
  udp.write((const uint8_t *)&p, sizeof(p));
  udp.endPacket();
}

static Packet header(uint8_t type) {
  Packet p = {};
  p.magic = MAGIC;
  p.version = VERSION;
  p.type = type;
  p.seq = seq++;
  p.id = trackerId;
  p.t_us = micros();
  return p;
}

static void pollPc() {
  int size = udp.parsePacket();
  while (size > 0) {
    uint8_t buf[16];
    int n = udp.read(buf, sizeof(buf));
    if (n >= 6) {
      uint32_t magic;
      memcpy(&magic, buf, 4);
      if (magic == MAGIC && buf[5] == TYPE_ACK) {
        if (!havePc || !(pc == udp.remoteIP())) Serial.printf("PC found: %s\n", udp.remoteIP().toString().c_str());
        pc = udp.remoteIP();
        havePc = true;
        lastAckMs = millis();
      }
    }
    size = udp.parsePacket();
  }
  if (havePc && millis() - lastAckMs > PC_TIMEOUT_MS) {
    Serial.println("PC silent: looking for it again");
    havePc = false;
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);
  trackerId = (uint32_t)(ESP.getEfuseMac() & 0xFFFFFFFF);
  Serial.printf("\nWebcamFBT IMU tracker %08lX\n", (unsigned long)trackerId);

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);
  mpuOk = initMpu();
  Serial.println(mpuOk ? "MPU6050 ready" : "MPU6050 NOT FOUND: check wiring (SDA 21, SCL 22, 3V3, GND)");

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // modem sleep would add 100+ ms of latency
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("Connecting to %s", WIFI_SSID);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) {
    delay(250);
    Serial.print(".");
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " connected" : " not yet (keeps trying)");
  udp.begin(PORT);
  nextSampleUs = micros();
}

void loop() {
  uint32_t nowUs = micros();
  if ((int32_t)(nowUs - nextSampleUs) >= 0) {
    nextSampleUs += PERIOD_US;
    if ((int32_t)(nowUs - nextSampleUs) > (int32_t)(4 * PERIOD_US)) nextSampleUs = nowUs + PERIOD_US;  // fell behind
    if (mpuOk && havePc && WiFi.status() == WL_CONNECTED) {
      Packet p = header(TYPE_SAMPLE);
      if (readMpu(p.accel, p.gyro)) {
        sendPacket(p, pc);
        sent++;
      } else {
        readErrors++;
      }
    }
  }

  if (WiFi.status() == WL_CONNECTED) {
    pollPc();
    if (!havePc && millis() - lastHelloMs > 500) {
      lastHelloMs = millis();
      sendPacket(header(TYPE_HELLO), IPAddress(255, 255, 255, 255));
    }
  }

  if (millis() - lastStatusMs > 1000) {
    lastStatusMs = millis();
    if (WiFi.status() != WL_CONNECTED)
      Serial.println("Wi-Fi: not connected (2.4 GHz network? antenna plugged in?)");
    else
      Serial.printf("Wi-Fi %d dBm, IP %s, PC %s, %lu samples/s, read errors %lu\n", WiFi.RSSI(),
                    WiFi.localIP().toString().c_str(), havePc ? pc.toString().c_str() : "searching",
                    (unsigned long)sent, (unsigned long)readErrors);
    sent = 0;
  }
}
