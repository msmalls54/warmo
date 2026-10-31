// Warmo tank firmware — Seeed XIAO ESP32-S3 Sense
// Arduino IDE: board "XIAO_ESP32S3", Tools > PSRAM = "OPI PSRAM", esp32 core 3.x
// Library: "Adafruit_VL53L1X" (Library Manager). Optional — firmware runs without the ToF attached.
//
// HTTP API (laptop calls these):
//   GET /capture                          -> JPEG
//   GET /move?l=-255..255&r=-255..255&ms=N  (ms <= 1000; stops early if ToF < 250mm while driving forward)
//   GET /ram?ms=N                          (ms <= 1500, full forward, ignores ToF)
//   GET /tof                               -> {"mm":N}  (-1 if no sensor)
//   GET /stop
//   GET /turret?dir=left|right&ms=N        (clamped to +/-700ms from where it was at boot)
// Safety: every motion ends by itself. Nothing ever "keeps driving".

#include <WiFi.h>
#include <WebServer.h>
#include <Wire.h>
#include "esp_camera.h"
#include <Adafruit_VL53L1X.h>

// Keep actual hotspot credentials in the ignored local wifi_secrets.h.
// Copy wifi_secrets.example.h to that name and fill it in before uploading.
#if __has_include("wifi_secrets.h")
#include "wifi_secrets.h"
#endif
#ifndef WARMO_WIFI_SSID
#define WARMO_WIFI_SSID "YOUR_HOTSPOT_NAME"
#endif
#ifndef WARMO_WIFI_PASS
#define WARMO_WIFI_PASS "YOUR_HOTSPOT_PASSWORD"
#endif
const char* WIFI_SSID = WARMO_WIFI_SSID;
const char* WIFI_PASS = WARMO_WIFI_PASS;

// ---- verified pin map ----
#define R_FWD  D3   // right track forward  -> MDD3A #1 M1A
#define R_BACK D6   // right track backward -> MDD3A #1 M1B
#define L_FWD  D2   // left track forward   -> MDD3A #1 M2B
#define L_BACK D1   // left track backward  -> MDD3A #1 M2A
#define T_LEFT D8   // turret left          -> MDD3A #2 M1A
#define T_RIGHT D9  // turret right         -> MDD3A #2 M1B
// D7 = gun: never used.

const int TOF_STOP_MM = 250;
const int MAX_MOVE_MS = 1000;
const int MAX_RAM_MS = 1500;
const int TURRET_LIMIT_MS = 700;
const int TURRET_SPEED = 128;

// ---- XIAO ESP32-S3 Sense camera pins (CAMERA_MODEL_XIAO_ESP32S3) ----
#define PWDN_GPIO_NUM  -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  10
#define SIOD_GPIO_NUM  40
#define SIOC_GPIO_NUM  39
#define Y9_GPIO_NUM    48
#define Y8_GPIO_NUM    11
#define Y7_GPIO_NUM    12
#define Y6_GPIO_NUM    14
#define Y5_GPIO_NUM    16
#define Y4_GPIO_NUM    18
#define Y3_GPIO_NUM    17
#define Y2_GPIO_NUM    15
#define VSYNC_GPIO_NUM 38
#define HREF_GPIO_NUM  47
#define PCLK_GPIO_NUM  13

WebServer server(80);
Adafruit_VL53L1X vl53 = Adafruit_VL53L1X();
bool tofOk = false;
int lastTof = -1;
bool camOk = false;
unsigned long motorDeadline = 0;
bool motorsOn = false;
bool motorPwmOk = false;
long turretOffset = 0;

const int MOTOR_PINS[] = {R_FWD, R_BACK, L_FWD, L_BACK, T_LEFT, T_RIGHT};
static_assert(R_FWD != D7 && R_BACK != D7 && L_FWD != D7 && L_BACK != D7 &&
              T_LEFT != D7 && T_RIGHT != D7, "D7 gun output must remain unused");

// Explicit motor channels 0..5 share one 1kHz/8-bit timer. Initialize them
// BEFORE the camera, whose clock uses channel 7 / timer 3. No analogWrite.
const uint32_t MOTOR_PWM_HZ = 1000;
const uint8_t MOTOR_PWM_BITS = 8;
bool motorPwmAttached[6] = {};

void pinOut(int p, int v) {
  for (size_t i = 0; i < 6; ++i) {
    if (MOTOR_PINS[i] != p) continue;
    if (motorPwmAttached[i]) ledcWrite(p, constrain(v, 0, 255));
    else digitalWrite(p, LOW);  // no full-speed fallback if PWM setup fails
    return;
  }
}

bool initMotorPwm() {
  // ESP32-S3 shares the LEDC source clock across timers. The camera's
  // 20MHz AUTO clock selects APB; use APB here too instead of Arduino's XTAL default.
  if (!ledcSetClockSource(LEDC_USE_APB_CLK)) return false;
  bool ok = true;
  for (uint8_t i = 0; i < 6; ++i) {
    motorPwmAttached[i] = ledcAttachChannel(MOTOR_PINS[i], MOTOR_PWM_HZ, MOTOR_PWM_BITS, i);
    if (motorPwmAttached[i]) ledcWrite(MOTOR_PINS[i], 0);
    else { pinMode(MOTOR_PINS[i], OUTPUT); digitalWrite(MOTOR_PINS[i], LOW); ok = false; }
  }
  return ok;
}

void allLow() {
  for (int p : MOTOR_PINS) pinOut(p, 0);
  motorsOn = false;
}

void setSide(int fwd, int back, int speed) {
  speed = constrain(speed, -255, 255);
  if (speed > 0)      { pinOut(back, 0); pinOut(fwd, speed); }
  else if (speed < 0) { pinOut(fwd, 0);  pinOut(back, -speed); }
  else                { pinOut(fwd, 0);  pinOut(back, 0); }
}

int readTof() {
  if (!tofOk) return -1;
  if (vl53.dataReady()) {
    int16_t d = vl53.distance();
    vl53.clearInterrupt();
    if (d > 0) lastTof = d;
  }
  return lastTof;
}

String json(const String& body) { return "{" + body + "}"; }

// Drive both tracks for ms; optionally stop if ToF sees something close while going forward.
String runMove(int l, int r, int ms, bool useTof) {
  allLow();
  l = constrain(l, -255, 255);
  r = constrain(r, -255, 255);
  if (ms <= 0 || (l == 0 && r == 0))
    return json("\"ran_ms\":0,\"stopped_by\":\"time\",\"tof_mm\":" + String(readTof()));
  bool forward = (l > 0 && r > 0);
  int before = readTof();
  if (useTof && forward && before > 0 && before < TOF_STOP_MM)
    return json("\"ran_ms\":0,\"stopped_by\":\"tof\",\"tof_mm\":" + String(before));
  setSide(L_FWD, L_BACK, l);
  setSide(R_FWD, R_BACK, r);
  motorsOn = true;
  unsigned long start = millis();
  motorDeadline = start + ms;
  String stoppedBy = "time";
  while ((int32_t)(millis() - motorDeadline) < 0) {
    int d = readTof();
    if (useTof && forward && d > 0 && d < TOF_STOP_MM) { stoppedBy = "tof"; break; }
    delay(10);
  }
  allLow();
  int ran = millis() - start;
  return json("\"ran_ms\":" + String(ran) + ",\"stopped_by\":\"" + stoppedBy + "\",\"tof_mm\":" + String(readTof()));
}

void handleMove() {
  if (!motorPwmOk) { allLow(); server.send(503, "application/json", json("\"error\":\"motor PWM unavailable\"")); return; }
  int l = server.hasArg("l") ? server.arg("l").toInt() : 0;
  int r = server.hasArg("r") ? server.arg("r").toInt() : 0;
  int ms = constrain(server.hasArg("ms") ? server.arg("ms").toInt() : 0, 0, MAX_MOVE_MS);
  server.send(200, "application/json", runMove(l, r, ms, true));
}

void handleRam() {
  if (!motorPwmOk) { allLow(); server.send(503, "application/json", json("\"error\":\"motor PWM unavailable\"")); return; }
  int ms = constrain(server.hasArg("ms") ? server.arg("ms").toInt() : 1000, 0, MAX_RAM_MS);
  server.send(200, "application/json", runMove(255, 255, ms, false));
}

void handleTof() { server.send(200, "application/json", json("\"mm\":" + String(readTof()))); }

void handleStop() { allLow(); server.send(200, "application/json", json("\"ok\":true")); }

void handleTurret() {
  allLow();
  if (!motorPwmOk) { server.send(503, "application/json", json("\"error\":\"motor PWM unavailable\"")); return; }
  String dir = server.hasArg("dir") ? server.arg("dir") : "left";
  int ms = constrain(server.hasArg("ms") ? server.arg("ms").toInt() : 300, 0, 500);
  int sign = dir.startsWith("r") ? 1 : -1;
  long target = constrain(turretOffset + sign * ms, -TURRET_LIMIT_MS, TURRET_LIMIT_MS);
  int run = abs(target - turretOffset);
  if (run > 0) {
    pinOut(sign > 0 ? T_LEFT : T_RIGHT, 0);
    pinOut(sign > 0 ? T_RIGHT : T_LEFT, TURRET_SPEED);
    motorsOn = true;
    motorDeadline = millis() + run;
    delay(run);
    allLow();
    turretOffset = target;
  }
  server.send(200, "application/json", json("\"turret_offset_ms\":" + String(turretOffset) + ",\"ran_ms\":" + String(run)));
}

void handleCapture() {
  if (!camOk) { server.send(500, "text/plain", "camera not ready"); return; }
  camera_fb_t* fb = esp_camera_fb_get();          // discard one stale frame
  if (fb) esp_camera_fb_return(fb);
  fb = esp_camera_fb_get();
  if (!fb) { server.send(500, "text/plain", "capture failed"); return; }
  server.setContentLength(fb->len);
  server.send(200, "image/jpeg", "");
  server.client().write(fb->buf, fb->len);
  esp_camera_fb_return(fb);
}

void handleRoot() {
  server.send(200, "text/plain", "WARMO online. /status /capture /move /ram /tof /stop /turret");
}

void handleStatus() {
  server.send(200, "application/json", json(
    "\"name\":\"WARMO\",\"protocol\":1,\"camera_ok\":" + String(camOk ? "true" : "false") +
    ",\"tof_ok\":" + String(tofOk ? "true" : "false") +
    ",\"motors_on\":" + String(motorsOn ? "true" : "false") +
    ",\"pwm\":" + String(motorPwmOk ? "true" : "false") +
    ",\"uptime_ms\":" + String(millis())));
}

bool initCamera() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_7;   // keep the camera clock away from channels/timers Arduino hands out first
  c.ledc_timer = LEDC_TIMER_3;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM; c.pin_vsync = VSYNC_GPIO_NUM;
  c.pin_href = HREF_GPIO_NUM; c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAMESIZE_VGA;      // 640x480; use FRAMESIZE_QVGA if slow
  c.jpeg_quality = 12;
  c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;
  if (esp_camera_init(&c) != ESP_OK) return false;
  sensor_t* s = esp_camera_sensor_get();
  if (s) { s->set_brightness(s, 1); s->set_saturation(s, 0); }
  return true;
}

void setup() {
  // FIRST after firmware starts: all motor pins low. ROM boot can still toggle D6 (UART TX).
  for (int p : MOTOR_PINS) { pinMode(p, OUTPUT); digitalWrite(p, LOW); }
  Serial.begin(115200);
  delay(300);
  Serial.println("\nWARMO booting");

  motorPwmOk = initMotorPwm();
  allLow();
  Serial.println(motorPwmOk ? "motor PWM OK (128 = 50% duty)" : "motor PWM FAILED; motion disabled");

  camOk = initCamera();
  Serial.println(camOk ? "camera OK" : "camera FAILED");

  Wire.begin(D4, D5);
  Wire.setTimeOut(50);
  Wire.beginTransmission(0x29);
  tofOk = (Wire.endTransmission() == 0) && vl53.begin(0x29, &Wire);
  if (tofOk) tofOk = vl53.setTimingBudget(33) && vl53.startRanging();
  Serial.println(tofOk ? "ToF OK" : "ToF not found (running without it)");

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  if (strcmp(WIFI_SSID, "YOUR_HOTSPOT_NAME") != 0 && strlen(WIFI_SSID) > 0) {
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    Serial.print("WiFi");
    unsigned long wifiStart = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - wifiStart < 30000) { delay(300); Serial.print("."); }
    if (WiFi.status() == WL_CONNECTED) {
      Serial.print("\nWARMO IP: http://");
      Serial.println(WiFi.localIP());
    } else Serial.println("\nWiFi not connected; waiting for hotspot with all motors stopped");
  } else Serial.println("WiFi not configured: add local wifi_secrets.h before uploading");

  server.on("/", handleRoot);
  server.on("/status", handleStatus);
  server.on("/capture", handleCapture);
  server.on("/move", handleMove);
  server.on("/ram", handleRam);
  server.on("/tof", handleTof);
  server.on("/stop", handleStop);
  server.on("/turret", handleTurret);
  server.begin();
}

void loop() {
  server.handleClient();
  if (motorsOn && (int32_t)(millis() - motorDeadline) >= 0) allLow();   // backstop
  readTof();
  static unsigned long lastPrint = 0;
  if (millis() - lastPrint > 10000) {                       // re-print IP every 10s for easy finding
    lastPrint = millis();
    if (WiFi.status() == WL_CONNECTED) {
      Serial.print("WARMO IP: http://"); Serial.println(WiFi.localIP());
    } else Serial.println("WiFi disconnected; all motors stopped");
  }
  delay(2);
}
